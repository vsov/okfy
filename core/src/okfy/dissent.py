"""Dissent ledger: the durable half of the shadow consolidation audit.

`okfy merge-audit` reports what a merge dropped, but it re-derives that report on
every run and nothing records what the owner decided about it. Without a durable
layer the same disagreement is re-adjudicated forever; with one, a merge decision
finally leaves an artifact, which is the property every other step of the pipeline
already has.

The format deliberately mirrors `ledger.py`: one JSON object per line in
`meta/dissent.jsonl`, append-only, committed by the same helper. A row records one
claim that a merge group hides a real distinction, who held it, where in the source
it is anchored, and how it was resolved.

ADJUDICATION FINGERPRINT. Every row — not only a waiver — is a statement about a
specific version of a specific merge. `adjudication_fingerprint` is a SHA-256 over
the merged concept's bytes AND the sorted ids of the drafts that fed it, so the row
stops closing the group the moment either side moves: edit the concept, or add a
draft to the group in a later run, and the group returns to `stale`. Binding to the
concept alone was not enough — a group that grew a new draft would still have read as
closed by an adjudication that never saw it. This is `retrieval_fingerprint`'s idiom
(evidence is only valid for the state it was produced against) transplanted onto merge.

OPT-IN BY CONSTRUCTION. `release-check` consults this ledger only when the bundle
declares `acceptance.dissent: required` in `meta/purpose.md`. Bundles built before
v0.10 have no dissent rows, and turning them red for missing an artifact that did not
exist when they were accepted would be retroactive — the same reasoning that made
`provenance: legacy` an escape hatch rather than a migration.
"""
import hashlib
import json
from pathlib import Path

from okfy.bundle import Bundle
from okfy.proposals import _commit

DISSENT = "meta/dissent.jsonl"

VERDICTS = ("split", "no-schism")
_REQUIRED_STR = ("run_id", "group", "claim", "anchor", "verdict")


def dissent_path(bundle: Bundle) -> Path:
    return bundle.root / "meta" / "dissent.jsonl"


def concept_fingerprint(bundle: Bundle, concept_id: str) -> str:
    """SHA-256 of a concept's file as it stands now."""
    p = bundle.root / f"{concept_id}.md"
    if not p.is_file():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _group_drafts(bundle: Bundle, group: str) -> list[str]:
    from okfy.merge_audit import merge_groups
    _, groups = merge_groups(bundle)
    for g in groups:
        if g["final"] == group:
            return list(g["drafts"])
    return []


def adjudication_fingerprint(bundle: Bundle, group: str,
                             drafts: list[str] | None = None) -> str:
    """What an adjudication of `group` was actually about: the merged concept's
    bytes plus the sorted ids of the drafts that fed it. A row carrying a stale
    fingerprint no longer closes its group — neither an edited concept nor a
    newly-added draft can inherit a decision made without it."""
    if drafts is None:
        drafts = _group_drafts(bundle, group)
    payload = json.dumps({"final": concept_fingerprint(bundle, group),
                          "drafts": sorted(str(d) for d in drafts)},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check(row: dict) -> None:
    for k in _REQUIRED_STR:
        v = row.get(k)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"dissent row: {k} must be a non-empty string")
    if row["verdict"] not in VERDICTS:
        raise ValueError(f"dissent row: verdict must be one of {list(VERDICTS)}, "
                         f"got {row['verdict']!r}")
    if not isinstance(row.get("drafts"), list) or not row["drafts"]:
        raise ValueError("dissent row: drafts must be a non-empty list")


def add_row(bundle: Bundle, run_id: str, group: str, drafts, claim: str,
            anchor: str, verdict: str, overruled_because: str = "") -> dict:
    """Append one adjudication row.

    A `split` verdict does NOT close its group and does not require a reason:
    an unresolved split has not been overruled by anyone yet, and demanding a
    justification at the moment of recording invited the consolidator to write
    one and move on. A split stays `open` until the owner waives it (with a
    reason) or the concept is actually split. `overruled_because` remains
    available as the consolidator's note on why the merge was kept — it
    annotates, it never resolves."""
    row = {"run_id": run_id, "group": group,
           "drafts": list(drafts) if isinstance(drafts, (list, tuple)) else drafts,
           "claim": claim, "anchor": anchor, "verdict": verdict}
    _check(row)
    if overruled_because:
        row["overruled_because"] = overruled_because
    row["adjudication_fingerprint"] = adjudication_fingerprint(
        bundle, group, row["drafts"])
    path = dissent_path(bundle)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _commit(bundle, [DISSENT], f"dissent: {verdict} {group}")
    return row


def read_rows(bundle: Bundle, group: str | None = None) -> list:
    """All dissent rows in order, optionally filtered to one merge group."""
    path = dissent_path(bundle)
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if group is not None:
        rows = [r for r in rows if r.get("group") == group]
    return rows


def waive(bundle: Bundle, group: str, reason: str) -> dict:
    """Owner-only: accept an open `split` for this group as adjudicated. The
    waiver pins the concept's current content AND the group's current draft
    set, so a later edit — or a later draft joining the group — reopens it."""
    if not reason.strip():
        raise ValueError("a waiver without a reason is not an adjudication — "
                         "pass --reason")
    if not read_rows(bundle, group=group):
        raise KeyError(f"no dissent rows for group: {group}")
    row = {"run_id": "owner-waiver", "group": group, "drafts": ["(owner)"],
           "claim": "owner waived the open dissent for this group",
           "anchor": f"{group}.md", "verdict": "no-schism",
           "overruled_because": reason,
           "waiver": reason,
           "adjudication_fingerprint": adjudication_fingerprint(bundle, group)}
    _check(row)
    path = dissent_path(bundle)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _commit(bundle, [DISSENT], f"dissent: waive {group}")
    return row


def group_state(bundle: Bundle, group: str,
                drafts: list[str] | None = None) -> str:
    """'unadjudicated' (no rows) | 'open' (a split nobody has resolved) |
    'stale' (adjudicated, but the concept or the draft set moved since) |
    'closed'.

    Only an owner waiver closes an open split. A later `no-schism` row does not:
    the party that recorded the merge cannot also dismiss the objection to it."""
    rows = read_rows(bundle, group=group)
    if not rows:
        return "unadjudicated"
    current = adjudication_fingerprint(bundle, group, drafts)
    waivers = [r for r in rows if r.get("waiver")]
    if waivers:
        return ("closed"
                if str(waivers[-1].get("adjudication_fingerprint") or "") == current
                else "stale")
    if any(r.get("verdict") == "split" for r in rows):
        return "open"
    return ("closed"
            if str(rows[-1].get("adjudication_fingerprint") or "") == current
            else "stale")
