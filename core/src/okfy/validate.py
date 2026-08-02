"""Layers 1-2 of the 4-layer validation. Layers 3-4 are LLM work, orchestrated by the plugin."""
import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from okfy import frontmatter
from okfy.bundle import Bundle
from okfy.lexicon import row_problems
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
                       strict_quality=False, strict_provenance=False,
                       strict_package=False, strict_execution=False,
                       strict_schema=False) -> Report:
    r = Report()
    concepts = []
    for p in bundle.iter_md_files():
        try:
            concepts.append(bundle.load(p))
        except frontmatter.FrontmatterError:
            continue  # layer 1's problem
    _check_meta(bundle, r)
    _check_write_policy(bundle, r)
    _check_acceptance(bundle, r)
    _check_adversarial(bundle, concepts, r)
    _check_types(bundle, concepts, archetype, r, strict=strict_schema)
    _check_corpus_snapshot(bundle, r, strict=strict_sources)
    _check_execution(bundle, r, strict=strict_execution)
    _check_collisions(concepts, r)
    _check_stale(concepts, r)
    _check_sources(bundle, concepts, r, strict=strict_sources)
    _check_anchors(bundle, concepts, r, strict=strict_sources)
    _check_lexicon(concepts, r)
    linked_ids = _check_links(bundle, concepts, r)
    _check_orphans(bundle, concepts, linked_ids, r, strict=strict_package)
    _check_quality(bundle, archetype, r, strict=strict_quality)
    _check_provenance(bundle, r, strict=strict_provenance)
    _check_package(bundle, r, strict=strict_package)
    for c in concepts:
        if not c.id.startswith("meta/"):
            if not c.meta.get("sources") and _sources_expected(c, archetype):
                r.add("warning", "W_NO_SOURCES", c.id, "extracted concept without sources")
            if archetype:
                _check_archetype(c, archetype, r)
    return r


def _sources_expected(c, archetype) -> bool:
    """With an archetype loaded, a missing `sources` only warns for types
    whose schema requires it — a Synthesis or GlossaryTerm without sources
    is by design, and an expected warning is noise (audit round 7)."""
    if archetype is None:
        return True
    required = set(archetype.required_fields.get("_all", []))
    required |= set(archetype.required_fields.get(str(c.meta.get("type")), []))
    return "sources" in required


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


WRITE_POLICIES = ("proposals", "direct")
# Every key release.py reads out of `acceptance`. Closed on purpose: a typo in
# `dissent` or `allow_open_dissent` silently changes which gates apply, and an
# open mapping means the misspelling reads as a policy that was never enforced.
ACCEPTANCE_KEYS = {
    "min_owner_pass": int,
    "allow_l3_fail": bool,
    "allow_open_dissent": bool,
    "min_adversarial_pass": int,
    # a tuple is an enum of permitted VALUES, not just a type. `dissent: str`
    # closed the key and left the value open, and release.py runs that gate only
    # on the exact literal `required` — so `requierd` passed the schema and
    # turned the whole dissent contract off while reading as if it were on.
    "dissent": ("required",),
}


def _check_write_policy(bundle: Bundle, r: Report):
    """`write_policy` is a trust boundary, so it is an enum, not a string.

    The pre-commit hook compares it to the exact literal `proposals`. Anything
    else — `proposal`, `PROPOSALS`, a trailing space — left the gate inert while
    the bundle read as gated. Only a non-empty value was ever required, which
    made the typo invisible at every strictness level."""
    p = bundle.get("meta/purpose")
    if p is None:
        return                                   # _check_meta already reported it
    v = p.meta.get("write_policy")
    if v in (None, "", []):
        return                                   # _check_meta reports the absence
    if str(v) not in WRITE_POLICIES:
        r.add("error", "E_WRITE_POLICY", p.id,
              f"write_policy must be one of {list(WRITE_POLICIES)}, got {v!r} — "
              "the pre-commit hook matches the literal, so a near-miss disables "
              "the gate without disabling the claim")


