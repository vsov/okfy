"""`okfy cost` — what answering one question costs, in tokens, three ways.

The README's first paragraph claims the bundle "eases context assembly … precise
access to just the slice a task needs". That is the project's central claim and
until now it carried no number. This module is the number.

Reimplemented from the method in `virgiliojr94/book-to-skill`'s
`tools/discovery_tax.py`, which compares a context-dump, a navigate-the-raw-source
loop, and the compiled artifact. Its honesty discipline is kept verbatim in
spirit: the token method is printed, the navigation figure is labelled a MODEL
with its assumptions stated, both a best case and a backtrack case are reported,
and the dump is flagged as RECURRING per turn while the others are one-shot.

One measured caveat on the ratios this report leads with. Swapping the counter
between the two token methods moved the ABSOLUTE totals by ~380% and the ratios
between strategies by 8-15%. The ratio is therefore far steadier than the
absolute, which is why it leads — but it is not invariant, because the scale
factor differs slightly between corpus prose and short concept files. The output
says "steadier", not "stable".

OKFy is stronger on one axis: its third strategy is not modelled at all. It runs
the real `query()` over the bundle's real `test_queries` and counts the tokens of
the concepts actually returned.

This is a REPORT, not a gate. It exits 0 whether the numbers flatter the project
or not, and it writes nothing — the `merge-audit` precedent exactly. Inventing a
threshold after seeing the numbers is the failure mode this shape rules out.
"""
import json
from pathlib import Path

from okfy.budget import resident_core
from okfy.bundle import Bundle
from okfy.evaluation import suite_queries
from okfy.query import query
from okfy.tokens import count_path, count_tokens, count_tree, token_method
from okfy.update import _source_path

STRATEGIES = ("corpus_dump", "naive_navigation", "bundle_retrieval")
DEFAULT_N = 5


def _skip_hidden(rel: str) -> bool:
    return any(part.startswith(".") for part in rel.split("/"))


def _corpus_root(bundle: Bundle) -> Path | None:
    c = bundle.get("meta/corpus")
    raw = c.meta.get("corpus") if c else None
    return Path(str(raw)) if raw else None


def _manifest_listing(bundle: Bundle) -> list[str] | None:
    """The corpus file listing an agent reads first to orient itself."""
    f = bundle.root / "meta" / "corpus-manifest.json"
    if f.is_file():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            return sorted(data)
    return None


def _corpus_dump(root: Path | None) -> dict:
    """Whole corpus resident. Measured, and re-billed on every turn."""
    out = {"kind": "measured", "tokens": None, "recurring": True,
           "detail": "the entire corpus stays in context"}
    if root is None:
        out.update(kind="unavailable",
                   reason="meta/corpus.md declares no corpus path")
        return out
    if not root.is_dir():
        out.update(kind="unavailable", reason=f"corpus path not readable: {root}")
        return out
    tree = count_tree(root, skip=_skip_hidden)
    out.update(tokens=tree["tokens"], files=tree["files"], bytes=tree["bytes"],
               detail=f"{tree['files']} corpus files, re-billed every turn")
    if tree["skipped"]:
        # A file that could not be read must not quietly contribute zero.
        out["incomplete"] = True
        out["skipped"] = tree["skipped"]
    return out


def _sibling_of(path: Path, cited: set[Path]) -> Path | None:
    """One neighbouring corpus file, standing for a single backtrack: the agent
    opened the right file and still had to look up a term defined elsewhere."""
    try:
        peers = sorted(p for p in path.parent.iterdir()
                       if p.is_file() and p not in cited
                       and not p.name.startswith("."))
    except OSError:
        return None
    return peers[0] if peers else None


