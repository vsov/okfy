"""Source map validator (v0.19, P0-a of the adjacent analysis).

A raw PDF, deck or scan cannot be handed to a worker, so it is converted to
Markdown outside the core and the corpus holds the Markdown. That conversion is
where provenance normally dies: a concept cites `handbook.md#L811-L824`, and
nothing connects those lines back to page 47 of the PDF they came from.

`meta/source-map.jsonl` is the sidecar that connects them — one JSON object per
normalized span, RECORDING the raw file's path and hash, the normalized span in
the SAME anchor grammar a concept cites, the hash of that span's text, and which
converter produced it. This module validates the sidecar.

Recording is not verifying, and the difference is limit 3 below. The word here
used to be "carrying", which read as a claim that the raw hash had been checked
against something — it had not, and an audit walked a hash of all zeros straight
through a green release.

THREE DELIBERATE LIMITS, all of which the output states rather than hides:

1. `page` and `bbox` are CARRIED, never verified. Verifying them means opening a
   PDF, which means a PDF library, which the core will not have — it has exactly
   one runtime dependency and keeps it. A bbox here is the converter's claim,
   recorded so a human can open the page and look.
2. When the corpus tree is not readable, a row is `unverifiable`, never `pass`.
   A hash cannot be recomputed from a file that is not there, and reporting that
   as verified is the failure mode `okfy cost` already refuses.
3. THE RAW BYTES ARE USUALLY NOT HERE. A bundle's corpus holds the NORMALIZED
   Markdown; the PDF it came from lives wherever the owner keeps it. So
   `raw_sha256` normally cannot be recomputed from anything the bundle carries,
   and a row in that state is `raw-unverified` — never `verified`. Reporting it
   as verified is what an audit found this module doing, and "carrying the raw
   file and its hash" was then a stronger claim than the code enforced.

   A bundle CAN close that half: declare `normalization.raw_root` in
   meta/purpose.md and, when the path is readable, the raw bytes are hashed and
   compared for real. Rows that pass both halves are `verified`.

The counts say which half was checked: `text_verified` and `raw_verified` are
separate integers, because collapsing them is precisely how one unchecked field
hid behind another that was.

The converter is not named by this schema — docling, marker, pymupdf or pandoc
all produce rows of the same shape. Recording `converter`, `converter_version`
and `converter_options_digest` is what makes a re-conversion comparable.

Imports: stdlib and `okfy` only. `tests/test_tokens.py` asserts this.
"""
import hashlib
import json
import re
from pathlib import Path

from okfy.bundle import Bundle
from okfy.validate import ANCHOR_LINE_RE, heading_spans

SOURCE_MAP = "meta/source-map.jsonl"
SCHEMA = "okfy-source-map@1"

REQUIRED = ("raw_path", "raw_sha256", "normalized_path", "normalized_lines",
            "text_sha256", "converter", "converter_version",
            "converter_options_digest")
# `converter_ref` is the converter's own handle on the region (docling calls it a
# provenance item id); it is opaque to OKFy and travels only so a re-run can be
# lined up against the original.
OPTIONAL = ("page", "bbox", "converter_ref", "granularity")

# What a row may claim about where its span came from. OPTIONAL, not required:
# rows written before v0.22 exist and are not wrong, they are unstated — and a
# release that turned every one of them into a finding would be inventing a
# regression. But a row that states a value nothing understands IS a finding,
# because it still reads as a claim. Restated from the adapter deliberately:
# core cannot import the adapter, exactly as with OPTIONS_DIGEST_LEN above,
# except that there the constant flows adapter <- core and here core is the
# validator of a vocabulary the adapter produces.
GRANULARITIES = ("whole-document", "page")

