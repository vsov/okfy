"""Size discipline for concepts and for the files billed on every turn.

The transferable rule from `virgiliojr94/book-to-skill` is not its numbers, it is
the sentence beside them: depth is earned with content, not with a bigger number.
A chapter that genuinely has less to say should land below the floor and be
reported thin. Padding it to hit a target makes the artifact worse while making
the metric better, so this report says so in its own output — a number printed
without that sentence invites exactly the wrong fix.

OKFy required sections and never sizes. More to the point, nothing in the tool
distinguished the two economies it actually has: `AGENTS.md` and `index.md` are
resident and re-read on EVERY turn, while concepts are fetched on demand. Those
are different costs and this module reports them separately.

Advisory by the owner's decision. `okfy budget` exits 0 always,
`W_BUDGET_RESIDENT` is a warning at every strictness level, and `release_check`
never composes any of it.
"""
from okfy.bundle import Bundle
import json
import re
from pathlib import Path

from okfy.tokens import count_path, count_tokens, token_method

RESIDENT_FILES = ("AGENTS.md", "index.md")

ANTI_PADDING = (
    "depth is earned with content, not with a bigger number: a thin concept "
    "should stay thin and be reported, never padded to reach a target"
)


def resident_core(bundle: Bundle) -> dict:
    """The files an agent keeps loaded for a whole session.

    One predicate, two callers: `okfy cost` bills this into its retrieval
    strategy and `okfy budget` reports it against a target. Two modules with
    two definitions of "resident" is the drift shape the audits kept finding.
    """
    tokens, present, missing = 0, [], []
    for name in RESIDENT_FILES:
        p = bundle.root / name
        if p.is_file():
            tokens += count_path(p)
            present.append(name)
        else:
            missing.append(name)
    return {"tokens": tokens, "files": present, "missing": missing}


def _percentile(sorted_vals: list[int], q: float) -> int:
    if not sorted_vals:
        return 0
    i = min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def _median(sorted_vals: list[int]) -> int:
    n = len(sorted_vals)
    if not n:
        return 0
    mid = n // 2
    return sorted_vals[mid] if n % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) // 2


def budget_report(bundle: Bundle, archetype=None) -> dict:
    """Read-only. An archetype with no `budgets:` block is not a defect: every
    target reads `None` and no warning is produced."""
    budgets = (getattr(archetype, "budgets", None) or {})
    type_targets = budgets.get("types") or {}
    resident_max = budgets.get("resident_max")

    sizes: dict[str, list[int]] = {}
    ids: dict[str, list[tuple[str, int]]] = {}
    unreadable: list[dict] = []
    for c in bundle.concepts():
        if c.id.startswith("meta/"):
            continue
        t = str(c.meta.get("type"))
        try:
            n = count_path(c.path)
        except OSError as e:
            unreadable.append({"id": c.id, "reason": type(e).__name__})
            continue
        sizes.setdefault(t, []).append(n)
        ids.setdefault(t, []).append((c.id, n))

    types = []
    for t in sorted(sizes):
        v = sorted(sizes[t])
        target = type_targets.get(t) or {}
        tmin, tmax = target.get("target_min"), target.get("target_max")
        thin = sorted(i for i, n in ids[t] if tmin is not None and n < tmin)
        over = sorted(i for i, n in ids[t] if tmax is not None and n > tmax)
        types.append({"type": t, "count": len(v), "median": _median(v),
                      "p90": _percentile(v, 0.9), "min": v[0], "max": v[-1],
                      "target_min": tmin, "target_max": tmax,
                      "thin": thin, "over": over})

    resident = resident_core(bundle)
    resident["target_max"] = resident_max
    resident["over"] = bool(resident_max and resident["tokens"] > resident_max)

    notes = [ANTI_PADDING]
    if not budgets:
        notes.append(
            f"archetype {getattr(archetype, 'name', '(none)')!r} declares no "
            "budgets: block — targets read '-' and nothing is out of range. "
            "The block is optional; its absence is not a defect")
    if resident["missing"]:
        notes.append("not packaged: " + ", ".join(resident["missing"])
                     + " absent, so the resident total is undercounted")
    if unreadable:
        notes.append(f"{len(unreadable)} concept(s) could not be read and are "
                     "absent from every figure below")
    return {"bundle": str(bundle.root),
            "archetype": getattr(archetype, "name", None),
            "token_method": token_method(),
            "resident": resident, "types": types,
            "unreadable": unreadable, "notes": notes,
            "anti_padding": ANTI_PADDING}


USAGE_LABEL = ("zero_hit = not reached by any recorded eval run, which can reach at "
               "most ceiling.reachable ids — never evidence that a concept is unused")
_INDEX_TARGET_RE = re.compile(r"\]\(([^)\s]+)\.md\)")


