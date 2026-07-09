from okfy import frontmatter
from okfy.bundle import Bundle
from okfy.proposals import (accept, list_proposals, propose, refine, reject)

from .common import _archetype_for, _print


def cmd_propose(a) -> int:
    b = Bundle(a.bundle)
    if a.action != "delete":
        if a.from_file is None:
            raise ValueError("--from <concept.md> required unless --action delete")
        meta, body = frontmatter.parse(a.from_file.read_text(encoding="utf-8"))
    else:
        meta, body = {}, ""
    path = propose(b, meta, body, target=a.target, action=a.action, note=a.note)
    _print({"proposal": b.concept_id(path)})
    return 0


def cmd_review(a) -> int:
    b = Bundle(a.bundle)
    if a.rcmd == "list":
        _print(list_proposals(b))
    elif a.rcmd == "accept":
        tid = accept(b, a.proposal_id, archetype=_archetype_for(b))
        _print({"accepted": a.proposal_id, "target": tid})
    else:
        reject(b, a.proposal_id, reason=a.reason)
        _print({"rejected": a.proposal_id})
    return 0


def cmd_refine(a) -> int:
    b = Bundle(a.bundle)
    refine(b, a.concept_id, a.from_file.read_text(encoding="utf-8"),
           message=a.message)
    _print({"refined": a.concept_id})
    return 0
