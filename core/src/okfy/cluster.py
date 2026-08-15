"""Deterministic pre-clustering of Draft Concepts (ADR-0008): same type AND
either a similar title or one draft naming the other. The LLM merge judge
decides within clusters; this only groups.

Title similarity alone cannot see a cross-language pair, and workers are
required to record cross-language equivalents in `aliases` (extract-worker rule
2): an RU draft titled "Непокрытая продажа опционов" and an EN draft titled
"Uncovered Option Writing" share no token and reached consolidation as two
unrelated concepts.

The alias rule is deliberately narrow: **one draft's alias must equal the
other's TITLE**. Sharing an alias is not enough, and aliases are never pooled
into the title's token bag (that would make the rule non-monotone — a
heavily-aliased draft's Jaccard against a bare-title twin would fall below the
threshold, breaking pairs that cluster today).

Measured over the 363-, 304- and 117-concept real bundles, "any shared alias"
was overwhelmingly false: 337 pairs in one bundle bridged by `билтины` (a
category word), 311 in another by `Release No. 34-46473` (a cited authority
several definitions carry), 35 by a shared technique label. Real aliases are
not only other names for the concept — they are also categories, cited
authorities and member names. Requiring the alias to equal the other's *title*
cut those to 0, 2 and 6, and of the eleven survivors across all bundles nine
were genuine same-thing pairs (`bear-put-spread`/`put-debit-spread`,
`collar`/`protective-collar`, `funding`/`funding-rate`), one borderline and one
wrong. That precision is acceptable precisely because grouping is not merging:
a false grouping costs the judge one comparison, a missed one leaves a
permanent duplicate.
"""
from okfy.bm25 import tokenize
from okfy.bundle import Bundle


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _title_key(meta: dict) -> frozenset:
    return frozenset(tokenize(str(meta.get("title", ""))))


def _alias_keys(meta: dict) -> set[frozenset]:
    """Each alias as a whole comparable name. Blank aliases yield no key — an
    empty one would otherwise match every other concept's empty alias."""
    aliases = meta.get("aliases") or []
    aliases = aliases if isinstance(aliases, list) else [aliases]
    return {frozenset(tokenize(str(a))) for a in aliases if tokenize(str(a))}


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
    titles = [_title_key(c.meta) for c in drafts]
    aliases = [_alias_keys(c.meta) for c in drafts]
    for i in range(n):
        for j in range(i + 1, n):
            if drafts[i].meta.get("type") != drafts[j].meta.get("type"):
                continue
            if toks[i] == toks[j] or _jaccard(toks[i], toks[j]) >= threshold:
                union(i, j)
            elif titles[j] in aliases[i] or titles[i] in aliases[j]:
                # One draft answers to the other's name. Exact equality, not
                # overlap: "this is also called X" is an assertion about the
                # whole name, and two multi-word aliases sharing a word is not
                # that assertion.
                union(i, j)

    groups: dict[int, list[str]] = {}
    for i, c in enumerate(drafts):
        groups.setdefault(find(i), []).append(c.id)
    return sorted([sorted(g) for g in groups.values()])
