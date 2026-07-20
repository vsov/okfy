from okfy.bundle import Bundle
from okfy.index import build_index, save_index
from okfy.init import init_bundle
from okfy.package import append_log, package
from okfy.validate import validate_conformance, validate_integrity

from .common import _archetype_for, _print


def cmd_init(a) -> int:
    b = init_bundle(a.bundle, a.corpus, language=a.language,
                    write_policy=a.write_policy, embed=a.embed)
    _print({"created": str(b)})
    return 0


def cmd_validate(a) -> int:
    b = Bundle(a.bundle)
    r = validate_conformance(b, include_drafts=a.all, include_proposals=a.all)
    arch = None if a.no_archetype else _archetype_for(b)
    r2 = validate_integrity(b, arch, strict_sources=a.strict_sources,
                            strict_quality=a.strict_quality,
                            strict_provenance=a.strict_provenance,
                            strict_package=a.strict_package)
    r.findings.extend(r2.findings)
    r.sources = r2.sources
    if not a.quiet:
        _print(r.to_dict())
    return 0 if r.ok else 1


def cmd_index(a) -> int:
    b = Bundle(a.bundle)
    idx = build_index(b)
    save_index(b, idx)
    _print({"indexed": len(idx["concepts"])})
    return 0


def cmd_package(a) -> int:
    b = Bundle(a.bundle)
    package(b, _archetype_for(b))
    _print({"packaged": str(b.root)})
    return 0


def cmd_log(a) -> int:
    b = Bundle(a.bundle)
    append_log(b, a.message)
    _print({"logged": a.message})
    return 0
