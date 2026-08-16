"""Source map validator (v0.19, P0-a of the adjacent analysis).

A raw PDF, deck or scan cannot be handed to a worker, so it is converted to
Markdown outside the core and the corpus holds the Markdown. That conversion is
where provenance normally dies: a concept cites `handbook.md#L811-L824`, and
nothing connects those lines back to page 47 of the PDF they came from.

`meta/source-map.jsonl` is the sidecar that connects them — one JSON object per
normalized span, carrying the raw file and its hash, the normalized span in the
SAME anchor grammar a concept cites, the hash of that span's text, and which
converter produced it. This module validates the sidecar.

TWO DELIBERATE LIMITS, both of which the output states rather than hides:

1. `page` and `bbox` are CARRIED, never verified. Verifying them means opening a
   PDF, which means a PDF library, which the core will not have — it has exactly
   one runtime dependency and keeps it. A bbox here is the converter's claim,
   recorded so a human can open the page and look.
2. When the corpus tree is not readable, a row is `unverifiable`, never `pass`.
   A hash cannot be recomputed from a file that is not there, and reporting that
   as verified is the failure mode `okfy cost` already refuses.

The converter is not named by this schema — docling, marker, pymupdf or pandoc
all produce rows of the same shape. Recording `converter`, `converter_version`
and `converter_options_digest` is what makes a re-conversion comparable.

Imports: stdlib and `okfy` only. `tests/test_tokens.py` asserts this.
"""
import hashlib
import json
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

E_JSON = "E_SOURCEMAP_JSON"
E_FIELD = "E_SOURCEMAP_FIELD"
E_LINES = "E_SOURCEMAP_LINES"
E_NO_FILE = "E_SOURCEMAP_NO_FILE"
E_TEXT_DRIFT = "E_SOURCEMAP_TEXT_DRIFT"


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


def _check_row(row: dict, corpus: Path | None) -> tuple[str, list[dict]]:
    """One row -> (state, problems). State is `verified` only when the text hash
    was actually recomputed and matched."""
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
    return "verified", []


def check_source_map(bundle: Bundle) -> dict:
    """Validate `meta/source-map.jsonl`. Reads only; writes nothing, ever."""
    path = bundle.root / SOURCE_MAP
    if not path.is_file():
        # Absence is not a defect. Most bundles are built from text corpora and
        # will never have one; a missing optional sidecar must not read as a gap.
        return {"schema": SCHEMA, "state": "absent", "note": "no source map",
                "ok": True, "rows": 0, "verified": 0, "unverifiable": 0,
                "problems": []}
    corpus = _corpus(bundle)
    problems: list[dict] = []
    verified = unverifiable = rows = 0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        rows += 1
        try:
            row = json.loads(line)
        except ValueError as e:
            problems.append({"line": n, "code": E_JSON, "message": str(e)})
            continue
        state, found = _check_row(row, corpus)
        for p in found:
            problems.append({"line": n, **p})
        if state == "verified":
            verified += 1
        elif state == "unverifiable":
            unverifiable += 1
    return {"schema": SCHEMA,
            "state": "measured" if corpus else "unverifiable",
            "corpus_readable": corpus is not None,
            "note": ("page and bbox are carried, not verified — the core cannot "
                     "open a raw document"),
            "ok": not problems, "rows": rows, "verified": verified,
            "unverifiable": unverifiable, "problems": problems}
