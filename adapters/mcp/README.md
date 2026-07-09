# okfy-mcp — MCP stdio adapter for OKFy

An isolated adapter that exposes one OKF Bundle (or a federation Workspace) to
any [MCP](https://modelcontextprotocol.io) client over **stdio**. It imports the
`okfy` core in-process and wraps five handlers as MCP tools: four read tools plus
one proposals-only write tool — so a remote agent can both *consume* a bundle and
*enrich* it, with the human review gate left intact.

This is a **separate package**. The MCP SDK lives here, never in core: the OKFy
core stays PyYAML-only and portable, and the daemon carries the SDK weight in
isolation. An adapter is exactly the place a dependency is allowed to sit,
precisely so the thing everyone imports does not have to carry it.

## Install

Python 3.11+, [uv](https://docs.astral.sh/uv/). Install the tool (it pulls in the
`mcp` SDK and the `okfy` core path dependency):

```bash
uv tool install ./adapters/mcp        # installs the `okfy-mcp` command
```

## Commands

`okfy-mcp` has two subcommands:

```bash
okfy-mcp serve  <path>                          # run the stdio MCP server
okfy-mcp config <path> --client claude-code     # print a client config snippet
```

- **`serve <path>`** binds the server to exactly one bundle or workspace — the
  path is a launch argument and is validated on startup (fail-fast). Clients
  launch this for you; you rarely run it by hand.
- **`config <path> [--client ...] [--name ...]`** prints a ready-to-paste JSON
  block. `--client` is one of `claude-code` (default), `claude-desktop`, or
  `cursor`; `--name` overrides the server name (defaults to the path's basename).

## Setup: we print a snippet, you paste it

**OKFy does not edit your client's config file.** Per-client config formats drift
and would fail silently, so the adapter only hands you a correct block — pasting
it is your explicit act:

```bash
okfy-mcp config ~/bundles/rayforce-okf --client claude-code
```

prints, e.g.:

```json
{
  "mcpServers": {
    "rayforce-okf": {
      "command": "okfy-mcp",
      "args": ["serve", "/Users/you/bundles/rayforce-okf"]
    }
  }
}
```

Paste that into your project's `.mcp.json` (Claude Code), restart the client, and
the five `okfy_*` tools appear.

## Tools

| Tool | Kind | What it does |
| --- | --- | --- |
| `okfy_query(text, type?, tag?, n=10, expand=true, include_stale=true)` | read | Ranked BM25 search → snippets (id, type, title, description, score) + `expanded_query` + `notes`. `expand` applies deterministic lexicon query expansion (`false` for the raw query); `include_stale` keeps owner-flagged stale concepts visible but marked (bundle mode; a workspace ignores it gracefully). On a workspace, results are federated: role-grouped with binding constraints auto-pulled. |
| `okfy_show(concept_id, max_chars=20000, section?)` | read | Fetch one full concept by id. On a workspace, use a namespaced id like `member:glossary/gamma`. `section` (a `## <heading>`, matched case-insensitively) returns only that block. Content over `max_chars` is truncated with a marker and the response carries `truncated=true`. |
| `okfy_links(concept_id)` | read | Outgoing links and backlinks for a concept (single bundle only). |
| `okfy_overview(type?, max_items=50, max_chars=20000)` | read | With no `type`: the bundle's `index.md` (or the workspace's member list), capped at `max_chars` (marker + `truncated=true` when cut). With `type` (bundle only; a workspace raises): a structured listing `{concepts:[{id,type,title,description}], total}` capped at `max_items`, `total` giving the full count. Read this first — progressive disclosure, never bulk-read concepts. |
| `okfy_propose(target_id, action, note, content?)` | write | Suggest a change. Writes **only** to `proposals/` (never final content); a human reviews it via `okfy review`. `action` is `create`\|`update`\|`delete`; `content` is a full concept `.md` (frontmatter + body), omitted for delete. |

The write tool is deliberately proposals-only: the v0.4a write-gate guarantees a
tool call can never touch a final concept, so enrichment over a transport keeps
the same review gate as a local agent. There is no `okfy_validate` tool —
validation is a maintainer operation, not a consumption one.

## One server = one bundle. Point it at a workspace to federate.

Each server instance serves exactly one access boundary (private-by-default). To
expose **several bundles together**, point `serve`/`config` at a Workspace
path (see the federation chapter of the User Guide) instead of a single
bundle — the adapter auto-detects it and federation happens with zero
MCP-specific code: `okfy_query` answers across members with constraints pulled in,
and `okfy_show` takes namespaced ids. `okfy_links` and `okfy_propose` target a
single bundle — point the server at that member bundle's path for those.

Transport is **stdio only** in this cut; SSE/HTTP is deferred.
