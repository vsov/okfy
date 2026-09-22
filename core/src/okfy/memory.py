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
# v0.26 audit A3: `dismiss` (okfy.proposals.dismiss) is the owner's explicit
# disposition of an unresolved REJECT — "I reviewed the rejection, and the
# SOURCE CHANGE itself needs no action", a different decision from the
# reject it follows (which only says the proposed TEXT was wrong). It
# carries no proposal (proposal=None, like an owner-refine `accept` row) and
# is the only event besides a fresh accepted proposal that
# `okfy.update.unresolved_rejections` treats as clearing a standing reject.
EVENTS = ("propose", "accept", "reject", "superseded", "dismiss")
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
# The ONE shape a ledger row's own `at` is ever legitimately written in
# (`okfy.actor.utc_now`) — unlike `_WINDOW_FORMATS` above, `_usable_at`
# below offers no bare-date tolerance: a row is machine-written, never
# user-typed, so anything else is unusable, not merely unconventional.
_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


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


def _usable_at(value) -> bool:
    """True when `value` parses as `_AT_FORMAT` — the only shape any ledger
    row's `at` is ever written in. A row failing this (an empty string, free
    text, a bare date missing the time-of-day `utc_now` always writes) is
    not a timestamp this module can place in a window at all: `_in_window`
    would otherwise compare it lexicographically against `since`/`until`
    like any other string, and an unparseable `at` sorts before every real
    timestamp — matching no window and silently vanishing from both the
    matched-events list and the problems list. See `_parse_lines`, which
    reports it as a problem instead, and A10 in CHANGELOG.md's v0.27.0
    entry."""
    if not isinstance(value, str):
        return False
    try:
        datetime.datetime.strptime(value, _AT_FORMAT)
    except ValueError:
        return False
    return True


def _parse_lines(raw_text: str) -> tuple[list[dict], list[str]]:
    """The JSONL row parser shared by `events` (working tree) and
    `events_at_head` (v0.25 F05: the committed HEAD blob) — identical
    per-line validation and problem-naming regardless of which source the
    text came from.

    A row whose `at` cannot be placed in a window at all (v0.26 audit A10:
    `_usable_at` fails) is reported as a problem here, the same way a
    missing key or an unknown event is — never silently returned as a
    "readable" row that then fails every `_in_window` comparison and
    vanishes from both `out` and `problems` at once. This is the reader's
    honest "I cannot tell what time this happened", not a refusal: every
    OTHER readable row on the same ledger is still returned."""
    out, problems = [], []
    for n, raw in enumerate(raw_text.splitlines(), start=1):
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
        if not _usable_at(row["at"]):
            problems.append(
                f"{where}: at {row['at']!r} is not a usable timestamp — "
                "expected RFC3339 UTC, e.g. 2026-01-31T00:00:00Z")
            continue
        out.append(row)
    return out, problems


def events(bundle) -> tuple[list[dict], list[str]]:
    """(readable events in file order, problems). A problem names its line."""
    path = bundle.root / MEMORY_FILE
    if not path.is_file():
        return [], []
    return _parse_lines(path.read_text(encoding="utf-8"))


def events_at_head(bundle) -> tuple[list[dict], list[str]]:
    """Same shape as `events` — (readable events, problems) — read from the
    COMMITTED HEAD blob instead of the working tree. `([], [])` when there is
    no git repository, or no committed version of this file yet.

    v0.25 F05: `propose`'s rejected-content gate falls back to this when
    `okfy.validate.ledger_prefix_check` reports the working copy REWRITTEN.
    The working copy cannot answer either way once that is true — a `None`
    result could be a genuine "never rejected" or a scrubbed one — but HEAD
    can: it is the exact trust boundary `ledger_prefix_check` itself already
    draws (its HONESTY LABEL: proves nothing about a rewrite that was itself
    already committed — it compares the index and the working tree against
    HEAD, never HEAD against an earlier HEAD)."""
    from okfy.gitenv import run_git
    shown = run_git(bundle.root, "show", f"HEAD:{MEMORY_FILE}", capture_output=True)
    if shown.returncode != 0:
        return [], []
    return _parse_lines(shown.stdout.decode("utf-8", errors="replace"))


