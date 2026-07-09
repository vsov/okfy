"""Layers 1-2 of the 4-layer validation. Layers 3-4 are LLM work, orchestrated by the plugin."""
import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from okfy import frontmatter
from okfy.bundle import Bundle
from okfy.lexicon import STATUSES
from okfy.update import _embedded_prefix, _source_path

DATE_HEADING_RE = re.compile(r"^## (.+)$", re.MULTILINE)


@dataclass
class Finding:
    level: str  # "error" | "warning"
    code: str
    path: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    sources: dict | None = None  # coverage summary, set when source checks ran

    def add(self, level, code, path, message):
        self.findings.append(Finding(level, code, str(path), message))

    @property
    def errors(self):
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self):
        return [f for f in self.findings if f.level == "warning"]

    @property
    def ok(self):
        return not self.errors

    def to_dict(self):
        d = {"ok": self.ok, "errors": len(self.errors), "warnings": len(self.warnings)}
        if self.sources is not None:
            d["sources"] = self.sources
        d["findings"] = [f.__dict__ for f in self.findings]
        return d


def validate_conformance(bundle: Bundle, include_drafts=False, include_proposals=False) -> Report:
    r = Report()
    for p in bundle.iter_md_files(include_drafts, include_proposals):
        rel = p.relative_to(bundle.root)
        try:
            meta, _ = frontmatter.parse(p.read_text(encoding="utf-8"))
        except frontmatter.FrontmatterError as e:
            r.add("error", "E_FRONTMATTER", rel, str(e))
            continue
        t = meta.get("type")
        if not isinstance(t, str) or not t.strip():
            r.add("error", "E_TYPE", rel, "frontmatter 'type' missing or empty")
    _check_reserved(bundle, r)
    return r


def _check_reserved(bundle: Bundle, r: Report):
    idx = bundle.root / "index.md"
    if idx.is_file() and idx.read_text(encoding="utf-8").startswith("---"):
        r.add("error", "E_INDEX_FRONTMATTER", "index.md", "index.md must not contain frontmatter")
    log = bundle.root / "log.md"
    if log.is_file():
        for heading in DATE_HEADING_RE.findall(log.read_text(encoding="utf-8")):
            try:
                datetime.date.fromisoformat(heading.strip())
            except ValueError:
                r.add("error", "E_LOG_DATE", "log.md", f"log heading not ISO 8601: {heading!r}")


LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
META_REQUIRED = {"purpose": ["language", "write_policy", "test_queries"],
                 "extraction-plan": ["archetype", "archetype_version"],
                 "corpus": ["corpus", "extracted_at"]}


def resolve_link(bundle: Bundle, concept_path, target: str) -> str | None:
    """Return concept id a local md link points to, or None for external/anchor-only."""
    target = target.split("#", 1)[0]
    if not target or target.startswith(("http://", "https://", "mailto:")):
        return None
    if not target.endswith(".md"):
        return None
    base = concept_path.parent if not target.startswith("/") else bundle.root
    resolved = (base / target.lstrip("/")).resolve()
    try:
        return resolved.relative_to(bundle.root).with_suffix("").as_posix()
    except ValueError:
        return None


def validate_integrity(bundle: Bundle, archetype=None, strict_sources=False,
                       strict_quality=False) -> Report:
    r = Report()
    concepts = []
    for p in bundle.iter_md_files():
        try:
            concepts.append(bundle.load(p))
        except frontmatter.FrontmatterError:
            continue  # layer 1's problem
    _check_meta(bundle, r)
    _check_collisions(concepts, r)
    _check_stale(concepts, r)
    _check_sources(bundle, concepts, r, strict=strict_sources)
    _check_lexicon(concepts, r)
    linked_ids = _check_links(bundle, concepts, r)
    _check_orphans(bundle, concepts, linked_ids, r)
    _check_quality(bundle, archetype, r, strict=strict_quality)
    for c in concepts:
        if not c.id.startswith("meta/"):
            if not c.meta.get("sources"):
                r.add("warning", "W_NO_SOURCES", c.id, "extracted concept without sources")
            if archetype:
                _check_archetype(c, archetype, r)
    return r


