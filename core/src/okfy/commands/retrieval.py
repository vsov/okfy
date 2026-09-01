import sys

from okfy.bundle import Bundle
from okfy.query import links, query, show
from okfy.sampling import sample_for_review
from okfy.workspace import Workspace, is_workspace

from .common import _print


def cmd_query(a) -> int:
    if is_workspace(a.bundle):
        from okfy.federate import federated_query
        ws = Workspace.load(a.bundle)
        _print(federated_query(ws, a.text, n=a.n))
        return 0
    b = Bundle(a.bundle)
    out = query(b, a.text, type_=a.type_, tag=a.tag, n=a.n,
                include_meta=a.include_meta, expand=a.expand,
                include_stale=a.include_stale)
    if out["expanded_query"] != a.text:
        print(f"expanded: {out['expanded_query']}", file=sys.stderr)
    for note in out["notes"]:
        print(f"note: {note}", file=sys.stderr)
    _print(out["results"])
    return 0


def cmd_show(a) -> int:
    if is_workspace(a.bundle):
        from okfy.federate import fed_show
        ws = Workspace.load(a.bundle)
        c = fed_show(ws, a.concept_id)
        print(c.path.read_text(encoding="utf-8"))
        return 0
    b = Bundle(a.bundle)
    c = show(b, a.concept_id)
    print(c.path.read_text(encoding="utf-8"))
    return 0


def cmd_links(a) -> int:
    b = Bundle(a.bundle)
    _print(links(b, a.concept_id))
    return 0


def cmd_sample(a) -> int:
    b = Bundle(a.bundle)
    out = sample_for_review(b, fraction=a.fraction, minimum=a.minimum)
    # The PINS come from the tool that picks the sample. v0.21 requires
    # meta/purpose-fitness.md to record what the review read
    # (`sampled_fingerprint`) and what it was read against (`checks_digest`),
    # and `okfy sample` is the only command that already knows the first of
    # those. Emitting them here is what makes the L3 artifact writable by
    # anything that cannot import okfy — the reference builder is shell, and
    # recomputing a digest in shell would restate a definition that must exist
    # exactly once.
    #
    # They describe THIS selection. A reviewer who samples, then reviews a
    # different set, must not copy these across — and `E_QUALITY_DRIFT` is what
    # catches it when they do.
    from okfy.archetype import checks_digest, load_archetype
    from okfy.sampling import sampled_fingerprint
    plan = b.plan()
    name = plan.meta.get("archetype") if plan else None
    arch = None
    if name:
        try:
            arch = load_archetype(str(name))
        except FileNotFoundError:
            arch = None
    out["sampled_fingerprint"] = sampled_fingerprint(b, out["sampled"])
    out["checks_digest"] = checks_digest(arch)
    _print(out)
    return 0
