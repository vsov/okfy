"""The memory ledger: every propose, accept and reject, in the order they happened.

`meta/memory.jsonl` is append-only, one JSON object per line, keys sorted. It is
what lets the proposal path remember a decision after the proposal file is gone:
a rejected text's hash stays here, so the same text cannot quietly re-enter, and
release_check can say how much memory was accepted since the bundle was last
packaged.

It is not a database and is never rewritten. A line this module cannot read is
REPORTED, never skipped: the rejected-content gate reads this file, and a gate
that silently drops a line it cannot parse fails open on exactly the line that
might have held the rejection.
"""
import json
import os

from okfy.actor import utc_now

MEMORY_FILE = "meta/memory.jsonl"
EVENTS = ("propose", "accept", "reject")
E_MEMORY_LINE = "E_MEMORY_LINE"
_REQUIRED = ("event", "at", "actor", "proposal", "target", "action", "content_sha256")


def record(bundle, event: str, *, actor: str, proposal: str, target: str | None,
           action: str, content_sha256: str, base_sha256: str | None = None,
           reason: str | None = None, reopen: str | None = None) -> dict:
    if event not in EVENTS:
        raise ValueError(f"unknown memory event {event!r} (use: {list(EVENTS)})")
    row = {"event": event, "at": utc_now(), "actor": actor, "proposal": proposal,
           "target": target, "action": action, "content_sha256": content_sha256}
    for k, v in (("base_sha256", base_sha256), ("reason", reason), ("reopen", reopen)):
        if v is not None:
            row[k] = v
    path = bundle.root / MEMORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    return row


def events(bundle) -> tuple[list[dict], list[str]]:
    """(readable events in file order, problems). A problem names its line."""
    path = bundle.root / MEMORY_FILE
    if not path.is_file():
        return [], []
    out, problems = [], []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        where = f"{E_MEMORY_LINE}: {MEMORY_FILE} line {n}"
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as e:
            problems.append(f"{where}: not JSON ({e.msg})")
            continue
        if not isinstance(row, dict):
            problems.append(f"{where}: not an object")
            continue
        missing = [k for k in _REQUIRED if k not in row]
        if missing:
            problems.append(f"{where}: missing {missing}")
            continue
        if row["event"] not in EVENTS:
            problems.append(f"{where}: unknown event {row['event']!r}")
            continue
        out.append(row)
    return out, problems


def rejected_hashes(bundle) -> dict[str, dict]:
    """content_sha256 -> the reject event still standing against it. A later
    propose of the same text that carried `reopen` lifts it; rejecting that
    reopened text again puts it back."""
    standing: dict[str, dict] = {}
    for row in events(bundle)[0]:
        if row["action"] == "delete":
            continue
        if row["event"] == "reject":
            standing[row["content_sha256"]] = row
        elif row["event"] == "propose" and row.get("reopen"):
            standing.pop(row["content_sha256"], None)
    return standing


def accepted_since(bundle, when: str | None) -> int:
    """Accept events strictly after `when` (an RFC3339 UTC string); all of them
    when `when` is None."""
    return sum(1 for row in events(bundle)[0]
               if row["event"] == "accept" and (when is None or str(row["at"]) > when))