def _check_meta(bundle: Bundle, r: Report):
    for name, fields in META_REQUIRED.items():
        c = bundle.get(f"meta/{name}")
        if c is None:
            r.add("error", "E_META_MISSING", f"meta/{name}.md", "required meta concept missing")
            continue
        for f in fields:
            v = c.meta.get(f)
            if v in (None, "", []):
                r.add("error", "E_META_FIELD", c.id, f"meta field missing/empty: {f}")


def _check_stale(concepts, r: Report):
    """ADR-0013: stale is a reviewed decision — the flag never travels without
    its reason and since date. ERROR, not warning: this is internal consistency
    (pre-v0.5 bundles carry no stale fields at all, nothing is retroactive)."""
    for c in concepts:
        if not c.meta.get("stale"):
            continue
        if c.meta.get("stale_reason") in (None, "") or c.meta.get("stale_since") in (None, ""):
            r.add("error", "E_STALE_FIELDS", c.id,
                  "stale: true requires stale_reason and stale_since")
            continue
        try:
            datetime.date.fromisoformat(str(c.meta["stale_since"]))
        except ValueError:
            r.add("error", "E_STALE_FIELDS", c.id,
                  f"stale_since is not an ISO date: {c.meta['stale_since']!r}")


def _source_checker(bundle: Bundle, r: Report | None = None, strict: bool = False):
    """What to resolve sources: against — manifest keys when
    meta/corpus-manifest.json travels with the bundle, the corpus tree for an
    embed bundle living inside it (same detection as update._embedded_prefix),
    else None: no basis to check (Standalone Bundle — the corpus may be gone).
    Exported fusions skip too: their concepts cite member corpora, not ws.root.
    A manifest that exists but cannot be read is reported, never silently
    skipped — a corrupt file must not quietly disable the check."""
    try:
        snap = bundle.get("meta/corpus")
        purpose = bundle.purpose()
    except frontmatter.FrontmatterError:
        return None  # layer 1's problem
    if purpose.get("exported") or (snap and snap.meta.get("exported")):
        return None
    manifest_file = bundle.root / "meta" / "corpus-manifest.json"
    if manifest_file.is_file():
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None
        if not isinstance(manifest, dict):
            if r is not None:
                level, code = (("error", "E_SOURCE_MANIFEST") if strict
                               else ("warning", "W_SOURCE_MANIFEST"))
                r.add(level, code, "meta/corpus-manifest.json",
                      "corpus manifest unreadable — source checks skipped")
            return None
        return lambda p: p in manifest
    if snap and snap.meta.get("corpus"):
        corpus = Path(str(snap.meta["corpus"]))
        if corpus.is_dir() and _embedded_prefix(bundle, corpus) is not None:
            root = corpus.resolve()

            def in_tree(p: str) -> bool:
                f = (root / p).resolve()
                return f.is_relative_to(root) and f.is_file()
            return in_tree
    return None


def _check_sources(bundle: Bundle, concepts, r: Report, strict=False):
    """ADR-0013: broken sources paths signal, never flip the flag — warnings by
    default, errors only under --strict-sources (new extractions)."""
    exists = _source_checker(bundle, r, strict)
    if exists is None:
        return
    level, code = ("error", "E_BAD_SOURCE") if strict else ("warning", "W_BAD_SOURCE")
    with_sources = broken = 0
    for c in concepts:
        if c.id.startswith("meta/"):
            continue
        srcs = c.meta.get("sources") or []
        srcs = srcs if isinstance(srcs, list) else [srcs]
        if not srcs:
            continue
        with_sources += 1
        bad = [p for p in (_source_path(s) for s in srcs) if not exists(p)]
        broken += bool(bad)
        for p in bad:
            r.add(level, code, c.id, f"concept {c.id}: source not in corpus: {p}")
    r.sources = {"concepts_with_sources": with_sources,
                 "all_valid": with_sources - broken, "with_broken_paths": broken}


