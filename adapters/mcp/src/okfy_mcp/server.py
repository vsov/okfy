"""FastMCP stdio server: binds one Bundle/Workspace (path at launch) to five
tools. Protocol surface lives here; logic in handlers.py (ADR-0012)."""
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from okfy_mcp import handlers
from okfy_mcp.resolve import Target


def build_server(path: Path) -> FastMCP:
    target = Target(path)                       # validates now, fails fast
    mcp = FastMCP("okfy")

    @mcp.tool()
    def okfy_query(text: str, type: str | None = None, tag: str | None = None,
                   n: int = 10, expand: bool = True,
                   include_stale: bool = True) -> dict:
        """Search this OKFy bundle/workspace. Returns ranked snippets
        (id, type, title, description, score) plus expanded_query + notes.
        On a workspace, results are federated: grouped by role
        (knowledge/constraints) with constraint concepts auto-pulled.
        expand (default true) applies deterministic lexicon query expansion;
        pass false for the raw query. include_stale (default true) keeps
        owner-flagged stale concepts visible but marked. Both flags apply in
        bundle mode; a workspace federates and always expands per-member +
        marks stale, so it ignores both flags gracefully. Translate the user's
        phrasing into the bundle's vocabulary (check okfy_overview) first."""
        return handlers.h_query(target, text, type_=type, tag=tag, n=n,
                                expand=expand, include_stale=include_stale)

    @mcp.tool()
    def okfy_show(concept_id: str, max_chars: int = 20000,
                  section: str | None = None) -> dict:
        """Fetch one full concept by id. On a workspace use a namespaced id
        like 'member:glossary/gamma'. section (a '## <heading>' text, matched
        case-insensitively) returns only that block — use it to drill in
        instead of pulling the whole concept. Content longer than max_chars
        (default 20000) is truncated with a marker and the response carries
        truncated=true."""
        return handlers.h_show(target, concept_id, max_chars=max_chars,
                               section=section)

    @mcp.tool()
    def okfy_links(concept_id: str) -> dict:
        """Outgoing links and backlinks for a concept (single bundle only)."""
        return handlers.h_links(target, concept_id)

    @mcp.tool()
    def okfy_overview(type: str | None = None, max_items: int = 50,
                      max_chars: int = 20000) -> dict:
        """The bundle's index (or the workspace's member list). Read this first
        for progressive disclosure — never bulk-read concepts. With no type,
        returns the index text (capped at max_chars, default 20000, with a
        truncation marker + truncated=true when cut). With type (bundle only;
        a workspace raises), returns a structured listing
        {concepts:[{id,type,title,description}], total} capped at max_items
        (default 50) while total reports the full count for that type."""
        return handlers.h_overview(target, type_=type, max_items=max_items,
                                   max_chars=max_chars)

    @mcp.tool()
    def okfy_propose(target_id: str, action: str, note: str,
                     content: str | None = None) -> dict:
        """Suggest a change. Writes ONLY to proposals/ (never final content);
        a human reviews it via `okfy review`. action is create|update|delete;
        content is a full concept .md (frontmatter + body), omitted for delete.
        target_id is the concept id the change concerns."""
        return handlers.h_propose(target, target=target_id, action=action,
                                  note=note, content=content)

    return mcp


def serve(path: Path) -> None:
    build_server(path).run(transport="stdio")
