"""Pure MCP tool handlers: import okfy core in-process, return JSON-able dicts.
No MCP types here — server.py owns the protocol surface, this owns the logic."""
import inspect
import re

from okfy import federate, frontmatter, proposals, query as q
from okfy.codes import CODES
from okfy.index import load_index
from okfy_mcp import render
from okfy_mcp.resolve import Target
from okfy_mcp.session import Session

_FED_PARAMS = inspect.signature(federate.federated_query).parameters

# v0.24 (e): new — a proposal's frontmatter block that opens but never
# closes (the way `frontmatter.parse` says "missing frontmatter closing
# '---'") means the AGENT's output was cut off before the write was
# complete, not that the text is wrong — a different way out (resend) than
# a genuinely malformed block. See h_propose below.
E_PROPOSAL_TRUNCATED = "E_PROPOSAL_TRUNCATED"


def _cap(text: str, max_chars: int) -> tuple[str, bool]:
    """Bound a text field. Longer than max_chars -> truncate + honest marker."""
    if len(text) <= max_chars:
        return text, False
    marker = f"…[truncated at {max_chars} chars — use the section parameter]"
    return text[:max_chars] + marker, True


def _section(text: str, heading: str) -> str:
    """Return the '## <heading>' block (heading line through the next '## ').
    Case-insensitive exact heading match; missing -> ValueError with the menu."""
    want = heading.strip().lower()
    out: list[str] = []
    grabbing = False
    for ln in text.splitlines(keepends=True):
        if ln.startswith("## "):
            if grabbing:
                break
            if ln[3:].strip().lower() == want:
                grabbing = True
                out.append(ln)
            continue
        if grabbing:
            out.append(ln)
    if not grabbing:
        avail = [ln[3:].strip() for ln in text.splitlines() if ln.startswith("## ")]
        raise ValueError(f"section not found: {heading!r}; available headings: {avail}")
    return "".join(out)


def _bundle_empty_reason(t: Target, text: str, type_: str | None, tag: str | None,
                         expand: bool, include_stale: bool) -> str:
    """Why a bundle-mode query came back with 0 hits, in one of three
    states — see okfy_query's docstring. `filtered-out` costs one extra
    cheap query() call (only made when a filter was actually in play);
    `bundle-empty` and `no-term-matched` cost nothing beyond the index
    already loaded for the real query."""
    idx = load_index(t.bundle)
    if not q.filter_pool(idx, type_=None, tag=None, include_stale=True):
        return "bundle-empty"
    filtered = type_ is not None or tag is not None or not include_stale
    if filtered:
        unfiltered = q.query(t.bundle, text, type_=None, tag=None, n=1,
                             expand=expand, include_stale=True)
        if unfiltered["results"]:
            return "filtered-out"
    return "no-term-matched"


def h_query(t: Target, text: str, type_: str | None = None,
            tag: str | None = None, n: int = 10, expand: bool = True,
            include_stale: bool = True,
            max_tokens: int = render.DEFAULT_QUERY_MAX_TOKENS,
            session: Session | None = None) -> dict:
    if t.is_workspace:
        kw = {"n": n}
        if "expand" in _FED_PARAMS:
            kw["expand"] = expand
        if "include_stale" in _FED_PARAMS:
            kw["include_stale"] = include_stale
        out = federate.federated_query(t.workspace, text, **kw)
        result = {"mode": "workspace", **out}
        ids = [h.get("ref") or h.get("id")
               for group in ("knowledge", "constraints", "personal")
               for h in out.get(group, []) if isinstance(h, dict)]
    else:
        out = q.query(t.bundle, text, type_=type_, tag=tag, n=n,
                      expand=expand, include_stale=include_stale)
        hits = out["results"]
        if hits:
            shown, omitted = render.shape_hits(hits, max_tokens,
                                               out["expanded_query"], text)
            out["results"] = shown
            out["omitted"] = omitted
        else:
            out["omitted"] = []
            out["empty_reason"] = _bundle_empty_reason(
                t, text, type_, tag, expand, include_stale)
        result = {"mode": "bundle", **out}
        ids = [h["id"] for h in out.get("results", [])]
    result["notice"] = render.NOTICE
    if session is not None:
        session.record_query(text, ids)
    return result