def _check_acceptance(bundle: Bundle, r: Report):
    """`acceptance` is the bundle's own release policy, so it is validated like
    one: closed key set, declared types, and `min_owner_pass` inside the range
    the eval can actually satisfy. A policy nobody checks is a comment."""
    p = bundle.get("meta/purpose")
    if p is None:
        return
    acc = p.meta.get("acceptance")
    if acc is None:
        return
    if not isinstance(acc, dict):
        r.add("error", "E_ACCEPTANCE_SHAPE", p.id,
              f"acceptance must be a mapping, got {type(acc).__name__}")
        return
    queries = p.meta.get("test_queries") or []
    adversarial = p.meta.get("adversarial_queries") or []
    for k, v in acc.items():
        if k not in ACCEPTANCE_KEYS:
            r.add("error", "E_ACCEPTANCE_KEY", p.id,
                  f"unknown acceptance key {k!r} (known: "
                  f"{sorted(ACCEPTANCE_KEYS)}) — an unrecognised key is a "
                  "policy that silently does nothing")
            continue
        want = ACCEPTANCE_KEYS[k]
        if isinstance(want, tuple):
            if v not in want:
                r.add("error", "E_ACCEPTANCE_VALUE", p.id,
                      f"acceptance.{k}={v!r} is not one of {list(want)} — the "
                      "release gate keys off the exact value, so a near-miss "
                      "turns the contract off while it still reads as declared")
            continue
        # bool is a subclass of int; an accidental `min_owner_pass: true` must
        # not read as 1
        if want is int and (isinstance(v, bool) or not isinstance(v, int)):
            r.add("error", "E_ACCEPTANCE_TYPE", p.id,
                  f"acceptance.{k} must be an integer, got {v!r}")
            continue
        if want is bool and not isinstance(v, bool):
            r.add("error", "E_ACCEPTANCE_TYPE", p.id,
                  f"acceptance.{k} must be true or false, got {v!r}")
            continue
        if want is str and not isinstance(v, str):
            r.add("error", "E_ACCEPTANCE_TYPE", p.id,
                  f"acceptance.{k} must be a string, got {v!r}")
            continue
        surface = {"min_owner_pass": ("test_queries", queries),
                   "min_adversarial_pass": ("adversarial_queries", adversarial)}
        if k in surface:
            field, pool = surface[k]
            if not 1 <= v <= max(len(pool), 1):
                r.add("error", "E_ACCEPTANCE_RANGE", p.id,
                      f"acceptance.{k}={v} is outside 1..{len(pool)} "
                      f"(the number of {field}) — a bar below 1 accepts a "
                      "bundle whose every query failed, and one above the query "
                      "count can never be met")
    # An escape hatch for a gate that was never turned on is not an escape
    # hatch; it is a declaration the reader will trust and nothing will honour.
    if acc.get("allow_open_dissent") and acc.get("dissent") != "required":
        r.add("error", "E_ACCEPTANCE_INERT", p.id,
              "acceptance.allow_open_dissent is set but acceptance.dissent is "
              "not 'required', so the dissent gate never runs and the waiver "
              "excuses nothing — declare the contract or drop the hatch")


ADVERSARIAL_KEYS = {"query", "expect", "concept", "why"}


