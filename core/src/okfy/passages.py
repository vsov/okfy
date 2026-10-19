"""Opt-in passage selector for `query` (v0.31 fragment retrieval).

The owning implementation of the v0.31 fragment contract (local design notes, not shipped).
`passage_query` ranks with `query.query` unchanged, then, for the first
PASSAGE_K hits only, returns exact source text from one raw-bytes capture per
concept file. It never re-ranks, writes nothing, and keeps the hit list, the
expanded query and the lexicon notes whatever the budget does.

Rules the contract leaves open, chosen here:
- Line numbers are 1-based over the WHOLE newline-normalized file, frontmatter
  included. The frontmatter is never part of a block. Blank lines at the start
  and end of a block are trimmed.
- A section is a heading line plus the lines up to the next ATX heading of any
  level, so nested sections are separate blocks. Text before the first heading
  is the preamble, with heading_path []. A preamble with no text is no block.
- A unit is a run of non-blank, non-heading lines, or a whole fenced block. A
  fence line starts a new run. An unclosed fence ends at the last non-blank
  line of the body.
- heading_path is the heading chain in force at the extract's FIRST line (`[]`
  for a preamble line). A block that spans sections is quoted whole and gets
  `spans_sections: true` when a heading line lies inside it after its first line.
- The best unit is the one with the most distinct query terms; ties go to the
  earliest in document order. A unit with no term is never selected.
- Growing a truncated window: try the next unit after the window, then the one
  before it, and repeat while the extract fits the remaining budget.
- A unit larger than the full budget is cut to the longest line prefix that
  fits the REMAINING budget. If no line fits, the hit is budget-omitted.
- Dedupe compares the real path of the captured file, so two concept ids that
  name one file (a symlink) cannot both be quoted over the same lines. The
  overlap check runs before any budget is spent.
- `source_changed_since_index` is set on the hit, not inside the fragment.
- `passage_terms.raw` are the query terms of the user's own text;
  `passage_terms.lexicon` are the expanded terms the user did not type.
- Bytes that are not UTF-8 give `not_utf8`. A captured file whose frontmatter
  does not parse gives `unreadable`, as does a file that cannot be read.

The CLI calls `passage_query`. The MCP adapter calls `check_request` before the
query, then `select_passages` on the hits it has already shaped with
`render.shape_hits`; only the shown hits can receive a fragment. The adapter
reads its default budget from here and contains no selection logic.
"""
import hashlib
import os
import re
from pathlib import Path

from okfy import frontmatter
from okfy import query as rank
from okfy.bm25 import query_terms, tokenize
from okfy.index import load_index, source_fingerprint
from okfy.release import retrieval_code_digest
from okfy.tokens import count_tokens, token_method
from okfy.workspace import is_workspace

DEFAULT_PASSAGE_TOKENS = 1200
MIN_PASSAGE_TOKENS = 100
MAX_PASSAGE_TOKENS = 8000
PASSAGE_K = 3
PASSAGES_NOTICE = ("Fragments are source text that shares query terms with the "
                   "question. They do not prove that the bundle answers it. Read "
                   "the whole concept when the answer depends on exceptions, "
                   "conditions or steps.")

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?=[ \t]|$)(.*)$")
_CLOSING_TAIL = re.compile(r"(?:^|[ \t]+)#+$")


