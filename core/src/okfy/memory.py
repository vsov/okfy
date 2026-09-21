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

NO TIME WORDS IN THE CORE (v0.25, preventive — phase 8 measured that OKFy has
no world-time field at all today, so nothing here is being fixed, only kept
from ever being needed): the core never parses a relative date expression —
"yesterday", "last quarter", "since the merger" — and never will. Naming a
date is the model's job; the core's only job is to check the date it was
given is real and refuse it otherwise. `parse_window_bound` below is exactly
the shape this permits: it accepts a bare ISO date or a full RFC3339 UTC
timestamp, validates it with `datetime.strptime` (which also catches a
calendar date that does not exist, e.g. `2026-02-30`), and refuses anything
else by naming both accepted shapes — it never guesses what "next week" or
"Q3" was supposed to mean. `core/tests/test_no_time_words.py` backs this
with a tripwire: a deliberately chosen list of relative-date vocabulary must
never appear in core module logic outside test files and message/docstring
text explaining the rule itself (this paragraph included).
"""
import datetime
import json
import os

from okfy.actor import utc_now

MEMORY_FILE = "meta/memory.jsonl"
# v0.24: `superseded` records a proposal LANE eviction (okfy propose
# --supersedes <old-proposal-id>) — the old proposal file is gone the same way
# a reject removes it, but the row names what replaced it (`by_proposal`)
# instead of a reason, so the two are distinguishable in the ledger.
EVENTS = ("propose", "accept", "reject", "superseded")
E_MEMORY_LINE = "E_MEMORY_LINE"
# v0.25: `okfy changes` refuses a malformed --since/--until, or --until
# earlier than --since, with this code (see parse_window_bound and
# okfy.commands.changes.cmd_changes).
E_CHANGES_WINDOW = "E_CHANGES_WINDOW"
_REQUIRED = ("event", "at", "actor", "proposal", "target", "action", "content_sha256")
# The two shapes `okfy changes --since/--until` accepts: a bare date (read as
# midnight UTC) or a full RFC3339 UTC timestamp — the exact shape `utc_now`
# writes. Order matters: a bare date must not partially match the long format.
_WINDOW_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ")


def record(bundle, event: str, *, actor: str, proposal: str | None, target: str | None,
           action: str, content_sha256: str, base_sha256: str | None = None,
           reason: str | None = None, reopen: str | None = None,
           match_sha256: str | None = None, new_id: str | None = None,
           by_proposal: str | None = None, origin: str | None = None,
           channel: str | None = None) -> dict:
    if event not in EVENTS:
        raise ValueError(f"unknown memory event {event!r} (use: {list(EVENTS)})")
    row = {"event": event, "at": utc_now(), "actor": actor, "proposal": proposal,
           "target": target, "action": action, "content_sha256": content_sha256}
    for k, v in (("base_sha256", base_sha256), ("reason", reason), ("reopen", reopen),
                 ("match_sha256", match_sha256), ("new_id", new_id),
                 ("by_proposal", by_proposal), ("origin", origin),
                 ("channel", channel)):
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


# `changes()` below takes an `events=` keyword filter, which as a local
# parameter would shadow the `events` function name inside its own body —
# this alias is what it calls instead, so the public `events(bundle)` name
# (read by many other modules and tests) stays exactly as it is.
_read_events = events


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


def rejected_match_hashes(bundle) -> dict[str, dict]:
    """match_sha256 -> the reject-or-delete event still standing against it.

    A tombstone that survives rewording: sibling to `rejected_hashes`, but
    keyed on the normalized-text fingerprint instead of the exact bytes, and
    fed by two kinds of standing row — a `reject` (the owner said no to this
    claim) and an accepted `delete` (the owner removed a concept that said
    this). Both are lifted the same way: a later `propose` that carries
    `reopen` and hashes to the same fingerprint pops the entry; rejecting or
    re-deleting that reopened text puts it back. Old rows carry no
    `match_sha256` and are silently invisible here — never refused, matching
    v0.23 behaviour exactly."""
    standing: dict[str, dict] = {}
    for row in events(bundle)[0]:
        mh = row.get("match_sha256")
        if not mh:
            continue
        if row["event"] == "reject":
            standing[mh] = row
        elif row["event"] == "accept" and row["action"] == "delete":
            standing[mh] = row
        elif row["event"] == "propose" and row.get("reopen"):
            standing.pop(mh, None)
    return standing


def parse_window_bound(value: str, *, flag: str) -> str:
    """Normalize a `--since`/`--until` value to the RFC3339 UTC shape every
    ledger `at` is written in (`okfy.actor.utc_now`): a bare date
    (`2026-01-31`, read as `T00:00:00Z`) or a full RFC3339 UTC timestamp
    (`2026-01-31T14:30:00Z`). `datetime.strptime` also rejects a
    calendar date that does not exist (e.g. `2026-02-30`), not just a
    wrong shape. Anything that matches neither shape is refused, naming
    both accepted shapes with an example and the way out."""
    v = str(value).strip()
    for fmt in _WINDOW_FORMATS:
        try:
            datetime.datetime.strptime(v, fmt)
        except ValueError:
            continue
        return v if fmt == "%Y-%m-%dT%H:%M:%SZ" else v + "T00:00:00Z"
    raise ValueError(
        f"{E_CHANGES_WINDOW}: {flag} {value!r} is not a recognized date — "
        "use a bare date (e.g. 2026-01-31, read as 00:00:00Z) or a full "
        "RFC3339 UTC timestamp (e.g. 2026-01-31T14:30:00Z)")


def _in_window(at: str, since: str | None, until: str | None, *,
               since_strict: bool = False) -> bool:
    """`at` (an RFC3339 UTC string) falls inside [since, until) — `since` is
    inclusive unless `since_strict` (what `accepted_since` has always meant
    by "strictly after"), `until` is always exclusive. Plain string
    comparison is safe: every `at` in this ledger is written by the single
    formatter in `okfy.actor.utc_now`, so lexicographic order is chronological
    order."""
    if since is not None:
        if since_strict:
            if not at > since:
                return False
        elif not at >= since:
            return False
    if until is not None and not at < until:
        return False
    return True


def changes(bundle, *, since: str | None = None, until: str | None = None,
           target: str | None = None, events: tuple[str, ...] | None = None,
           actions: tuple[str, ...] | None = None,
           actor: str | None = None) -> list[dict]:
    """Ledger rows, in file order, whose OWN `at` falls in the half-open
    window `[since, until)` — `since`/`until` must already be normalized
    RFC3339 UTC strings (see `parse_window_bound`), or `None` for no bound.
    `target`/`events`/`actions`/`actor` filter further and combine with the
    window, and with each other, as AND.

    `events` and `actions` filter two DIFFERENT fields a row carries: `events`
    restricts `row["event"]` (this module's `EVENTS`: propose/accept/reject/
    superseded — what kind of ledger entry it is), while `actions` restricts
    `row["action"]` (`okfy.proposals.ACTIONS`: create/update/delete/
    supersede/flag/gap — what the underlying proposal does); a row can be an
    `accept` event of a `delete` action, and the two names are not
    interchangeable.

    Every event is judged by ITS OWN `at` only — never by a related event's.
    A `propose` row filed before the window whose sibling `accept` row lands
    inside it contributes the `accept` row, once, and never the `propose`
    row: reading "proposed in the window" as "settled in the window" is
    exactly the mistake this function (and `okfy changes`) refuses to make.
    """
    out = []
    for row in _read_events(bundle)[0]:
        if not _in_window(row["at"], since, until):
            continue
        if target is not None and row.get("target") != target:
            continue
        if events is not None and row["event"] not in events:
            continue
        if actions is not None and row.get("action") not in actions:
            continue
        if actor is not None and row.get("actor") != actor:
            continue
        out.append(row)
    return out


def accepted_since(bundle, when: str | None) -> int:
    """Accept events strictly after `when` (an RFC3339 UTC string); all of them
    when `when` is None. Shares `_in_window` with `changes` (since_strict=True
    keeps this function's exact pre-existing "strictly after" behaviour) —
    `ws_release`/`release_check` read this count and must see it unchanged."""
    return sum(1 for row in events(bundle)[0]
               if row["event"] == "accept"
               and _in_window(row["at"], when, None, since_strict=True))
