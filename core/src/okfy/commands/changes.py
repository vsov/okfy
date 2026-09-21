"""`okfy changes <bundle>`: a read-only window query over the memory ledger
(`meta/memory.jsonl`). Every ledger row already carries its own `at`
(RFC3339 UTC, written once by `okfy.actor.utc_now`) — this command adds
nothing but filtering on it.

The trap, and why it gets a whole paragraph: each row in this ledger is
ALREADY its own event with its own timestamp, so a caller must not read
"proposed in the window" as "settled in the window". A `propose` row filed
BEFORE the window, whose sibling `accept` row lands INSIDE it, contributes
the `accept` row — once — and never the `propose` row. Every event here is
judged by ITS OWN `at` only, never by a related event's. See
`okfy.memory.changes` for the filter itself.
"""
from okfy.bundle import Bundle
from okfy.memory import changes as memory_changes
from okfy.memory import parse_window_bound

from .common import _print


def cmd_changes(a) -> int:
    """JSON is the default output (a skill can parse it); `--json` is
    accepted so a caller can say what it means, matching `okfy diff`'s
    convention. `--text` is the human summary.

    `--event` and `--action` are two different filters on two different
    fields of the same row: `--event` is the ledger EVENT kind (propose/
    accept/reject/superseded — `okfy.memory.EVENTS`), `--action` is the
    underlying proposal's ACTION (create/update/delete/supersede/flag/gap —
    `okfy.proposals.ACTIONS`). An `accept` event can carry a `delete`
    action; the two names are not interchangeable.

    v0.25 F12b: `memory_changes` also returns the reader's own `problems`
    (unreadable ledger lines, `E_MEMORY_LINE`) — a ledger that is entirely
    unreadable must not come back looking like a genuinely empty one.
    Readable rows are still reported (the 8-good/2-corrupt case degrades to
    "report the knowable, flag the rest", matching how `validate`'s
    `_check_memory_log` already treats this file: an unreadable line is
    named, never silently dropped). What changes here is the exit code:
    `problems` non-empty exits 1 (the `ok: false` convention `release.py`/
    `sourcemap.py` already use), so a caller can tell "nothing has happened
    yet" (exit 0, count 0, no problems) apart from "history exists but part
    of it could not be read" (exit 1, problems non-empty) even when both
    happen to report the same event count."""
    b = Bundle(a.bundle)
    since = parse_window_bound(a.since, flag="--since") if a.since else None
    until = parse_window_bound(a.until, flag="--until") if a.until else None
    if since is not None and until is not None and until < since:
        raise ValueError(
            f"E_CHANGES_WINDOW: --until ({a.until}) is earlier than --since "
            f"({a.since}) — the window would run backwards; pass a --until "
            "that is later than --since (bare dates like 2026-01-31, or "
            "full RFC3339 UTC timestamps like 2026-01-31T14:30:00Z), or "
            "drop one of the two flags")
    # --event filters row["event"] (propose/accept/reject/superseded — the
    # ledger's own EVENTS); --action filters row["action"]
    # (okfy.proposals.ACTIONS — create/update/delete/supersede/flag/gap).
    # Two different fields on the same row: never conflate them.
    event_kinds = tuple(a.event) if a.event else None
    actions = tuple(a.action) if a.action else None
    rows, problems = memory_changes(b, since=since, until=until, target=a.target,
                                    events=event_kinds, actions=actions, actor=a.actor)
    exit_code = 1 if problems else 0
    result = {"bundle": str(a.bundle), "since": since, "until": until,
              "count": len(rows), "window_applies_to": "event time",
              "events": rows, "problems": problems}
    if not getattr(a, "text", False):
        _print(result)
        return exit_code

    print(f"changes: bundle={a.bundle} since={since} until={until} "
          f"count={len(rows)}")
    print("(each event below is in the window by its own `at` — never by a "
          "related propose/accept/reject/superseded row's time)")
    if problems:
        print(f"WARNING: {len(problems)} unreadable meta/memory.jsonl "
              "line(s) — the events below are only what could be read:")
        for p in problems:
            print(f"  {p}")
    for r in rows:
        print(f"  {r['at']}  {r['event']:<10} target={r.get('target') or '-':<28} "
              f"actor={r['actor']} action={r['action']} proposal={r['proposal']}")
    return exit_code
