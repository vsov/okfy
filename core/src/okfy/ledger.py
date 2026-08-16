"""Extraction Ledger (ADR-0013, v0.5 engineering): append-only meta/ledger.jsonl,
one row per pipeline artifact transition (a Worker or a Consolidation step).
Deliberately SHALLOW — segment-level provenance, not per-claim. Rows are written
AFTER the artifact commit, so `commit` pins the bundle HEAD that already contains
the outputs; `input_hashes` resolve from the corpus manifest so a third party can
tell exactly what each transition consumed (or that a hash was 'unknown')."""
import json
import subprocess
from pathlib import Path

from okfy.bundle import Bundle
from okfy.proposals import _commit

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
    r = subprocess.run(["git", "-C", str(bundle.root), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def _check(row: dict) -> None:
    for k in _REQUIRED_STR:
        v = row[k]
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"ledger row: {k} must be a non-empty string")
    for k in _REQUIRED_LIST:
        v = row[k]
        if not isinstance(v, list) or not v:
            raise ValueError(f"ledger row: {k} must be a non-empty list")


def add_row(bundle: Bundle, run_id: str, segment: str, inputs, prompt_version: str,
            outputs, validation: str, merge_map: dict | None = None,
            job_digest: str | None = None, spans: dict | None = None) -> dict:
    """Append one transition row to meta/ledger.jsonl and commit the ledger
    --no-verify. input_hashes come from the corpus manifest ('unknown' when the
    path is absent); commit captures the current bundle HEAD (the artifact commit
    this row records)."""
    row = {
        "run_id": run_id,
        "segment": segment,
        "inputs": list(inputs) if isinstance(inputs, (list, tuple)) else inputs,
        "prompt_version": prompt_version,
        "outputs": list(outputs) if isinstance(outputs, (list, tuple)) else outputs,
        "validation": validation,
    }
    _check(row)
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
        row["spans"] = checked

    path = ledger_path(bundle)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _commit(bundle, [LEDGER], f"ledger: {run_id} {segment}")
    return row


def latest_span_outcomes(bundle: Bundle) -> dict:
    """The live span report per segment. A segment can be re-run, so the newest
    row carrying spans wins — an older row's outcomes describe material a later
    pass has already superseded."""
    out: dict[str, dict] = {}
    for row in read_rows(bundle):
        if isinstance(row.get("spans"), dict):
            out[str(row.get("segment"))] = row["spans"]
    return out


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