def _naive_navigation(root: Path | None, listing: list[str] | None,
                      top_hit_sources: list[str]) -> dict:
    """A MODEL of an agent navigating the raw corpus, built from real sizes.

    Not a measurement of any particular agent — a defensible estimate whose every
    input is a real byte count and whose every assumption is printed beside it.
    """
    unavailable: list[str] = []
    listing_tokens = 0
    if listing is None and root is not None and root.is_dir():
        listing = sorted(p.relative_to(root).as_posix()
                         for p in root.rglob("*") if p.is_file())
    if listing is None:
        unavailable.append("corpus file listing (no manifest, corpus unreadable)")
        listing = []
    else:
        listing_tokens = count_tokens("\n".join(listing))

    cited: list[dict] = []
    cited_paths: set[Path] = set()
    cited_tokens = 0
    for src in top_hit_sources:
        rel = _source_path(src)
        if root is None or not root.is_dir():
            unavailable.append(f"source file size: {rel}")
            cited.append({"path": rel, "tokens": None, "reason": "corpus unreadable"})
            continue
        p = (root / rel)
        if not p.is_file():
            unavailable.append(f"source file size: {rel}")
            cited.append({"path": rel, "tokens": None, "reason": "not in corpus"})
            continue
        t = count_path(p)
        cited.append({"path": rel, "tokens": t})
        cited_paths.add(p.resolve())
        cited_tokens += t

    sibling = None
    first_real = next((root / _source_path(s) for s in top_hit_sources
                       if root is not None and root.is_dir()
                       and (root / _source_path(s)).is_file()), None)
    if first_real is not None:
        sp = _sibling_of(first_real, {p.resolve() for p in [first_real]} | cited_paths)
        if sp is not None:
            sibling = {"path": sp.relative_to(root).as_posix(), "tokens": count_path(sp)}

    best = listing_tokens + cited_tokens
    loop = best + (sibling["tokens"] if sibling else 0)

    assumptions = [
        f"the agent reads a listing of the corpus ({len(listing)} files) once to orient",
        f"it then reads IN FULL the {len(cited)} source file(s) the answering "
        "concept cites — no grep slices, no partial reads",
        "the answering concept is the top hit for this query; a real agent may "
        "need more than one attempt to find it",
        "the loop case adds ONE sibling file, standing for a single backtrack "
        "for a definition that lives elsewhere",
        "nothing is re-read: this is a one-shot cost, unlike the corpus dump",
    ]
    if not sibling:
        assumptions.append("no sibling file was available, so the loop case "
                           "equals the best case here")
    if unavailable:
        assumptions.append("parts of this model could not be measured (listed "
                           "under unavailable_parts); the figure is a FLOOR, "
                           "not an estimate")

    out = {"kind": "modelled", "tokens": best, "best": best, "loop": loop,
           "recurring": False,
           "parts": {"listing": listing_tokens, "cited_sources": cited_tokens,
                     "sibling": sibling["tokens"] if sibling else 0},
           "cited": cited, "sibling": sibling, "assumptions": assumptions,
           "detail": "listing + every cited source read whole"}
    if unavailable:
        out["unavailable_parts"] = unavailable
        out["incomplete"] = True
    return out


def _bundle_retrieval(bundle: Bundle, text: str, n: int, resident: dict) -> dict:
    """Measured: the resident core plus exactly what `query()` returned."""
    res = query(bundle, text, n=n)
    concepts, total, unreadable = [], 0, []
    for h in res["results"]:
        p = bundle.root / f"{h['id']}.md"
        try:
            t = count_path(p)
        except OSError as e:
            unreadable.append({"id": h["id"], "reason": type(e).__name__})
            concepts.append({"id": h["id"], "tokens": None})
            continue
        concepts.append({"id": h["id"], "tokens": t})
        total += t
    out = {"kind": "measured", "tokens": resident["tokens"] + total,
           "recurring": False, "resident_tokens": resident["tokens"],
           "resident_files": resident["files"], "concept_tokens": total,
           "concepts": concepts,
           "detail": f"resident core + {len(concepts)} concept(s) returned"}
    if unreadable:
        out["incomplete"] = True
        out["unreadable"] = unreadable
    return out


def _ratio(a, b):
    if a is None or not b:
        return None
    return round(a / b, 1)


def _totals_state(per_query: list[dict], name: str) -> str:
    """How much of this strategy's total is actually known.

    `unavailable` — nothing could be measured for any question.
    `incomplete`  — some of it could not be, so the total is a FLOOR.
    Otherwise the strategy's own kind. Callers must render the first two
    differently from a real figure; a floor printed as a total is a lie of the
    same shape as a model printed as a measurement.
    """
    strats = [q["strategies"][name] for q in per_query]
    if not strats:
        return "empty"
    if all(s["kind"] == "unavailable" for s in strats):
        return "unavailable"
    if any(s["kind"] == "unavailable" or s.get("incomplete") for s in strats):
        return "incomplete"
    return strats[0]["kind"]


