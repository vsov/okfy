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

WAIVER FINGERPRINT. A waiver is a statement about a specific version of a concept.
`waiver_fingerprint` is a SHA-256 over the waived concept's content at the moment of
waiving, so editing that concept afterwards reopens the row rather than silently
inheriting the old decision. This is `retrieval_fingerprint`'s idiom (an eval run is
only valid for the bundle state it ran against) transplanted onto merge.

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
    """SHA-256 of a concept's file as it stands now. A waiver carries this so a
    later edit to the concept reopens the row instead of inheriting the decision."""
    p = bundle.root / f"{concept_id}.md"
    if not p.is_file():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


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
    """Append one adjudication row. A `split` verdict that was nonetheless merged
    must say why in overruled_because — an unexplained override is the thing this
    ledger exists to prevent."""
    row = {"run_id": run_id, "group": group,
           "drafts": list(drafts) if isinstance(drafts, (list, tuple)) else drafts,
           "claim": claim, "anchor": anchor, "verdict": verdict}
    _check(row)
    if verdict == "split" and not overruled_because.strip():
        raise ValueError("dissent row: a split verdict that was merged anyway "
                         "requires --overruled-because")
    if overruled_because:
        row["overruled_because"] = overruled_because
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
    waiver pins the concept's current content, so a later edit reopens it."""
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
           "waiver_fingerprint": concept_fingerprint(bundle, group)}
    _check(row)
    path = dissent_path(bundle)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _commit(bundle, [DISSENT], f"dissent: waive {group}")
    return row


def group_state(bundle: Bundle, group: str) -> str:
    """'unadjudicated' (no rows) | 'open' (a split with no later waiver) |
    'stale' (waived, but the concept changed since) | 'closed'."""
    rows = read_rows(bundle, group=group)
    if not rows:
        return "unadjudicated"
    waivers = [r for r in rows if r.get("waiver_fingerprint") is not None]
    if waivers:
        pinned = str(waivers[-1].get("waiver_fingerprint") or "")
        return "closed" if pinned == concept_fingerprint(bundle, group) else "stale"
    return "open" if any(r.get("verdict") == "split" for r in rows) else "closed"
