"""Raw documents to normalized Markdown plus an OKFy source-map sidecar.

Deliberately OUTSIDE core. `core/src/okfy/sourcemap.py` validates the sidecar
with stdlib only; this package produces it, and may depend on whatever a
converter needs. That split is the whole design: the proof costs core nothing,
and the conversion can be as heavy as it likes without ever entering core's
dependency tree.

The text hash is computed with `okfy.sourcemap.span_text`, the same function the
validator uses. Sharing it means the producer and the checker cannot disagree
about whether a trailing newline is part of the span — a disagreement that would
surface as `E_SOURCEMAP_TEXT_DRIFT` on output that was never wrong.
"""
import hashlib
import json
from pathlib import Path

from okfy.sourcemap import span_text

from okfy_normalize.backends import (BackendUnavailable, get_backend,
                                     options_digest)

__version__ = "0.19.0"
SOURCE_MAP = "source-map.jsonl"

__all__ = ["BackendUnavailable", "normalize_tree", "__version__", "SOURCE_MAP"]


def _sources(src: Path) -> list[Path]:
    if src.is_file():
        return [src]
    return sorted(p for p in src.rglob("*")
                  if p.is_file() and not p.name.startswith("."))


def normalize_tree(src: Path, dest: Path, backend: str = "passthrough",
                   options: dict | None = None) -> dict:
    """Convert every file under `src` into Markdown under `dest`, writing
    `dest/source-map.jsonl`. Never writes into `src`."""
    src = Path(src).resolve()
    dest = Path(dest).resolve()
    if dest == src or dest.is_relative_to(src):
        raise ValueError(f"dest {dest} is inside src {src} — the adapter never "
                         "writes into the source tree")
    convert, version_of = get_backend(backend)
    options = dict(options or {})
    digest = options_digest(options)
    ver = version_of()
    dest.mkdir(parents=True, exist_ok=True)

    rows, converted, skipped = [], 0, []
    root = src.parent if src.is_file() else src
    for f in _sources(src):
        rel = f.relative_to(root)
        try:
            out = convert(f, options)
        except ValueError as e:
            skipped.append({"path": str(rel), "reason": str(e)})
            continue
        target = dest / rel.with_suffix(".md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(out.text, encoding="utf-8")
        converted += 1
        raw_sha = hashlib.sha256(f.read_bytes()).hexdigest()
        for span in out.spans:
            start, end = int(span["start"]), int(span["end"])
            row = {
                "raw_path": str(rel),
                "raw_sha256": raw_sha,
                "normalized_path": str(target.relative_to(dest)),
                "normalized_lines": f"L{start}-L{end}",
                "text_sha256": hashlib.sha256(
                    span_text(target, start, end).encode("utf-8")).hexdigest(),
                "converter": backend,
                "converter_version": ver,
                "converter_options_digest": digest,
            }
            for k in ("page", "bbox", "converter_ref"):
                if span.get(k) is not None:
                    row[k] = span[k]
            rows.append(row)

    (dest / SOURCE_MAP).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")
    return {"backend": backend, "converter_version": ver,
            "converter_options_digest": digest, "converted": converted,
            "rows": len(rows), "skipped": skipped,
            "source_map": str(dest / SOURCE_MAP)}