def _check_lexicon(concepts, r: Report):
    """ADR-0013 rows contract, tolerantly: load_rows raises on bad content at
    query time, but validate must report malformed rows, never crash on them."""
    lex = next((c for c in concepts if c.id == "meta/lexicon"), None)
    if lex is None:
        return
    rows = lex.meta.get("rows") or []
    if not isinstance(rows, list):
        r.add("warning", "W_LEXICON_STATUS", lex.id,
              f"rows must be a list, got {type(rows).__name__}")
        return
    ids = {c.id for c in concepts}
    for row in rows:
        term = row.get("term") if isinstance(row, dict) else row
        status = row.get("status") if isinstance(row, dict) else None
        if status not in STATUSES:
            r.add("warning", "W_LEXICON_STATUS", lex.id,
                  f"row {term!r}: unknown status {status!r} (use: {sorted(STATUSES)})")
        maps_to = (row.get("maps_to") if isinstance(row, dict) else None) or []
        for target in maps_to if isinstance(maps_to, list) else [maps_to]:
            if not isinstance(target, str) or target not in ids:
                r.add("warning", "W_LEXICON_TARGET", lex.id,
                      f"row {term!r}: maps_to unknown concept: {target}")


def _check_collisions(concepts, r: Report):
    seen = {}
    for c in concepts:
        key = c.id.lower()
        if key in seen:
            r.add("error", "E_ID_COLLISION", c.id, f"case-insensitive collision with {seen[key]}")
        seen[key] = c.id


def _check_links(bundle, concepts, r: Report) -> set[str]:
    ids = {c.id for c in concepts}
    linked = set()
    for c in concepts:
        for target in LINK_RE.findall(c.body):
            cid = resolve_link(bundle, c.path, target)
            if cid is None:
                continue
            if cid in ids:
                linked.add(cid)
            else:
                r.add("warning", "W_DANGLING_LINK", c.id, f"link to missing concept: {cid}")
    return linked


def _check_orphans(bundle, concepts, linked_ids, r: Report):
    idx = bundle.root / "index.md"
    indexed = set()
    if idx.is_file():
        for target in LINK_RE.findall(idx.read_text(encoding="utf-8")):
            cid = resolve_link(bundle, idx, target)
            if cid:
                indexed.add(cid)
    for c in concepts:
        if c.id.startswith("meta/"):
            continue
        if c.id not in indexed and c.id not in linked_ids:
            r.add("warning", "W_ORPHAN", c.id, "not reachable from index.md or any concept")


QUALITY_FIELDS = ["date", "prompt_version", "selector_version", "seed", "sampled"]
QUALITY_VERDICTS = {"pass", "fail", "n/a"}


