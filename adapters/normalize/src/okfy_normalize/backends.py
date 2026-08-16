"""Conversion backends, keyed by name.

A backend turns one raw file into Markdown plus the spans it can vouch for. The
registry exists so OKFy never hard-depends on any particular converter: docling,
marker, pymupdf and pandoc all produce the same row shape, and the source-map
schema does not care which one ran.

`passthrough` is the backend that needs nothing installed. It is what makes this
adapter testable in CI at all — a converter that requires a multi-hundred-package
ML stack cannot produce test evidence, so a phase that only shipped `docling`
would be unverifiable by construction.
"""
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

PASSTHROUGH_EXTS = {".md", ".markdown", ".txt"}


class BackendUnavailable(RuntimeError):
    """A named backend exists but its package is not installed. Carries the
    install line — an ImportError traceback tells a user nothing actionable."""


@dataclass
class Converted:
    """Markdown plus the provenance the backend can honestly attest.

    `spans` are 1-based inclusive `(start, end)` line ranges into `text`, each
    with whatever the converter knows about where they came from. `page` and
    `bbox` are optional because most converters cannot supply them and inventing
    a page number is worse than omitting one.
    """
    text: str
    spans: list[dict] = field(default_factory=list)


def options_digest(options: dict) -> str:
    """Digest of the options that actually shaped the conversion. Canonical JSON
    so key order cannot change it, and the real dict so an option change DOES —
    a digest that ignores its input is worse than no digest, because it reads as
    a guarantee."""
    blob = json.dumps(options or {}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _passthrough(src: Path, options: dict) -> Converted:
    """Markdown and plain text, unchanged. One span covering the whole file:
    passthrough has no page structure to report, and a fabricated finer mapping
    would be a claim nothing backs."""
    if src.suffix.lower() not in PASSTHROUGH_EXTS:
        raise ValueError(
            f"passthrough handles {', '.join(sorted(PASSTHROUGH_EXTS))}, not "
            f"{src.suffix or 'files without a suffix'} — use a converting "
            "backend (see --backend) for raw documents")
    text = src.read_text(encoding="utf-8")
    n = len(text.splitlines())
    return Converted(text=text, spans=[{"start": 1, "end": max(n, 1)}])


def _passthrough_version() -> str:
    from okfy_normalize import __version__
    return __version__


def _docling(src: Path, options: dict) -> Converted:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as e:                                  # pragma: no cover
        raise BackendUnavailable(
            "backend 'docling' needs the docling package, which is not "
            "installed. Install it with:\n"
            "    uv pip install docling\n"
            "or convert Markdown and text with `--backend passthrough`, which "
            "needs nothing.") from e
    doc = DocumentConverter().convert(str(src)).document       # pragma: no cover
    text = doc.export_to_markdown()                            # pragma: no cover
    # docling's own provenance is per-item and does not line up with the
    # exported Markdown's line numbers without re-deriving them; until that is
    # measured against a real document, claiming a finer mapping would be the
    # fabrication this module exists to avoid.
    n = len(text.splitlines())                                 # pragma: no cover
    return Converted(text=text, spans=[{"start": 1, "end": max(n, 1)}])


def _docling_version() -> str:                                 # pragma: no cover
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("docling")
    except PackageNotFoundError as e:
        raise BackendUnavailable(
            "backend 'docling' needs the docling package, which is not "
            "installed. Install it with:\n"
            "    uv pip install docling") from e


BACKENDS = {
    "passthrough": (_passthrough, _passthrough_version),
    "docling": (_docling, _docling_version),
}


def get_backend(name: str):
    """(convert, version) for a named backend. Unknown names list what exists —
    a typo must not read like a missing install."""
    if name not in BACKENDS:
        raise KeyError(f"unknown backend {name!r} "
                       f"(available: {', '.join(sorted(BACKENDS))})")
    return BACKENDS[name]