def _resolve_concept(t: Target, concept_id: str):
    if t.is_workspace:
        return federate.fed_show(t.workspace, concept_id)
    return q.show(t.bundle, concept_id)


_MAX_SHOW_IDS = 10


def _neighbours(t: Target, c) -> tuple[list[dict], int]:
    """Outgoing-link descriptions only (no bodies), capped at 5 — the
    concept's own out-links, already computed at index time (index.py's
    `concept_links`), resolved to each target's frontmatter `description`.
    Workspace mode: [] — cross-bundle link resolution is out of scope for
    this budget feature (adapters/mcp/README.md)."""
    if t.is_workspace:
        return [], 0
    idx = load_index(t.bundle)
    by_id = {e["id"]: e for e in idx["concepts"]}
    cid = t.bundle.concept_id(c.path)
    out_links = by_id.get(cid, {}).get("links", [])
    neighbours = [{"id": lid, "description": by_id[lid].get("description", "")}
                 for lid in out_links[:5] if lid in by_id]
    return neighbours, len(out_links) - len(neighbours)


def _h_show_one(t: Target, concept_id: str, max_chars: int,
                section: str | None, session: Session | None) -> dict:
    c = _resolve_concept(t, concept_id)
    # v0.24 (c): the FILE's bytes, same function proposals._file_sha256 uses —
    # independent of section/truncation, so it always names what is actually
    # on disk, not what this particular call happened to return.
    file_sha256 = proposals._file_sha256(c.path)
    content = c.path.read_text(encoding="utf-8")
    if section is not None:
        content = _section(content, section)
    content, truncated = _cap(content, max_chars)
    out = {"id": concept_id, "content": content, "sha256": file_sha256}
    if truncated:
        out["truncated"] = True
    if session is not None:
        session.record_show(concept_id, file_sha256)
    return out


def _h_show_many(t: Target, concept_ids: list[str], max_tokens: int,
                 session: Session | None) -> dict:
    """v0.24 (f): several concepts under ONE fair-shared body budget —
    A concept whose fair
    share rounds to nothing is OMITTED entirely (not shown with an empty
    body) — see render.fair_share's docstring for that convention."""
    if len(concept_ids) > _MAX_SHOW_IDS:
        return {"error": "too_many_concept_ids", "limit": _MAX_SHOW_IDS,
                "message": f"at most {_MAX_SHOW_IDS} concept_ids per call, "
                          f"got {len(concept_ids)}"}
    seen: list[str] = []
    for cid in concept_ids:
        if cid not in seen:
            seen.append(cid)

    resolved: dict[str, object] = {}
    missing: list[str] = []
    for cid in seen:
        try:
            resolved[cid] = _resolve_concept(t, cid)
        except KeyError:
            missing.append(cid)
    ok_ids = [cid for cid in seen if cid in resolved]

    # `.body` (frontmatter already stripped by Bundle.get) is what gets
    # budgeted — repeating each concept's YAML frontmatter inside a shared
    # token budget would waste it on fixed overhead already visible via the
    # concept's own id/type/title (okfy_overview) and via `neighbours`
    # below; `sha256` stays the FILE's bytes regardless, same as single-id
    # show.
    bodies = [resolved[cid].body for cid in ok_ids]
    shas = {cid: proposals._file_sha256(resolved[cid].path) for cid in ok_ids}
    rendered = render.fair_share(bodies, max_tokens) if bodies else []

    concepts: list[dict] = []
    omitted: list[str] = []
    for cid, (body, truncated, _tok) in zip(ok_ids, rendered):
        if truncated and body == "":
            omitted.append(cid)
            continue
        nb, nb_omitted = _neighbours(t, resolved[cid])
        concepts.append({"id": cid, "sha256": shas[cid],
                         "view": "truncated" if truncated else "full",
                         "body": body, "neighbours": nb,
                         "neighbours_omitted": nb_omitted})
        if session is not None:
            session.record_show(cid, shas[cid])
    return {"concepts": concepts, "missing": missing, "omitted": omitted}