def _check_adversarial(bundle: Bundle, concepts, r: Report):
    """`adversarial_queries` is a closed schema, for the same reason every other
    declaration here is one.

    An adversarial query carries a stated expectation — that is what separates
    the suite from ten more questions judged by the same person in the same
    sitting. If the expectation may be misspelled, absent, or point at a concept
    that does not exist, the criterion evaporates and the suite is back to vibes
    with extra ceremony."""
    from okfy.evaluation import EXPECTATIONS
    p = bundle.get("meta/purpose")
    if p is None:
        return
    rows = p.meta.get("adversarial_queries")
    if rows is None:
        return
    if not isinstance(rows, list):
        r.add("error", "E_ADVERSARIAL_SHAPE", p.id,
              f"adversarial_queries must be a list, got {type(rows).__name__}")
        return
    ids = {c.id for c in concepts}
    for i, row in enumerate(rows):
        where = f"adversarial_queries[{i}]"
        if not isinstance(row, dict):
            r.add("error", "E_ADVERSARIAL_SHAPE", p.id,
                  f"{where} must be a mapping with query/expect/why, got "
                  f"{type(row).__name__} {row!r} — a bare string carries no "
                  "expectation, so nothing about it can be falsified")
            continue
        for k in sorted(set(row) - ADVERSARIAL_KEYS):
            r.add("error", "E_ADVERSARIAL_FIELD", p.id,
                  f"{where}: unknown key {k!r} (known: {sorted(ADVERSARIAL_KEYS)})")
        for f in ("query", "why"):
            if not isinstance(row.get(f), str) or not row.get(f, "").strip():
                r.add("error", "E_ADVERSARIAL_FIELD", p.id,
                      f"{where}.{f} must be a non-empty string" +
                      ("" if f == "query" else
                       " — state why this query is adversarial, or the suite "
                       "records an answer to a question nobody framed"))
        expect = row.get("expect")
        if expect not in EXPECTATIONS:
            r.add("error", "E_ADVERSARIAL_FIELD", p.id,
                  f"{where}.expect must be one of {list(EXPECTATIONS)}, got "
                  f"{expect!r}")
            continue
        concept = row.get("concept")
        if expect == "covered":
            if not isinstance(concept, str) or not concept.strip():
                r.add("error", "E_ADVERSARIAL_FIELD", p.id,
                      f"{where}.concept is required when expect is 'covered' — "
                      "the expectation is that THIS concept comes back")
            elif concept not in ids:
                r.add("error", "E_ADVERSARIAL_TARGET", p.id,
                      f"{where}.concept is not a concept in this bundle: "
                      f"{concept} — an expectation nothing can satisfy is not a "
                      "test, it is a guaranteed failure")
        elif concept is not None:
            r.add("error", "E_ADVERSARIAL_FIELD", p.id,
                  f"{where}.concept is set but expect is 'not-covered', so it is "
                  "never read — a field nothing honours reads as a claim")


def _check_types(bundle: Bundle, concepts, archetype, r: Report,
                 strict: bool = False):
    """Every concept type must be a declared type.

    Archetypes ship `canonical_types` and `/okfy:new` may adapt the set per
    bundle — so the authority is `types` in meta/extraction-plan.md when it is
    declared, and the archetype's canonical list otherwise. Without this there
    was no difference between a deliberate custom type and a typo: `Strategyy`
    validated clean, contributed no required fields, and required no sections."""
    if archetype is None:
        return
    plan = bundle.plan()
    declared = (plan.meta.get("types") if plan else None)
    # `/okfy:new` writes `types` as a mapping of name -> extraction rule, which
    # is what every real bundle carries; a bare list is accepted too. Reading
    # only one of the two shapes would silently fall through to canonical_types
    # and ignore the declaration — the same fail-open this check exists to close.
    allowed = ({str(t) for t in declared}
               if isinstance(declared, (dict, list)) else None)
    if allowed:
        source = "meta/extraction-plan.md `types`"
        level, code = "error", "E_UNKNOWN_TYPE"   # an explicit set is a contract
    else:
        allowed = set(archetype.canonical_types or [])
        source = f"archetype {archetype.name} canonical_types"
        # No declaration means the adaptation was never recorded. That is worth
        # an error only where the bundle claims to be releasable.
        level, code = ("error", "E_UNDECLARED_TYPE") if strict else \
                      ("warning", "W_UNDECLARED_TYPE")
    if not allowed:
        return
    for c in concepts:
        if c.id.startswith("meta/"):
            continue
        t = str(c.meta.get("type"))
        if t in allowed:
            continue
        hint = ("" if declared else
                " — declare the adapted set as `types:` in "
                "meta/extraction-plan.md if this type is deliberate")
        r.add(level, code, c.id, f"type {t!r} is not in {source}{hint}")


