"""Lexicon rows as the retrieval contract (ADR-0013). meta/lexicon.md keeps a
single file: YAML frontmatter rows are the source of truth, the body a human
rendering. Rows drive deterministic query expansion: accepted pins + canonical
terms, ambiguous / not-covered surface as notes instead of silent noise.
Pre-rows lexicons (no rows key) stay valid — expansion is a no-op."""
from okfy.bundle import Bundle

STATUSES = {"accepted", "ambiguous", "not-covered"}


def load_rows(bundle: Bundle) -> list[dict]:
    c = bundle.get("meta/lexicon")
    if c is None:
        return []
    rows = c.meta.get("rows") or []
    if not isinstance(rows, list):
        raise ValueError("lexicon rows must be a list")
    for r in rows:
        status = r.get("status") if isinstance(r, dict) else None
        if status not in STATUSES:
            term = r.get("term") if isinstance(r, dict) else r
            raise ValueError(f"lexicon row for term {term!r}: bad status {status!r} "
                             f"(use: {sorted(STATUSES)})")
    return rows


def expand(rows: list[dict], text: str) -> dict:
    """Match row terms against the query (case-insensitive substring). Longest
    terms match first and consume their spans: shorter terms inside a claimed
    span do not fire. Effects apply in row order; pins/extra_terms dedup
    preserving first-seen order.

    Known ceiling: substring matching can fire inside a longer word ("вол"
    matching "револьвер"). Multilingual word-boundary detection is easy to
    overcomplicate and Cyrillic/Latin rules differ — do not fix speculatively;
    revisit only when a real false positive shows up in an eval run."""
    low = text.lower()
    claimed: list[tuple[int, int]] = []
    hits: set[int] = set()
    for i in sorted(range(len(rows)), key=lambda i: -len(str(rows[i].get("term") or ""))):
        term = str(rows[i].get("term") or "").lower()
        pos = 0
        while term and (at := low.find(term, pos)) != -1:
            end = at + len(term)
            if not any(a < end and at < b for a, b in claimed):
                claimed.append((at, end))
                hits.add(i)
            pos = at + 1
    pins: list[str] = []
    extra: list[str] = []
    notes: list[str] = []
    matched: list[str] = []
    for i, r in enumerate(rows):
        if i not in hits:
            continue
        term, status = r.get("term"), r.get("status")
        matched.append(term)
        if status == "accepted":
            for c in r.get("maps_to") or []:
                if c not in pins:
                    pins.append(c)
            for t in r.get("canonical_terms") or []:
                if t not in extra:
                    extra.append(t)
        elif status == "ambiguous":
            cands = r.get("maps_to") or []
            tail = f"candidates: {', '.join(cands)}" if cands else "no candidates listed"
            notes.append(f'term "{term}" ambiguous — {tail}')
        elif status == "not-covered":
            notes.append(f'term "{term}" not covered by this bundle')
        else:
            raise ValueError(f"lexicon row for term {term!r}: bad status {status!r}")
    return {"pins": pins, "extra_terms": extra, "notes": notes, "matched_terms": matched,
            "expanded_query": f"{text} {' '.join(extra)}" if extra else text}
