"""Risk-oriented deterministic sampling for the L3 purpose-fitness pass.

Split out of `query.py` deliberately. `retrieval_fingerprint` hashes the bytes of
the modules that decide what a query returns, and L3 sample selection is not one
of them — leaving it in `query.py` meant tuning the selector, or editing a comment
beside it, invalidated every recorded eval on every bundle for a change that
cannot move a single retrieval result."""
import hashlib
import math

from okfy.bundle import Bundle

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
