"""Print a client MCP-config snippet the user pastes into their own client
config. OKFy never edits client config files (formats drift; silent breakage) —
it only hands over a correct block (ADR-0012)."""
import json
from pathlib import Path

from okfy_mcp.resolve import Target

# All three clients use the same mcpServers shape; kept as one map for when
# they diverge.
_SHAPES = {"claude-code", "claude-desktop", "cursor"}


def snippet(path: Path, client: str = "claude-code", name: str | None = None) -> str:
    if client not in _SHAPES:
        raise ValueError(f"unknown client {client!r} (use: {sorted(_SHAPES)})")
    t = Target(path)                     # validates it is a real bundle/workspace
    server_name = name or t.path.name
    doc = {"mcpServers": {server_name: {
        "command": "okfy-mcp", "args": ["serve", str(t.path)]}}}
    return json.dumps(doc, indent=2, ensure_ascii=False)