class PassageError(ValueError):
    """A coded refusal. `str()` starts with the code, as the other coded
    errors do, and `code` carries it for the surfaces."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def validate_passage_tokens(value) -> int:
    """Refuse anything that is not an int in [MIN, MAX]. A bool is an int in
    Python, so it is refused explicitly. Nothing is clamped."""
    if isinstance(value, bool) or not isinstance(value, int) \
            or not MIN_PASSAGE_TOKENS <= value <= MAX_PASSAGE_TOKENS:
        raise PassageError(
            "E_PASSAGE_BUDGET",
            f"passage_tokens {value!r} is not an integer from {MIN_PASSAGE_TOKENS} "
            f"to {MAX_PASSAGE_TOKENS} — pass an integer from {MIN_PASSAGE_TOKENS} to "
            f"{MAX_PASSAGE_TOKENS}, or omit it for the default {DEFAULT_PASSAGE_TOKENS}")
    return value


def passage_code_digest() -> str:
    """sha256 of this module's bytes: the selector's code identity."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def passage_identity(passage_tokens: int) -> dict:
    """The content-delivery identity recorded beside experiment evidence. The
    retrieval digest is an input: this module consumes `bm25.tokenize` and the
    lexicon output through the query path."""
    return {"code_sha256": passage_code_digest(), "k": PASSAGE_K,
            "passage_tokens": passage_tokens, "token_method": token_method(),
            "retrieval_code_digest": retrieval_code_digest()}


def _capture(path: Path) -> bytes:
    """The only read of a concept file during a passages request."""
    return path.read_bytes()


def _extract(lines: list[str], start: int, end: int) -> str:
    """The exact text of normalized lines start..end (1-based, inclusive)."""
    return "\n".join(lines[start - 1:end])


def _title(text: str) -> str:
    return _CLOSING_TAIL.sub("", text.strip()).strip()


def _closes(text: str, char: str, length: int) -> bool:
    m = re.match(r"^ {0,3}(`{3,}|~{3,})[ \t]*$", text)
    return bool(m) and m.group(1)[0] == char and len(m.group(1)) >= length


def _parse(lines: list[str], first: int):
    """Sections and body bounds of lines[first:], 1-based numbering.

    Returns (sections, (body_start, body_end)) or (sections, None) when the
    body has no non-blank line. Each section: start, end, path, units, and
    heading (the heading's line, None for the preamble)."""
    sections: list[dict] = []
    stack: list[tuple[int, str]] = []
    cur = {"start": None, "end": None, "path": [], "units": [], "heading": None}
    sections.append(cur)
    run: list[int] | None = None
    fence: tuple[str, int, int] | None = None
    body = [None, None]

    def mark(ln: int) -> None:
        if body[0] is None:
            body[0] = ln
        body[1] = ln
        if cur["start"] is None:
            cur["start"] = ln
        cur["end"] = ln

    for ln in range(first + 1, len(lines) + 1):
        text = lines[ln - 1]
        if fence is not None:
            if text.strip():
                mark(ln)
            if _closes(text, fence[0], fence[1]):
                cur["units"].append((fence[2], ln))
                fence = None
            continue
        if not text.strip():
            if run is not None:
                cur["units"].append(tuple(run))
                run = None
            continue
        f = _FENCE_RE.match(text)
        if f:
            if run is not None:
                cur["units"].append(tuple(run))
                run = None
            mark(ln)
            fence = (f.group(1)[0], len(f.group(1)), ln)
            continue
        h = _HEADING_RE.match(text)
        if h:
            if run is not None:
                cur["units"].append(tuple(run))
                run = None
            level = len(h.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, _title(h.group(2))))
            cur = {"start": None, "end": None, "path": [t for _, t in stack], "units": [],
                   "heading": ln}
            sections.append(cur)
            mark(ln)
            continue
        if run is None:
            run = [ln, ln]
        else:
            run[1] = ln
        mark(ln)
    if run is not None:
        cur["units"].append(tuple(run))
    if fence is not None:
        cur["units"].append((fence[2], body[1]))
    sections = [s for s in sections if s["start"] is not None]
    return sections, (None if body[0] is None else (body[0], body[1]))


def _doc(raw: bytes):
    """(lines, sections, body) of one captured file. Decoding and frontmatter
    errors propagate to the caller, which names them as reasons."""
    text = raw.decode("utf-8")
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = norm.split("\n") if norm else []
    if norm.endswith("\n"):
        lines.pop()
    _, body_text = frontmatter.parse(norm)
    first = norm.count("\n", 0, len(norm) - len(body_text))
    sections, body = _parse(lines, first)
    return lines, sections, body