def h_show(t: Target, concept_id: str | None = None,
           concept_ids: list[str] | None = None, max_chars: int = 20000,
           max_tokens: int = render.DEFAULT_SHOW_MAX_TOKENS,
           section: str | None = None, session: Session | None = None) -> dict:
    if concept_ids is not None:
        out = _h_show_many(t, concept_ids, max_tokens, session)
    else:
        if concept_id is None:
            raise ValueError("either concept_id or concept_ids is required")
        out = _h_show_one(t, concept_id, max_chars, section, session)
    out["notice"] = render.NOTICE
    return out


def h_links(t: Target, concept_id: str) -> dict:
    if t.is_workspace:
        raise ValueError("links works on a single bundle; query a member "
                         "bundle path directly for its link graph")
    return q.links(t.bundle, concept_id)


def h_overview(t: Target, type_: str | None = None, max_items: int = 50,
               max_chars: int = 20000) -> dict:
    if type_ is not None:
        if t.is_workspace:
            raise ValueError("type listing is a single-bundle view; point at a "
                             "member bundle path for its concept list")
        matches = [c for c in load_index(t.bundle)["concepts"]
                   if c.get("type") == type_]
        concepts = [{"id": c["id"], "type": c.get("type"),
                     "title": c.get("title", ""),
                     "description": c.get("description", "")}
                    for c in matches[:max_items]]
        return {"concepts": concepts, "total": len(matches)}
    if t.is_workspace:
        lines = ["# Workspace: " + str(t.workspace.meta.get("title", ""))]
        for m in t.workspace.members:
            lines.append(f"- {m.name} ({m.role}) — {m.path}")
        text = "\n".join(lines)
    else:
        idx = t.bundle.root / "index.md"
        text = idx.read_text(encoding="utf-8") if idx.is_file() else "# (no index)"
    index, truncated = _cap(text, max_chars)
    out = {"index": index}
    if truncated:
        out["truncated"] = True
    return out


_CODED = re.compile(r"^([EW]_[A-Z_]+): ")


