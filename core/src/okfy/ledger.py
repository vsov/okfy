"""Extraction Ledger (ADR-0013, v0.5 engineering): append-only meta/ledger.jsonl,
one row per pipeline artifact transition (a Worker or a Consolidation step).
Deliberately SHALLOW — segment-level provenance, not per-claim. Rows are written
AFTER the artifact commit, so `commit` pins the bundle HEAD that already contains
the outputs; `input_hashes` resolve from the corpus manifest so a third party can
tell exactly what each transition consumed (or that a hash was 'unknown')."""
import json
from pathlib import Path

from okfy.bundle import Bundle
from okfy.gitenv import run_git
from okfy.proposals import _commit, _refuse_if_ledger_rewritten

LEDGER = "meta/ledger.jsonl"

_REQUIRED_STR = ("run_id", "segment", "prompt_version", "validation")
_REQUIRED_LIST = ("inputs", "outputs")

# SPAN OUTCOMES (v0.19). A green ledger row proves a worker wrote something; it
# does not prove every assigned span was looked at. The optional `spans` block
# records one outcome per input span of the segment's job artifact. It is an
# ATTESTATION — the worker's own report about itself, the same category as the
# `execution` block in job.py — and the output must say so. The core cannot
# observe whether a span was read; it can only record the claim, cover it by the
# ledger, and check it against what IS measurable (see validate.py).
SPAN_CLASSES = ("covered", "reviewed_empty", "dropped")

E_SPAN_BAD_REPORT = "E_SPAN_BAD_REPORT"
E_SPAN_BLANK_REASON = "E_SPAN_BLANK_REASON"
E_SPAN_EMPTY_DRAFTS = "E_SPAN_EMPTY_DRAFTS"
E_SPAN_NO_JOB = "E_SPAN_NO_JOB"
E_SPAN_OUTPUT = "E_SPAN_OUTPUT"

# DROP ACCOUNTING (v0.25, report item 2.2). The donor's costliest bug was
# four unnamed early-exits that swallowed 12 of 12 proposals with no counter
# anywhere. The optional `dropped` block on a ledger row is how a run says
# what it dropped instead of dropping it silently: a plain {reason: count}
# object in the WRITER'S OWN vocabulary — the core never interprets a reason
# string, only counts it (see validate._check_drops_unexplained, which reads
# these totals back to decide whether a declared output was accounted for).
E_LEDGER_DROPPED = "E_LEDGER_DROPPED"

# DROP CORRECTIONS (v0.25 audit F09). The ledger is append-only — an already
# committed row can never explain a loss after the fact, only a NEW row can.
# The optional `corrects` block on a row is that new row: it names an
# EARLIER row (`run_id`/`segment`) and the specific `output` it accounts
# for, carrying its own `dropped` explanation. It never edits history, only
# adds to it — see `check_corrects` and `validate._check_drops_unexplained`,
# which credits the named row/output, and only that row/output, never a
# bundle-wide pool.
E_LEDGER_CORRECTION = "E_LEDGER_CORRECTION"
E_LEDGER_CORRECTION_UNKNOWN = "E_LEDGER_CORRECTION_UNKNOWN"


def unknown_covered_outputs(spans: dict, outputs) -> list[str]:
    """Draft ids a `covered` span names that the row does not list in `outputs`.

    ONE definition, used at write time by `add_row` and at validate time by
    `_check_span_coverage`, because rows already on disk cannot be fixed by a
    write-time refusal and two copies of a join drift.

    This grades a JOIN and nothing else: `outputs` is what the row itself says
    this pass produced, so a `covered` span citing an id that is not there is two
    halves of one row disagreeing — arithmetic over two lists written in the same
    call. It says nothing about whether a worker read the span. That remains
    unknowable to the core and unlabelled as known; see `SPAN_ATTESTED_NOTE`."""
    known = set(outputs if isinstance(outputs, (list, tuple, set)) else [])
    named = {i for ids in (spans.get("covered") or {}).values() for i in ids}
    return sorted(named - known)


def span_key(entry) -> str:
    """The canonical key for one input span, in the anchor grammar concepts
    already cite: `a.md`, `a.md#L1-40` for a `lines` entry, `a.md#C1-4000` for a
    `chars` entry. Accepts a bare path string, a plan segment entry or a job
    artifact input — all three carry the same shape."""
    if isinstance(entry, str):
        return entry
    path = str(entry["path"])
    if entry.get("lines"):
        return f"{path}#L{entry['lines']}"
    if entry.get("chars"):
        return f"{path}#C{entry['chars']}"
    return path


