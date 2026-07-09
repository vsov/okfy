import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="okfy-mcp")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the stdio MCP server for a bundle/workspace")
    s.add_argument("path", type=Path)
    c = sub.add_parser("config", help="print an MCP client config snippet")
    c.add_argument("path", type=Path)
    c.add_argument("--client", choices=["claude-code", "claude-desktop", "cursor"],
                   default="claude-code")
    c.add_argument("--name", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        from okfy_mcp.server import serve
        serve(a.path)
        return 0
    if a.cmd == "config":
        from okfy_mcp.config import snippet
        print(snippet(a.path, client=a.client, name=a.name))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