# A digest field has to contain a digest. `raw_sha256: x` used to pass, so the
# claim that this sidecar pins a raw file's hash was stronger than the contract
# enforced — the field was checked for being a non-empty string and nothing
# else. Form is checked here; whether the digest MATCHES anything is limit 3.
#
# Lowercase only, both patterns. `hashlib.hexdigest()` emits lowercase and the
# adapter is the only producer; accepting uppercase as well would make one field
# two formats, and anything joining two source maps would have to normalise
# before comparing. One format is the cheaper contract.
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# `okfy_normalize.backends.options_digest` truncates a sha256 to this many hex
# characters. Declared HERE, in core, and asserted by the adapter — never the
# reverse: core cannot import the adapter, and a constant restated in two places
# is a constant that will drift. Same reasoning as `span_text` above, where the
# producer and the validator share one definition of what a span's text is.
OPTIONS_DIGEST_LEN = 32
OPTIONS_DIGEST_RE = re.compile(r"^[0-9a-f]{%d}$" % OPTIONS_DIGEST_LEN)

E_JSON = "E_SOURCEMAP_JSON"
E_FIELD = "E_SOURCEMAP_FIELD"
E_DIGEST = "E_SOURCEMAP_DIGEST"
E_LINES = "E_SOURCEMAP_LINES"
E_NO_FILE = "E_SOURCEMAP_NO_FILE"
E_TEXT_DRIFT = "E_SOURCEMAP_TEXT_DRIFT"
E_RAW_DRIFT = "E_SOURCEMAP_RAW_DRIFT"
E_DUPLICATE = "E_SOURCEMAP_DUPLICATE"
E_EMPTY = "E_SOURCEMAP_EMPTY"
E_MISSING = "E_SOURCEMAP_MISSING"
E_COVERAGE = "E_SOURCEMAP_COVERAGE"
# The normalization BLOCK's own schema, separate codes because they are findings
# about meta/purpose.md rather than about the sidecar.
E_NORM_KEY = "E_NORMALIZATION_KEY"
E_NORM_VALUE = "E_NORMALIZATION_VALUE"
E_NORM_ROOT = "E_NORMALIZATION_ROOT"

NORMALIZATION_KNOWN = ("source_map", "raw_root")
SOURCE_MAP_VALUES = ("required",)


def normalization(bundle: Bundle) -> dict:
    """The `normalization:` block from meta/purpose.md, as a mapping.

    A bundle built from authored text has no such block and never gains a
    finding from this module — the checks below return on exactly that absence.
    That is the same shape `acceptance.dissent` uses, and it is why nothing
    anywhere holds a list of which bundles are exempt.

        normalization:
          source_map: required     # an absent sidecar now blocks
          raw_root: /path/to/raw   # optional; enables real raw verification
    """
    n = bundle.purpose().get("normalization")
    return n if isinstance(n, dict) else {}


