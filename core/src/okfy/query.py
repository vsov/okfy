"""Retrieval: lexicon-expanded BM25 over the index (ADR-0013). Accepted lexicon
rows pin their maps_to concepts first (via: lexicon); BM25 over the expanded
query fills the rest. Stale concepts stay visible but marked — no demotion."""
import hashlib
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


SELECTOR_VERSION = 2


def _selector_seed(bundle: Bundle) -> str:
    """Deterministic seed tied to the corpus state: git SHA when the corpus is
    a git repo, else a digest of the corpus manifest. Recording it in the
    PurposeFitness artifact makes the sample replayable — and lets the
    validator detect when the recorded sample no longer matches."""
    c = bundle.get("meta/corpus")
    sha = c.meta.get("git_sha") if c else None
    if sha:
        return str(sha)
    mf = bundle.root / "meta" / "corpus-manifest.json"
    if mf.is_file():
        return hashlib.sha256(mf.read_bytes()).hexdigest()[:16]
    return "no-seed"


def sample_for_review(bundle: Bundle, fraction: float = 0.1, minimum: int = 20) -> dict:
    """Risk-oriented deterministic L3 sample. Priority tiers first — concepts
    whose sources changed since the snapshot, stale concepts, rare types, weak
    source coverage — then a seeded stratified fill across types. Alphabetical
    position carries no weight (selector v1 was systematically biased to it)."""
    finals = sorted((c for c in bundle.concepts() if not c.id.startswith("meta/")),
                    key=lambda c: c.id)
    seed = _selector_seed(bundle)
    out = {"selector_version": SELECTOR_VERSION, "seed": seed,
           "sampled": [], "reasons": {}, "notes": []}
    if not finals:
        return out

    def rank(s: str) -> str:
        return hashlib.sha256(f"{seed}:{s}".encode()).hexdigest()

    changed: set[str] = set()
    try:
        from okfy.update import corpus_diff
        d = corpus_diff(bundle)
        changed = set(d["changed"]) | set(d["removed"])
    except Exception as e:
        out["notes"].append(f"corpus diff unavailable ({e}); "
                            "changed-source tier skipped")

    by_type: dict[str, list] = {}
    for c in finals:
        by_type.setdefault(str(c.meta.get("type")), []).append(c)

    risk: dict[str, list[str]] = {}
    for c in finals:
        srcs = {str(s).split("#", 1)[0] for s in (c.meta.get("sources") or [])}
        rs = []
        if srcs & changed:
            rs.append("changed-source")
        if c.meta.get("stale"):
            rs.append("stale")
        if len(srcs) <= 1:
            rs.append("weak-coverage")
        if len(by_type[str(c.meta.get("type"))]) <= 2:
            rs.append("rare-type")
        if rs:
            risk[c.id] = rs

    target = min(len(finals), max(minimum, math.ceil(len(finals) * fraction)))
    picked: list[str] = []
    seen: set[str] = set()
    reasons: dict[str, list[str]] = {}
    for cid in sorted(risk, key=lambda i: (-len(risk[i]), rank(i))):
        if len(picked) >= target:
            break
        picked.append(cid)
        seen.add(cid)
        reasons[cid] = list(risk[cid])

    # stratified fill: round-robin across types in seeded order
    queues = {t: [c.id for c in sorted(by_type[t], key=lambda c: rank(c.id))
                  if c.id not in seen] for t in by_type}
    order = sorted(queues, key=rank)
    while len(picked) < target and any(queues.values()):
        for t in order:
            if len(picked) >= target:
                break
            if queues[t]:
                cid = queues[t].pop(0)
                picked.append(cid)
                seen.add(cid)
                reasons[cid] = ["stratified"]

    # every type represented, even past target (v1 guarantee kept)
    for t in order:
        if not any(str(c.meta.get("type")) == t
                   for c in finals if c.id in seen) and by_type[t]:
            cid = sorted(by_type[t], key=lambda c: rank(c.id))[0].id
            picked.append(cid)
            seen.add(cid)
            reasons[cid] = risk.get(cid, []) + ["type-coverage"]

    out["sampled"] = sorted(picked)
    out["reasons"] = {cid: reasons[cid] for cid in out["sampled"]}
    return out
