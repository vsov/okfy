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

# What a conversion can honestly attest about WHERE a span came from. The set is
# small on purpose and adding to it is a data change, not a schema rewrite.
#
#   whole-document  the span is the whole converted file, and the row proves
#                   only "this Markdown came from that raw file". It licenses NO
#                   claim about a page, a column or a region.
#   page            the span is bounded to one page of the raw document, and
#                   `page` on the row is that page.
#
# Every backend here reports `whole-document` today. That is the honest answer
# and it was already the behaviour — what was missing is that the row did not
# SAY so, and an unstated granularity reads as a finer claim than the converter
# can support. On a 400-page PDF the difference between "came from this file"
# and "came from page 4" is the entire value of the provenance chain.
GRANULARITIES = ("whole-document", "page")


class OptionsNotHonoured(ValueError):
    """An option was passed that the chosen backend does not apply.

    Its own type, not a bare ValueError, because `normalize_tree` turns a
    ValueError from a conversion into a per-file SKIP — which would silently
    skip every file in the tree and write an empty sidecar, reporting a contract
    violation as an absent corpus."""


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

    `granularity` states which of those two situations this is, from
    `GRANULARITIES`. It is required rather than defaulted: a backend that does
    not think about what it can attest is exactly the backend whose rows should
    not be trusted with a page number, and a default would let it skip the
    question silently.
    """
    text: str
    spans: list[dict] = field(default_factory=list)
    granularity: str = "whole-document"

    def __post_init__(self):
        if self.granularity not in GRANULARITIES:
            raise ValueError(
                f"granularity {self.granularity!r} is not one of "
                f"{list(GRANULARITIES)} — a value nothing understands is worse "
                "than an unstated one, because it still reads as a claim")


def options_digest(options: dict) -> str:
    """Digest of the options that actually shaped the conversion. Canonical JSON
    so key order cannot change it, and the real dict so an option change DOES —
    a digest that ignores its input is worse than no digest, because it reads as
    a guarantee."""
    from okfy.sourcemap import OPTIONS_DIGEST_LEN
    blob = json.dumps(options or {}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    # The length comes from core's validator, not from a number repeated here.
    # Core cannot import this adapter, so the constant can only live there; a
    # producer that restated it would drift the day the validator changed and
    # would fail with E_SOURCEMAP_DIGEST on output that was never wrong.
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:OPTIONS_DIGEST_LEN]


# Which option keys each backend actually APPLIES. Both are empty today, and
# that is the finding: `options` was accepted, hashed into
# `converter_options_digest`, and read by nobody — so the digest pinned a
# setting that had shaped nothing. A digest over an ignored value is worse than
# no digest, because it reads as a guarantee; emptying the digest instead would
# have been the same lie told more quietly. The refusal is the fix, and this
# table is where a backend declares it has started honouring something.
BACKEND_OPTIONS: dict[str, frozenset] = {
    "passthrough": frozenset(),   # copies bytes; there is nothing to configure
    "docling": frozenset(),       # whole-document export; no option changes it
}


def check_options(backend: str, options: dict) -> None:
    """Refuse option keys the named backend does not apply.

    Called BOTH from `normalize_tree`'s preflight — so a whole run is refused
    before anything is written — and from each backend, so a caller invoking a
    backend directly gets the same answer."""
    unknown = sorted(set(options or ()) - BACKEND_OPTIONS.get(backend, frozenset()))
    if not unknown:
        return
    honoured = sorted(BACKEND_OPTIONS.get(backend, frozenset()))
    raise OptionsNotHonoured(
        f"backend {backend!r} does not apply {', '.join(unknown)} "
        f"({'it honours ' + ', '.join(honoured) if honoured else 'it honours no options'}). "
        "The options mapping is hashed into converter_options_digest, so "
        "accepting an option that changes nothing would pin a setting that "
        "never took effect. Drop the option, or use a backend that honours it")


def _passthrough(src: Path, options: dict) -> Converted:
    """Markdown and plain text, unchanged. One span covering the whole file:
    passthrough has no page structure to report, and a fabricated finer mapping
    would be a claim nothing backs."""
    check_options("passthrough", options)
    if src.suffix.lower() not in PASSTHROUGH_EXTS:
        raise ValueError(
            f"passthrough handles {', '.join(sorted(PASSTHROUGH_EXTS))}, not "
            f"{src.suffix or 'files without a suffix'} — use a converting "
            "backend (see --backend) for raw documents")
    text = src.read_text(encoding="utf-8")
    n = len(text.splitlines())
    return Converted(text=text, spans=[{"start": 1, "end": max(n, 1)}],
                     granularity="whole-document")


def _passthrough_version() -> str:
    from okfy_normalize import __version__
    return __version__


def _docling(src: Path, options: dict) -> Converted:
    # BEFORE the import, so the refusal is reachable and testable on a machine
    # without docling's ~130-package stack installed.
    check_options("docling", options)
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
    # whole-document, and the row will say so. docling's own provenance is
    # per-item and does not line up with the exported Markdown's line numbers
    # without re-deriving them; until that is measured against a real document,
    # claiming `page` here would be the fabrication this module exists to avoid.
    return Converted(text=text, spans=[{"start": 1, "end": max(n, 1)}],
                     granularity="whole-document")             # pragma: no cover


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
