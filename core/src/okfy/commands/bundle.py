from okfy.budget import usage_report
from okfy.bundle import Bundle
from okfy.index import build_index, save_index
from okfy.init import init_bundle
from okfy.package import append_log, package
from okfy.validate import validate_conformance, validate_integrity

from .common import _archetype_for, _print


def cmd_init(a) -> int:
    b = init_bundle(a.bundle, a.corpus, language=a.language,
                    write_policy=a.write_policy, embed=a.embed)
    # `acceptance` echoed back because the scaffold now declares a policy the
    # caller did not ask for: a default that is on is only honest if it is said.
    _print({"created": str(b), "acceptance": {"dissent": "required"}})
    return 0


def cmd_validate(a) -> int:
    b = Bundle(a.bundle)
    r = validate_conformance(b, include_drafts=a.all, include_proposals=a.all)
    arch = None if a.no_archetype else _archetype_for(b)
    r2 = validate_integrity(b, arch, strict_sources=a.strict_sources,
                            strict_quality=a.strict_quality,
                            strict_provenance=a.strict_provenance,
                            strict_package=a.strict_package,
                            strict_execution=a.strict_execution,
                            strict_schema=a.strict_schema,
                            strict_injection=a.strict_injection)
    r.findings.extend(r2.findings)
    r.sources = r2.sources
    r.coverage = r2.coverage
    r.spans = r2.spans
    if not a.quiet:
        _print(r.to_dict())
    return 0 if r.ok else 1


def cmd_sourcemap(a) -> int:
    """Read-only: proves a normalized citation maps back to the raw document it
    was converted from. Absence of the sidecar is not a defect."""
    from okfy.sourcemap import check_source_map
    out = check_source_map(Bundle(a.bundle))
    if a.json:
        _print(out)
        return 0 if out["ok"] else 1
    if out["state"] == "absent":
        print(f"sourcemap: {out['note']} (meta/source-map.jsonl)")
        return 0
    print(f"sourcemap: {out['rows']} row(s) — {out['verified']} verified, "
          f"{out['unverifiable']} unverifiable, {len(out['problems'])} problem(s)")
    if not out["corpus_readable"]:
        print("corpus tree not readable — rows are unverifiable, not verified")
    print(out["note"])
    for p in out["problems"]:
        print(f"  line {p.get('line', '?')}: {p['code']} {p['message']}")
    return 0 if out["ok"] else 1


def cmd_release_check(a) -> int:
    """Auto-detects a workspace, the same way `okfy query` does — the artifact
    decides which predicate applies, so a workspace path can never silently get
    the (weaker, wrong) single-bundle answer."""
    from okfy.workspace import Workspace, is_workspace
    if is_workspace(a.bundle):
        from okfy.ws_release import workspace_release_check
        out = workspace_release_check(Workspace.load(a.bundle))
    else:
        from okfy.release import release_check
        out = release_check(Bundle(a.bundle))
    _print(out)
    return 0 if out["ok"] else 1


def cmd_index(a) -> int:
    b = Bundle(a.bundle)
    if getattr(a, "usage", False):
        _print(usage_report(b, journal=getattr(a, "journal", None)))
        return 0
    idx = build_index(b)
    save_index(b, idx)
    _print({"indexed": len(idx["concepts"])})
    return 0


def cmd_package(a) -> int:
    b = Bundle(a.bundle)
    package(b, _archetype_for(b), demote_unretrieved=getattr(a, "demote_unretrieved", False),
           shard_index=getattr(a, "shard_index", False))
    _print({"packaged": str(b.root)})
    return 0


def cmd_log(a) -> int:
    b = Bundle(a.bundle)
    append_log(b, a.message)
    _print({"logged": a.message})
    return 0


def cmd_codes(a) -> int:
    """The diagnostic code registry (okfy.codes.CODES), sorted by code. Not
    bundle-specific — every E_/W_ code the core and MCP adapter can raise."""
    from okfy.codes import CODES
    if a.json:
        _print(CODES)
        return 0
    for code in sorted(CODES):
        row = CODES[code]
        print(f"{code}  {row['kind']}  {row['summary']}")
        print(f"  way out: {row['way_out']}")
    return 0