def normalization_problems(bundle: Bundle) -> list[dict]:
    """The normalization block's schema, in ONE place.

    Two fail-open defects lived here, both the shape `acceptance` had before
    v0.19:

    - `source_map: requierd` is not `required`, so `required` was False, so the
      declaration silently became optional and the bundle shipped. A declaration
      a typo can switch off is not a declaration.
    - `raw_root` could be declared and absent. `_raw_root` collapsed that into
      `None`, the module behaved as though no root were declared, and the advice
      told the owner to declare the root they had already declared.

    The contract:

    - The block ABSENT means text-only, and produces no finding ever. That is
      the same exemption-by-construction shape as `acceptance.dissent`, and it
      is why nothing anywhere holds a list of which bundles are excused. No real
      bundle here declares the block at all.
    - Known keys are exactly `source_map` and `raw_root`. A key the tool ignores
      is a setting the owner believes they made.
    - `source_map` has ONE accepted value, `required`. Absent means text-only;
      present and anything else is a typo, including `optional` — there is no
      second active value to spell wrong.
    - `raw_root` present must name a readable directory. Present and dead is an
      error, not a fallback to text-only."""
    block = bundle.purpose().get("normalization")
    if block is None:
        return []
    if not isinstance(block, dict):
        return [{"code": E_NORM_KEY,
                 "message": f"normalization is {type(block).__name__} "
                            f"{block!r}, not a mapping of "
                            f"{'/'.join(NORMALIZATION_KNOWN)}"}]
    out = []
    for k in sorted(set(block) - set(NORMALIZATION_KNOWN)):
        out.append({"code": E_NORM_KEY,
                    "message": f"normalization.{k} is not a known setting "
                               f"(known: {', '.join(NORMALIZATION_KNOWN)}) — a "
                               "key this tool ignores is a setting you believe "
                               "you made, so it is reported rather than dropped"})
    if "source_map" in block:
        v = block["source_map"]
        if v not in SOURCE_MAP_VALUES:
            out.append({"code": E_NORM_VALUE,
                        "message": f"normalization.source_map is {v!r}; the "
                                   f"only accepted value is "
                                   f"{SOURCE_MAP_VALUES[0]!r}. Omit the key for "
                                   "a text-only corpus — a misspelling used to "
                                   "turn the declaration off silently, which is "
                                   "the one thing a declaration exists to "
                                   "prevent"})
    if "raw_root" in block:
        raw = block["raw_root"]
        if not isinstance(raw, str) or not raw.strip():
            out.append({"code": E_NORM_ROOT,
                        "message": f"normalization.raw_root is {raw!r}, not a "
                                   "path. Omit the key if there is no raw tree "
                                   "to verify against"})
        elif not Path(raw.strip()).is_dir():
            out.append({"code": E_NORM_ROOT,
                        "message": f"normalization.raw_root {raw.strip()!r} is "
                                   "declared and is not a readable directory. "
                                   "The declaration is what makes the raw half "
                                   "checkable, so a dead path leaves every "
                                   "raw_sha256 in the sidecar unverified while "
                                   "reading as though it were pinned — point it "
                                   "at the raw tree, or remove the key"})
    return out


def _raw_root(bundle: Bundle) -> Path | None:
    """The raw tree, when one is declared AND readable.

    A declared-but-dead root returns None here too, but it is no longer silent:
    `normalization_problems` reports it as E_NORMALIZATION_ROOT, and the notes
    below distinguish "no root declared" from "root declared, not there". The
    two used to produce the same message, which advised declaring a root that
    was already declared."""
    root = str(normalization(bundle).get("raw_root") or "").strip()
    if not root:
        return None
    p = Path(root)
    return p if p.is_dir() else None


def _raw_root_declared(bundle: Bundle) -> bool:
    return bool(str(normalization(bundle).get("raw_root") or "").strip())


def _corpus(bundle: Bundle) -> Path | None:
    snap = bundle.get("meta/corpus")
    root = Path(str(snap.meta.get("corpus") or "")) if snap else None
    return root if root and root.is_dir() else None


def span_text(path: Path, start: int, end: int,
              lines_cache: dict[str, list[str] | None] | None = None) -> str:
    """The cited lines, joined with their line endings intact. `text_sha256` is
    the SHA-256 of this string's UTF-8 bytes — defined once, here, so a converter
    and this validator cannot disagree about whether the trailing newline counts.

    `lines_cache`: optional, the same shape and key (`str(path.resolve())`)
    as `cited_span`'s — added so a caller that already resolved `start`/`end`
    via `cited_span(..., lines_cache=...)` can read THIS span through the same
    cache instead of paying for a second, independent open+read of a file
    `cited_span`'s own bounds check (v0.25 audit F11) just read. Absent (the
    default), this behaves exactly as it always has — a private
    `path.read_text()` — so every existing caller is unaffected."""
    if lines_cache is None:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        return "".join(lines[start - 1:end])
    lines = _cited_lines(path, lines_cache)
    if lines is None:
        # The cache already recorded this file as unreadable (it can only get
        # here after a caller resolved a span against it, which means a prior
        # read through this same cache failed). Re-attempt the read so the
        # ORIGINAL exception (FileNotFoundError, UnicodeDecodeError, ...)
        # propagates exactly as the uncached path above would raise it —
        # this only ever runs on that already-failing path, never on the
        # shared hot path a cache hit takes.
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(lines[start - 1:end])


