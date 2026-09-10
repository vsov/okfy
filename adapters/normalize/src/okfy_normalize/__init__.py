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
import unicodedata
import json
from pathlib import Path

from okfy.sourcemap import span_text

from okfy_normalize.backends import (BackendUnavailable, check_options,
                                     get_backend,
                                     options_digest)

__version__ = "0.23.0"
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
    # BEFORE anything is written, and before dest exists. A per-file refusal
    # would be caught by the skip handler below and reported as a skipped
    # document, so an unhonoured option would empty the whole corpus and exit
    # describing it as an absent one.
    check_options(backend, options)
    digest = options_digest(options)
    ver = version_of()

    # The whole output-path table BEFORE anything is written, and BEFORE dest is
    # even created. Two documents whose names differ only by extension normalize
    # onto one `.md`, and converting them in turn made the second silently
    # overwrite the first: the sidecar then carried two rows with one
    # `normalized_path` and two different `raw_sha256`, and the command exited 0
    # with a document gone. Refusing mid-way would leave a half-written dest,
    # which is a worse state than either outcome — so the refusal happens first
    # and dest is left absent.
    root = src.parent if src.is_file() else src
    targets: dict[str, tuple[str, str]] = {}
    for f in _sources(src):
        rel = str(f.relative_to(root))
        out = str(Path(rel).with_suffix(".md"))
        # THE KEY IS FOLDED, THE MESSAGE IS NOT. v0.20 compared literal path
        # strings, which is the comparison the filesystem does NOT do: on
        # case-insensitive APFS and NTFS, `Same.md` and `same.md` are two keys
        # and one file, so the second conversion silently replaced the first
        # and the command exited 0 with `converted: 2, skipped: []`. macOS also
        # stores names in NFD while most tools emit NFC, so `résumé` written two
        # ways is the same collision wearing different bytes.
        #
        # Conservative on purpose: this refuses a few pairs that a strictly
        # case-sensitive volume would accept. That trade is deliberate and one
        # directional — a false refusal costs a rename, a missed collision costs
        # a document, and the sidecar then carries a text hash describing a file
        # that is not there.
        #
        # NOT handled, and worth naming: a filesystem that folds MORE than
        # NFC+casefold — some SMB and older HFS+ configurations fold width or
        # diacritics too. Those would still collide undetected. Widening the key
        # further starts refusing legitimately distinct names, so this is where
        # the line sits rather than where the problem ends.
        key = unicodedata.normalize("NFC", out).casefold()
        if key in targets:
            first_src, first_out = targets[key]
            # Three wordings, because the reader's next action differs. Naming
            # the two paths is useless when they RENDER identically — an NFC and
            # an NFD spelling of `résumé.md` print the same and differ only in
            # bytes, so "'résumé.md' and 'résumé.md'" reads as a bug in the
            # error message rather than a fact about the files.
            if first_out == out:
                same = "the same output path"
            elif unicodedata.normalize("NFC", first_out) == \
                    unicodedata.normalize("NFC", out):
                same = (f"{out!r} written two ways — the names differ only in "
                        "Unicode normalization form (NFC vs NFD), which is one "
                        "file on disk though the bytes are not equal")
            else:
                same = (f"{first_out!r} and {out!r}, which are one file on a "
                        "case-insensitive filesystem")
            raise ValueError(
                f"output path collision: {first_src!r} and {rel!r} both "
                f"normalize to {same}. Nothing was written. Rename one of them, "
                "or convert them into separate dest trees — normalizing both "
                "would silently replace the first document with the second")
        targets[key] = (rel, out)

    dest.mkdir(parents=True, exist_ok=True)

    rows, converted, skipped = [], 0, []
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
                # What this row can attest about WHERE the span came from. A row
                # that does not say reads as a finer claim than the converter
                # can support.
                "granularity": out.granularity,
            }
            for k in ("page", "bbox", "converter_ref"):
                if span.get(k) is not None:
                    row[k] = span[k]
            rows.append(row)

    # `converted == 0` and any skipped file are REPORTED here and refused by the
    # CLI, not raised here. The split is deliberate: this function's product is
    # the report, and a caller embedding the adapter in a pipeline needs the
    # skip reasons in order to decide. The command line has no such caller — a
    # shell script reads exit 0 as "the corpus is complete" — so `__main__`
    # turns the same report into a nonzero exit. That is also why no
    # `--allow-skipped` flag exists: the API is already the escape hatch.
    (dest / SOURCE_MAP).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")
    return {"backend": backend, "converter_version": ver,
            "converter_options_digest": digest, "converted": converted,
            "rows": len(rows), "skipped": skipped,
            "source_map": str(dest / SOURCE_MAP)}
