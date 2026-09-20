import json
import sys

from okfy import frontmatter
from okfy.bundle import Bundle
from okfy.proposals import (accept, evidence_state, list_proposals, near_warning,
                            nearest, propose, propose_batch, refine, reject,
                            show_proposal)

from .common import _archetype_for, _print


def cmd_propose(a) -> int:
    b = Bundle(a.bundle)
    if a.batch is not None:
        # v0.24 (a): --batch is exclusive of every single-entry flag — each
        # JSONL line supplies its own action/target/etc., so a single-entry
        # flag sitting alongside --batch would either be silently ignored or
        # silently misread as applying to every line. Neither is honest.
        single_entry_flags = (a.target, a.from_file, a.patch_file, a.query,
                              a.extends, a.new_id, a.supersedes, a.reverts,
                              a.flag_type, a.distinct_from, a.evidence, a.reopen)
        if any(single_entry_flags) or a.action != "update" or a.note:
            raise ValueError(
                "--batch is exclusive of the single-entry propose flags — "
                "each line in the batch file supplies its own action/target/"
                "note/etc.")
        lines = a.batch.read_text(encoding="utf-8").splitlines()
        result = propose_batch(b, lines, actor=a.actor, dry_run=a.dry_run,
                               partial=a.partial)
        _print(result)
        return 0 if result["ok"] else 1
    if a.dry_run or a.partial:
        raise ValueError("--dry-run and --partial only apply with --batch")
    if a.patch_file is not None and a.from_file is not None:
        raise ValueError("--patch-file and --from are mutually exclusive — a "
                         "patch is applied to the target's CURRENT body, --from "
                         "supplies the whole new body")
    patch = None
    if a.patch_file is not None:
        if a.action != "update":
            raise ValueError("--patch-file is only valid with --action update")
        patch = json.loads(a.patch_file.read_text(encoding="utf-8"))
    if a.action in ("delete", "flag", "gap") or patch is not None:
        meta, body = {}, ""
    else:
        if a.from_file is None:
            raise ValueError("--from <concept.md> required unless --action "
                             "delete, flag, gap, or --patch-file is given")
        meta, body = frontmatter.parse(a.from_file.read_text(encoding="utf-8"))
    evidence = None
    if a.evidence:
        kind, _, ref = a.evidence.partition("=")
        evidence = {"kind": kind.strip(), "ref": ref.strip()}
    distinct_from = None
    if a.distinct_from:
        distinct_from = {}
        for item in a.distinct_from:
            did, _, reason = item.partition("=")
            distinct_from[did.strip()] = reason.strip()
    path = propose(b, meta, body, target=a.target, action=a.action, note=a.note,
                   actor=a.actor, evidence=evidence, extends=a.extends,
                   reopen=a.reopen, distinct_from=distinct_from,
                   new_id=a.new_id, supersedes=a.supersedes, reverts=a.reverts,
                   flag_type=a.flag_type, query=a.query, patch=patch)
    c = Bundle(a.bundle).get(b.concept_id(path))
    env = c.meta.get("proposal") or {}
    near = env.get("near") or []
    dist = env.get("distinct_from") or {}
    out = {"proposal": b.concept_id(path)}
    if near:
        out["near"] = near
    if dist:
        out["distinct_from"] = dist
    ev_state = evidence_state(b, c.meta)
    if ev_state:
        out["evidence_state"] = ev_state
    if env.get("sources_state"):
        out["sources_state"] = env["sources_state"]
    warn = near_warning(near, dist)
    if warn:
        out["warnings"] = [warn]
    # v0.24 (b): only meaningful for a real create (an --extends turns one
    # into an update before propose() ever sees "create").
    if env.get("action") == "create":
        out["nearest"] = nearest(b, meta)
    _print(out)
    return 0


def _print_observed(observed: dict) -> None:
    """v0.24 (a): the adapter's own session trace, rendered in TEXT mode too
    — GUIDE.md and adapters/mcp/README.md both document `review show`
    rendering it, marking each read concept moved/deleted; only `--json`
    actually did."""
    print("observed:")
    if "queries" in observed:
        print(f"  queries: {observed['queries']}")
    if "searched_before_propose" in observed:
        print(f"  searched_before_propose: {observed['searched_before_propose']}")
    if "target_shown" in observed:
        print(f"  target_shown: {observed['target_shown']}")
    read_set = observed.get("read_set") or []
    print(f"  read_set ({len(read_set)}):")
    for entry in read_set:
        state = "deleted" if entry.get("deleted") else (
            "moved" if entry.get("moved") else "unchanged")
        print(f"    {entry.get('id')}: {state}")


def _print_show(row: dict) -> None:
    print(f"{row['id']}  action={row['action']}  target={row['target']}  "
         f"valid={row['valid']}")
    if row["issues"]:
        print("issues: " + "; ".join(row["issues"]))
    if row.get("flag_type"):
        print(f"flag_type: {row['flag_type']}")
    if row.get("query"):
        print(f"query: {row['query']}")
    if row["note"]:
        print(f"note: {row['note']}")
    if row.get("observed"):
        _print_observed(row["observed"])
    impact = row.get("impact")
    if impact is None:
        return
    print("impact:")
    print(f"  inbound_links ({len(impact['inbound_links'])}): "
         f"{', '.join(impact['inbound_links']) or '(none)'}")
    print(f"  verified_linkers: {impact['verified_linkers']}")
    rows = ", ".join(f"{r['term']} ({r['status']})" for r in impact["lexicon_rows"])
    print(f"  lexicon_rows ({len(impact['lexicon_rows'])}): {rows or '(none)'}")
    exps = ", ".join(f"[{e['suite']}] {e['query']!r}" for e in impact["expectations"])
    print(f"  expectations ({len(impact['expectations'])}): {exps or '(none)'}")
    print(f"  index_line: {impact['index_line']}")
    print(f"  open_proposals ({len(impact['open_proposals'])}): "
         f"{', '.join(impact['open_proposals']) or '(none)'}")


def cmd_review(a) -> int:
    b = Bundle(a.bundle)
    if a.rcmd == "list":
        _print(list_proposals(b))
    elif a.rcmd == "show":
        row = show_proposal(b, a.proposal_id)
        if a.json:
            _print(row)
        else:
            _print_show(row)
    elif a.rcmd == "accept":
        # v0.24 (c): MEASURED, not asserted — sys.stdin.isatty() at the CLI
        # layer is the only place that can tell tty from non-tty; a library
        # caller (accept()'s own default) records `api`.
        channel = "tty" if sys.stdin.isatty() else "non-tty"
        tid = accept(b, a.proposal_id, archetype=_archetype_for(b),
                    as_actor=a.as_actor, channel=channel)
        _print({"accepted": a.proposal_id, "target": tid})
    else:
        channel = "tty" if sys.stdin.isatty() else "non-tty"
        reject(b, a.proposal_id, reason=a.reason, as_actor=a.as_actor,
              channel=channel)
        _print({"rejected": a.proposal_id})
    return 0


def cmd_refine(a) -> int:
    b = Bundle(a.bundle)
    refine(b, a.concept_id, a.from_file.read_text(encoding="utf-8"),
           message=a.message)
    _print({"refined": a.concept_id})
    return 0