class _Pick:
    __slots__ = ("state", "reason", "lines", "start", "end", "path", "spans")

    def __init__(self, state, reason, lines=None, start=0, end=0, path=None, spans=False):
        self.state, self.reason = state, reason
        self.lines, self.start, self.end, self.path = lines, start, end, path
        self.spans = spans


def _chain_at(sections: list[dict], line: int) -> list[str]:
    """The heading chain in force at `line`: the path of the last section whose
    heading is at or above it. Text before the first heading gives []."""
    chain: list[str] = []
    for sec in sections:
        if sec["heading"] is not None and sec["heading"] > line:
            break
        chain = sec["path"]
    return chain


def _spans_sections(sections: list[dict], start: int, end: int) -> bool:
    """True when a heading line lies inside start..end after the first line."""
    return any(sec["heading"] is not None and start < sec["heading"] <= end
               for sec in sections)


def _fitting_prefix(lines: list[str], start: int, end: int, room: int) -> int:
    """The longest prefix length k with the extract of lines start..start+k-1
    fitting `room`. Every value the search keeps was verified to fit."""
    lo, hi = 0, end - start + 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count_tokens(_extract(lines, start, start + mid - 1)) <= room:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _pick(raw: bytes, qset: set[str], full: int, remaining: int) -> _Pick:
    try:
        lines, sections, body = _doc(raw)
    except UnicodeDecodeError:
        return _Pick(None, "not_utf8")
    except ValueError:
        return _Pick(None, "unreadable")
    if body is None:
        return _Pick(None, "empty_body")
    best = None
    for si, sec in enumerate(sections):
        for ui, (s, e) in enumerate(sec["units"]):
            score = len(qset & set(tokenize(_extract(lines, s, e))))
            if score and (best is None or score > best[0]):
                best = (score, si, ui)
    if best is None:
        return _Pick(None, "no_block_matched")
    _, si, ui = best
    sec = sections[si]

    def fits(start: int, end: int, room: int) -> bool:
        return count_tokens(_extract(lines, start, end)) <= room

    def located(state: str, start: int, end: int) -> _Pick:
        # heading_path is the chain at the extract's first line, not the best
        # unit's section (contract c); spans_sections is a heading inside it.
        return _Pick(state, None, lines, start, end, _chain_at(sections, start),
                     _spans_sections(sections, start, end))

    body_start, body_end = body
    if fits(body_start, body_end, full):
        if fits(body_start, body_end, remaining):
            return located("quoted", body_start, body_end)
        return _Pick(None, "budget")
    if fits(sec["start"], sec["end"], remaining):
        return located("quoted", sec["start"], sec["end"])
    start, end = sec["units"][ui]
    if fits(start, end, full):
        if not fits(start, end, remaining):
            return _Pick(None, "budget")
        units = sec["units"]
        i = j = ui
        while True:
            if j + 1 < len(units) and fits(units[i][0], units[j + 1][1], remaining):
                j += 1
            elif i > 0 and fits(units[i - 1][0], units[j][1], remaining):
                i -= 1
            else:
                break
        return located("truncated", units[i][0], units[j][1])
    k = _fitting_prefix(lines, start, end, remaining)
    if k == 0:
        return _Pick(None, "budget")
    return located("truncated", start, start + k - 1)


def _none(hit: dict, reason: str) -> None:
    hit["passage_state"] = "none_selected"
    hit["passage_reason"] = reason


def check_request(root: Path, passage_tokens) -> int:
    """The refusals every passages request makes before any query runs and
    before any concept file is read: a workspace root, then the budget. Returns
    the validated budget."""
    if is_workspace(Path(root)):
        raise PassageError(
            "E_PASSAGES_WORKSPACE",
            "passages read one bundle's concept files, and a workspace has no single "
            "concept set — point the server at a member bundle's own path, or drop "
            "passages for the federated result")
    return validate_passage_tokens(passage_tokens)


def bundle_fingerprint(bundle) -> str:
    """The bundle-wide fingerprint taken before a request (contract g)."""
    return source_fingerprint(bundle)