def job_span_keys(job: dict) -> list[str]:
    """The span key set a worker was assigned, in job artifact order."""
    return [span_key(i) for i in (job.get("inputs") or [])]


def check_spans(spans) -> dict:
    """Validate a worker's span outcome report and return it normalised to all
    three classes in fixed order. Malformed reports raise a named error rather
    than reaching the ledger — a partition computed from garbage is worse than
    no partition, because it reads as complete."""
    if not isinstance(spans, dict):
        raise ValueError(f"{E_SPAN_BAD_REPORT}: span report must be an object "
                         f"with keys {', '.join(SPAN_CLASSES)}")
    unknown = sorted(set(spans) - set(SPAN_CLASSES))
    if unknown:
        raise ValueError(f"{E_SPAN_BAD_REPORT}: unknown span class(es): "
                         f"{', '.join(unknown)} (allowed: {', '.join(SPAN_CLASSES)})")
    out: dict[str, dict] = {}
    for cls in SPAN_CLASSES:
        block = spans.get(cls) or {}
        if not isinstance(block, dict):
            raise ValueError(f"{E_SPAN_BAD_REPORT}: {cls} must map span keys to "
                             + ("draft ids" if cls == "covered" else "a reason"))
        norm: dict = {}
        for key, value in block.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{E_SPAN_BAD_REPORT}: {cls} has a blank span key")
            if cls == "covered":
                ids = [str(v).strip() for v in value] \
                    if isinstance(value, (list, tuple)) else None
                if not ids or not all(ids):
                    raise ValueError(
                        f"{E_SPAN_EMPTY_DRAFTS}: {key!r} is covered but names no "
                        "draft — a span that produced nothing is reviewed_empty")
                norm[key] = ids
            else:
                reason = str(value).strip()
                if not reason:
                    raise ValueError(
                        f"{E_SPAN_BLANK_REASON}: {key!r} is {cls} with no reason "
                        "— the reason is the whole point of the class")
                norm[key] = reason
        out[cls] = norm
    return out


def check_dropped(dropped) -> dict:
    """Validate a writer's optional `dropped: {reason: count}` object and
    return it normalised (string reason -> non-negative int count), or `{}`
    for `None`. Same discipline as `check_spans` above: a malformed shape is
    refused rather than reaching the ledger, naming the offending key, because
    a `dropped` block computed from garbage would be silently COUNTED as an
    explanation by `validate._check_drops_unexplained` — worse than no block
    at all. Reasons are the writer's own vocabulary; this function (and the
    validator that reads it back) never interprets one, only counts it."""
    if dropped is None:
        return {}
    if not isinstance(dropped, dict):
        raise ValueError(f"{E_LEDGER_DROPPED}: dropped must be an object "
                         "mapping reason -> non-negative integer count, got "
                         f"{type(dropped).__name__}")
    out: dict[str, int] = {}
    for key, value in dropped.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{E_LEDGER_DROPPED}: {key!r} is not a valid "
                             "reason key — dropped keys must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{E_LEDGER_DROPPED}: {key!r} has value {value!r} "
                             "— dropped counts must be non-negative integers, "
                             "never negative, fractional, or another shape")
        out[key] = value
    return out


def check_corrects(corrects) -> dict:
    """Validate a writer's optional `corrects` block and return it normalised
    (`run_id`, `segment`, `output` — all non-empty strings — plus `dropped`,
    itself checked with `check_dropped`). Same discipline as `check_spans`
    and `check_dropped`: a malformed shape is refused rather than reaching
    the ledger, because a `corrects` block computed from garbage would
    silently fail to name anything and read as a no-op explanation — worse
    than the still-open warning it was meant to clear.

    This only checks SHAPE. Whether `run_id`/`segment`/`output` name a row
    that actually exists on the ledger is `add_row`'s job
    (`E_LEDGER_CORRECTION_UNKNOWN`), because that check needs the bundle's
    other rows, which this function does not have access to."""
    if not isinstance(corrects, dict):
        raise ValueError(f"{E_LEDGER_CORRECTION}: corrects must be an object "
                         "with run_id, segment, output and dropped, got "
                         f"{type(corrects).__name__}")
    out: dict = {}
    for key in ("run_id", "segment", "output"):
        v = corrects.get(key)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{E_LEDGER_CORRECTION}: corrects.{key} must be "
                             "a non-empty string naming the row/output this "
                             "correction accounts for")
        out[key] = v
    dropped = check_dropped(corrects.get("dropped"))
    if not dropped:
        raise ValueError(f"{E_LEDGER_CORRECTION}: corrects.dropped must "
                         "record at least one reason/count — a correction "
                         "with no explanation is the same defect the "
                         "warning exists to catch")
    out["dropped"] = dropped
    return out


