"""Resolve a path to an OKFy Target: a single Bundle or a federation Workspace.
Auto-detect keeps one MCP verb serving both worlds (ADR-0010/0012)."""
from pathlib import Path

from okfy.bundle import Bundle
from okfy.workspace import Workspace, is_workspace


class Target:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        if not self.path.is_dir():
            raise FileNotFoundError(f"path not found: {self.path}")
        if is_workspace(self.path):
            self.is_workspace = True
            self.workspace = Workspace.load(self.path)
            self.bundle = None
        elif (self.path / "meta" / "purpose.md").is_file():
            self.is_workspace = False
            self.bundle = Bundle(self.path)
            self.workspace = None
        else:
            raise ValueError(f"not an OKFy bundle or workspace: {self.path}")