def passage_query(bundle, text: str, *, type_: str | None = None, tag: str | None = None,
                  n: int = 10, include_meta: bool = False, expand: bool = True,
                  include_stale: bool = True,
                  passage_tokens: int = DEFAULT_PASSAGE_TOKENS) -> dict:
    """Ranked concepts (query.query, unchanged) with passages for the top
    PASSAGE_K. Refusals (E_PASSAGES_WORKSPACE, E_PASSAGE_BUDGET) come before
    any query runs and before any concept file is read."""
    check_request(bundle.root, passage_tokens)
    fp_before = bundle_fingerprint(bundle)
    out = rank.query(bundle, text, type_=type_, tag=tag, n=n,
                     include_meta=include_meta, expand=expand,
                     include_stale=include_stale)
    out.update(select_passages(bundle, text, out, fp_before=fp_before,
                               passage_tokens=passage_tokens, type_=type_, tag=tag,
                               include_meta=include_meta, include_stale=include_stale))
    return out


def select_passages(bundle, text: str, out: dict, *, fp_before: str,
                    passage_tokens: int, type_: str | None = None,
                    tag: str | None = None, include_meta: bool = False,
                    include_stale: bool = True) -> dict:
    """The selection step over an already ranked list. Only `out["results"]` is
    considered, in the order given; the top PASSAGE_K of THAT list are candidates.
    `out` is a query.query() result, or the same with `results` already shaped by
    the caller (the MCP adapter passes only the hits it shows). Nothing is
    re-ranked and the query is not run. Hits are updated in place; the return
    value is the response-level keys (passages_omitted, passage_budget,
    passages_notice)."""
    full = validate_passage_tokens(passage_tokens)
    pool = rank.filter_pool(load_index(bundle), type_, tag, include_meta, include_stale)
    domain = rank.domain_tokens(pool, text)
    raw_terms = set(query_terms(text, domain))
    all_terms = set(query_terms(out["expanded_query"], domain))
    lexicon_terms = all_terms - raw_terms

    captured: dict[str, bytes | None] = {}
    kept: list[tuple[str, int, int]] = []
    omitted: list[dict] = []
    remaining = full
    for rank_no, hit in enumerate(out["results"]):
        if rank_no >= PASSAGE_K:
            _none(hit, "outside_top_k")
            continue
        rel = f"{hit['id']}.md"
        path = bundle.root / rel
        real = os.path.realpath(path)
        if real not in captured:
            try:
                captured[real] = _capture(path)
            except OSError:
                captured[real] = None
        raw = captured[real]
        if raw is None:
            _none(hit, "unreadable")
            continue
        pick = _pick(raw, all_terms, full, remaining)
        if pick.state is None:
            _none(hit, pick.reason)
            if pick.reason == "budget":
                omitted.append({"id": hit["id"], "reason": "budget"})
            continue
        if any(real == r and pick.start <= e and s <= pick.end for r, s, e in kept):
            _none(hit, "overlaps_higher_hit")
            continue
        extract = _extract(pick.lines, pick.start, pick.end)
        remaining -= count_tokens(extract)
        kept.append((real, pick.start, pick.end))
        tokens = set(tokenize(extract))
        hit["passage_state"] = pick.state
        fragment = {"concept_id": hit["id"], "path": rel,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "lines": [pick.start, pick.end],
                    "heading_path": list(pick.path)}
        if pick.spans:
            fragment["spans_sections"] = True
        fragment["extract"] = extract
        hit["fragment"] = fragment
        hit["passage_terms"] = {"raw": sorted(raw_terms & tokens),
                                "lexicon": sorted(lexicon_terms & tokens)}
    if source_fingerprint(bundle) != fp_before:
        for hit in out["results"]:
            if "fragment" in hit:
                hit["source_changed_since_index"] = True
    return {"passages_omitted": omitted,
            "passage_budget": {"tokens": full, "k": PASSAGE_K, "method": token_method()},
            "passages_notice": PASSAGES_NOTICE}
