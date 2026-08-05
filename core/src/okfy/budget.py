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
from okfy.tokens import count_path, token_method

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
