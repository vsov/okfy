"""Crosswalk rows between workspace members (ADR-0011). Two layers:
same-as (GlossaryTerm equivalence -> query expansion) and constrains/related
(concept links -> deterministic auto-pull). Stored as type: Crosswalk concepts
in the workspace's links/ directory, rows in frontmatter.

ADR 0018 adds a third kind, `conflicts`: an owner-declared contradiction
between two refs. It is symmetric, drives no expansion, no merge and no pull,
and carries a required `reason` plus an optional `scope` and `resolution`.
Two readers: `load_rows` is TOLERANT (federation, `workspace_status` and the
release fold depend on that and stay byte-identical); `read_rows` is the
VALIDATING reader the pre-edit lookup uses — a row with a problem comes back as
a named diagnostic, never dropped silently and never coerced to `related`."""
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

from okfy import frontmatter
from okfy.bm25 import tokenize
from okfy.bundle import Bundle
from okfy.index import load_index
from okfy.pathscope import path_scope_problems
from okfy.workspace import Workspace

RELS = {"same-as", "constrains", "related", "conflicts"}
STATUSES = {"accepted", "proposed"}
LEGACY_KEYS = ("src", "rel", "dst", "status", "origin")
OPTIONAL_KEYS = ("reason", "scope", "resolution")     # ADR 0018; `conflicts` rows
RESOLUTION_KEYS = {"decision", "scope"}


@dataclass
class Row:
    src: str      # "member:concept-id" ; for constrains: the CONSTRAINT side
    rel: str
    dst: str      # "member:concept-id" ; for constrains: the KNOWLEDGE side
    status: str   # accepted | proposed
    origin: str   # alias-exact | candidate | llm | owner
    # ADR 0018 `conflicts` fields. None = unset, and an unset field is OMITTED
    # from the serialised row and from `stale_rows` dicts (`as_dict`), so every
    # links file written before this change round-trips byte for byte.
    reason: str | None = None
    scope: list | None = None
    resolution: dict | None = None

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in LEGACY_KEYS}
        for k in OPTIONAL_KEYS:
            if getattr(self, k) is not None:
                d[k] = getattr(self, k)
        return d


def parse_ref(ref: str) -> tuple[str, str]:
    if ":" not in ref:
        raise ValueError(f"bad concept ref (want member:concept-id): {ref!r}")
    member, cid = ref.split(":", 1)
    return member, cid


def crosswalk_path(ws_root: Path, a: str, b: str) -> Path:
    lo, hi = sorted([a, b])
    return Path(ws_root) / "links" / f"{lo}--{hi}.md"


def conflict_problem(reason, scope, resolution) -> tuple[str, str] | None:
    """`(diagnostic, message)` for the `conflicts`-only fields, or None. The ONE
    predicate: `read_rows` reports it as a diagnostic and `write_rows` refuses
    it, so what the writer accepts the validating reader reads back clean."""
    if not isinstance(reason, str) or not reason.strip():
        return ("conflict_without_reason",
                "a conflicts row needs a non-empty `reason`: say why the two cannot both hold")
    if scope is not None and path_scope_problems(scope):
        return ("invalid_scope", "row `scope` is outside the path_scope grammar: "
                + "; ".join(path_scope_problems(scope)))
    if resolution is not None:
        if not isinstance(resolution, dict):
            return ("invalid_resolution", "`resolution` must be a mapping {decision, scope}")
        extra = sorted(str(k) for k in resolution if k not in RESOLUTION_KEYS)
        if extra:
            return ("invalid_resolution", f"`resolution` has unknown key(s) {extra} "
                                          f"(known: {sorted(RESOLUTION_KEYS)})")
        decision = resolution.get("decision")
        if not _is_ref(decision):
            return ("invalid_resolution", "`resolution.decision` must be a member:concept-id ref")
        rscope = resolution.get("scope")
        if not isinstance(rscope, list) or not rscope or path_scope_problems(rscope):
            return ("invalid_resolution", "`resolution.scope` must be a non-empty list in the "
                                          "path_scope grammar")
    return None


def _is_ref(x) -> bool:
    if not isinstance(x, str) or ":" not in x:
        return False
    member, cid = x.split(":", 1)
    return bool(member.strip()) and bool(cid.strip())


def write_rows(ws_root: Path, a: str, b: str, rows: list[Row], note: str = "") -> Path:
    for r in rows:
        if r.rel not in RELS:
            raise ValueError(f"bad rel {r.rel!r} (use: {sorted(RELS)})")
        if r.status not in STATUSES:
            raise ValueError(f"bad status {r.status!r}")
        parse_ref(r.src), parse_ref(r.dst)
        if r.rel == "conflicts":
            problem = conflict_problem(r.reason, r.scope, r.resolution)
            if problem:
                raise ValueError(f"bad conflicts row {r.src} -> {r.dst}: {problem[1]} "
                                 f"({problem[0]})")
    p = crosswalk_path(ws_root, a, b)
    p.parent.mkdir(exist_ok=True)
    lo, hi = sorted([a, b])
    p.write_text(frontmatter.serialize(
        {"type": "Crosswalk", "title": f"Crosswalk {lo} -- {hi}",
         "members": [lo, hi],
         "rows": [r.as_dict() for r in rows]},
        note or "Reviewed cross-bundle links.\n"), encoding="utf-8")
    return p


