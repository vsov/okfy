"""Federated querying over a Workspace (ADR-0010/0011/0013): per-member
expansion via lexicon rows (member's own + workspace-level meta/lexicon.md)
and accepted same-as rows, per-member BM25 with lexicon pins entering RRF at
rank 1, role grouping, deterministic constrains auto-pull."""
from okfy import lexicon
from okfy.bm25 import tokenize
from okfy.bundle import Bundle
from okfy.crosswalk import load_rows, parse_ref
from okfy.index import load_index
from okfy.validate import applies_to_malformed
from okfy.workspace import Member, Workspace

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


def active_rows(ws: Workspace) -> tuple[list, int, list[str]]:
    """Accepted crosswalk rows minus stale ones (a row citing a concept that
    changed since its member SHA was pinned — or touching a member whose
    baseline cannot be verified at all: no pin, unreachable pin, broken git.
    An unverifiable baseline is stale-by-definition, never fresh-by-default;
    audit round 8). Stale rows are DETECTED by workspace_status; here they
    stop influencing answers too — an equivalence or constraint reviewed
    against a concept that has since changed is a hypothesis again."""
    from okfy.workspace import workspace_status
    st = workspace_status(ws)
    stale = {(s["src"], s["rel"], s["dst"]) for s in st["stale_rows"]}
    rows, n_stale = [], 0
    for r in load_rows(ws):
        if r.status != "accepted":
            continue
        if (r.src, r.rel, r.dst) in stale:
            n_stale += 1
            continue
        rows.append(r)
    return rows, n_stale, st.get("unverifiable_members", [])


def expansion_terms(ws: Workspace, member_name: str, text: str,
                    rows: list | None = None) -> list[str]:
    """Terms to ADD when querying member_name: for each accepted same-as row
    with one side in another member and one side in member_name, if the query
    lexically touches the FAR side's title/aliases, contribute the NEAR side's
    title+alias tokens.

    A caller-supplied `rows` (as `federated_query` passes) is trusted as-is —
    it has already been through `_scope_crosswalk_rows`. The default path
    (no `rows`, e.g. a direct call) scopes for itself: a personal member's
    out-of-scope concept must not drive expansion regardless of who calls
    this (finding 41)."""
    if rows is None:
        rows, _, _ = active_rows(ws)
        rows, _ = _scope_crosswalk_rows(rows, personal_scope_ids(ws, ws.project_key()))
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


def _in_scope(applies_to, project_key: str | None) -> bool:
    """Fail closed (ADR-0015): missing, empty, or invalid `applies_to` —
    shape OR grammar — is OUT of scope, never in. Invalidity is
    `validate.applies_to_malformed`, the COMPLETE predicate
    `validate._check_applies_to` uses to raise E_APPLIES_TO on this same
    concept: not a list / empty / a non-string-or-blank entry (shape), or
    an entry that is neither `*` nor a legal `workspace.PROJECT_KEY_RE`
    project key (grammar). Calling the one shared function for BOTH halves
    is what keeps every reader of `applies_to` from drifting apart again —
    finding F10 (v0.25): dropping bad entries and matching on what survived
    made `['*', 123]` a search hit here while `validate` called it
    malformed; finding A7 (v0.26): even after F10 unified the shape half,
    this function still skipped the grammar half, so `['*', 'BAD KEY']`
    matched via `'*'` here while `validate` called `'BAD KEY'`'s grammar
    malformed. Only a value `applies_to_malformed` calls valid can be in
    scope, and only via an exact project_key match or the `*` wildcard."""
    if applies_to_malformed(applies_to):
        return False
    return any(k == "*" or k == project_key for k in applies_to)


def _personal_scope_filter(m: Member, pool: list[dict], project_key: str | None
                           ) -> tuple[list[dict], str]:
    """Drop every pool entry out of scope for a `personal` member and return
    (filtered pool, note). `applies_to` is not carried by the index (only
    concept_tokens/id/title/... are — see index.py), so it is read straight
    from the concept files, once per member, rather than by changing the
    index schema for a v1 read-only feature."""
    applies = {c.id: c.meta.get("applies_to") for c in Bundle(m.path).concepts()}
    total = len(pool)
    kept = [c for c in pool if _in_scope(applies.get(c["id"]), project_key)]
    note = (f"{m.name}: personal scope {project_key} — "
           f"{len(kept)} of {total} concepts in scope")
    return kept, note