def _check_quality(bundle: Bundle, archetype, r: Report, strict: bool = False):
    """PurposeFitness artifact (external review round 4): L3 must persist as a
    checkable artifact — meta/purpose-fitness.md — not a prompt instruction
    that evaporates with the transcript. Warning by default (old bundles),
    errors under --strict-quality (the bar new extractions are held to)."""
    def code(s: str) -> str:
        return ("E_" if strict else "W_") + s

    level = "error" if strict else "warning"
    c = bundle.get("meta/purpose-fitness")
    if c is None:
        r.add(level, code("QUALITY_MISSING"), "meta/purpose-fitness.md",
              "purpose-fitness artifact missing — L3 pass not persisted")
        return
    for f in QUALITY_FIELDS:
        if c.meta.get(f) in (None, "", []):
            r.add(level, code("QUALITY_FIELD"), c.id,
                  f"purpose-fitness field missing/empty: {f}")
    sampled = [str(s) for s in (c.meta.get("sampled") or [])]
    known = {x.id for x in bundle.concepts()}
    for sid in sampled:
        if sid not in known:
            r.add(level, code("QUALITY_UNKNOWN_ID"), c.id,
                  f"sampled id not in bundle: {sid}")
    checks = [str(pc.get("id")) for pc in (archetype.purpose_checks if archetype else [])]
    rows = [ln for ln in c.body.splitlines()
            if ln.lstrip().startswith("|") and not set(ln) <= set("|-: \t")]
    for sid in sampled:
        if sid not in known:
            continue
        for chk in checks:
            match = [ln for ln in rows if sid in ln and chk in ln]
            if not match:
                r.add(level, code("QUALITY_ROW"), c.id,
                      f"no verdict row for {sid} x {chk}")
            elif not any(cell.strip().lower() in QUALITY_VERDICTS
                         for ln in match for cell in ln.split("|")):
                r.add(level, code("QUALITY_VERDICT"), c.id,
                      f"row for {sid} x {chk} has no pass/fail/n-a verdict")
    # replay: if the corpus hasn't moved (seed still current) the deterministic
    # sample must be covered; a moved corpus is not replayable — skip silently.
    from okfy.query import SELECTOR_VERSION, _selector_seed, sample_for_review
    if (str(c.meta.get("seed")) == _selector_seed(bundle)
            and c.meta.get("selector_version") == SELECTOR_VERSION):
        rerun = sample_for_review(
            bundle, fraction=float(c.meta.get("fraction", 0.1)),
            minimum=int(c.meta.get("minimum", 20)))
        missing = sorted(set(rerun["sampled"]) - set(sampled))
        if missing:
            r.add(level, code("QUALITY_SAMPLE"), c.id,
                  f"recorded sample misses deterministic selection: {missing}")


def _section_text(body: str, name: str) -> str | None:
    """Text of one '## name' section (case-insensitive), None if absent."""
    m = re.search(rf"^##\s+{re.escape(name)}\s*$(.*?)(?=^##\s|\Z)", body,
                  re.IGNORECASE | re.MULTILINE | re.DOTALL)
    return m.group(1) if m else None


def _check_archetype(c, archetype, r: Report):
    required = list(archetype.required_fields.get("_all", []))
    required += archetype.required_fields.get(str(c.meta.get("type")), [])
    for f in required:
        if c.meta.get(f) in (None, "", []):
            r.add("error", "E_REQUIRED_FIELD", c.id, f"required field missing/empty: {f}")
    ctype = str(c.meta.get("type"))
    sections = archetype.required_sections.get(ctype, [])
    if sections:
        headings = {h.strip().lower() for h in HEADING_RE.findall(c.body)}
        for s in sections:
            if s.lower() not in headings:
                r.add("error", "E_REQUIRED_SECTION", c.id, f"required section missing: ## {s}")
    # deterministic value rules (ADR-0013 spirit: form checks that carry substance)
    for rule in archetype.link_rules:
        if rule.get("from") != ctype:
            continue
        scope = _section_text(c.body, rule["section"]) if rule.get("section") else c.body
        if scope is None:
            continue  # missing section already reported above
        dirs = tuple(rule.get("to_dirs", []))
        found = sum(1 for t in re.findall(r"\]\(/([^)#\s]+?)(?:\.md)?\)", scope)
                    if t.startswith(dirs))
        if found < int(rule.get("min", 1)):
            where = f"section ## {rule['section']}" if rule.get("section") else "body"
            r.add("error", "E_LINK_RULE", c.id,
                  f"{ctype} must link >= {rule.get('min', 1)} concept(s) under "
                  f"{'/'.join(dirs) if len(dirs) == 1 else dirs} in {where}; found {found}")
    for s in archetype.nonempty_sections.get(ctype, []):
        text = _section_text(c.body, s)
        if text is not None and not text.strip():
            r.add("error", "E_EMPTY_SECTION", c.id,
                  f"section ## {s} is present but empty — the heading alone carries nothing")
