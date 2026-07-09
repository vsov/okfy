"""Retrieval: lexicon-expanded BM25 over the index (ADR-0013). Accepted lexicon
rows pin their maps_to concepts first (via: lexicon); BM25 over the expanded
query fills the rest. Stale concepts stay visible but marked — no demotion."""
import math

from okfy import lexicon
from okfy.bm25 import BM25, tokenize
from okfy.bundle import Bundle, Concept
from okfy.index import load_index


def _hit(c: dict, score: float | None = None) -> dict:
    h = {"id": c["id"], "type": c["type"], "title": c["title"],
         "description": c["description"], "score": score}
    if c.get("stale"):
        h["stale"] = True
        h["stale_reason"] = c.get("stale_reason", "")
    return h


def filter_pool(idx: dict, type_: str | None = None, tag: str | None = None,
                include_meta: bool = False, include_stale: bool = True) -> list[dict]:
    return [c for c in idx["concepts"]
            if (include_meta or not c["id"].startswith("meta/"))
            and (type_ is None or c["type"] == type_)
            and (tag is None or tag in c["tags"])
            and (include_stale or not c.get("stale"))]


def search_pool(pool: list[dict], pins: list[str], text: str, n: int) -> list[dict]:
    """Pinned concepts first (via: lexicon, no score); BM25 over the rest of
    the pool fills the remainder of n. Pins outside the pool are dropped."""
    by_id = {c["id"]: c for c in pool}
    results = [dict(_hit(by_id[p]), via="lexicon") for p in pins if p in by_id][:n]
    rest = [c for c in pool if c["id"] not in set(pins)]
    bm = BM25([c["tokens"] for c in rest])
    results += [_hit(rest[i], round(s, 3))
                for i, s in bm.search(tokenize(text), max(0, n - len(results)))]
    return results


def query(bundle: Bundle, text: str, type_: str | None = None, tag: str | None = None,
          n: int = 10, include_meta: bool = False, expand: bool = True,
          include_stale: bool = True) -> dict:
    pool = filter_pool(load_index(bundle), type_, tag, include_meta, include_stale)
    eff = lexicon.expand(lexicon.load_rows(bundle) if expand else [], text)
    return {"results": search_pool(pool, eff["pins"], eff["expanded_query"], n),
            "expanded_query": eff["expanded_query"], "notes": eff["notes"]}


def show(bundle: Bundle, concept_id: str) -> Concept:
    c = bundle.get(concept_id)
    if c is None:
        raise KeyError(f"concept not found: {concept_id}")
    return c


def links(bundle: Bundle, concept_id: str) -> dict:
    idx = load_index(bundle)
    by_id = {c["id"]: c for c in idx["concepts"]}
    if concept_id not in by_id:
        raise KeyError(f"concept not found in index: {concept_id}")
    out = by_id[concept_id]["links"]
    backlinks = sorted(c["id"] for c in idx["concepts"] if concept_id in c["links"])
    return {"id": concept_id, "out": out, "backlinks": backlinks}


def sample_for_review(bundle: Bundle, fraction: float = 0.1, minimum: int = 20) -> list[str]:
    finals = sorted((c for c in bundle.concepts() if not c.id.startswith("meta/")),
                    key=lambda c: c.id)
    if not finals:
        return []
    target = min(len(finals), max(minimum, math.ceil(len(finals) * fraction)))
    picked: list[str] = []
    seen: set[str] = set()
    by_type: dict[str, list] = {}
    for c in finals:
        by_type.setdefault(str(c.meta.get("type")), []).append(c)
    for t in sorted(by_type):                      # >=1 per type
        cid = by_type[t][0].id
        picked.append(cid)
        seen.add(cid)
    step = max(1, len(finals) // target)
    for c in finals[::step]:                       # fill evenly
        if len(picked) >= max(target, len(by_type)):
            break
        if c.id not in seen:
            picked.append(c.id)
            seen.add(c.id)
    return sorted(picked)
