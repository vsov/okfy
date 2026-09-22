"""Pure MCP tool handlers: import okfy core in-process, return JSON-able dicts.
No MCP types here — server.py owns the protocol surface, this owns the logic."""
import hashlib
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

# v0.25 (R2): `content` missing for an action that requires it — this used
# to be a bare `ValueError` (no `E_` prefix, so it never matched `_CODED`
# and reached the caller as a raw ToolError instead of the adapter's
# promised {error, message, way_out}). See h_propose below.
E_PROPOSAL_CONTENT_REQUIRED = "E_PROPOSAL_CONTENT_REQUIRED"

# v0.25 (R2): the remaining bare-`ValueError` guards reachable through a
# registered MCP tool (server.py's docstring promises {error, message,
# way_out} for every refusal — a bare ValueError becomes a raw ToolError
# instead). Each guard below keeps its own code and wording rather than
# sharing one, matching the existing E_PROPOSAL_TRUNCATED/E_FRONTMATTER
# precedent in h_propose.
E_SHOW_ID_REQUIRED = "E_SHOW_ID_REQUIRED"
E_LINKS_WORKSPACE = "E_LINKS_WORKSPACE"
E_OVERVIEW_TYPE_WORKSPACE = "E_OVERVIEW_TYPE_WORKSPACE"
E_PROPOSE_WORKSPACE = "E_PROPOSE_WORKSPACE"
E_PROPOSAL_PATCH_CONTENT = "E_PROPOSAL_PATCH_CONTENT"
E_FRESH_WORKSPACE = "E_FRESH_WORKSPACE"

# v0.27 (A9 follow-up): a refusal reaches a handler's caller ONE OF TWO WAYS
# — it RAISES (a ValueError/KeyError propagating out of a handler function),
# or it RETURNS a dict directly (a handler-level early-exit check, like the
# workspace guards below, that never touches core at all). `safe_call`
# structurally catches every raised one, no matter which core module raises
# it — but a RETURNED dict never reaches `safe_call`'s `except`, so nothing
# stops one from being malshaped. `_h_show_many`'s too-many-ids guard was
# exactly that: {error: "too_many_concept_ids", limit, message} — no
# `way_out`, and a code neither `E_`-prefixed nor registered. It routed
# around the boundary entirely, silently, because returning is not raising.
#
# There is no single choke point for a RETURN the way `safe_call` is one for
# a RAISE — a dict is just data, nothing forces it through one function. The
# guarantee here is instead an exhaustive, checked enumeration: handlers.py
# is the ONLY place in the adapter that constructs a refusal dict (this
# module's own docstring: "Pure MCP tool handlers ... No MCP types here"),
# so `grep -n '"error"' adapters/mcp/src/okfy_mcp/handlers.py` finds the
# complete, closed set of them. Every one of those sites (v0.27) has been
# checked to carry {error, message, way_out} with an `E_`-prefixed,
# registered code — `too_many_concept_ids` was the one exception, now fixed
# (E_SHOW_TOO_MANY_IDS). Unlike the raise side, this fact is NOT structural —
# a future returned-dict refusal added to handlers.py without the same shape
# would not be caught by anything short of re-running this grep. That
# distinction — one guarantee is boundary-structural, the other is a
# verified-but-re-checkable enumeration over a single, small, closed file —
# is deliberately not blurred into one blanket claim.
E_SHOW_TOO_MANY_IDS = "E_SHOW_TOO_MANY_IDS"

# v0.27 (A9): a core refusal — a ValueError (most of core) or KeyError
# (`okfy.query.show`/`links`, `okfy.federate.fed_show` — "concept not
# found") — that carries no leading `E_<NAME>: `/`W_<NAME>: ` prefix of its
# own. Rather than hand-coding every such guard one at a time (the v0.26 R2
# fix's mistake: it swept "six neighbouring bare guards" and the changelog
# then claimed the whole class was fixed — precisely the over-claim the
# v0.26 audit called out, because "all of them" is not a fact you can
# establish by finding some of them), `safe_call` below wraps EVERY
# registered MCP tool's handler dispatch (see server.py), so an uncoded
# refusal reaches the caller as this documented envelope no matter which
# core module raised it, or how many more of them exist that nobody has
# gone looking for. `message` always carries the refusal's own original
# text verbatim, so nothing is lost — only the shape changes.
#
# Only ValueError/KeyError are caught by `safe_call` — every OTHER
# exception type (TypeError, AttributeError, RuntimeError, ...) is a
# genuine bug, not something the caller can fix and retry, and is left to
# propagate as the raw ToolError it already is. Dressing an unexpected
# defect up as a polite, actionable refusal would hide it behind the exact
# same reassuring shape a caller is told to just act on — a worse defect
# than the uncoded-refusal problem this fixes.
#
# `safe_call` is used only at the server.py tool-dispatch boundary. Handler
# functions called directly (as many tests do) keep their own existing
# raise/return contracts unchanged — see h_propose's own `_coded_envelope`
# use below, and tests that call handlers.* directly and assert an uncoded
# ValueError/KeyError still raises rather than returning data:
# test_uncoded_errors_still_raise, test_supersede_without_new_id_raises,
# test_show_missing_raises.
E_MCP_REFUSAL = "E_MCP_REFUSAL"

