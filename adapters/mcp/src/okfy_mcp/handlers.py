"""Pure MCP tool handlers: import okfy core in-process, return JSON-able dicts.
No MCP types here — server.py owns the protocol surface, this owns the logic."""
import inspect
import re

from okfy import federate, frontmatter, proposals, query as q
from okfy.index import load_index
from okfy_mcp.resolve import Target

_FED_PARAMS = inspect.signature(federate.federated_query).parameters


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


def h_query(t: Target, text: str, type_: str | None = None,
            tag: str | None = None, n: int = 10, expand: bool = True,
            include_stale: bool = True) -> dict:
    if t.is_workspace:
        kw = {"n": n}
        if "expand" in _FED_PARAMS:
            kw["expand"] = expand
        if "include_stale" in _FED_PARAMS:
            kw["include_stale"] = include_stale
        out = federate.federated_query(t.workspace, text, **kw)
        return {"mode": "workspace", **out}
    out = q.query(t.bundle, text, type_=type_, tag=tag, n=n,
                  expand=expand, include_stale=include_stale)
    return {"mode": "bundle", **out}


def h_show(t: Target, concept_id: str, max_chars: int = 20000,
           section: str | None = None) -> dict:
    if t.is_workspace:
        c = federate.fed_show(t.workspace, concept_id)
    else:
        c = q.show(t.bundle, concept_id)
    content = c.path.read_text(encoding="utf-8")
    if section is not None:
        content = _section(content, section)
    content, truncated = _cap(content, max_chars)
    out = {"id": concept_id, "content": content}
    if truncated:
        out["truncated"] = True
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
              extends: str | None = None, reopen: str | None = None) -> dict:
    """File a proposal. A refusal the core names with a code comes back as data
    — `{error, message, way_out}` — so an agent can act on it instead of reading
    a traceback; `persisted: true` appears ONLY when a proposal file was written,
    and is the only thing an agent may report as remembered."""
    if t.is_workspace:
        raise ValueError("propose targets a single member bundle; point the "
                         "server at that bundle's path, not the workspace")
    if action == "delete":
        meta, body = {}, ""
    else:
        if not content:
            raise ValueError("content (a full concept .md) is required unless "
                             "action=delete")
        meta, body = frontmatter.parse(content)
    try:
        path = proposals.propose(t.bundle, meta, body, target=target,
                                 action=action, note=note, actor=actor,
                                 evidence=evidence, extends=extends, reopen=reopen)
    except ValueError as e:
        msg = str(e)
        m = _CODED.match(msg)
        if m is None:
            raise
        return {"error": m.group(1), "message": msg,
                "way_out": msg.rsplit(" — ", 1)[1] if " — " in msg else ""}
    return {"proposal": t.bundle.concept_id(path), "persisted": True,
            "path": path.relative_to(t.bundle.root).as_posix()}