def ledger_path(bundle: Bundle) -> Path:
    return bundle.root / "meta" / "ledger.jsonl"


def parse_merge_map(spec: str | None) -> dict | None:
    """Parse a CLI --merge-map string 'draft=final,draft2=final2' into a dict.
    Empty/None yields None (no merge_map key on the row)."""
    if not spec:
        return None
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"bad --merge-map entry {part!r} (want draft=final)")
        draft, final = part.split("=", 1)
        out[draft.strip()] = final.strip()
    return out or None


def _manifest(bundle: Bundle) -> dict:
    p = bundle.root / "meta" / "corpus-manifest.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _head(bundle: Bundle) -> str:
    r = run_git(bundle.root, "rev-parse", "HEAD", capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def _check(row: dict, require_nonempty_lists: bool = True) -> None:
    for k in _REQUIRED_STR:
        v = row[k]
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"ledger row: {k} must be a non-empty string")
    for k in _REQUIRED_LIST:
        v = row[k]
        if not isinstance(v, list):
            raise ValueError(f"ledger row: {k} must be a list")
        if require_nonempty_lists and not v:
            raise ValueError(f"ledger row: {k} must be a non-empty list")


def add_row(bundle: Bundle, run_id: str, segment: str, inputs, prompt_version: str,
            outputs, validation: str, merge_map: dict | None = None,
            job_digest: str | None = None, spans: dict | None = None,
            dropped: dict | None = None, corrects: dict | None = None) -> dict:
    """Append one transition row to meta/ledger.jsonl and commit the ledger
    --no-verify. input_hashes come from the corpus manifest ('unknown' when the
    path is absent); commit captures the current bundle HEAD (the artifact commit
    this row records).

    `corrects` (v0.25 audit F09) makes this row a CORRECTION instead of an
    ordinary transition: it names an earlier row and one of its declared
    outputs, with its own `dropped` explanation — see `check_corrects`. A
    pure correction has nothing to report about a pass, so it is the one
    case `inputs`/`outputs` may be empty lists rather than non-empty ones;
    every other row keeps the original non-empty requirement unchanged."""
    row = {
        "run_id": run_id,
        "segment": segment,
        "inputs": list(inputs) if isinstance(inputs, (list, tuple)) else inputs,
        "prompt_version": prompt_version,
        "outputs": list(outputs) if isinstance(outputs, (list, tuple)) else outputs,
        "validation": validation,
    }
    _check(row, require_nonempty_lists=corrects is None)
    manifest = _manifest(bundle)
    # inputs/input_hashes kept adjacent; commit last per the documented shape.
    row = {
        "run_id": run_id, "segment": segment, "inputs": row["inputs"],
        "input_hashes": {i: manifest.get(i, "unknown") for i in row["inputs"]},
        "prompt_version": prompt_version, "outputs": row["outputs"],
        "validation": validation, "commit": _head(bundle),
    }
    if merge_map:
        row["merge_map"] = dict(merge_map)
    if job_digest:
        # digest of the worker-job artifact (meta/jobs/<segment>.json) — the
        # reproducible version of what this worker actually consumed
        row["job_digest"] = job_digest
    if spans is not None:
        # Appended last, like merge_map and job_digest: a row written without a
        # span report must stay byte-identical to what v0.18 wrote, or the ledger
        # of every already-accepted bundle moves under it.
        checked = check_spans(spans)
        if not (bundle.root / "meta" / "jobs" / f"{segment}.json").is_file():
            raise FileNotFoundError(
                f"{E_SPAN_NO_JOB}: span outcomes given for segment {segment!r} "
                f"but meta/jobs/{segment}.json does not exist — the job artifact "
                "is the denominator these outcomes partition; run `okfy job` first")
        unknown = unknown_covered_outputs(checked, row["outputs"])
        if unknown:
            raise ValueError(
                f"{E_SPAN_OUTPUT}: {len(unknown)} covered span(s) name draft(s) "
                f"this row does not list in outputs ({', '.join(unknown[:3])}) — "
                "outputs is this row's own account of what the pass produced, so "
                "the two halves of one row disagree. Add them to outputs if they "
                "were written, or move the span to reviewed_empty if nothing was")
        row["spans"] = checked
    if dropped is not None:
        # Appended last, same reasoning as spans/merge_map/job_digest: a row
        # written without a dropped block must stay byte-identical to what
        # pre-v0.25 wrote, or every already-accepted bundle's ledger moves
        # under it. Refuses before anything is written — see check_dropped.
        row["dropped"] = check_dropped(dropped)
    if corrects is not None:
        # Appended last, same reasoning as spans/dropped/merge_map/job_digest.
        # Refuses before anything is written — shape first (check_corrects),
        # then existence: a correction naming a run_id/segment this ledger
        # has never seen, or an output the named row never declared, is a
        # silent no-op if let through — the same defect class as an
        # unchecked state that looks like success, so it is refused instead.
        normalized = check_corrects(corrects)
        existing = read_rows(bundle)
        matched = [er for er in existing
                  if er.get("run_id") == normalized["run_id"]
                  and er.get("segment") == normalized["segment"]]
        if not matched:
            raise ValueError(
                f"{E_LEDGER_CORRECTION_UNKNOWN}: no ledger row exists with "
                f"run_id={normalized['run_id']!r} segment={normalized['segment']!r} "
                "— a correction must reference a row already on the ledger")
        # A correction binds to a UNIQUE earlier transition (v0.26 audit
        # A8), not to whichever row happens to match: `declaring` narrows
        # `matched` to rows that actually declared this output, and either
        # zero or more than one is refused rather than guessed at — zero
        # means the output was never lost by this run_id/segment, more than
        # one means run_id/segment alone cannot tell which loss this
        # correction is meant to explain.
        declaring = [er for er in matched if isinstance(er.get("outputs"), list)
                    and normalized["output"] in er["outputs"]]
        if not declaring:
            raise ValueError(
                f"{E_LEDGER_CORRECTION_UNKNOWN}: run_id={normalized['run_id']!r} "
                f"segment={normalized['segment']!r} never declared output "
                f"{normalized['output']!r} in its outputs — a correction "
                "must name an output the target row actually declared")
        if len(declaring) > 1:
            raise ValueError(
                f"{E_LEDGER_CORRECTION_UNKNOWN}: run_id={normalized['run_id']!r} "
                f"segment={normalized['segment']!r} declared output "
                f"{normalized['output']!r} in more than one ledger row — "
                "ambiguous which loss this correction explains, so it is "
                "refused rather than applied to whichever matches")
        row["corrects"] = normalized

    # v0.26 audit A5: checked at entry, before the row is appended to disk
    # — see `proposals._accept`'s matching comment and
    # `_refuse_if_ledger_rewritten`'s docstring (the same guard `accept`,
    # `reject`, `dismiss` and `refine` call, applied here to
    # meta/ledger.jsonl instead of meta/memory.jsonl). `_commit` below
    # still re-checks as the backstop.
    _refuse_if_ledger_rewritten(bundle, LEDGER)
    path = ledger_path(bundle)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _commit(bundle, [LEDGER], f"ledger: {run_id} {segment}")
    return row


def latest_span_rows(bundle: Bundle) -> dict:
    """The whole live row per segment, not just its span block. A segment can be
    re-run, so the newest row carrying spans wins — an older row's outcomes
    describe material a later pass has already superseded.

    Whole rows because the `covered`-to-`outputs` join needs both halves, and
    they only travel together on the row that wrote them."""
    out: dict[str, dict] = {}
    for row in read_rows(bundle):
        if isinstance(row.get("spans"), dict):
            out[str(row.get("segment"))] = row
    return out


def latest_span_outcomes(bundle: Bundle) -> dict:
    """The live span report per segment. Derived from `latest_span_rows` so the
    two cannot pick different rows."""
    return {seg: row["spans"] for seg, row in latest_span_rows(bundle).items()}


def read_rows(bundle: Bundle, run_id: str | None = None) -> list:
    """All ledger rows in order (one JSON object per line), optionally filtered
    to a single run_id."""
    path = ledger_path(bundle)
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if run_id is not None:
        rows = [r for r in rows if r.get("run_id") == run_id]
    return rows