def _check_corpus_snapshot(bundle: Bundle, r: Report, strict: bool = False):
    """The snapshot's git_sha is the temporal identity of the evidence (a
    frozen regulatory corpus lives or dies by it). When the corpus tree is
    locally present and is a git repo, the pinned SHA must be a real commit
    of that repo — a mutated or unreachable pin is reported, never silently
    trusted (audit round 8 regulatory mutation M1)."""
    import subprocess
    try:
        snap = bundle.get("meta/corpus")
    except frontmatter.FrontmatterError:
        return
    if snap is None:
        return
    sha = str(snap.meta.get("git_sha") or "").strip()
    corpus = Path(str(snap.meta.get("corpus") or ""))
    if not sha or not corpus.is_dir() or not (corpus / ".git").exists():
        return
    level, code = (("error", "E_CORPUS_SHA") if strict
                   else ("warning", "W_CORPUS_SHA"))
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        r.add(level, code, "meta/corpus.md",
              f"corpus snapshot git_sha is malformed: {sha!r}")
        return
    probe = subprocess.run(
        ["git", "-C", str(corpus), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True, text=True)
    if probe.returncode != 0:
        r.add(level, code, "meta/corpus.md",
              f"corpus snapshot git_sha {sha[:12]}... is not a commit of "
              f"{corpus} — the snapshot no longer identifies real corpus "
              "state (rewritten history, corrupted pin, or wrong corpus)")


def _check_execution(bundle: Bundle, r: Report, strict: bool = False):
    """Every worker-job artifact should record WHO ran it — model, provider,
    sampling, harness version — not only what it consumed. A frozen prompt plus
    a frozen input set still says nothing about the executor, so a replay across
    a model change is indistinguishable from a replay across a bundle change.

    This is attestation, not measurement: the core is agent-neutral (ADR-0002)
    and cannot observe the model, so it checks that the harness declared one, not
    that the declaration is true. Absent is a warning by default — every bundle
    built before v0.10 lacks it and must not turn red — and an error only under
    --strict-execution (new extractions). A block that exists but is incomplete
    is an error at BOTH levels: a half-filled attestation reads as complete."""
    from okfy.job import EXECUTION_FIELDS
    jobs = sorted((bundle.root / "meta" / "jobs").glob("*.json"))
    if not jobs:
        return
    level, code = (("error", "E_EXEC_MISSING") if strict
                   else ("warning", "W_EXEC_MISSING"))
    for p in jobs:
        where = f"meta/jobs/{p.name}"
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue          # unreadable artifacts are _check_provenance's job
        ex = job.get("execution")
        if ex is None:
            r.add(level, code, where,
                  "job artifact records no execution identity — the prompt and "
                  "inputs are frozen but the executor is not; a replay cannot "
                  "tell a model change from a bundle change")
            continue
        if not isinstance(ex, dict):
            r.add("error", "E_EXEC_FIELD", where,
                  f"execution must be a mapping of {', '.join(EXECUTION_FIELDS)}")
            continue
        missing = [k for k in EXECUTION_FIELDS
                   if not str(ex.get(k) or "").strip()]
        if missing:
            r.add("error", "E_EXEC_FIELD", where,
                  f"execution is missing or blank: {', '.join(missing)} — a "
                  "half-filled attestation reads as complete and is not")
        unknown = sorted(set(ex) - set(EXECUTION_FIELDS))
        if unknown:
            r.add("error", "E_EXEC_FIELD", where,
                  f"unknown execution field(s): {', '.join(unknown)}")


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


ANCHOR_LINE_RE = re.compile(r"^L(\d+)(?:-L(\d+))?$")
MD_EXTS = {".md", ".markdown"}


def _heading_slugs(text: str) -> set[str]:
    """Plausible slugs of every markdown heading. Real corpora disagree on the
    slugger: GitHub keeps one dash per space around removed punctuation
    ("NumPy / Lists" -> numpy--lists), mkdocs collapses runs (numpy-lists),
    and mkdocs-material prefixes headings with :icon-codes: that no slugger
    keeps. Accept any of these variants — the check exists to catch anchors
    pointing at nothing, not to referee slugger dialects."""
    out = set()
    for h in re.findall(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE):
        h = re.sub(r":[a-z0-9_+-]+:", "", h.lower()).strip()  # icon/emoji codes
        s = re.sub(r"[^\w\s-]", "", h, flags=re.UNICODE)
        out.add(re.sub(r"[\s_]", "-", s).strip("-"))    # github: dash per space
        out.add(re.sub(r"[-\s_]+", "-", s).strip("-"))  # mkdocs: collapsed
    return out


def _check_anchors(bundle: Bundle, concepts, r: Report, strict=False):
    """Source anchors (external review round 4, item 4): `path#L10-L20` must be
    a real line range, `guide.md#heading-id` a real heading — checkable only
    when the corpus tree is locally readable (a manifest carries hashes, not
    content). Provenance stays shallow; the link stops being decorative.
    Non-line fragments on non-markdown files have no checkable meaning —
    warning only, even strict: code/binary corpora must not fail falsely."""
    try:
        snap = bundle.get("meta/corpus")
        purpose = bundle.purpose()
    except frontmatter.FrontmatterError:
        return
    if snap is None or purpose.get("exported") or snap.meta.get("exported"):
        return
    corpus = Path(str(snap.meta.get("corpus") or ""))
    if not corpus.is_dir():
        return  # no local corpus — no basis to check (same rule as paths)
    root = corpus.resolve()
    level, code = ("error", "E_BAD_ANCHOR") if strict else ("warning", "W_BAD_ANCHOR")
    for c in concepts:
        if c.id.startswith("meta/"):
            continue
        srcs = c.meta.get("sources") or []
        for s in (srcs if isinstance(srcs, list) else [srcs]):
            s = str(s)
            if "#" not in s:
                continue
            rel, frag = s.split("#", 1)
            f = (root / rel).resolve()
            if not (f.is_relative_to(root) and f.is_file()):
                continue  # missing path is _check_sources' finding, not ours
            m = ANCHOR_LINE_RE.match(frag)
            if m:
                start = int(m.group(1))
                end = int(m.group(2) or m.group(1))
                try:
                    nlines = len(f.read_text(encoding="utf-8").splitlines())
                except (OSError, UnicodeDecodeError):
                    r.add("warning", "W_ANCHOR_UNCHECKED", c.id,
                          f"anchor {s}: source unreadable as text")
                    continue
                if start < 1 or end < start or end > nlines:
                    r.add(level, code, c.id,
                          f"anchor {s}: line range invalid (file has {nlines} lines)")
            elif f.suffix.lower() in MD_EXTS:
                if frag.strip().lower() not in _heading_slugs(
                        f.read_text(encoding="utf-8")):
                    r.add(level, code, c.id,
                          f"anchor {s}: no heading with that id in the source")
            else:
                r.add("warning", "W_ANCHOR_UNCHECKED", c.id,
                      f"anchor {s}: non-line fragment on non-markdown source — "
                      "not checkable")


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
        # shape comes from lexicon.row_problems — the same predicate load_rows
        # enforces, so a row validate calls clean can never make query raise
        for msg in row_problems(row):
            code = "W_LEXICON_STATUS" if "bad status" in msg else "W_LEXICON_ROW"
            r.add("warning", code, lex.id, msg)
        if not isinstance(row, dict):
            continue
        term = row.get("term")
        maps_to = row.get("maps_to") or []
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


def _check_orphans(bundle, concepts, linked_ids, r: Report, strict=False):
    """strict (--strict-package): an unreachable concept is an error — agents
    following the consumption protocol through index.md must find everything."""
    idx = bundle.root / "index.md"
    indexed = set()
    if idx.is_file():
        for target in LINK_RE.findall(idx.read_text(encoding="utf-8")):
            cid = resolve_link(bundle, idx, target)
            if cid:
                indexed.add(cid)
    level, code = ("error", "E_ORPHAN") if strict else ("warning", "W_ORPHAN")
    for c in concepts:
        if c.id.startswith("meta/"):
            continue
        if c.id not in indexed and c.id not in linked_ids:
            r.add(level, code, c.id, "not reachable from index.md or any concept")


QUALITY_FIELDS = ["date", "prompt_version", "selector_version", "seed",
                  "sampled", "rows"]
QUALITY_VERDICTS = {"pass", "fail", "n/a"}


def _check_quality(bundle: Bundle, archetype, r: Report, strict: bool = False):
    """PurposeFitness artifact (external review round 4): L3 must persist as a
    checkable artifact — meta/purpose-fitness.md — not a prompt instruction
    that evaporates with the transcript. Round 5 item 3: verdicts live in
    frontmatter `rows:` (concept_id/check_id/verdict/evidence) — the same call
    the lexicon made; markdown is human rendering, never the source of truth.
    Warning by default (old bundles), errors under --strict-quality."""
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
    rows = [x for x in (c.meta.get("rows") or []) if isinstance(x, dict)]
    by_key: dict[tuple, list[dict]] = {}
    for row in rows:
        by_key.setdefault((str(row.get("concept_id")), str(row.get("check_id"))),
                          []).append(row)
    for sid in sampled:
        if sid not in known:
            continue
        for chk in checks:
            match = by_key.get((sid, chk), [])
            if not match:
                r.add(level, code("QUALITY_ROW"), c.id,
                      f"no verdict row for {sid} x {chk}")
                continue
            if len(match) > 1:
                r.add(level, code("QUALITY_DUP"), c.id,
                      f"duplicate verdict rows for {sid} x {chk}")
            row = match[0]
            if str(row.get("verdict", "")).strip().lower() not in QUALITY_VERDICTS:
                r.add(level, code("QUALITY_VERDICT"), c.id,
                      f"row for {sid} x {chk}: verdict must be pass/fail/n-a")
            if not str(row.get("evidence", "")).strip():
                r.add(level, code("QUALITY_EVIDENCE"), c.id,
                      f"row for {sid} x {chk}: evidence is empty")
    # replay: if the corpus hasn't moved (seed still current) the deterministic
    # sample must be covered; a moved corpus is not replayable — skip silently.
    from okfy.sampling import SELECTOR_VERSION, _selector_seed, sample_for_review
    if (str(c.meta.get("seed")) == _selector_seed(bundle)
            and c.meta.get("selector_version") == SELECTOR_VERSION):
        rerun = sample_for_review(
            bundle, fraction=float(c.meta.get("fraction", 0.1)),
            minimum=int(c.meta.get("minimum", 20)))
        missing = sorted(set(rerun["sampled"]) - set(sampled))
        if missing:
            r.add(level, code("QUALITY_SAMPLE"), c.id,
                  f"recorded sample misses deterministic selection: {missing}")


def _check_provenance(bundle: Bundle, r: Report, strict: bool = False):
    """Worker-job chain (external review round 5, item 2): every frozen job
    artifact must be internally consistent (digest recomputes, prompt copy
    present and unmodified), and every ledger row that claims a job must match
    it — digest and inputs. Bundles with no jobs have nothing to verify."""
    import hashlib as _hashlib

    from okfy.job import job_digest
    from okfy.ledger import read_rows

    def code(s: str) -> str:
        return ("E_" if strict else "W_") + s

    level = "error" if strict else "warning"
    jobs_dir = bundle.root / "meta" / "jobs"
    jobs: dict[str, dict] = {}
    if jobs_dir.is_dir():
        for f in sorted(jobs_dir.glob("*.json")):
            rel = f"meta/jobs/{f.name}"
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                r.add(level, code("PROV_JOB_UNREADABLE"), rel,
                      "job artifact unreadable")
                continue
            if data.get("segment") != f.stem:
                r.add(level, code("PROV_JOB_SEGMENT"), rel,
                      f"artifact claims segment {data.get('segment')!r}")
            if job_digest(data) != data.get("digest"):
                r.add(level, code("PROV_JOB_DIGEST"), rel,
                      "stored digest does not recompute — artifact edited "
                      "after freeze")
            pp = data.get("prompt_path")
            pf = (bundle.root / pp) if pp else None
            if not pp or not pf.is_file():
                r.add(level, code("PROV_PROMPT"), rel,
                      f"frozen prompt copy missing: {pp}")
            elif (_hashlib.sha256(pf.read_bytes()).hexdigest()
                  != data.get("prompt_sha256")):
                r.add(level, code("PROV_PROMPT"), rel,
                      f"prompt copy {pp} does not match prompt_sha256 — "
                      "edited after freeze")
            jobs[f.stem] = data
    for row in read_rows(bundle):
        jd = row.get("job_digest")
        if not jd:
            continue
        where = f"meta/ledger.jsonl ({row.get('run_id')} {row.get('segment')})"
        job = jobs.get(str(row.get("segment")))
        if job is None:
            r.add(level, code("PROV_LEDGER_JOB"), where,
                  "row claims a job but no artifact exists for its segment")
            continue
        if job.get("digest") != jd:
            r.add(level, code("PROV_LEDGER_DIGEST"), where,
                  "row's job_digest does not match the frozen artifact")
        job_paths = {i.get("path") for i in job.get("inputs", [])}
        outside = sorted(set(row.get("inputs", [])) - job_paths)
        if outside:
            r.add(level, code("PROV_LEDGER_INPUTS"), where,
                  f"row inputs not in the job artifact: {outside}")


def package_fingerprint(bundle: Bundle) -> str:
    """Content fingerprint of the final concept set: sorted id:sha256 lines,
    hashed. Any concept mutation, addition, or removal changes it — so a
    package generated before the change is provably stale."""
    import hashlib as _hashlib
    lines = []
    for c in sorted(bundle.concepts(), key=lambda c: c.id):
        if c.id.startswith("meta/"):
            continue
        lines.append(f"{c.id}:{_hashlib.sha256(c.path.read_bytes()).hexdigest()}")
    return _hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _check_package(bundle: Bundle, r: Report, strict: bool = False):
    """Package freshness (external review round 5, item 4): the generated
    index is the consumption contract for agents without the CLI — a concept
    accepted after `okfy package` is invisible to them until repackage.
    meta/package.json records the concept-set fingerprint at package time."""
    def code(s: str) -> str:
        return ("E_" if strict else "W_") + s

    level = "error" if strict else "warning"
    p = bundle.root / "meta" / "package.json"
    if not p.is_file():
        r.add(level, code("PACKAGE_MISSING"), "meta/package.json",
              "no package fingerprint — run `okfy package`")
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        recorded = data.get("fingerprint")
    except (OSError, ValueError):
        recorded = None
    if recorded != package_fingerprint(bundle):
        r.add(level, code("STALE_PACKAGE"), "meta/package.json",
              "concepts changed since `okfy package` — the generated index "
              "no longer reflects the bundle; repackage before acceptance")


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
    for fname, allowed in (archetype.field_enums.get(ctype) or {}).items():
        v = c.meta.get(fname)
        if v is not None and str(v) not in [str(a) for a in allowed]:
            r.add("error", "E_FIELD_ENUM", c.id,
                  f"{fname}: {v!r} not in {allowed}")
    for s in archetype.nonempty_sections.get(ctype, []):
        text = _section_text(c.body, s)
        if text is not None and not text.strip():
            r.add("error", "E_EMPTY_SECTION", c.id,
                  f"section ## {s} is present but empty — the heading alone carries nothing")
