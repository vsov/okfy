"""Context-economics reports: what a bundle costs to consult, in tokens.

Reports, not gates. They exit 0 whatever the numbers say — the `merge-audit`
precedent — so that no threshold can be invented after the data is in.
"""
from okfy.bundle import Bundle

from .common import _archetype_for, _print


def _fmt(tok, ratio, floor: bool = False) -> str:
    """One aligned `ratio  tokens` cell.

    `floor=True` prefixes both numbers with `>=`: a figure some of whose inputs
    could not be measured is a lower bound, and printing it bare would let
    "cannot check" read as a small, favourable number.
    """
    if tok is None:
        return "      n/a           — tok"
    mark = ">=" if floor else "  "
    r = f"{mark}{ratio:>4.1f}x" if ratio is not None else "     n/a"
    return f"{r} {mark}{tok:>9,} tok"


def _strategy_lines(q: dict) -> list[str]:
    s, r = q["strategies"], q["ratios_vs_bundle_retrieval"]
    dump, nav, ret = s["corpus_dump"], s["naive_navigation"], s["bundle_retrieval"]
    nav_floor = bool(nav.get("incomplete"))
    out = []
    if dump["kind"] == "unavailable":
        out.append(f"  corpus dump      {_fmt(None, None)}  "
                   f"[unavailable] {dump['reason']}")
    else:
        out.append(f"  corpus dump      "
                   f"{_fmt(dump['tokens'], r['corpus_dump'], dump.get('incomplete'))}"
                   f"  [measured{', FLOOR' if dump.get('incomplete') else ''}]"
                   f"  RECURRING every turn")
    out.append(f"  naive nav (best) "
               f"{_fmt(nav['best'], r['naive_navigation_best'], nav_floor)}  "
               f"[modelled{', FLOOR' if nav_floor else ''}]  one-shot")
    out.append(f"  naive nav (loop) "
               f"{_fmt(nav['loop'], r['naive_navigation_loop'], nav_floor)}  "
               f"[modelled{', FLOOR' if nav_floor else ''}]  one-shot, +1 backtrack")
    out.append(f"  bundle retrieval {_fmt(ret['tokens'], 1.0)}  [measured]  "
               f"resident {ret['resident_tokens']:,} + "
               f"{len(ret['concepts'])} concept(s)")
    return out


def cmd_cost(a) -> int:
    """Read-only context-economics report. Always exits 0 when the report ran."""
    from okfy.cost import cost_report
    b = Bundle(a.bundle)
    out = cost_report(b, queries=([a.query] if a.query else None), n=a.n)
    if a.json:
        _print(out)
        return 0

    print(f"cost: {out['bundle']}")
    # Measured, not asserted: swapping the counter moved the absolutes ~380%
    # and the ratios 8-15%. Far steadier, and not invariant — so the line says
    # "steadier", which is what was observed, rather than "stable".
    print(f"token method: {out['token_method']}  "
          f"(absolutes are ESTIMATES; ratios move far less between token "
          f"methods — measured 8-15% against ~380% — but are not invariant)")
    print(f"queries: {out['totals']['queries']} from {out['query_source']}   "
          f"n={out['n']}")

    if not a.quiet:
        for i, q in enumerate(out["queries"], 1):
            print(f"\nQ{i}  {q['query']}")
            if q["answering_concept"]:
                print(f"  answering concept: {q['answering_concept']}")
            for line in _strategy_lines(q):
                print(line)

    t = out["totals"]
    tr = t["ratios_vs_bundle_retrieval"]
    st = t["states"]

    def tag(name: str, base: str) -> str:
        state = st.get(name)
        if state == "unavailable":
            return "[unavailable — nothing to total]"
        return f"[{base}, FLOOR]" if state == "incomplete" else f"[{base}]"

    dump_floor = st.get("corpus_dump") == "incomplete"
    nav_floor = st.get("naive_navigation") == "incomplete"
    print(f"\ntotals over {t['queries']} question(s), tokens entering context:")
    print(f"  corpus dump      {_fmt(t['corpus_dump'], tr['corpus_dump'], dump_floor)}"
          f"  {tag('corpus_dump', 'measured, RECURRING')}")
    print(f"  naive nav (best) "
          f"{_fmt(t['naive_navigation_best'], tr['naive_navigation_best'], nav_floor)}"
          f"  {tag('naive_navigation', 'modelled')}")
    print(f"  naive nav (loop) "
          f"{_fmt(t['naive_navigation_loop'], tr['naive_navigation_loop'], nav_floor)}"
          f"  {tag('naive_navigation', 'modelled')}")
    print(f"  bundle retrieval {_fmt(t['bundle_retrieval'], 1.0)}  "
          f"{tag('bundle_retrieval', 'measured')}")
    print("\nthe corpus dump is RE-BILLED EVERY TURN while it stays resident; "
          "the other two\nare one-shot costs paid once per question.")

    if out["queries"] and not a.quiet:
        navs = [q["strategies"]["naive_navigation"] for q in out["queries"]]
        print("\nassumptions — naive navigation is a MODEL built from real file "
              "sizes,\nnot a measurement of any agent:")
        for line in navs[0]["assumptions"]:
            print(f"  - {line}")
        # The counts inside those lines are per question. Say so rather than
        # letting Q1's assumptions read as if they covered every question.
        differing = sum(1 for nav in navs[1:]
                        if nav["assumptions"] != navs[0]["assumptions"])
        if differing:
            print(f"  (shown for Q1; {differing} other question(s) assume "
                  f"different counts — see --json)")
        missing = sorted({p for nav in navs for p in nav.get("unavailable_parts", [])})
        for part in missing:
            print(f"  ! could not measure: {part}")
    for note in out["notes"]:
        print(f"note: {note}")
    return 0


def _target(t: dict) -> str:
    lo, hi = t["target_min"], t["target_max"]
    return f"{lo:,}-{hi:,}" if lo is not None and hi is not None else "-"


def cmd_budget(a) -> int:
    """Advisory size report. Exits 0 always — no strictness level turns any
    budget finding into an error, by the owner's decision."""
    from okfy.budget import budget_report
    b = Bundle(a.bundle)
    arch = None if a.no_archetype else _archetype_for(b)
    out = budget_report(b, arch)
    if a.json:
        _print(out)
        return 0

    print(f"budget: {out['bundle']}")
    print(f"archetype: {out['archetype'] or '(none)'}   "
          f"token method: {out['token_method']}")

    res = out["resident"]
    over = "  OVER TARGET" if res["over"] else ""
    print(f"\nALWAYS RESIDENT ({' + '.join(res['files']) or 'nothing packaged'}): "
          f"{res['tokens']:,} tok   target {res['target_max'] or '-'}{over}")
    print("  this is the only figure billed on EVERY turn; concepts below are "
          "fetched on demand")

    print(f"\n{'type':<22} {'n':>4} {'median':>8} {'p90':>8} {'target':>14} "
          f"{'thin':>5} {'over':>5}")
    for t in out["types"]:
        print(f"{t['type']:<22} {t['count']:>4} {t['median']:>8,} {t['p90']:>8,} "
              f"{_target(t):>14} {len(t['thin']):>5} {len(t['over']):>5}")

    thin = [(t["type"], i) for t in out["types"] for i in t["thin"]]
    if thin and not a.quiet:
        print(f"\nthin ({len(thin)}) — under target_min, reported not condemned:")
        for typ, i in thin[:20]:
            print(f"  {typ:<20} {i}")
        if len(thin) > 20:
            print(f"  ... and {len(thin) - 20} more (use --json for all)")
    for n in out["notes"]:
        print(f"note: {n}")
    return 0