def h_propose(t: Target, target: str, action: str, note: str,
              content: str | None, actor: str, evidence: dict | None = None,
              extends: str | None = None, reopen: str | None = None,
              distinct_from: dict[str, str] | None = None,
              new_id: str | None = None, supersedes: str | None = None,
              reverts: str | None = None, flag_type: str | None = None,
              query: str | None = None, patch: list[dict] | None = None,
              session: Session | None = None) -> dict:
    """File a proposal. A refusal the core names with a code comes back as data
    — `{error, message, way_out}` — so an agent can act on it instead of reading
    a traceback; `persisted: true` appears ONLY when a proposal file was written,
    and is the only thing an agent may report as remembered. A success may also
    carry `near` (ids of close-enough existing concepts), `evidence_state`
    (`{kind: resolved|not-found|unchecked}` for the declared evidence ref),
    `sources_state` (`checked`/`unchecked` for a proposal's `sources:`) and
    `warnings` (W_PROPOSAL_NEAR text) — none of these block anything.
    action `supersede` (v0.24) needs `new_id`, the successor concept's id;
    `supersedes` (any action) replaces an open proposal that shares this one's
    actor and target — otherwise `E_PROPOSAL_LANE`. action `flag` ("this is
    wrong") needs `flag_type` and `target` or `query`. action `gap` ("the
    bundle could not answer this") needs `query`. `patch` (action `update`
    only) is `[{old, new, count=1}]` applied to the target's current body
    instead of `content`. A `create` success also carries `nearest` (top-3
    BM25 hits for the proposal's own text, computed fresh — never a refusal)
    and, when non-empty, `nearest_guidance`."""
    if t.is_workspace:
        raise ValueError("propose targets a single member bundle; point the "
                         "server at that bundle's path, not the workspace")
    if patch is not None and content:
        raise ValueError("patch and content are mutually exclusive — a patch "
                         "is applied to the target's CURRENT body, content "
                         "supplies the whole new body")
    if action in ("delete", "flag", "gap") or patch is not None:
        meta, body = {}, ""
    else:
        if not content:
            raise ValueError("content (a full concept .md) is required unless "
                             "action=delete|flag|gap or patch is given")
        # v0.24 (e): truncated vs malformed intake — a proposal `content`
        # that got cut off mid-generation (frontmatter opens with `---` but
        # never closes, or the string just stops inside the block) needs a
        # DIFFERENT way out — resend, don't re-explain YAML — than content
        # that is simply bad YAML. `frontmatter.parse` already distinguishes
        # these by message; reuse the registered E_FRONTMATTER code for the
        # malformed half instead of inventing a second code for the same
        # underlying failure (core/src/okfy/validate.py already raises
        # E_FRONTMATTER for the identical parse failure on a committed
        # concept file — see codes.py).
        try:
            meta, body = frontmatter.parse(content)
        except frontmatter.FrontmatterError as e:
            msg = str(e)
            if msg == "missing frontmatter closing '---'":
                return {"error": E_PROPOSAL_TRUNCATED,
                        "message": f"{E_PROPOSAL_TRUNCATED}: {msg}",
                        "way_out": ("the content was cut off before the "
                                   "frontmatter closed — resend the whole "
                                   "text; if your output limit is the "
                                   "cause, send the body in a patch or "
                                   "split the change")}
            return {"error": "E_FRONTMATTER",
                    "message": f"E_FRONTMATTER: {msg}",
                    "way_out": CODES["E_FRONTMATTER"]["way_out"]}
    # v0.24 (a): the adapter's OWN session trace, never the caller's word for
    # it — `session` is this process's in-memory record, not a parameter an
    # MCP client can pass. `target_shown`/`searched_before_propose` are null/
    # false with no session (e.g. a handler called directly, as the tests do).
    #
    # v0.24 review fix (finding 24): core's `proposals.propose` rewrites
    # `target, action = extends, "update"` when `extends` is given — the
    # STORED proposal describes `extends`, not the caller's own `target`.
    # `observed.target_shown` must answer "was the concept this proposal
    # actually targets shown", so it has to be computed against that same
    # effective target, after the same rewrite, or an agent-controlled
    # `target`/`extends` pair could flip an "attested by adapter process"
    # field to whatever the agent wants.
    effective_target = extends if extends is not None else target
    observed = session.observed(effective_target) if session is not None else None
    try:
        path = proposals.propose(t.bundle, meta, body, target=target,
                                 action=action, note=note, actor=actor,
                                 evidence=evidence, extends=extends, reopen=reopen,
                                 distinct_from=distinct_from, new_id=new_id,
                                 supersedes=supersedes, reverts=reverts,
                                 flag_type=flag_type, query=query, patch=patch,
                                 observed=observed)
    except ValueError as e:
        msg = str(e)
        m = _CODED.match(msg)
        if m is None:
            raise
        return {"error": m.group(1), "message": msg,
                "way_out": msg.rsplit(" — ", 1)[1] if " — " in msg else ""}
    c = t.bundle.get(t.bundle.concept_id(path))
    env = c.meta.get("proposal") or {}
    near = env.get("near") or []
    dist = env.get("distinct_from") or {}
    out = {"proposal": t.bundle.concept_id(path), "persisted": True,
          "path": path.relative_to(t.bundle.root).as_posix()}
    if near:
        out["near"] = near
    if dist:
        out["distinct_from"] = dist
    ev_state = proposals.evidence_state(t.bundle, c.meta)
    if ev_state:
        out["evidence_state"] = ev_state
    if env.get("sources_state"):
        out["sources_state"] = env["sources_state"]
    warn = proposals.near_warning(near, dist)
    if warn:
        out["warnings"] = [warn]
    # v0.24 (b): only for a real create (an --extends turns one into an
    # update before this function ever sees "create") — the MCP result adds
    # one guidance sentence the CLI's JSON output does not.
    if env.get("action") == "create":
        out["nearest"] = proposals.nearest(t.bundle, meta)
        if out["nearest"]:
            out["nearest_guidance"] = ("if one of these is the same thing, "
                                       "re-file with extends=<id>")
    return out


def h_fresh(t: Target, ids: dict) -> dict:
    """v0.24 (c): read-only per-id freshness check — see
    `okfy.proposals.fresh` for the five states. Single bundle only (a
    workspace has no one root to resolve `id` against)."""
    if t.is_workspace:
        raise ValueError("fresh targets a single bundle; point the server at "
                         "a member bundle path")
    return proposals.fresh(t.bundle, ids)
