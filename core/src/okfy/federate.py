"""Federated querying over a Workspace (ADR-0010/0011/0013): per-member
expansion via lexicon rows (member's own + workspace-level meta/lexicon.md)
and accepted same-as rows, per-member BM25 with lexicon pins entering RRF at
rank 1, role grouping, deterministic constrains auto-pull."""
from okfy import lexicon
from okfy.bm25 import tokenize
from okfy.bundle import Bundle
from okfy.crosswalk import load_rows, parse_ref
from okfy.index import load_index
from okfy.workspace import Workspace

RRF_K = 60


def _entry(idx: dict, cid: str) -> dict | None:
    for c in idx["concepts"]:
        if c["id"] == cid:
            return c
    return None


def _name_tokens(entry: dict) -> set[str]:
    toks = set(tokenize(str(entry.get("title", ""))))
    for a in entry.get("aliases", []) or []:
        toks |= set(tokenize(str(a)))
    return toks


def active_rows(ws: Workspace) -> tuple[list, int]:
    """Accepted crosswalk rows minus stale ones (a row citing a concept that
    changed since its member SHA was pinned). Stale rows are DETECTED by
    workspace_status; here they stop influencing answers too — an equivalence
    or constraint reviewed against a concept that has since changed is a
    hypothesis again, not a fact (audit round 7, finding 3)."""
    from okfy.workspace import workspace_status
    stale = {(s["src"], s["rel"], s["dst"])
             for s in workspace_status(ws)["stale_rows"]}
    rows, n_stale = [], 0
    for r in load_rows(ws):
        if r.status != "accepted":
            continue
        if (r.src, r.rel, r.dst) in stale:
            n_stale += 1
            continue
        rows.append(r)
    return rows, n_stale


def expansion_terms(ws: Workspace, member_name: str, text: str,
                    rows: list | None = None) -> list[str]:
    """Terms to ADD when querying member_name: for each accepted same-as row
    with one side in another member and one side in member_name, if the query
    lexically touches the FAR side's title/aliases, contribute the NEAR side's
    title+alias tokens."""
    if rows is None:
        rows, _ = active_rows(ws)
    qtok = set(tokenize(text))
    indexes = {m.name: load_index(Bundle(m.path)) for m in ws.members}
    extra: list[str] = []
    for r in rows:
        if r.rel != "same-as":
            continue
        (m1, c1), (m2, c2) = parse_ref(r.src), parse_ref(r.dst)
        if member_name == m1:
            near, far = (m1, c1), (m2, c2)
        elif member_name == m2:
            near, far = (m2, c2), (m1, c1)
        else:
            continue
        far_e = _entry(indexes[far[0]], far[1])
        near_e = _entry(indexes[near[0]], near[1])
        if not far_e or not near_e:
            continue
        if qtok & _name_tokens(far_e):
            extra.extend(sorted(_name_tokens(near_e) - qtok))
    return sorted(set(extra))


