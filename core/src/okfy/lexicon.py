"""Lexicon rows as the retrieval contract (ADR-0013). meta/lexicon.md keeps a
single file: YAML frontmatter rows are the source of truth, the body a human
rendering. Rows drive deterministic query expansion: accepted pins + canonical
terms, ambiguous / not-covered surface as notes instead of silent noise.
Pre-rows lexicons (no rows key) stay valid — expansion is a no-op."""
from okfy.bundle import Bundle

STATUSES = {"accepted", "ambiguous", "not-covered"}


TEXT_FIELDS = ("term", "language", "note")
LIST_FIELDS = ("maps_to", "canonical_terms")


def row_problems(row) -> list[str]:
    """The row schema, in ONE place.

    `validate` reports these and `load_rows` refuses them, and both must agree —
    checking the container in one module and the elements in the other is how
    `canonical_terms: [123]` reached `' '.join(extra)` and made a query raise
    TypeError on an otherwise accepted bundle. The shape lives here; whether a
    `maps_to` target actually exists is a bundle-level question and stays in
    validate, which is the layer that knows the concept ids."""
    if not isinstance(row, dict):
        return [f"row must be a mapping, got {type(row).__name__}: {row!r}"]
    out = []
    term = row.get("term")
    if not isinstance(term, str) or not term.strip():
        out.append(f"row has no usable term: {row!r}")
    if row.get("status") not in STATUSES:
        out.append(f"row {term!r}: bad status {row.get('status')!r} "
                   f"(use: {sorted(STATUSES)})")
    for f in TEXT_FIELDS[1:]:
        if f in row and not isinstance(row[f], str):
            out.append(f"row {term!r}: {f} must be a string, got "
                       f"{type(row[f]).__name__}")
    for f in LIST_FIELDS:
        v = row.get(f)
        if v is None:
            continue
        # expand() iterates these, so a scalar does not degrade gracefully: a
        # string maps_to yields one hard pin per character
        if not isinstance(v, list):
            out.append(f"row {term!r}: {f} must be a list, got "
                       f"{type(v).__name__} {v!r} — a scalar expands character "
                       "by character")
            continue
        # ...and every element is interpolated into a query or used as a concept
        # id, so a non-string element is a crash waiting for the query that
        # matches this term
        for i, el in enumerate(v):
            if not isinstance(el, str) or not el.strip():
                out.append(f"row {term!r}: {f}[{i}] must be a non-empty "
                           f"string, got {type(el).__name__} {el!r}")
    return out


def load_rows(bundle: Bundle) -> list[dict]:
    c = bundle.get("meta/lexicon")
    if c is None:
        return []
    rows = c.meta.get("rows") or []
    if not isinstance(rows, list):
        raise ValueError("lexicon rows must be a list")
    for r in rows:
        problems = row_problems(r)
        if problems:
            raise ValueError("lexicon: " + "; ".join(problems))
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