def personal_scope_ids(ws: Workspace, project_key: str | None) -> dict[str, set[str]]:
    """For every `personal` member, the set of concept ids in scope for this
    query's project_key. Built ONCE per query (audit finding 41) so every
    path that can put a ref into the federated result — the search pool via
    `_personal_scope_filter`, the crosswalk (same-as merge, expansion,
    constrains auto-pull) via `ref_in_scope` below — checks the identical
    predicate. A member absent from this dict is not `personal` and is never
    narrowed here."""
    out: dict[str, set[str]] = {}
    for m in ws.members:
        if m.role != "personal":
            continue
        applies = {c.id: c.meta.get("applies_to") for c in Bundle(m.path).concepts()}
        out[m.name] = {cid for cid, a in applies.items()
                       if _in_scope(a, project_key)}
    return out


def ref_in_scope(scope_ids: dict[str, set[str]], ref: str) -> bool:
    """True unless `ref` (\"member:concept-id\") names a `personal` member's
    concept that this query's project_key put out of scope. A non-personal
    member (absent from scope_ids) is always in scope — only `personal`
    narrows, same as the search-pool filter."""
    member, cid = parse_ref(ref)
    if member not in scope_ids:
        return True
    return cid in scope_ids[member]


def _scope_crosswalk_rows(rows: list, scope_ids: dict[str, set[str]]
                          ) -> tuple[list, int]:
    """Drop every crosswalk row (same-as OR constrains) with either side
    naming an out-of-scope personal concept. The crosswalk is a side door
    into the federated result — same-as drives merge and query expansion,
    constrains drives the auto-pull — and it must fail closed exactly like
    the search pool does, regardless of how the row got there (owner-written,
    `crosswalk.candidates`, or LLM-proposed then accepted)."""
    kept, dropped = [], 0
    for r in rows:
        if ref_in_scope(scope_ids, r.src) and ref_in_scope(scope_ids, r.dst):
            kept.append(r)
        else:
            dropped += 1
    return kept, dropped


def federated_query(ws: Workspace, text: str, n: int = 10,
                    pull_top: int = 5) -> dict:
    from okfy.query import filter_pool, search_pool
    ws_rows = lexicon.load_rows(Bundle(ws.root))   # workspace meta/lexicon.md
    role_of = {m.name: m.role for m in ws.members}
    rows, n_stale, unverifiable = active_rows(ws)
    scope_ids = personal_scope_ids(ws, ws.project_key())
    rows, n_scoped = _scope_crosswalk_rows(rows, scope_ids)
    ranked: dict[str, dict] = {}
    notes: list[str] = []
    if n_scoped:
        notes.append(
            f"workspace: {n_scoped} crosswalk row(s) excluded — touch a "
            "personal concept out of this workspace's scope (ADR-0015 "
            "fail-closed; same-as merge/expansion and constrains auto-pull "
            "never see them)")
    if unverifiable:
        notes.append(
            f"workspace: member baseline unverifiable for "
            f"{', '.join(unverifiable)} (no pin / unreachable pin / broken "
            "git) — ALL crosswalk rows touching them are excluded until "
            "re-pinned (okfy workspace status)")
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
        if m.role == "personal":
            # scope filtering happens BEFORE search_pool, so BM25's idf/avgdl
            # are computed over the already-narrowed pool — ranking of the
            # concepts that remain equals a direct query of a bundle that
            # never held the filtered ones (see test_personal_scope.py).
            pool, note = _personal_scope_filter(m, pool, ws.project_key())
            notes.append(note)
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
    # `personal` is a third, ADDITIVE result group — added to the payload
    # only when the workspace actually has a personal member, so a workspace
    # of only knowledge/constraints members keeps a byte-identical shape.
    if any(m.role == "personal" for m in ws.members):
        out["personal"] = [e for e in ordered if e["role"] == "personal"][:n]
    for e in out["knowledge"] + out["constraints"] + out.get("personal", []):
        e["score"] = round(e["score"], 5)
    pulled: dict[str, dict] = {}
    # a top result answers FOR its whole accepted same-as class, not only
    # for the duplicates that happened to be retrieved: a constraint bound
    # to ANY class member fires regardless of that member's retrieval rank
    # (audit round 8 — `duplicates` alone missed equivalents outside top-N)
    root = _same_as_root(rows)
    by_root: dict[str, set[str]] = {}
    for ref, rt in root.items():
        by_root.setdefault(rt, set()).add(ref)
    top_refs = set()
    for e in out["knowledge"][:pull_top]:
        top_refs |= by_root.get(root.get(e["ref"], e["ref"]), {e["ref"]})
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
