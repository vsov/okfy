"""Deterministic pre-clustering of Draft Concepts (ADR-0008): same type AND
similar title. The LLM merge judge decides within clusters; this only groups."""
from okfy.bm25 import tokenize
from okfy.bundle import Bundle


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def cluster_drafts(bundle: Bundle, threshold: float = 0.6) -> list[list[str]]:
    drafts = [c for c in bundle.concepts(include_drafts=True)
              if c.id.startswith("drafts/")]
    drafts.sort(key=lambda c: c.id)
    n = len(drafts)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        parent[find(i)] = find(j)

    toks = [set(tokenize(str(c.meta.get("title", "")))) for c in drafts]
    for i in range(n):
        for j in range(i + 1, n):
            if drafts[i].meta.get("type") != drafts[j].meta.get("type"):
                continue
            if toks[i] == toks[j] or _jaccard(toks[i], toks[j]) >= threshold:
                union(i, j)

    groups: dict[int, list[str]] = {}
    for i, c in enumerate(drafts):
        groups.setdefault(find(i), []).append(c.id)
    return sorted([sorted(g) for g in groups.values()])
