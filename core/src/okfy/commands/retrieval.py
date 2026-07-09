import sys

from okfy.bundle import Bundle
from okfy.query import links, query, sample_for_review, show
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
    _print(sample_for_review(b, fraction=a.fraction, minimum=a.minimum))
    return 0