def _enum_shape_problem(d: dict) -> tuple[str, str] | None:
    """`(diagnostic, message)` when `rel` or `status` is not a string (R21-8), else
    None. The ONE predicate for both readers, and it must run BEFORE any set
    membership test: `[x] in RELS` raises TypeError (unhashable), and a list or
    mapping in a Row would crash every later `(src, rel, dst)` key."""
    if not isinstance(d.get("rel"), str):
        return ("malformed_rel", f"rel must be a string, got {type(d.get('rel')).__name__}")
    if not isinstance(d.get("status"), str):
        return ("malformed_status",
                f"status must be a string, got {type(d.get('status')).__name__}")
    return None


def load_rows(ws: Workspace) -> list[Row]:
    """The tolerant loader. A row whose `rel`/`status` is not a string is skipped
    (the validating reader names it); no other check is made here."""
    out: list[Row] = []
    links = ws.root / "links"
    if not links.is_dir():
        return out
    for p in sorted(links.glob("*.md")):
        meta, _ = frontmatter.parse(p.read_text(encoding="utf-8"))
        for d in meta.get("rows", []):
            if isinstance(d, dict) and _enum_shape_problem(d):
                continue
            out.append(Row(d["src"], d["rel"], d["dst"], d["status"], d["origin"],
                           d.get("reason"), d.get("scope"), d.get("resolution")))
    return out


def _diagnostic(name: str, file: str, detail: str, d=None) -> dict:
    """A row problem as data. `src`/`rel`/`dst` identify the row when it can be
    attributed (both refs well formed, `rel` a string); `raw_*` always carry what
    the file said, for the caller that must not lose the row."""
    ok = (isinstance(d, dict) and _is_ref(d.get("src")) and _is_ref(d.get("dst"))
          and isinstance(d.get("rel"), str))
    raw = d if isinstance(d, dict) else {}
    return {"diagnostic": name, "file": file, "detail": detail,
            "src": d["src"] if ok else None, "rel": d["rel"] if ok else None,
            "dst": d["dst"] if ok else None,
            "status": raw.get("status") if isinstance(raw.get("status"), str) else None,
            "raw_src": raw.get("src"), "raw_dst": raw.get("dst")}


def _check_row(d, file: str) -> tuple[Row | None, dict | None]:
    """Validate one raw row mapping: `(Row, None)` or `(None, diagnostic)`. The
    first problem wins, in this order: missing_field, malformed_ref, malformed_rel,
    malformed_status (non-string `rel`/`status`, checked before any membership test),
    unknown_rel, unknown_status, then the `conflicts`-only checks."""
    if not isinstance(d, dict):
        return None, _diagnostic("missing_field", file, "a row must be a mapping")
    missing = [k for k in LEGACY_KEYS if d.get(k) is None]
    if missing:
        return None, _diagnostic("missing_field", file, f"row lacks {missing}", d)
    if not (_is_ref(d["src"]) and _is_ref(d["dst"])):
        return None, _diagnostic("malformed_ref", file,
                                 "src and dst must be member:concept-id refs", d)
    shape = _enum_shape_problem(d)
    if shape:
        return None, _diagnostic(shape[0], file, shape[1], d)
    if d["rel"] not in RELS:
        return None, _diagnostic("unknown_rel", file,
                                 f"rel {d['rel']!r} is not one of {sorted(RELS)}", d)
    if d["status"] not in STATUSES:
        return None, _diagnostic("unknown_status", file,
                                 f"status {d['status']!r} is not one of {sorted(STATUSES)}", d)
    if not isinstance(d["origin"], str):
        return None, _diagnostic("missing_field", file, "origin must be a string", d)
    if d["rel"] == "conflicts":
        problem = conflict_problem(d.get("reason"), d.get("scope"), d.get("resolution"))
        if problem:
            return None, _diagnostic(problem[0], file, problem[1], d)
    return Row(d["src"], d["rel"], d["dst"], d["status"], d["origin"],
               d.get("reason"), d.get("scope"), d.get("resolution")), None


def read_rows_located(ws: Workspace) -> tuple[list[tuple[Row, str]], list[dict]]:
    """The validating reader, with the file each row came from (a path relative
    to the workspace root, POSIX). A file the tolerant loader refuses (a
    duplicate key, bad YAML, unreadable bytes) makes the WHOLE file's rows
    unavailable and is one `duplicate_key` diagnostic with `src`/`rel`/`dst`
    None: its rows cannot be attributed."""
    rows: list[tuple[Row, str]] = []
    diags: list[dict] = []
    links = ws.root / "links"
    if not links.is_dir():
        return rows, diags
    for p in sorted(links.glob("*.md")):
        rel_file = f"links/{p.name}"
        try:
            meta, _ = frontmatter.parse(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:       # FrontmatterError, UnicodeDecodeError
            diags.append(_diagnostic("duplicate_key", rel_file,
                                     f"the file cannot be loaded ({e})"))
            continue
        raw_rows = meta.get("rows", [])
        if not isinstance(raw_rows, list):
            diags.append(_diagnostic("missing_field", rel_file, "`rows` is not a list"))
            continue
        for d in raw_rows:
            row, diag = _check_row(d, rel_file)
            if diag is not None:
                diags.append(diag)
            else:
                rows.append((row, rel_file))
    return rows, diags


def read_rows(ws: Workspace) -> tuple[list[Row], list[dict]]:
    """`(rows, diagnostics)`: the validating reader (ADR 0018, Declared
    conflicts). Diagnostic names: unknown_rel, unknown_status, malformed_rel, malformed_status, malformed_ref,
    missing_field, conflict_without_reason, invalid_scope, invalid_resolution,
    duplicate_key. `load_rows` stays the tolerant loader."""
    located, diags = read_rows_located(ws)
    return [r for r, _ in located], diags


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