_CODED = re.compile(r"^([EW]_[A-Z_]+): ")


def _coded_envelope(msg: str) -> dict | None:
    """`msg`'s {error, message, way_out} envelope if it carries a leading
    `E_<NAME>: `/`W_<NAME>: ` prefix (core's own convention for a coded
    refusal) — `way_out` is the message's own ' — ' tail, the same way
    core writes a coded ValueError. None if `msg` carries no such prefix —
    the caller decides what to do with an uncoded one."""
    m = _CODED.match(msg)
    if m is None:
        return None
    return {"error": m.group(1), "message": msg,
           "way_out": msg.rsplit(" — ", 1)[1] if " — " in msg else ""}


def safe_call(fn, *args, **kwargs) -> dict:
    """Boundary: call `fn` (a handler function) and turn an escaping
    ValueError/KeyError into the documented refusal envelope — a coded one
    keeps its own code (`_coded_envelope`), an uncoded one gets the generic
    `E_MCP_REFUSAL` so the original message is preserved rather than lost.
    Every registered MCP tool (server.py) routes its handler call through
    this, structurally guaranteeing no RAISED refusal escapes as a raw
    ToolError, regardless of which core module raises it.

    This does NOT, by itself, cover a refusal a handler RETURNS as a dict
    instead of raising (e.g. a workspace-target guard) — those never reach
    this `except`. Every returned refusal in this adapter is separately
    verified, by enumeration over the only file that constructs one
    (handlers.py — see the E_SHOW_TOO_MANY_IDS comment above for how and
    why that enumeration is closed, and why it is a weaker guarantee than
    this function's)."""
    try:
        return fn(*args, **kwargs)
    except (ValueError, KeyError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        return _coded_envelope(msg) or {
            "error": E_MCP_REFUSAL, "message": f"{E_MCP_REFUSAL}: {msg}",
            "way_out": "read the message for the underlying refusal and "
                      "what to change, then retry"}


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


def surfaced_ids(result: dict) -> list[str]:
    """Every concept id/ref an `h_query` result actually surfaced to the
    agent, deduplicated once. Bundle mode: the flat `results` list. Workspace
    mode: the federated `knowledge`/`constraints`/`personal` groups PLUS
    `pulled` — the auto-pulled `constrains` targets `federate.federated_query`
    returns as a fourth group (core/src/okfy/federate.py) — a concept the
    agent genuinely saw but that two separate enumerations here used to drop
    (F14). The one function both `h_query`'s session record and the server's
    usage journal call, so `n_results` and `top_ids` always name the same
    set."""
    if result.get("mode") == "bundle":
        ids = [h["id"] for h in result.get("results", [])]
    else:
        ids = [h.get("ref") or h.get("id")
               for group in ("knowledge", "constraints", "personal", "pulled")
               for h in result.get(group, []) if isinstance(h, dict)]
    seen: list[str] = []
    for i in ids:
        if i not in seen:
            seen.append(i)
    return seen


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
    result["notice"] = render.NOTICE
    if session is not None:
        session.record_query(text, surfaced_ids(result))
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


def _read_concept(path) -> tuple[bytes, str, str]:
    """Read a concept file's bytes exactly ONCE — v0.25 (F06): the digest
    and the text handed back must be two derivations of that SAME capture,
    never a hash of one read paired with the text of a separate, later
    read (an edit landing in between would then hand back content under a
    digest that names different bytes than what was actually returned; a
    later okfy_fresh call would wrongly answer "unchanged" for content the
    agent never received).

    Returns `(raw_bytes, sha256_of_raw_bytes, normalized_text)`. The digest
    is always over the RAW bytes on disk. The text is decoded and then
    newline-normalized (CRLF and lone CR -> LF) — `bytes.decode()` alone
    does not perform the universal-newline translation `Path.read_text()`
    used to do before v0.25 moved every caller here to a raw-bytes read
    (F06); `frontmatter.parse`'s exact `text.startswith("---\\n")` checks
    need that normalization, since a CRLF-line-ended file is a perfectly
    valid concept, just spelled with `\\r\\n`. Only the TEXT view is
    normalized — the digest, and what's on disk, are untouched."""
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return raw, sha256, text


def _h_show_one(t: Target, concept_id: str, max_chars: int,
                section: str | None, session: Session | None) -> dict:
    c = _resolve_concept(t, concept_id)
    _, file_sha256, content = _read_concept(c.path)
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
        return {"error": E_SHOW_TOO_MANY_IDS, "limit": _MAX_SHOW_IDS,
                "message": f"{E_SHOW_TOO_MANY_IDS}: at most {_MAX_SHOW_IDS} "
                          f"concept_ids per call, got {len(concept_ids)}",
                "way_out": f"pass at most {_MAX_SHOW_IDS} concept_ids per call"}
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

    # Body (frontmatter stripped) is what gets budgeted — repeating each
    # concept's YAML frontmatter inside a shared token budget would waste
    # it on fixed overhead already visible via the concept's own
    # id/type/title (okfy_overview) and via `neighbours` below; `sha256`
    # stays the FILE's bytes regardless, same as single-id show. See
    # `_read_concept` for why this is ONE read per concept (F06) and why
    # its text view is newline-normalized before frontmatter.parse (A6).
    bodies = []
    shas: dict[str, str] = {}
    for cid in ok_ids:
        _, shas[cid], text = _read_concept(resolved[cid].path)
        _, body = frontmatter.parse(text)
        bodies.append(body)
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
    elif concept_id is None:
        out = {"error": E_SHOW_ID_REQUIRED,
              "message": f"{E_SHOW_ID_REQUIRED}: either concept_id or "
                        "concept_ids is required",
              "way_out": "pass concept_id (one id) or concept_ids "
                        "(a list, max 10)"}
    else:
        out = _h_show_one(t, concept_id, max_chars, section, session)
    out["notice"] = render.NOTICE
    return out


def h_links(t: Target, concept_id: str) -> dict:
    if t.is_workspace:
        return {"error": E_LINKS_WORKSPACE,
                "message": f"{E_LINKS_WORKSPACE}: links works on a single "
                          "bundle; query a member bundle path directly for "
                          "its link graph",
                "way_out": "point the server at the member bundle's own "
                          "path, not the workspace"}
    return q.links(t.bundle, concept_id)


def h_overview(t: Target, type_: str | None = None, max_items: int = 50,
               max_chars: int = 20000) -> dict:
    if type_ is not None:
        if t.is_workspace:
            return {"error": E_OVERVIEW_TYPE_WORKSPACE,
                    "message": f"{E_OVERVIEW_TYPE_WORKSPACE}: type listing "
                              "is a single-bundle view; point at a member "
                              "bundle path for its concept list",
                    "way_out": "point the server at the member bundle's "
                              "own path, not the workspace, or call "
                              "okfy_overview with no type for the "
                              "workspace's member list"}
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
    `warnings` (W_PROPOSAL_NEAR text, and W_DISTINCT_ALIAS_OVERLAP text when a
    `--distinct-from`-style claim still overlaps enough that retrieval would
    conflate the two) — none of these block anything.
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
        return {"error": E_PROPOSE_WORKSPACE,
                "message": f"{E_PROPOSE_WORKSPACE}: propose targets a "
                          "single member bundle; point the server at that "
                          "bundle's path, not the workspace",
                "way_out": "point the server at the target member bundle's "
                          "own path, not the workspace"}
    if patch is not None and content:
        return {"error": E_PROPOSAL_PATCH_CONTENT,
                "message": f"{E_PROPOSAL_PATCH_CONTENT}: patch and content "
                          "are mutually exclusive — a patch is applied to "
                          "the target's CURRENT body, content supplies the "
                          "whole new body",
                "way_out": "pass either patch (hunks against the current "
                          "body) or content (the whole new body), not both"}
    if action in ("delete", "flag", "gap") or patch is not None:
        meta, body = {}, ""
    else:
        if not content:
            return {"error": E_PROPOSAL_CONTENT_REQUIRED,
                    "message": f"{E_PROPOSAL_CONTENT_REQUIRED}: content (a "
                              "full concept .md) is required unless "
                              "action=delete|flag|gap or patch is given",
                    "way_out": ("pass content (frontmatter + body), or use "
                               "action=delete|flag|gap, or pass patch "
                               "instead of content")}
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
        env = _coded_envelope(str(e))
        if env is None:
            raise
        return env
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
    warnings = []
    warn = proposals.near_warning(near, dist)
    if warn:
        warnings.append(warn)
    # v0.25: W_DISTINCT_ALIAS_OVERLAP — computed from the CALL's own
    # (meta, distinct_from), not from `dist` (the persisted echo): see
    # okfy.proposals.distinct_overlap_warnings' docstring.
    warnings += proposals.distinct_overlap_warnings(t.bundle, meta, distinct_from)
    if warnings:
        out["warnings"] = warnings
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
        return {"error": E_FRESH_WORKSPACE,
                "message": f"{E_FRESH_WORKSPACE}: fresh targets a single "
                          "bundle; point the server at a member bundle path",
                "way_out": "point the server at the member bundle's own "
                          "path, not the workspace"}
    return proposals.fresh(t.bundle, ids)