# `changes()` below takes an `events=` keyword filter, which as a local
# parameter would shadow the `events` function name inside its own body —
# this alias is what it calls instead, so the public `events(bundle)` name
# (read by many other modules and tests) stays exactly as it is.
_read_events = events


def rejected_hashes(bundle, *, from_events: list[dict] | None = None) -> dict[str, dict]:
    """content_sha256 -> the reject event still standing against it. A later
    propose of the same text that carried `reopen` lifts it; rejecting that
    reopened text again puts it back.

    `from_events` (v0.25 F05): an already-read event list to fold instead of
    re-reading `events(bundle)` — how `propose`'s gate reuses the SAME
    predicate machinery against `events_at_head`'s output when the working
    tree is untrustworthy, without a second copy of this loop."""
    standing: dict[str, dict] = {}
    for row in (from_events if from_events is not None else events(bundle)[0]):
        if row["action"] == "delete":
            continue
        if row["event"] == "reject":
            standing[row["content_sha256"]] = row
        elif row["event"] == "propose" and row.get("reopen"):
            standing.pop(row["content_sha256"], None)
    return standing


def rejected_match_hashes(bundle, *, from_events: list[dict] | None = None) -> dict[str, dict]:
    """match_sha256 -> the reject-or-delete event still standing against it.

    A tombstone that survives rewording: sibling to `rejected_hashes`, but
    keyed on the normalized-text fingerprint instead of the exact bytes, and
    fed by two kinds of standing row — a `reject` (the owner said no to this
    claim) and an accepted `delete` (the owner removed a concept that said
    this). Both are lifted the same way: a later `propose` that carries
    `reopen` and hashes to the same fingerprint pops the entry; rejecting or
    re-deleting that reopened text puts it back. Old rows carry no
    `match_sha256` and are silently invisible here — never refused, matching
    v0.23 behaviour exactly.

    `from_events`: see `rejected_hashes` — same v0.25 F05 reuse against
    `events_at_head`'s output."""
    standing: dict[str, dict] = {}
    for row in (from_events if from_events is not None else events(bundle)[0]):
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
    both accepted shapes with an example and the way out.

    v0.25 F12a: `strptime` accepts unpadded fields (`2026-9-01`, `T2:00:00Z`)
    as well as the canonical zero-padded ones — it validates the CALENDAR
    shape, not the SPELLING. The parsed `datetime` is reformatted through
    `strftime` below rather than returned as the original string, so every
    accepted spelling of the same instant comes back byte-identical and
    downstream lexicographic comparison against `utc_now`-written `at`
    values (`_in_window`) stays chronological order, never string order."""
    v = str(value).strip()
    for fmt in _WINDOW_FORMATS:
        try:
            dt = datetime.datetime.strptime(v, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%dT%H:%M:%SZ":
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%Y-%m-%dT00:00:00Z")
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
           actor: str | None = None) -> tuple[list[dict], list[str]]:
    """(matching rows in file order, reader problems) — `since`/`until` must
    already be normalized RFC3339 UTC strings (see `parse_window_bound`), or
    `None` for no bound. `target`/`events`/`actions`/`actor` filter further
    and combine with the window, and with each other, as AND.

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

    v0.25 F12b: the second element is `_read_events(bundle)[1]` verbatim —
    the lines the reader could not parse (named `E_MEMORY_LINE`, same as
    `validate`'s W_MEMORY_LINE finding). Earlier this was discarded here, so
    a ledger of nothing but unreadable lines came back exactly like a
    genuinely empty one: `[]`. A caller (`cmd_changes`) that drops this
    return value on the floor reintroduces that bug — it must surface it in
    both the payload and the exit code, never just one."""
    readable, problems = _read_events(bundle)
    out = []
    for row in readable:
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
    return out, problems


def accepted_since(bundle, when: str | None) -> int:
    """Accept events strictly after `when` (an RFC3339 UTC string); all of them
    when `when` is None. Shares `_in_window` with `changes` (since_strict=True
    keeps this function's exact pre-existing "strictly after" behaviour) —
    `ws_release`/`release_check` read this count and must see it unchanged."""
    return sum(1 for row in events(bundle)[0]
               if row["event"] == "accept"
               and _in_window(row["at"], when, None, since_strict=True))