def _check_row(row: dict, corpus: Path | None,
               raw_root: Path | None = None) -> tuple[str, list[dict]]:
    """One row -> (state, problems).

    Four states, because three were not enough to be honest:

    - `error`        something is wrong with the row
    - `unverifiable` the corpus is not readable; nothing could be recomputed
    - `raw-unverified` the SPAN TEXT was recomputed and matched, and the raw
                     bytes were not available to check. This is the normal state
                     for a bundle that does not declare `normalization.raw_root`,
                     and it used to be reported as `verified` — which is the
                     overstatement the audit caught.
    - `verified`     both halves recomputed and matched."""
    problems = []
    if not isinstance(row, dict):
        return "error", [{"code": E_FIELD, "message": "row is not a JSON object"}]
    for f in REQUIRED:
        if not str(row.get(f) or "").strip():
            problems.append({"code": E_FIELD,
                             "message": f"required field missing or blank: {f}"})
    unknown = sorted(set(row) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        problems.append({"code": E_FIELD,
                         "message": f"unknown field(s): {', '.join(unknown)} "
                                    f"(allowed: {', '.join(REQUIRED + OPTIONAL)})"})
    if problems:
        return "error", problems

    # AFTER the presence check, never merged with it: a field that is absent and
    # a field that holds `x` are different findings, and reporting the second
    # for the first would send a reader looking for a value that is not there.
    for field, pattern, shape in (
            ("raw_sha256", SHA256_RE, "64 lowercase hex characters"),
            ("text_sha256", SHA256_RE, "64 lowercase hex characters"),
            ("converter_options_digest", OPTIONS_DIGEST_RE,
             f"{OPTIONS_DIGEST_LEN} lowercase hex characters")):
        value = str(row[field])
        if not pattern.match(value):
            problems.append({
                "code": E_DIGEST,
                "message": f"{field} is {value!r}, not {shape} — the field is "
                           "documented as pinning a hash, and a value that "
                           "cannot be one makes that claim unenforceable"})
    if problems:
        return "error", problems

    if "granularity" in row and row["granularity"] not in GRANULARITIES:
        return "error", [{"code": E_FIELD,
                          "message": f"granularity is "
                                     f"{row['granularity']!r}, not one of "
                                     f"{list(GRANULARITIES)} — an unstated "
                                     "granularity is a row that makes no claim "
                                     "about pages, but an unknown one still "
                                     "reads as a claim"}]

    m = ANCHOR_LINE_RE.match(str(row["normalized_lines"]))
    if not m:
        return "error", [{"code": E_LINES,
                          "message": f"normalized_lines {row['normalized_lines']!r} "
                                     "is not a line anchor (want L12 or L12-L40)"}]
    start = int(m.group(1))
    end = int(m.group(2) or m.group(1))
    if start < 1 or end < start:
        return "error", [{"code": E_LINES,
                          "message": f"normalized_lines {row['normalized_lines']!r} "
                                     "is not an ascending 1-based range"}]

    if corpus is None:
        return "unverifiable", []
    f = (corpus / str(row["normalized_path"])).resolve()
    if not f.is_relative_to(corpus.resolve()) or not f.is_file():
        return "error", [{"code": E_NO_FILE,
                          "message": f"normalized_path {row['normalized_path']!r} "
                                     "is not a readable file inside the corpus"}]
    total = len(f.read_text(encoding="utf-8").splitlines())
    if end > total:
        return "error", [{"code": E_LINES,
                          "message": f"normalized_lines {row['normalized_lines']!r} "
                                     f"runs past the end of the file ({total} lines)"}]
    got = hashlib.sha256(span_text(f, start, end).encode("utf-8")).hexdigest()
    if got != str(row["text_sha256"]):
        return "error", [{"code": E_TEXT_DRIFT,
                          "message": f"text_sha256 does not match the cited lines "
                                     f"of {row['normalized_path']} — the normalized "
                                     "file changed after conversion, so the raw "
                                     "mapping no longer describes it"}]
    # The raw half. Without a declared root there is nothing to hash: the raw
    # document is not in the bundle and never was, so the honest report is that
    # this half was not checked — not that it passed.
    if raw_root is None:
        return "raw-unverified", []
    raw = (raw_root / str(row["raw_path"])).resolve()
    if not raw.is_relative_to(raw_root.resolve()) or not raw.is_file():
        return "raw-unverified", [{
            "code": E_NO_FILE,
            "message": f"raw_path {row['raw_path']!r} is not a readable file "
                       f"inside the declared normalization.raw_root — the root "
                       "is declared, so a row it cannot account for is a gap in "
                       "the chain rather than an absent option"}]
    if hashlib.sha256(raw.read_bytes()).hexdigest() != str(row["raw_sha256"]):
        return "error", [{"code": E_RAW_DRIFT,
                          "message": f"raw_sha256 does not match the bytes of "
                                     f"{row['raw_path']} under the declared "
                                     "raw_root — the raw document changed after "
                                     "conversion, so this mapping describes a "
                                     "file that no longer exists in that form"}]
    return "verified", []


ANCHOR_CHAR_RE = re.compile(r"^C(\d+)(?:-(\d+))?$")
# TWO line-anchor grammars exist in this codebase and both are cited in real
# bundles: concepts write `#L12-L40`, which is `validate.ANCHOR_LINE_RE`, and
# `ledger.span_key` writes `#L12-40` for a `lines` entry. The canonical regex is
# imported, never restated; this one covers only the ledger's dashless variant,
# and it is named for that so nobody mistakes it for a second definition of the
# same thing.
LEDGER_LINE_RE = re.compile(r"^L(\d+)-(\d+)$")


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Overlapping and ADJACENT intervals merged. Adjacent matters: rows
    covering L1-L10 and L11-L20 are a continuous mapping of L1-L20, and a
    citation of L5-L15 is covered by them together even though neither contains
    it alone."""
    out: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _cited_lines(f: Path,
                 lines_cache: dict[str, list[str] | None] | None
                 ) -> list[str] | None:
    """The lines of `f` (split with endings kept, so the count is exact and
    the same list a caller like `_check_quotes` slices for text) — read once
    and shared through `lines_cache` when one is supplied.

    Follow-up to v0.25 audit F11: making `cited_span` bounds-check a citation
    against the corpus was correct and stays, but the read it does to check
    those bounds used to be its OWN, private `Path.read_text` call — even
    when the caller (`_check_quotes`, `check_source_map`'s coverage loop,
    `eval_qrels`) had already read and cached this exact file for its own
    purposes. `lines_cache is None` (the default) is a caller that built no
    such cache; behaviour is then exactly the private read `cited_span` has
    always done. A caller that passes one gets a read-through: a hit costs a
    dict lookup, a miss reads once and fills the cache for every span after
    it, in this call and (if the caller reuses its own dict, as
    `_check_quotes` already did before this) in the caller's own reads too.

    The key is the RESOLVED path, matching `_check_quotes`'s own key
    (`(root / path).resolve()`) exactly — `corpus / path` here and `root /
    path` there name the same file whenever `corpus` and `root` are the same
    object, which every current caller passes. An unresolvable path (rare;
    `Path.resolve()` does not require the file to exist) falls back to the
    unresolved string rather than failing the lookup."""
    if lines_cache is None:
        try:
            return f.read_text(encoding="utf-8").splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            return None
    try:
        key = str(f.resolve())
    except OSError:
        key = str(f)
    if key not in lines_cache:
        try:
            lines_cache[key] = f.read_text(encoding="utf-8").splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            lines_cache[key] = None
    return lines_cache[key]


def cited_span(source: str, corpus: Path | None,
               lines_cache: dict[str, list[str] | None] | None = None
               ) -> tuple[str, int | None, int | None]:
    """A cited source resolved to (path, start, end), 1-based inclusive.

    The coverage check used to compare the WHOLE source string against a bare
    `normalized_path`, so a mapping of `handbook.md` did not cover a citation of
    `handbook.md#L11-L14` and a fully mapped file was reported uncovered — which
    made a required source map incompatible with anchored sources, which is to
    say with normal bundles.

    Stripping the anchor instead would have been worse than the bug: a row
    covering L1-L2 would then stand as evidence for a citation of L900-L920. So
    the anchor is RESOLVED, not discarded, and coverage becomes containment.

    `(path, None, None)` means the citation could not be resolved to lines —
    an unreadable file, or a heading that is not in it. That is reported as
    unresolved rather than guessed at, because a guess here silently decides
    whether provenance holds.

    v0.25 audit F11: a line/ledger anchor used to be returned on grammar
    alone — `int(m.group(1))`, no existence check, no bounds check — so
    `missing.md#L1-L5`, a reversed `L9-L4`, `L0-L1` and a beyond-EOF
    `L9-L99` all came back as if resolved. Three things are now checked, in
    the same order `_check_row` already checks them for a source-map row:
    ascending and 1-based (a grammar fact, independent of corpus, so this
    runs even when `corpus is None`), then — only when corpus bytes are
    actually available — that the file exists and that `end` does not run
    past it. A span that passes the ascending/1-based check but had no
    corpus to verify against is still returned as real ints: it PARSED, it
    is just not VERIFIED, and the caller (`eval_qrels`, the one caller that
    can receive `corpus=None` here and still looks at the ints) is the one
    that knows to label that UNVERIFIABLE rather than resolved — see its
    docstring.

    `lines_cache`: optional, `dict[str, list[str] | None]` keyed by resolved
    path — the same shape `_check_quotes` already builds. When supplied, the
    bare-path and line-anchor branches below (the two that read a file just
    to count or bound-check its lines) read through it instead of opening
    the file themselves; see `_cited_lines`. Absent, they behave exactly as
    they always have. The char-anchor and heading branches need the file's
    full text, not its line list, so they are unaffected either way."""
    path, _, frag = source.partition("#")
    if not frag:
        # A bare path claims the whole file, so the whole file must be mapped.
        if corpus is None:
            return path, None, None
        f = corpus / path
        lines = _cited_lines(f, lines_cache)
        if lines is None:
            return path, None, None
        return path, 1, max(len(lines), 1)
    m = ANCHOR_LINE_RE.match(frag) or LEDGER_LINE_RE.match(frag)
    if m:
        start = int(m.group(1))
        end = int(m.group(2) or start)
        if start < 1 or end < start:
            # Not a line range at all, corpus or no corpus — the same
            # structural check `_check_row` runs before it ever looks at a
            # file.
            return path, None, None
        if corpus is None:
            return path, start, end
        f = corpus / path
        lines = _cited_lines(f, lines_cache)
        if lines is None:
            return path, None, None
        if end > len(lines):
            return path, None, None
        return path, start, end
    m = ANCHOR_CHAR_RE.match(frag)
    if m:
        # Character anchors are what the ledger writes for a `chars` span. They
        # are resolvable, but only by reading the file — counting newlines is
        # the whole conversion, and refusing to do it would leave every
        # char-anchored citation permanently unresolved.
        if corpus is None:
            return path, None, None
        try:
            text = (corpus / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return path, None, None
        a = int(m.group(1))
        b = int(m.group(2) or a)
        # Clamped to the file. A `chars` span the ledger wrote as C1-4000
        # against a 1020-character file is the whole file, not a line past its
        # end — and an end past the end used to report the last line as a gap.
        n = max(len(text.splitlines()), 1)
        return (path,
                min(text.count("\n", 0, max(a - 1, 0)) + 1, n),
                min(text.count("\n", 0, min(max(b, 1), len(text))) + 1, n))
    if corpus is None:
        return path, None, None
    try:
        text = (corpus / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return path, None, None
    span = heading_spans(text).get(frag.strip().lower())
    return (path, *span) if span else (path, None, None)


def _gaps(want: tuple[int, int], have: list[tuple[int, int]]) -> list[str]:
    """The parts of `want` no interval in `have` covers, as `L5-L9` strings.

    Reported rather than a bare file name: "handbook.md is not covered" sends a
    reader to look at a file that is mostly covered, and the lines are the
    actionable part."""
    out, cursor = [], want[0]
    for s, e in _merge(have):
        if e < cursor:
            continue
        if s > want[1]:
            break
        if s > cursor:
            out.append((cursor, min(s - 1, want[1])))
        cursor = max(cursor, e + 1)
        if cursor > want[1]:
            break
    if cursor <= want[1]:
        out.append((cursor, want[1]))
    return [f"L{a}" if a == b else f"L{a}-L{b}" for a, b in out]


def check_source_map(bundle: Bundle) -> dict:
    """Validate `meta/source-map.jsonl`. Reads only; writes nothing, ever."""
    path = bundle.root / SOURCE_MAP
    # The BLOCK's schema first, and on every return path below. A typo in
    # `source_map` used to make `required` False, which sent this function down
    # the "absent, and that is fine" branch and made release return before it
    # ever looked — so the declaration switched itself off and the silence was
    # complete.
    norm = normalization_problems(bundle)
    required = str(normalization(bundle).get("source_map") or "").strip() == "required"
    if not path.is_file():
        if required:
            # The bundle SAID the map is mandatory. Absence is then not "this
            # corpus has no raw documents" — it is a declared artifact that is
            # not there, which is the one thing a declaration is for.
            return {"schema": SCHEMA, "state": "missing",
                    "note": "meta/purpose.md declares normalization.source_map: "
                            "required and the sidecar is absent",
                    "ok": False, "rows": 0, "verified": 0, "text_verified": 0,
                    "raw_verified": 0, "unverifiable": 0, "required": True,
                    "problems": norm + [{"code": E_MISSING,
                                  "message": "meta/purpose.md declares "
                                             "normalization.source_map: required "
                                             "and meta/source-map.jsonl is absent"}]}
        # Absence is not a defect. Most bundles are built from text corpora and
        # will never have one; a missing optional sidecar must not read as a gap.
        # Present-and-invalid normalization is NOT the absent case. Reporting it
        # as `absent, ok: true` is precisely how a misspelled declaration
        # shipped: release returns early on `absent` and never asks again.
        return {"schema": SCHEMA,
                "state": "absent" if not norm else "invalid",
                "note": ("no source map" if not norm else
                         "meta/purpose.md declares a normalization block this "
                         "tool cannot act on"),
                "ok": not norm, "rows": 0, "verified": 0, "text_verified": 0,
                "raw_verified": 0, "unverifiable": 0, "required": False,
                "problems": []}
    corpus = _corpus(bundle)
    raw_root = _raw_root(bundle)
    problems: list[dict] = list(norm)
    text_verified = raw_verified = unverifiable = rows = 0
    seen: dict[tuple, int] = {}
    # Per PATH, the line intervals the sidecar maps. A set of bare paths was
    # what made coverage a string comparison, and a string comparison is what
    # could not see an anchor.
    mapped: dict[str, list[tuple[int, int]]] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        rows += 1
        try:
            row = json.loads(line)
        except ValueError as e:
            problems.append({"line": n, "code": E_JSON, "message": str(e)})
            continue
        # Uniqueness BEFORE the content checks, and on the normalized span
        # rather than the whole row: two rows saying the same thing are not two
        # pieces of evidence, and two rows saying DIFFERENT things about one
        # span are a contradiction that used to count as two verifications.
        # The reverse — one raw span producing two normalized spans — is
        # legitimate (a page can become two sections) and is not keyed here.
        if isinstance(row, dict):
            key = (str(row.get("normalized_path")),
                   str(row.get("normalized_lines")))
            if key in seen:
                problems.append({
                    "line": n, "code": E_DUPLICATE,
                    "message": f"{key[0]} {key[1]} is already mapped on line "
                               f"{seen[key]} — one normalized span has one "
                               "origin, and counting it twice inflates the "
                               "verified total without verifying anything"})
                continue
            seen[key] = n
            span = ANCHOR_LINE_RE.match(key[1])
            if span:
                a = int(span.group(1))
                mapped.setdefault(key[0], []).append(
                    (a, int(span.group(2) or a)))
        state, found = _check_row(row, corpus, raw_root)
        for pr in found:
            problems.append({"line": n, **pr})
        if state == "verified":
            text_verified += 1
            raw_verified += 1
        elif state == "raw-unverified":
            text_verified += 1
        elif state == "unverifiable":
            unverifiable += 1

    if rows == 0:
        # Present and empty is not the same finding as absent. Absent means the
        # bundle has no raw documents; empty means something produced a sidecar
        # and it says nothing — a broken artifact, and reporting it as `0/0
        # verified, ok: true` is how it passed a release.
        problems.append({"code": E_EMPTY,
                         "message": "meta/source-map.jsonl exists and contains "
                                    "no rows — an empty sidecar is a broken "
                                    "artifact, not an absent one; delete it if "
                                    "this corpus was never normalized"})

    if required and corpus is not None:
        # Completeness, and ONLY when the bundle declared the map mandatory.
        # Without the declaration there is nothing to be complete about: a
        # corpus can legitimately be part authored and part converted.
        cited = {str(s) for c in bundle.concepts()
                 for s in (c.meta.get("sources") or [])}
        uncovered, unresolved = [], []
        # Per-run cache, local to this one call, same shape and reasoning as
        # `_check_quotes`'s: distinct citations of the same corpus file are
        # the normal case (many concepts drawn from one source document), and
        # `cited_span`'s own bounds check (v0.25 audit F11) must not re-read
        # a file this loop has already read for an earlier citation.
        lines_cache: dict[str, list[str] | None] = {}
        for s in sorted(cited):
            path, start, end = cited_span(s, corpus, lines_cache=lines_cache)
            if start is None:
                unresolved.append(s)
                continue
            gaps = _gaps((start, end), mapped.get(path, []))
            if gaps:
                uncovered.append(f"{s} (unmapped: {', '.join(gaps[:3])}"
                                 f"{', …' if len(gaps) > 3 else ''})")
        if uncovered:
            problems.append({
                "code": E_COVERAGE,
                "message": f"{len(uncovered)} cited span(s) are not covered by "
                           f"the map: {'; '.join(uncovered[:3])}"
                           f"{'; …' if len(uncovered) > 3 else ''} — the map is "
                           "declared required, so a cited line the sidecar does "
                           "not account for is a hole in the provenance chain"})
        if unresolved:
            problems.append({
                "code": E_COVERAGE,
                "message": f"{len(unresolved)} cited source(s) could not be "
                           f"resolved to lines ({', '.join(unresolved[:3])}"
                           f"{', …' if len(unresolved) > 3 else ''}) — an "
                           "unreadable file or a heading anchor that names no "
                           "heading. Coverage is undecidable for these, and "
                           "guessing which way would decide whether provenance "
                           "holds"})

    if corpus is None:
        state = "unverifiable"
    elif raw_verified == text_verified and rows and not problems:
        state = "verified"
    else:
        state = "measured" if raw_root else "raw-unverified"
    return {"schema": SCHEMA,
            "state": state,
            "corpus_readable": corpus is not None,
            "raw_root_readable": raw_root is not None,
            "required": required,
            # THREE cases, not two. "declared and dead" used to print the same
            # sentence as "not declared", so the advice told an owner to declare
            # a root that was already sitting in their purpose.md.
            "note": ("page and bbox are carried, not verified — the core cannot "
                     "open a raw document"
                     + ("" if raw_root else
                        "; raw_sha256 was not recomputed, because "
                        + ("normalization.raw_root is declared and is not a "
                           "readable directory"
                           if _raw_root_declared(bundle) else
                           "this bundle declares no normalization.raw_root"))),
            "ok": not problems, "rows": rows,
            # `verified` is retained and means BOTH halves, so a reader who does
            # not know about the split cannot mistake a text-only check for a
            # complete one. The two components are what to look at.
            "verified": raw_verified,
            "text_verified": text_verified, "raw_verified": raw_verified,
            "unverifiable": unverifiable, "problems": problems}
