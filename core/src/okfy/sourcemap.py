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
from okfy.validate import ANCHOR_LINE_RE

SOURCE_MAP = "meta/source-map.jsonl"
SCHEMA = "okfy-source-map@1"

REQUIRED = ("raw_path", "raw_sha256", "normalized_path", "normalized_lines",
            "text_sha256", "converter", "converter_version",
            "converter_options_digest")
# `converter_ref` is the converter's own handle on the region (docling calls it a
# provenance item id); it is opaque to OKFy and travels only so a re-run can be
# lined up against the original.
OPTIONAL = ("page", "bbox", "converter_ref")

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


def _raw_root(bundle: Bundle) -> Path | None:
    root = str(normalization(bundle).get("raw_root") or "").strip()
    if not root:
        return None
    p = Path(root)
    return p if p.is_dir() else None


def _corpus(bundle: Bundle) -> Path | None:
    snap = bundle.get("meta/corpus")
    root = Path(str(snap.meta.get("corpus") or "")) if snap else None
    return root if root and root.is_dir() else None


def span_text(path: Path, start: int, end: int) -> str:
    """The cited lines, joined with their line endings intact. `text_sha256` is
    the SHA-256 of this string's UTF-8 bytes — defined once, here, so a converter
    and this validator cannot disagree about whether the trailing newline counts."""
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


def check_source_map(bundle: Bundle) -> dict:
    """Validate `meta/source-map.jsonl`. Reads only; writes nothing, ever."""
    path = bundle.root / SOURCE_MAP
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
                    "problems": [{"code": E_MISSING,
                                  "message": "meta/purpose.md declares "
                                             "normalization.source_map: required "
                                             "and meta/source-map.jsonl is absent"}]}
        # Absence is not a defect. Most bundles are built from text corpora and
        # will never have one; a missing optional sidecar must not read as a gap.
        return {"schema": SCHEMA, "state": "absent", "note": "no source map",
                "ok": True, "rows": 0, "verified": 0, "text_verified": 0,
                "raw_verified": 0, "unverifiable": 0, "required": False,
                "problems": []}
    corpus = _corpus(bundle)
    raw_root = _raw_root(bundle)
    problems: list[dict] = []
    text_verified = raw_verified = unverifiable = rows = 0
    seen: dict[tuple, int] = {}
    mapped: set[str] = set()
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
            mapped.add(key[0])
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
        uncovered = sorted(s for s in cited if s not in mapped)
        if uncovered:
            problems.append({
                "code": E_COVERAGE,
                "message": f"{len(uncovered)} cited corpus file(s) have no "
                           f"mapping row ({', '.join(uncovered[:3])}"
                           f"{', …' if len(uncovered) > 3 else ''}) — the map is "
                           "declared required, so a cited file it does not "
                           "mention is a hole in the provenance chain"})

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
            "note": ("page and bbox are carried, not verified — the core cannot "
                     "open a raw document"
                     + ("" if raw_root else
                        "; raw_sha256 was not recomputed, because this bundle "
                        "declares no normalization.raw_root")),
            "ok": not problems, "rows": rows,
            # `verified` is retained and means BOTH halves, so a reader who does
            # not know about the split cannot mistake a text-only check for a
            # complete one. The two components are what to look at.
            "verified": raw_verified,
            "text_verified": text_verified, "raw_verified": raw_verified,
            "unverifiable": unverifiable, "problems": problems}