def usage_report(bundle: Bundle, journal: Path | None = None) -> dict:
    """Which concepts the recorded eval runs ever returned, read from
    meta/eval.json and index.md only — it writes nothing. The share is bounded
    by the query set (queries x top_n), so it is reported with that ceiling.

    `journal` (v0.24 (d)) adds a SECOND, separately labelled `journal` section
    built from real okfy-mcp sessions (see `journal_report`) — additive only;
    the eval-run section above and its label are unchanged either way."""
    ids = sorted(c.id for c in bundle.concepts() if not c.id.startswith("meta/"))
    ev = bundle.root / "meta" / "eval.json"
    runs = (json.loads(ev.read_text(encoding="utf-8")).get("runs") or []) if ev.is_file() else []
    hit: set[str] = set()
    queries = top_n = 0
    for run in runs:
        for res in run.get("results") or []:
            if not isinstance(res, dict):
                continue
            hits = res.get("top_hits") or []
            queries += 1
            top_n = max(top_n, len(hits))
            hit.update(h["id"] if isinstance(h, dict) else str(h) for h in hits)
    zero = [i for i in ids if i not in hit] if runs else []
    idx = bundle.root / "index.md"
    lines = [ln for ln in (idx.read_text(encoding="utf-8").splitlines() if idx.is_file() else [])
             if (m := _INDEX_TARGET_RE.search(ln)) and m.group(1) in set(zero)]
    out = {"concepts": len(ids), "runs": len(runs),
          "ever_hit": len(ids) - len(zero) if runs else None,
          "zero_hit": len(zero) if runs else None,
          "share": round(len(zero) / len(ids), 4) if runs and ids else None,
          "ceiling": {"queries": queries, "top_n": top_n,
                      "reachable": min(len(ids), queries * top_n)},
          "zero_hit_ids": zero,
          "zero_hit_index_tokens": count_tokens("\n".join(lines)) if lines else 0,
          "token_method": token_method(), "label": USAGE_LABEL}
    if journal is not None:
        out["journal"] = journal_report(bundle, journal, ids=ids)
    return out


def _read_journal_rows(path: Path) -> tuple[list[dict], int]:
    """(readable JSONL rows, count of lines that were not one). Never fatal —
    a malformed line is counted (`skipped_lines`) and skipped, not raised."""
    if not path.is_file():
        return [], 0
    rows: list[dict] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(row, dict):
            skipped += 1
            continue
        rows.append(row)
    return rows, skipped


def journal_report(bundle: Bundle, journal: Path, ids: list[str] | None = None) -> dict:
    """v0.24 (d): a second usage source, built from an okfy-mcp `--journal`
    file — OBSERVED tool calls from real sessions, distinctly labelled from
    the eval-run section `usage_report` already reports (never merged with
    it: an eval run is a designed suite, a journal is whatever actually
    happened).

    v0.24 review fix: a concept counts as EVER REACHED (`ever_hit`/
    `zero_hit_ids`) when it appears either in a query row's `top_ids` (what
    a search surfaced) or in a show row's `shown_id`/`shown_ids` (what was
    actually fetched — a targeted `okfy_show` on an id copied from an
    earlier result, a link, or the agent's own memory never goes through
    `okfy_query`, so a journal that only looked at `top_ids` undercounted
    reach). The two sources are ALSO reported separately, additively:
    `queried_ids_n` (distinct bundle ids ever in a query's `top_ids`) and
    `shown_ids_n` (distinct bundle ids ever shown) sit alongside the
    combined `ever_hit`/`zero_hit_ids`, not in place of them."""
    ids = sorted(c.id for c in bundle.concepts() if not c.id.startswith("meta/")) \
        if ids is None else ids
    id_set = set(ids)
    rows, skipped = _read_journal_rows(journal)
    sessions = {r["session"] for r in rows if r.get("session")}
    query_rows = [r for r in rows if r.get("tool") == "query"]
    show_rows = [r for r in rows if r.get("tool") == "show"]
    queried_ids: set[str] = set()
    for r in query_rows:
        queried_ids.update(i for i in (r.get("top_ids") or []) if isinstance(i, str))
    shown_ids: set[str] = set()
    for r in show_rows:
        sid = r.get("shown_id")
        if isinstance(sid, str):
            shown_ids.add(sid)
        shown_ids.update(i for i in (r.get("shown_ids") or []) if isinstance(i, str))
    hit = queried_ids | shown_ids
    zero = [i for i in ids if i not in hit]
    return {"label": f"observed MCP tool calls from {journal} — real "
                     "sessions, not the eval set",
            "sessions": len(sessions), "queries": len(query_rows),
            "ever_hit": len(ids) - len(zero), "zero_hit_ids": zero,
            "queried_ids_n": len(queried_ids & id_set),
            "shown_ids_n": len(shown_ids & id_set),
            "skipped_lines": skipped}
