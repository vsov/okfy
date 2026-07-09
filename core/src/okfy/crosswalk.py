"""Crosswalk rows between workspace members (ADR-0011). Two layers:
same-as (GlossaryTerm equivalence -> query expansion) and constrains/related
(concept links -> deterministic auto-pull). Stored as type: Crosswalk concepts
in the workspace's links/ directory, rows in frontmatter."""
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

from okfy import frontmatter
from okfy.bm25 import tokenize
from okfy.bundle import Bundle
from okfy.index import load_index
from okfy.workspace import Workspace

RELS = {"same-as", "constrains", "related"}
STATUSES = {"accepted", "proposed"}


@dataclass
class Row:
    src: str      # "member:concept-id" ; for constrains: the CONSTRAINT side
    rel: str
    dst: str      # "member:concept-id" ; for constrains: the KNOWLEDGE side
    status: str   # accepted | proposed
    origin: str   # alias-exact | candidate | llm


def parse_ref(ref: str) -> tuple[str, str]:
    if ":" not in ref:
        raise ValueError(f"bad concept ref (want member:concept-id): {ref!r}")
    member, cid = ref.split(":", 1)
    return member, cid


def crosswalk_path(ws_root: Path, a: str, b: str) -> Path:
    lo, hi = sorted([a, b])
    return Path(ws_root) / "links" / f"{lo}--{hi}.md"


def write_rows(ws_root: Path, a: str, b: str, rows: list[Row], note: str = "") -> Path:
    for r in rows:
        if r.rel not in RELS:
            raise ValueError(f"bad rel {r.rel!r} (use: {sorted(RELS)})")
        if r.status not in STATUSES:
            raise ValueError(f"bad status {r.status!r}")
        parse_ref(r.src), parse_ref(r.dst)
    p = crosswalk_path(ws_root, a, b)
    p.parent.mkdir(exist_ok=True)
    lo, hi = sorted([a, b])
    p.write_text(frontmatter.serialize(
        {"type": "Crosswalk", "title": f"Crosswalk {lo} -- {hi}",
         "members": [lo, hi],
         "rows": [r.__dict__ for r in rows]},
        note or "Reviewed cross-bundle links.\n"), encoding="utf-8")
    return p


def load_rows(ws: Workspace) -> list[Row]:
    out: list[Row] = []
    links = ws.root / "links"
    if not links.is_dir():
        return out
    for p in sorted(links.glob("*.md")):
        meta, _ = frontmatter.parse(p.read_text(encoding="utf-8"))
        for d in meta.get("rows", []):
            out.append(Row(d["src"], d["rel"], d["dst"], d["status"], d["origin"]))
    return out


FUZZY_THRESHOLD = 0.85


def _norms(entry: dict) -> set[str]:
    """Normalized title+alias strings of an index entry."""
    out = {" ".join(tokenize(str(entry.get("title", ""))))}
    for a in entry.get("aliases", []) or []:
        out.add(" ".join(tokenize(str(a))))
    return {s for s in out if s}


def candidates(ws: Workspace) -> list[Row]:
    """Deterministic same-as candidates between every member pair.
    Exact title/alias intersection -> accepted (origin alias-exact);
    fuzzy title similarity >= 0.85 -> proposed (origin candidate).
    Zero-token semantic pairs are the LLM judge's job (plugin), not ours."""
    idx = {m.name: load_index(Bundle(m.path)) for m in ws.members}
    rows: list[Row] = []
    for a, b in combinations([m.name for m in ws.members], 2):
        for ca in idx[a]["concepts"]:
            if ca["id"].startswith("meta/"):
                continue
            na = _norms(ca)
            ta = " ".join(tokenize(str(ca.get("title", ""))))
            for cb in idx[b]["concepts"]:
                if cb["id"].startswith("meta/"):
                    continue
                nb = _norms(cb)
                if na & nb:
                    rows.append(Row(f"{a}:{ca['id']}", "same-as", f"{b}:{cb['id']}",
                                    "accepted", "alias-exact"))
                    continue
                tb = " ".join(tokenize(str(cb.get("title", ""))))
                if ta and tb and SequenceMatcher(None, ta, tb).ratio() >= FUZZY_THRESHOLD:
                    rows.append(Row(f"{a}:{ca['id']}", "same-as", f"{b}:{cb['id']}",
                                    "proposed", "candidate"))
    return rows