def cost_report(bundle: Bundle, queries: list[str] | None = None,
                n: int = DEFAULT_N) -> dict:
    """Read-only. Writes nothing, never raises on a missing corpus."""
    root = _corpus_root(bundle)
    listing = _manifest_listing(bundle)
    # one definition of "resident", shared with `okfy budget`
    resident = resident_core(bundle)
    dump = _corpus_dump(root)  # same corpus for every query: measure once

    source = "explicit --query"
    if queries is None:
        queries = [str(q) for q in suite_queries(bundle, "acceptance")]
        source = "meta/purpose.md test_queries"

    notes: list[str] = []
    if resident["missing"]:
        notes.append("bundle is not packaged: " + ", ".join(resident["missing"])
                     + " absent, so the resident core is undercounted")
    if dump.get("incomplete"):
        notes.append(f"{len(dump['skipped'])} corpus file(s) unreadable — the "
                     "dump figure is a floor, not a total")
    if not queries:
        notes.append("no queries: meta/purpose.md declares no test_queries")

    per_query = []
    for text in queries:
        retrieval = _bundle_retrieval(bundle, text, n, resident)
        top = retrieval["concepts"][0]["id"] if retrieval["concepts"] else None
        srcs: list[str] = []
        if top:
            c = bundle.get(top)
            raw = (c.meta.get("sources") if c else None) or []
            srcs = [str(s) for s in (raw if isinstance(raw, list) else [raw])]
        nav = _naive_navigation(root, listing, srcs)
        base = retrieval["tokens"]
        per_query.append({
            "query": text,
            "answering_concept": top,
            "strategies": {"corpus_dump": dump, "naive_navigation": nav,
                           "bundle_retrieval": retrieval},
            "ratios_vs_bundle_retrieval": {
                "corpus_dump": _ratio(dump.get("tokens"), base),
                "naive_navigation_best": _ratio(nav["best"], base),
                "naive_navigation_loop": _ratio(nav["loop"], base)},
        })

    def _sum(getter):
        vals = [getter(q) for q in per_query]
        return sum(v for v in vals if v is not None) if vals else 0

    states = {name: _totals_state(per_query, name) for name in STRATEGIES}
    t_dump = _sum(lambda q: q["strategies"]["corpus_dump"].get("tokens"))
    t_best = _sum(lambda q: q["strategies"]["naive_navigation"]["best"])
    t_loop = _sum(lambda q: q["strategies"]["naive_navigation"]["loop"])
    t_bundle = _sum(lambda q: q["strategies"]["bundle_retrieval"]["tokens"])
    # A total that could not be measured is None, never 0. Summing an
    # unavailable strategy to zero would render "cannot check" as "free", which
    # is the inversion audit round 8 ruled out.
    if states["corpus_dump"] == "unavailable":
        t_dump = None
    if states["naive_navigation"] == "unavailable":
        t_best = t_loop = None
    if states["bundle_retrieval"] == "unavailable":
        t_bundle = None
    if per_query and dump["kind"] == "measured":
        notes.append("the dump total repeats the corpus once per question "
                     "because it is resident — that is the cost being shown")

    return {
        "bundle": str(bundle.root),
        "token_method": token_method(),
        "n": n,
        "query_source": source,
        "resident": resident,
        "queries": per_query,
        "totals": {"queries": len(per_query), "corpus_dump": t_dump,
                   "naive_navigation_best": t_best,
                   "naive_navigation_loop": t_loop,
                   "bundle_retrieval": t_bundle,
                   "states": states,
                   "ratios_vs_bundle_retrieval": {
                       "corpus_dump": _ratio(t_dump, t_bundle),
                       "naive_navigation_best": _ratio(t_best, t_bundle),
                       "naive_navigation_loop": _ratio(t_loop, t_bundle)}},
        "notes": notes,
    }
