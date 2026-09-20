"""v0.24 (d): an OPT-IN, append-only JSONL journal of this server's tool
calls — real sessions, not the eval set. Off by default; `--journal <file>`
(or `OKFY_MCP_JOURNAL`) turns it on. No query TEXT is ever written unless
`--journal-text` is ALSO given.

A journal write failing must never fail the tool call it is describing — the
journal is an observability side-channel, not a gate.
"""
import datetime
import json
import os
import random
import sys
import time
from pathlib import Path

SCHEMA = "okfy-mcp-journal@1"
E_JOURNAL_INSIDE_BUNDLE = "E_JOURNAL_INSIDE_BUNDLE"


def _new_session_id() -> str:
    # pid + start time, per the spec's "random-free id" — no os.urandom, just
    # what the process already knows about itself plus a small tiebreak for
    # two servers started in the same second.
    return f"{os.getpid()}-{int(time.time())}-{random.randrange(16**6):06x}"


def resolve_journal_path(raw: Path, *roots: Path) -> Path:
    """Resolve `raw` and refuse (`E_JOURNAL_INSIDE_BUNDLE`) if it sits inside
    any of `roots` (the served bundle root, every member bundle root in
    workspace mode, and the workspace root itself) — a journal inside the
    bundle would be committed and billed like any other file there.

    Compared by filesystem IDENTITY, not string spelling: on a
    case-insensitive filesystem (macOS/APFS, this project's default),
    `Path.resolve()` does not canonicalise the case of a not-yet-existing
    leaf's parents as typed, so `.../BUNDLE/journal.jsonl` would otherwise
    slip past a `root in p.parents` string check even though `BUNDLE` and
    the served `bundle` are the same directory on disk."""
    p = Path(raw).expanduser().resolve()
    resolved_roots = [Path(root).resolve() for root in roots]

    def _refuse(root: Path) -> None:
        raise ValueError(
            f"{E_JOURNAL_INSIDE_BUNDLE}: journal path {p} is inside {root} "
            "— a journal inside the bundle would be committed and billed; "
            "put it outside, e.g. ~/.okfy/journal/<name>.jsonl")

    # Cheap exact/string-prefix check first — catches the common case (and
    # works even when nothing on `p`'s path exists yet on disk).
    for root in resolved_roots:
        if p == root or root in p.parents:
            _refuse(root)

    # Identity check: walk p's ancestors starting from the first one that
    # actually EXISTS (p's own leaf — the journal file — usually does not
    # exist yet), and compare each to every root with os.path.samefile,
    # which resolves by inode rather than by spelling. On a case-sensitive
    # filesystem a case-swapped ancestor simply does not exist, so this
    # never fires there; on a case-insensitive one it catches exactly the
    # case-mismatch the string check above misses.
    ancestors = [p, *p.parents]
    existing = next((a for a in ancestors if a.exists()), None)
    if existing is not None:
        for ancestor in ancestors[ancestors.index(existing):]:
            for root in resolved_roots:
                if root.exists() and os.path.samefile(ancestor, root):
                    _refuse(root)
    return p


class Journal:
    """One instance per server process. `write()` never raises — an OSError
    is caught, counted, and reported once on stderr so a bad path does not
    spam every subsequent call."""

    def __init__(self, path: Path, text: bool = False):
        self.path = Path(path)
        self.text = text
        self.session = _new_session_id()
        self.failures = 0
        self._warned = False

    def write(self, tool: str, **fields) -> None:
        # The single point that enforces "no query text unless --journal-text"
        # — callers may pass `query=` unconditionally; this is where it is
        # actually dropped, so there is exactly one place that decision lives.
        if not self.text:
            fields.pop("query", None)
        row = {"schema": SCHEMA,
              "at": datetime.datetime.now(datetime.timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
              "session": self.session, "tool": tool}
        for k, v in fields.items():
            if v is not None:
                row[k] = v
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            self.failures += 1
            if not self._warned:
                print(f"okfy-mcp: journal write failed ({e}); further "
                     f"failures at {self.path} are counted, not printed",
                     file=sys.stderr)
                self._warned = True