def _same_as_root(rows: list) -> dict[str, str]:
    """Union-find over accepted non-stale same-as rows: ref -> class root.
    Only accepted rows merge — a proposed equivalence is a hypothesis, not
    an identity."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in rows:
        if r.rel == "same-as":
            ra, rb = find(r.src), find(r.dst)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    return {x: find(x) for x in list(parent)}


def _merge_same_as(ranked: dict[str, dict], rows: list) -> list[dict]:
    """Merge-proper dedup: entries of one same-as class within one ROLE
    collapse into a single result — scores add (both members' evidence
    counts once, not as two rank-eating rows), the stronger entry is
    canonical, the rest land in `duplicates`. Cross-role pairs stay
    separate: a constraint mirror of a knowledge concept must remain
    visible in the constraints group."""
    root = _same_as_root(rows)
    groups: dict[tuple, list[dict]] = {}
    for e in ranked.values():
        groups.setdefault((root.get(e["ref"], e["ref"]), e["role"]),
                          []).append(e)
    out = []
    for members in groups.values():
        members.sort(key=lambda e: (-e["score"], e["ref"]))
        canon = members[0]
        if len(members) > 1:
            canon = dict(canon)
            canon["score"] = sum(m["score"] for m in members)
            canon["duplicates"] = sorted(m["ref"] for m in members[1:])
        out.append(canon)
    return out


def federated_query(ws: Workspace, text: str, n: int = 10,
                    pull_top: int = 5) -> dict:
    from okfy.query import filter_pool, search_pool
    ws_rows = lexicon.load_rows(Bundle(ws.root))   # workspace meta/lexicon.md
    role_of = {m.name: m.role for m in ws.members}
    rows, n_stale = active_rows(ws)
    ranked: dict[str, dict] = {}
    notes: list[str] = []
    if n_stale:
        notes.append(f"workspace: {n_stale} stale crosswalk row(s) excluded "
                     "from expansion/merge/constrains — re-review and re-pin "
                     "(okfy workspace status, /okfy:relink)")
    expanded: dict[str, str] = {}
    for m in ws.members:
        b = Bundle(m.path)
        eff = lexicon.expand(lexicon.load_rows(b) + ws_rows, text)
        extra = expansion_terms(ws, m.name, text, rows=rows)
        qtext = eff["expanded_query"] + (" " + " ".join(extra) if extra else "")
        expanded[m.name] = qtext
        notes += [f"{m.name}: {note}" for note in eff["notes"]]
        pool = filter_pool(load_index(b))
        for rank, h in enumerate(search_pool(pool, eff["pins"], qtext, n)):
            ref = f"{m.name}:{h['id']}"
            e = ranked.setdefault(ref, {
                "ref": ref, "member": m.name, "role": role_of[m.name],
                "type": h["type"], "title": h["title"],
                "description": h["description"], "score": 0.0,
                "via": h.get("via", "search")})
            if h.get("stale"):
                e["stale"] = True
                e["stale_reason"] = h.get("stale_reason", "")
            e["score"] += 1.0 / (RRF_K + rank + 1)
    ordered = sorted(_merge_same_as(ranked, rows),
                     key=lambda e: (-e["score"], e["ref"]))
    out = {"knowledge": [e for e in ordered if e["role"] == "knowledge"][:n],
           "constraints": [e for e in ordered if e["role"] == "constraints"][:n],
           "notes": notes, "expanded_query": expanded}
    for e in out["knowledge"] + out["constraints"]:
        e["score"] = round(e["score"], 5)
    pulled: dict[str, dict] = {}
    # a merged canonical answers FOR its absorbed duplicates — constraints
    # bound to an absorbed ref must still fire (audit round 7, finding 2)
    top_refs = set()
    for e in out["knowledge"][:pull_top]:
        top_refs.add(e["ref"])
        top_refs.update(e.get("duplicates", []))
    indexes = {m.name: load_index(Bundle(m.path)) for m in ws.members}
    for r in rows:
        if r.rel != "constrains":
            continue
        if r.dst in top_refs:
            cm, cc = parse_ref(r.src)
            e = _entry(indexes[cm], cc)
            if e is not None:
                pulled.setdefault(r.src, {
                    "ref": r.src, "member": cm, "role": role_of.get(cm, "constraints"),
                    "type": e["type"], "title": e["title"],
                    "description": e["description"], "score": None,
                    "via": "constrains"})
    out["pulled"] = sorted(pulled.values(), key=lambda e: e["ref"])
    return out


def fed_show(ws: Workspace, ref: str):
    member, cid = parse_ref(ref)
    names = {m.name for m in ws.members}
    if member not in names:
        raise KeyError(f"unknown member: {member}")
    c = Bundle(ws.member(member).path).get(cid)
    if c is None:
        raise KeyError(f"concept not found: {ref}")
    return c
