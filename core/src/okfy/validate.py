"""Layers 1-2 of the 4-layer validation. Layers 3-4 are LLM work, orchestrated by the plugin."""
import datetime
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from okfy import frontmatter
from okfy.bundle import RESERVED_DIRS, Bundle
from okfy.lexicon import row_problems
from okfy.update import _embedded_prefix, _source_path
from okfy.workspace import PROJECT_KEY_RE

DATE_HEADING_RE = re.compile(r"^## (.+)$", re.MULTILINE)


@dataclass
class Finding:
    level: str  # "error" | "warning"
    code: str
    path: str
    message: str


@dataclass
class LedgerPrefixCheck:
    """Result of `ledger_prefix_check` — the ONE append-only predicate shared
    by the reader (`okfy validate`, via `_check_one_ledger_append_only`) and
    every sanctioned ledger writer (`okfy propose`'s rejected-content gate,
    and — via `proposals._commit`, the one function every sanctioned mutator's
    commit converges through — `okfy review accept`/`reject`, `dismiss`,
    `refine`, `ledger.add_row`). Three outcomes, kept distinct on purpose
    (v0.25 F05): collapsing `unverifiable` into either of the other two either
    refuses every bundle with no git repository (folded into `rewritten`) or
    silently stops guarding the moment one exists (folded into `intact`).

    v0.26 audit A5 widened WHAT `rewritten` can be built from. Three
    independent sources exist for a tracked file: the committed HEAD blob,
    the INDEX (git's stage 0 — what the NEXT commit would ship if nothing
    else is staged first), and the working tree (what is on disk right now).
    Before A5 this predicate compared only working tree vs. committed, so a
    rewrite that was `git add`ed and then the working-tree file was restored
    to match HEAD byte-for-byte read as `intact` — the corruption was real
    and sitting in the index, ready to ride into HEAD on the next commit
    (any commit, not necessarily one that names the ledger path), and this
    predicate had nothing to say about it. Now BOTH the index and the
    working tree are checked against the committed blob, independently:
    either one failing the byte-prefix test is `rewritten`. This closes the
    "stage the rewrite, then put the working tree back" gap without adding a
    fourth outcome — the INDEX and the WORKING TREE are two different
    questions ("what would ship next" vs. "what is on disk"), but a caller
    of this predicate has always had exactly one question ("is the
    append-only contract intact"), and either source breaking it answers
    that question the same way.

    - `outcome == "intact"`: no committed baseline to contradict — either
      BOTH the index and the working tree are byte-supersets of HEAD's
      version, or there is no committed version AND no working-tree file at
      all (nothing to claim append-only about; see
      `_check_one_ledger_append_only`).
    - `outcome == "rewritten"`: a committed baseline exists and the index OR
      the working tree (or both) is NOT a byte-prefix-superset of it —
      `offset`/`row` name the EARLIEST divergence between the two (0-based
      byte offset, 1-based line), so the report always points at the first
      byte an owner needs to look at regardless of which source carries it.
    - `outcome == "unverifiable"`: a working-tree file exists but there is no
      committed baseline to compare it against — `reason` says why:
      `"no_repo"` (bundle.root is not a git repository) or `"not_committed"`
      (a repository exists but HEAD has no version of this file yet). This is
      NOT the same as `intact` — a caller that must not proceed on faith
      checks `reason` itself rather than treating "not rewritten" as "safe".

    What this STILL cannot prove, unchanged by A5: it reads exactly ONE
    committed blob (`git show HEAD:<path>`), never a walk of history. A
    rewrite that was itself already folded into a commit — any commit, not
    just one made through this predicate's own callers — is invisible to it;
    HEAD simply IS the rewritten content by the time this runs, and nothing
    here compares HEAD against an earlier HEAD. Reading the index closes the
    "staged now, committed later" gap, not the "already committed" one — the
    original honesty label stands."""
    outcome: str  # "intact" | "rewritten" | "unverifiable"
    relpath: str
    offset: int | None = None   # rewritten only
    row: int | None = None      # rewritten only
    reason: str | None = None   # unverifiable only: "no_repo" | "not_committed"


def _byte_prefix_divergence(committed_n: bytes, candidate_n: bytes) -> int | None:
    """0-based byte offset of the first mismatch between `candidate_n` and
    `committed_n`, or None when `candidate_n` is a byte-prefix-superset of
    `committed_n` (an append, or no change at all). Both arguments are
    already line-ending-normalized by the caller."""
    if candidate_n.startswith(committed_n):
        return None
    n = min(len(committed_n), len(candidate_n))
    offset = 0
    while offset < n and committed_n[offset] == candidate_n[offset]:
        offset += 1
    return offset


def ledger_prefix_check(bundle: Bundle, relpath: str) -> LedgerPrefixCheck:
    """The append-only byte-prefix predicate itself (see `LedgerPrefixCheck`
    for the three outcomes, and its v0.26 audit A5 note for what changed:
    the index is now read alongside the working tree). Reads exactly one
    committed blob per call (`git show HEAD:<path>`) plus, only when that
    blob exists, one index blob (`git show :<path>`) — never a walk of
    history — same limit the original check documented: this proves nothing
    about a rewrite that was itself already committed, only catches an
    uncommitted (or un-committed-yet-staged) one."""
    from okfy.gitenv import run_git
    wt_path = bundle.root / relpath
    has_repo = (bundle.root / ".git").exists()
    committed = None  # bytes of the committed HEAD blob, or None if there isn't one
    if has_repo:
        shown = run_git(bundle.root, "show", f"HEAD:{relpath}", capture_output=True)
        if shown.returncode == 0:
            committed = shown.stdout  # capture_output without text=True: bytes
    has_working = wt_path.is_file()

    if committed is None and not has_working:
        # Nothing to verify and nothing to be silent about: no committed
        # version AND no working-tree file — there is no append-only claim
        # to make about a file that is not there at all.
        return LedgerPrefixCheck("intact", relpath)

    if committed is None:
        # A working-tree file exists but there is nothing committed yet to
        # compare it against.
        reason = "no_repo" if not has_repo else "not_committed"
        return LedgerPrefixCheck("unverifiable", relpath, reason=reason)

    # A committed baseline exists. `has_working` may be False here — the
    # whole file was deleted from the working tree — which the byte-prefix
    # comparison below catches on its own (an empty working copy can never
    # be a superset of a non-empty committed prefix).
    working = wt_path.read_bytes() if has_working else b""
    # The index (stage 0) — what the NEXT commit ships for this path unless
    # something re-stages it first. Absent when nothing ever staged this
    # path (impossible once `committed` exists and nothing has since `git
    # rm --cached`ed it) or when it was staged-deleted — either way treated
    # as b"", same as a missing working-tree file: an empty stage can never
    # be a superset of a non-empty committed prefix, so a staged deletion of
    # an append-only ledger is correctly caught as `rewritten` too.
    indexed_show = run_git(bundle.root, "show", f":{relpath}", capture_output=True)
    indexed = indexed_show.stdout if indexed_show.returncode == 0 else b""
    # Line endings are normalized on ALL THREE sides before the prefix
    # comparisons (CRLF, and lone CR, folded to LF) — see the long note this
    # carried in `_check_one_ledger_append_only` before extraction: a smudge
    # filter re-materializing a committed LF file with CRLF endings must not
    # read as a rewrite, and an edit INSIDE a line still changes bytes
    # normalization never touches, so this keeps the check's teeth.
    def norm(b: bytes) -> bytes:
        return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    working_n, indexed_n, committed_n = norm(working), norm(indexed), norm(committed)

    # The index and the working tree are two INDEPENDENT questions ("what
    # would ship next" vs. "what is on disk") — either breaking the
    # append-only contract is `rewritten`. When both diverge, the EARLIER
    # byte offset is reported: it is closer to the true first divergence a
    # repair needs to see, and picking one deterministically (rather than
    # "whichever source happened to be checked first") keeps the report
    # stable regardless of which source a future caller reads first.
    offsets = [o for o in (_byte_prefix_divergence(committed_n, working_n),
                          _byte_prefix_divergence(committed_n, indexed_n))
              if o is not None]
    if not offsets:
        return LedgerPrefixCheck("intact", relpath)
    offset = min(offsets)
    row = committed_n[:offset].count(b"\n") + 1
    return LedgerPrefixCheck("rewritten", relpath, offset=offset, row=row)


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    sources: dict | None = None  # coverage summary, set when source checks ran
    coverage: dict | None = None  # reverse coverage, set when the plan has segments
    spans: dict | None = None    # attested span outcomes, set when a plan has done segments

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
        if self.coverage is not None:
            d["coverage"] = self.coverage
        if self.spans is not None:
            d["spans"] = self.spans
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


def _norm(s) -> str:
    return " ".join(unicodedata.normalize("NFC", str(s)).casefold().split())


def _index_body(text: str) -> str:
    """index.md without its version frontmatter, if it has one that parses."""
    if text.startswith("---"):
        try:
            return frontmatter.parse(text)[1]
        except frontmatter.FrontmatterError:
            pass
    return text


def _check_reserved(bundle: Bundle, r: Report):
    idx = bundle.root / "index.md"
    text = idx.read_text(encoding="utf-8") if idx.is_file() else ""
    if text.startswith("---"):
        try:
            meta = frontmatter.parse(text)[0]
        except frontmatter.FrontmatterError as e:
            meta = {"<unparseable>": str(e)}
        extra = {k: v for k, v in meta.items() if (k, v) != ("okf_version", "0.2")}
        if extra or not meta:
            r.add("error", "E_INDEX_FRONTMATTER", "index.md",
                  'index.md frontmatter may only declare okf_version: "0.2" '
                  f"(OKF v0.2 §8); found {extra or 'an empty block'} — "
                  "`okfy package` regenerates it")
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
    """Return concept id a local md link points to, or None for external/anchor-only.

    v0.24 review fix (finding 32): a shard link target may be percent-encoded
    (`okfy.package._link_target`) — a directory name containing a space or
    `)` would otherwise never round-trip through `LINK_RE`/`INDEX_LINE_RE`
    (`[^)\\s]+` can't capture either character). Decoded after the
    leading-`/` check, since that slash is never itself encoded."""
    target = target.split("#", 1)[0]
    if not target or target.startswith(("http://", "https://", "mailto:")):
        return None
    if not target.endswith(".md"):
        return None
    base = concept_path.parent if not target.startswith("/") else bundle.root
    resolved = (base / unquote(target).lstrip("/")).resolve()
    try:
        return resolved.relative_to(bundle.root).with_suffix("").as_posix()
    except ValueError:
        return None


def validate_integrity(bundle: Bundle, archetype=None, strict_sources=False,
                       strict_quality=False, strict_provenance=False,
                       strict_package=False, strict_execution=False,
                       strict_schema=False, strict_injection=False) -> Report:
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
    _check_supersede(concepts, r)
    _check_supersede_cycles(concepts, r)
    _check_verified(concepts, r)
    _check_memory_log(bundle, r)
    _check_ledger_append_only(bundle, r)
    _check_review_due(concepts, r)
    _check_applies_to(concepts, r)
    _check_sources(bundle, concepts, r, strict=strict_sources)
    _check_coverage(bundle, concepts, r)
    _check_span_coverage(bundle, r)
    _check_span_contradiction(bundle, r)  # needs both halves above
    _check_drops_unexplained(bundle, r)
    _check_anchors(bundle, concepts, r, strict=strict_sources)
    _check_quotes(bundle, concepts, r)
    _check_lexicon(concepts, r)
    linked_ids = _check_links(bundle, concepts, r)
    _check_orphans(bundle, concepts, linked_ids, r, strict=strict_package)
    _check_index_drift(bundle, concepts, r)
    _check_index_shard(bundle, concepts, r)
    _check_reserved_dir_concepts(bundle, r)
    _check_quality(bundle, archetype, r, strict=strict_quality)
    _check_provenance(bundle, r, strict=strict_provenance)
    _check_package(bundle, r, strict=strict_package)
    _check_injection(bundle, r, strict=strict_injection)
    _check_source_map(bundle, r)
    _check_budget(bundle, archetype, r)
    for c in concepts:
        if archetype_applies(c.id):
            if not c.meta.get("sources") and _sources_expected(c, archetype):
                r.add("warning", "W_NO_SOURCES", c.id, "extracted concept without sources")
            if archetype:
                _check_archetype(c, archetype, r)
    return r


def archetype_applies(concept_id: str) -> bool:
    """Does the archetype's concept schema govern this concept?

    No for `meta/*`: purpose, corpus snapshot, extraction plan, lexicon and the
    rest describe the bundle, they are not domain concepts, and no shipped
    archetype declares fields for them — none carries `description`, which every
    archetype's `_all` requires.

    Exported as a shared predicate on purpose. `validate_integrity` skipped
    meta and `proposals.accept` did not, so a bundle with
    `write_policy: proposals` could not update its own `meta/purpose.md` through
    the flow the pre-commit hook tells the owner to use: the proposal was
    refused for a missing `description` the validator never asks for. Two
    callers, two answers to one question — the drift shape the audit rounds keep
    finding. One predicate, both callers.
    """
    return not concept_id.startswith("meta/")


def _check_source_map(bundle: Bundle, r: Report):
    """`meta/source-map.jsonl`, verified as part of validation rather than only
    by `okfy sourcemap`.

    The verification was correct and complete and simply wired to nothing that
    gates: a syntactically broken sidecar could be dropped into a green bundle
    and release-check stayed true. A check that no gate calls is documentation.

    THREE outcomes, kept apart on purpose:

    - absent      -> nothing at all. Most corpora are authored text and will
                     never have a sidecar; a missing optional artifact must not
                     read as a gap, and treating it as one would turn every
                     existing bundle red for a reason nobody asked about.
    - unverifiable-> reported, because a hash that could not be recomputed is
                     not a hash that matched. Same reasoning as an unscanned
                     file not being a clean one.
    - failed      -> reported with the row's own code and line.

    Warnings here, errors at release: the W_/E_ escalation this module already
    uses everywhere. The import is local because `okfy.sourcemap` imports
    `ANCHOR_LINE_RE` from this module — one grammar for line anchors, which is
    worth a lazy import.
    """
    from okfy.sourcemap import SOURCE_MAP, check_source_map
    out = check_source_map(bundle)
    if out["state"] == "absent":
        return
    # `missing` is NOT `absent`: the bundle declared the sidecar mandatory and
    # it is not there. Returning early on that would honour the declaration by
    # ignoring it.
    for p in out["problems"]:
        # Not every problem is about a row. An empty file, an uncovered cited
        # source and a declared-but-absent map are facts about the ARTIFACT, and
        # `line 3` on any of them would send a reader to a line that is not
        # there.
        where = (f"{SOURCE_MAP} (line {p['line']})" if "line" in p
                 else SOURCE_MAP)
        r.add("warning", "W_SOURCEMAP", where, f"{p['code']}: {p['message']}")
    if out["unverifiable"]:
        r.add("warning", "W_SOURCEMAP_UNVERIFIABLE", SOURCE_MAP,
              f"{out['unverifiable']} of {out['rows']} row(s) could not be "
              "checked against the corpus — the raw-to-normalized mapping is "
              "carried, not verified")


def _check_injection(bundle: Bundle, r: Report, strict: bool = False):
    """Instructions smuggled in from the corpus (ADR-0013 idiom: signal by
    default, gate under strict).

    Warnings by default because the phrase rules are heuristics and a corpus
    that quotes an instruction is not an attack. Errors under
    `--strict-injection`, which is what `release-check` composes. A file that
    could not be scanned is reported at the same level as a finding: an
    unscanned file is not a clean one."""
    from okfy.injection import scan_bundle
    level, code = ("error", "E_INJECTION") if strict else ("warning", "W_INJECTION")
    out = scan_bundle(bundle)
    for f in out["findings"]:
        r.add(level, code, f["path"],
              f"{f['path']}:{f['line']} [{f['rule']}/{f['kind']}] {f['excerpt']}")
    for s in out["skipped"]:
        r.add(level, code, s["path"],
              f"{s['path']}: not scanned for injection ({s['reason']})")


def _check_budget(bundle: Bundle, archetype, r: Report):
    """The always-resident total against the archetype's advisory target.

    Note the signature: there is NO `strict` parameter, deliberately. This is
    advisory by the owner's decision, so the code has no path that turns it into
    an error at any strictness level and `release_check` never composes it. A
    flag that could flip it would eventually be flipped."""
    budgets = getattr(archetype, "budgets", None) or {}
    cap = budgets.get("resident_max")
    if not cap:
        return
    from okfy.budget import resident_core
    res = resident_core(bundle)
    if res["missing"] or res["tokens"] <= cap:
        return
    r.add("warning", "W_BUDGET_RESIDENT", "AGENTS.md+index.md",
          f"always-resident files are {res['tokens']:,} tokens against the "
          f"{archetype.name} target of {cap:,} — this is the only cost billed "
          "on every turn. Advisory: shrink index.md or accept it, but do not "
          "pad anything to change the number")


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
    "allow_injection": bool,
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


def _check_adversarial(bundle: Bundle, concepts, r: Report):
    """`adversarial_queries` is a closed schema, for the same reason every other
    declaration here is one.

    An adversarial query carries a stated expectation — that is what separates
    the suite from ten more questions judged by the same person in the same
    sitting. If the expectation may be misspelled, absent, or point at a concept
    that does not exist, the criterion evaporates and the suite is back to vibes
    with extra ceremony. The predicate itself lives in `evaluation` so the
    workspace suite cannot drift away from this one."""
    from okfy.evaluation import adversarial_row_problems
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
        for msg in adversarial_row_problems(row, valid_target=ids.__contains__):
            code = ("E_ADVERSARIAL_SHAPE" if "must be a mapping" in msg
                    else "E_ADVERSARIAL_TARGET" if "not reachable" in msg
                    else "E_ADVERSARIAL_FIELD")
            r.add("error", code, p.id, f"adversarial_queries[{i}]: {msg}")


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
    from okfy.gitenv import run_git
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
    probe = run_git(corpus, "cat-file", "-e", f"{sha}^{{commit}}",
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
    if strict and str(bundle.purpose().get("provenance", "")).strip() == "legacy":
        # v0.21 composed this check into release_check, where it had never run.
        # A bundle extracted before v0.10 has job artifacts that CANNOT gain an
        # executor identity — the run is over and nobody recorded who did it —
        # so the escape is the declaration the other release gates already
        # honour rather than a new one. Warning level still applies below when
        # not strict, because a warning is a description and this is one.
        r.add("warning", "W_EXEC_LEGACY", "meta/purpose.md",
              "provenance: legacy declared — executor identity not enforced on "
              f"{len(jobs)} job artifact(s); nothing records who ran them")
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




def _check_supersede(concepts, r: Report):
    """`supersedes` / `superseded_by` (action `supersede`, v0.24) must point to
    an existing concept AND be reciprocal — an agent that follows one
    direction must land somewhere the other direction confirms. Each finding
    names both ids: `c.id` as the finding's own path, the id it points at in
    the message."""
    by_id = {c.id: c for c in concepts}
    for c in concepts:
        sb = c.meta.get("superseded_by")
        if sb is not None:
            target = by_id.get(str(sb))
            if target is None or str(target.meta.get("supersedes")) != c.id:
                r.add("error", "E_SUPERSEDE_DANGLING", c.id,
                      f"superseded_by names {sb!r}, which does not exist or "
                      f"does not itself carry `supersedes: {c.id}` — fix the "
                      "link or remove it via `okfy refine`")
        sp = c.meta.get("supersedes")
        if sp is not None:
            target = by_id.get(str(sp))
            if target is None or str(target.meta.get("superseded_by")) != c.id:
                r.add("error", "E_SUPERSEDE_DANGLING", c.id,
                      f"supersedes names {sp!r}, which does not exist or does "
                      f"not itself carry `superseded_by: {c.id}` — fix the "
                      "link or remove it via `okfy refine`")


def _check_supersede_cycles(concepts, r: Report):
    """Cycle detection over the `supersedes`/`superseded_by` graph — a
    concern `_check_supersede` above does not cover. That function checks
    each PAIR for reciprocity only: does `superseded_by` point at a concept
    whose own `supersedes` points back. A ring A -> B -> C -> A satisfies
    every one of those pairwise checks (each link's reciprocal exists and
    agrees) and still means no concept in the ring is current — there is no
    member the chain ever lets an agent call "the latest one".

    ONE finding per cycle, never one per member: a concept sitting in a
    3-ring is not three separate problems, it is one topology, reported once.

    The finding's IDENTITY — the key two runs must agree on for it to count
    as "the same finding" — is the SORTED TUPLE of the ring's ids, never the
    order `concepts` happened to be read or walked in. This is not a style
    choice: the donor project's migrations 0052-0054 exist ONLY because a
    hash-map traversal order once changed which id a cycle's key was built
    from, which silently orphaned the human accept/reject decisions that had
    been recorded against the OLD key — a dict/list iteration order must
    never leak into a finding's identity. The finding is attached to the
    ring's lexicographically smallest id, so the report has a stable path to
    point at regardless of which member the traversal happened to enter the
    ring through.

    A self-loop — `superseded_by` naming its own id, reciprocated by
    `supersedes` naming its own id right back — is a cycle of one member,
    reported the same way: it is exactly as true that "no concept here is
    current" as it is for a longer ring.

    Traversal: `superseded_by` gives each concept at most one outgoing edge
    (old -> new), so this is a functional graph — three-colour DFS (white/
    grey/black) finds every cycle in one pass over `by_id`, scanned in
    SORTED id order so which node a walk starts from can never vary with
    `concepts`' own order (and so the SAME ring is never rediscovered twice
    under a different rotation). Dangling edges — pointing at an id that does
    not exist, which `_check_supersede` above already flags — are dead ends
    here, not cycles."""
    by_id = {c.id: c for c in concepts}

    def next_id(cid: str) -> str | None:
        target = by_id[cid].meta.get("superseded_by")
        if target is None:
            return None
        target = str(target)
        return target if target in by_id else None

    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    seen_rings: set[tuple[str, ...]] = set()
    for start in sorted(by_id):
        if color.get(start, WHITE) != WHITE:
            continue
        path: list[str] = []
        cur = start
        while cur is not None and color.get(cur, WHITE) != BLACK:
            if color.get(cur, WHITE) == GREY:
                idx = path.index(cur)
                ring = path[idx:]
                key = tuple(sorted(ring))
                if key not in seen_rings:
                    seen_rings.add(key)
                    anchor = min(ring)
                    i = ring.index(anchor)
                    ordered = ring[i:] + ring[:i]
                    chain = " -> ".join([*ordered, ordered[0]])
                    r.add("error", "E_SUPERSEDES_CYCLE", anchor,
                          f"supersedes cycle: {chain} — no concept in this "
                          "ring is current; `okfy refine` on any one link "
                          "(drop or repoint one supersedes/superseded_by "
                          "pair) breaks it")
                break
            color[cur] = GREY
            path.append(cur)
            cur = next_id(cur)
        for n in path:
            color[n] = BLACK


def applies_to_malformed(v) -> bool:
    """The ONE predicate for "is this `applies_to` value invalid" — shape
    AND grammar together, the COMPLETE rule `_check_applies_to` below
    enforces. Invalid when: not a list, an empty list, or containing any
    entry that is not a non-blank string (shape); or containing any entry
    that is neither `*` nor a legal project key matching
    `workspace.PROJECT_KEY_RE` (grammar, v0.24 review finding 45) —
    whitespace-padded, uppercase, an embedded space, or any other shape a
    real `project_key` can never equal.

    Every `applies_to` reader shares this one function, so none can certify
    a value in- or out-of-scope that this rule calls malformed:
    `federate._in_scope` (fail closed, ADR-0015), and through it
    `personal_scope_ids`/`ref_in_scope`/`_personal_scope_filter` and
    `export_fusion.export_workspace`. Before v0.25 F10, `federate._in_scope`
    filtered bad entries out of the shape check and matched on the
    remainder instead of checking the whole value, so `applies_to: ['*',
    123]` was a search hit while `validate` called the concept malformed.
    Before the v0.27 fix, the identical drift still existed for grammar
    alone (finding A7): `_in_scope` never ran the grammar half of this
    rule, so `applies_to: ['*', 'BAD KEY']` matched via `'*'` there while
    `validate` called `'BAD KEY'`'s grammar malformed — two functions that
    happened to agree on shape, not one shared complete predicate."""
    if not isinstance(v, list) or not v:
        return True
    for x in v:
        if not isinstance(x, str) or not x.strip():
            return True
        if x != "*" and not PROJECT_KEY_RE.match(x):
            return True
    return False


def _check_applies_to(concepts, r: Report):
    """`applies_to` (v0.24, personal memory, ADR-0015) is an optional list of
    project keys — or `*` — naming which workspace(s) may see this concept
    when it is read through a `personal`-role workspace member. Validated in
    EVERY bundle (a personal bundle is an ordinary bundle when queried
    directly) but only READ at query time for `personal` members
    (federate.py). Invalidity — shape OR grammar — is `applies_to_malformed`
    above. ERROR, not warning: an invalid value is neither provably in
    scope nor provably out of it, and a fail-closed filter must never guess
    — `federate._in_scope` drops it either way (same predicate, see above),
    but a silent drop reads as "no memory exists" instead of "this
    frontmatter is broken".

    The two branches below only choose which MESSAGE to show (shape vs.
    grammar); the decision to raise at all is `applies_to_malformed`'s
    alone, so a reader can never see a value this function calls valid and
    a scope check calls invalid, or the reverse."""
    for c in concepts:
        if "applies_to" not in c.meta:
            continue
        v = c.meta["applies_to"]
        if not applies_to_malformed(v):
            continue
        if not isinstance(v, list) or not v or \
                any(not isinstance(x, str) or not x.strip() for x in v):
            r.add("error", "E_APPLIES_TO", c.id,
                  f"applies_to must be a non-empty list of project-key "
                  f"strings (or `*`): {v!r}")
            continue
        bad = [x for x in v if x != "*" and not PROJECT_KEY_RE.match(x)]
        r.add("error", "E_APPLIES_TO", c.id,
              f"applies_to entries must be `*` or a project key matching "
              f"{PROJECT_KEY_RE.pattern} (a lowercase slug, no leading/"
              f"trailing whitespace, no uppercase): {bad!r} in {v!r} — "
              "fix by lowercasing/trimming to a valid slug")


def review_due_date(value) -> datetime.date | None:
    """`review_due` as a date, or None when it is not one. YAML reads an
    unquoted 2026-10-01 as a date and a quoted one as a string; both count."""
    if isinstance(value, datetime.datetime):
        return None
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        return None


def _check_review_due(concepts, r: Report):
    """`review_due` is when someone should look at a concept again. A passed date
    is a WARNING and never touches `stale`: stale is the owner's ruling that a
    text is not to be trusted as current (ADR-0013), a review date is only a
    reminder, and an expired reminder proves nothing about the text."""
    today = datetime.date.today()
    for c in concepts:
        if "review_due" not in c.meta:
            continue
        d = review_due_date(c.meta["review_due"])
        if d is None:
            r.add("error", "E_REVIEW_DUE", c.id,
                  f"review_due is not an ISO date (YYYY-MM-DD): "
                  f"{c.meta['review_due']!r} — correct it with `okfy refine`")
        elif d < today:
            r.add("warning", "W_REVIEW_DUE", c.id,
                  f"review_due {d.isoformat()} passed {(today - d).days} day(s) ago — "
                  "the text may be out of date, which is not the same as stale; "
                  "`okfy stale <bundle> --due` lists every overdue concept")


def _check_memory_log(bundle: Bundle, r: Report):
    """meta/memory.jsonl is read, never judged: an unreadable line is a warning
    here, because validate must not block a bundle on its own history. The place
    it DOES refuse is `okfy propose`, whose rejected-content gate reads this log
    and cannot run fail-open over a line it cannot parse."""
    from okfy import memory
    for problem in memory.events(bundle)[1]:
        r.add("warning", "W_MEMORY_LINE", memory.MEMORY_FILE,
              problem.removeprefix(f"{memory.E_MEMORY_LINE}: ")
              + " — `okfy propose` refuses until it is fixed")


def _check_ledger_append_only(bundle: Bundle, r: Report):
    """meta/memory.jsonl (okfy.memory) and meta/ledger.jsonl (okfy.ledger) are
    append-only BY CONTRACT and nothing enforced it. The check: the file's
    committed content at HEAD must be a byte-prefix of BOTH the index (what
    the next commit would ship) and the working-tree file (what is on disk).
    An append only ever extends the file, so the prefix always holds; an edit
    to an already-committed row, or a deletion (of a row, or of the whole
    file), breaks it — in either source — and is reported as
    E_LEDGER_REWRITTEN.

    HONESTY LABEL: this proves nothing about a rewrite that was itself
    committed — it only ever compares the index and the working tree against
    HEAD, so a rewrite laundered through its own commit is invisible to it.
    It catches an UNCOMMITTED edit (or one already staged but not yet
    committed), which is the case that actually happens (a hand edit or an
    agent touching an already-written row before the next commit); see the
    finding text below and GUIDE.md/GUIDE.ru.md for the same wording.

    Both files are checked and reported separately. When the file exists in
    the working tree but cannot be verified against a committed baseline —
    no git repository at all, or a repository whose HEAD has no version of
    this file yet — that is reported too, as W_LEDGER_UNVERIFIABLE, at the
    same level a real finding would be: an unverifiable ledger is not a
    verified one, the same call `_check_injection` above already makes for a
    file it could not scan. A file that exists NOWHERE (no committed
    version, no working-tree file) is the ONE case reported as nothing at
    all — there is no append-only claim to make about a file that is not
    there, and whether it should exist is another check's business (see
    `_check_one_ledger_append_only` below); the asymmetry is deliberate, not
    an oversight. A file that IS committed but has since been deleted
    entirely from the working tree is neither of those — it has a real
    baseline to compare against, so it falls through to the ordinary
    byte-prefix check below and reports E_LEDGER_REWRITTEN, the limit case
    of "a row was deleted".

    Reads exactly one committed blob per file (`git show HEAD:<path>`), plus
    one index blob (`git show :<path>`) when that committed blob exists —
    never a walk of history."""
    from okfy import ledger, memory
    for relpath in (memory.MEMORY_FILE, ledger.LEDGER):
        _check_one_ledger_append_only(bundle, relpath, r)


def _check_one_ledger_append_only(bundle: Bundle, relpath: str, r: Report):
    """Reports all three `ledger_prefix_check` outcomes as findings, unchanged
    from before the predicate was extracted (v0.25 F05) — `intact` reports
    nothing, `unverifiable` and `rewritten` register at the same level a real
    finding always would (a warning, an error), never silently passed."""
    check = ledger_prefix_check(bundle, relpath)
    if check.outcome == "intact":
        return
    if check.outcome == "unverifiable":
        # Reported at the same level a real finding would be — never
        # silently passed. Whether the file OUGHT to exist is a different
        # check's business.
        if check.reason == "no_repo":
            r.add("warning", "W_LEDGER_UNVERIFIABLE", relpath,
                  f"{relpath}: append-only cannot be verified — {bundle.root} "
                  "is not a git repository, so there is no committed baseline "
                  "to compare the working tree against; commit the bundle so "
                  "a later edit can be checked against a real HEAD version — "
                  "until then this file is observed, not verified")
        else:
            r.add("warning", "W_LEDGER_UNVERIFIABLE", relpath,
                  f"{relpath}: append-only cannot be verified — the file "
                  "exists in the working tree but has no committed version "
                  "at HEAD yet (never committed), so there is nothing to "
                  "compare it against; commit this file so a later edit can "
                  "be checked against a real HEAD version — until then it is "
                  "observed, not verified")
        return
    # outcome == "rewritten"
    r.add("error", "E_LEDGER_REWRITTEN", relpath,
          f"{relpath} was rewritten, not appended: the index or the working "
          f"tree diverges from the committed HEAD version at byte offset "
          f"{check.offset} (row {check.row}) — {relpath} is append-only, so "
          "committed content must stay a byte-prefix of both the staged "
          "and the working copy, and an already-committed row was edited "
          "or removed. Repair: restore the file from git (the committed "
          f"HEAD:{relpath} blob), re-stage it, then re-append the "
          "corrected decision as a NEW row — the ledger never edits a row "
          "in place. This check compares the index and the working tree "
          "against HEAD only and proves nothing about a rewrite that was "
          "itself already committed; it catches an uncommitted (or "
          "staged-but-uncommitted) edit, which is the case that actually "
          "happens.")


def _check_verified(concepts, r: Report):
    """A verification binds to the text it verified (`content`, sha256 of the
    body). When the latest one no longer matches, the current text is unverified
    and every earlier verification is history — never an error, because an
    owner `refine` is a legitimate edit, but never silent either.

    An entry without `content` (a pre-v0.23 or foreign OKF `verified`) binds to
    nothing, so there is nothing to compare: exempt by construction."""
    import hashlib
    for c in concepts:
        v = c.meta.get("verified")
        if not isinstance(v, list) or not v or not isinstance(v[-1], dict):
            continue
        want = v[-1].get("content")
        if want is None:
            continue
        if str(want) != hashlib.sha256(c.body.encode("utf-8")).hexdigest():
            r.add("warning", "W_VERIFIED_SUPERSEDED", c.id,
                  f"text changed after its last verification ({v[-1].get('by')}, "
                  f"{v[-1].get('at')}) — that verification is now historical; "
                  "re-verify with `okfy propose` and `okfy review accept`")

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


def _cited_paths(concepts) -> set[str]:
    """Every corpus path any non-meta concept cites, anchors stripped."""
    out = set()
    for c in concepts:
        if c.id.startswith("meta/"):
            continue
        srcs = c.meta.get("sources") or []
        for s in (srcs if isinstance(srcs, list) else [srcs]):
            out.add(_source_path(str(s)))
    return out


def _check_coverage(bundle: Bundle, concepts, r: Report):
    """Reverse source coverage: corpus files the extraction was ASSIGNED that no
    concept cites. `_check_sources` measures only the forward direction — every
    cited path resolves — which stays green when a whole segment yielded nothing.

    The denominator is the plan's `done` segments, not the corpus manifest: the
    manifest is a raw rglob (images, lockfiles), while segments encode the
    deliberate scope, `--include`/`--exclude` included. Files of segments not yet
    `done` are not gaps — extraction has not run — so they are excluded and
    counted as `pending_files`.

    Never an error, at any strictness (same rule as `_check_budget`): a file
    legitimately yields no concept — an empty `__init__.py`, a licence, a source
    register — and no threshold separates that from a real gap. Both files and
    bytes are reported because they disagree informatively: measured over the
    eight real bundles, rayforce-api-okf reads 91% by file but 99% by byte (seven
    tiny examples), while rayforce-py-okf reads 75% and 88% — whole modules
    missing. Bytes need a readable corpus tree; without one they read
    `unavailable`, never 0.
    """
    try:
        plan = bundle.plan()
        snap = bundle.get("meta/corpus")
        purpose = bundle.purpose()
    except frontmatter.FrontmatterError:
        return  # layer 1's problem
    if plan is None or purpose.get("exported") or (snap and snap.meta.get("exported")):
        return
    assigned: set[str] = set()
    planned: set[str] = set()          # any status — the outside-scope basis
    pending_files = 0
    for s in plan.meta.get("segments") or []:
        files = s.get("files") or []
        paths = {f["path"] if isinstance(f, dict) else str(f) for f in files}
        planned |= paths
        if s.get("status") == "done":
            assigned |= paths
        else:
            pending_files += len(paths)
    if not assigned:
        return  # nothing extracted yet, or a bundle built without segments

    cited = _cited_paths(concepts)
    uncited = sorted(assigned - cited)

    # Outside-scope is narrowed to paths that ARE in the corpus. Measured over
    # the eight real bundles the unnarrowed rule found three: two were
    # `meta/lexicon.md` — a bundle file cited as corpus evidence, already
    # reported by `_check_sources` as W_BAD_SOURCE — and one was
    # `src/store/splay.h`, a real corpus file no segment ever assigned. Only the
    # third is news; the others were this check restating a finding the reader
    # already has. Without a manifest or a readable corpus tree the two cases
    # are indistinguishable, so the list reads `unavailable` instead of guessing.
    in_corpus = _source_checker(bundle)
    outside_state = "measured" if in_corpus else "unavailable"
    outside = sorted(p for p in cited - planned if in_corpus(p)) if in_corpus else []

    corpus = Path(str(snap.meta.get("corpus") or "")) if snap else Path("")
    measurable = corpus.is_dir()

    def _size(p: str) -> int:
        f = (corpus / p).resolve()
        return f.stat().st_size if f.is_relative_to(corpus.resolve()) and f.is_file() else 0

    files_pct = round(100 * (len(assigned) - len(uncited)) / len(assigned))
    cov = {"assigned_files": len(assigned), "uncited_files": len(uncited),
           "files_pct": files_pct, "pending_files": pending_files,
           "bytes_state": "measured" if measurable else "unavailable",
           "assigned_bytes": None, "uncited_bytes": None, "bytes_pct": None,
           "uncited": uncited, "outside_scope": outside,
           "outside_scope_state": outside_state}
    byte_note = ""
    if measurable:
        total = sum(_size(p) for p in assigned)
        missed = sum(_size(p) for p in uncited)
        cov["assigned_bytes"] = total
        cov["uncited_bytes"] = missed
        cov["bytes_pct"] = round(100 * (total - missed) / total) if total else None
        # Largest first: the ranking IS the triage — a 23 kB uncited chapter and
        # a 0 B `__init__.py` are the same finding by count and nothing alike.
        cov["uncited"] = sorted(uncited, key=lambda p: (-_size(p), p))
        if cov["bytes_pct"] is not None:
            byte_note = f", {cov['bytes_pct']}% by byte"
    r.coverage = cov

    if uncited:
        r.add("warning", "W_CORPUS_COVERAGE", "meta/extraction-plan.md",
              f"{len(uncited)} of {len(assigned)} assigned corpus files are cited by "
              f"no concept ({files_pct}% covered by file{byte_note}) — "
              "a file may legitimately yield nothing; see coverage.uncited")
    for p in outside:
        r.add("warning", "W_SOURCE_OUTSIDE_SCOPE", "meta/extraction-plan.md",
              f"in the corpus but assigned to no segment, yet cited as a source: {p}")


SPAN_ATTESTED_NOTE = ("span outcomes are reported by the worker, not measured — "
                      "the core cannot observe what a worker actually read, only "
                      "that its report partitions the frozen job artifact")


def _check_span_coverage(bundle: Bundle, r: Report):
    """The ATTESTED half of coverage (v0.19). `_check_coverage` above measures
    which assigned files no concept cites; this one reads what the worker SAID
    happened to each span it was handed. The two are different categories and
    neither replaces the other — see `_check_span_contradiction` for what their
    disagreement buys.

    What is checked here is the only thing that CAN be checked: the report must
    partition the job artifact's input span set exactly — no span missing, none
    invented, none in two classes at once. Those are errors, because the job
    artifact is frozen and the comparison is arithmetic. Whether a span called
    `reviewed_empty` was ever opened is unknowable to the core, so the content of
    the claim is never graded, and the summary says so in those words. Same
    discipline as `_check_execution`.

    A `done` segment with no span data is a WARNING at every strictness: eight
    real bundles predate this block entirely and must not turn red. The release
    gate is where it becomes an obligation.
    """
    from okfy.ledger import (SPAN_CLASSES, job_span_keys, latest_span_rows,
                             unknown_covered_outputs)
    try:
        plan = bundle.plan()
        purpose = bundle.purpose()
    except frontmatter.FrontmatterError:
        return  # layer 1's problem
    if plan is None or purpose.get("exported"):
        return
    done = [str(s.get("id")) for s in (plan.meta.get("segments") or [])
            if isinstance(s, dict) and s.get("status") == "done"]
    if not done:
        return

    latest = latest_span_rows(bundle)
    totals = dict.fromkeys(SPAN_CLASSES, 0)
    without = []
    for seg in done:
        row = latest.get(seg)
        if row is None:
            without.append(seg)
            continue
        spans = row["spans"]
        for cls in SPAN_CLASSES:
            totals[cls] += len(spans.get(cls) or {})
        jf = bundle.root / "meta" / "jobs" / f"{seg}.json"
        if not jf.is_file():
            continue  # a missing artifact is _check_provenance's finding, not a
            # second copy of it here — but without it there is no denominator
        try:
            job = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        where = f"meta/ledger.jsonl ({seg})"
        assigned = set(job_span_keys(job))
        seen: dict[str, list[str]] = {}
        for cls in SPAN_CLASSES:
            for key in spans.get(cls) or {}:
                seen.setdefault(key, []).append(cls)
        doubled = sorted(k for k, cs in seen.items() if len(cs) > 1)
        for key in doubled:
            r.add("error", "E_SPAN_DOUBLE", where,
                  f"span {key} is declared {' and '.join(seen[key])} at once — "
                  "an outcome is a partition, not a set of labels")
        missing = sorted(assigned - set(seen))
        if missing:
            r.add("error", "E_SPAN_UNACCOUNTED", where,
                  f"{len(missing)} assigned span(s) have no recorded outcome "
                  f"(e.g. {', '.join(missing[:3])}) — the report is incomplete, "
                  "which is exactly the gap it exists to close")
        extra = sorted(set(seen) - assigned)
        if extra:
            r.add("error", "E_SPAN_UNKNOWN", where,
                  f"{len(extra)} span(s) are not in the job artifact "
                  f"(e.g. {', '.join(extra[:3])}) — the artifact is the "
                  "denominator; a span outside it was never assigned")
        # The join, for rows already on disk. `add_row` refuses this at write
        # time, but a ledger is append-only and rows written before the check
        # existed cannot be refused retroactively — they can only be found.
        unknown = unknown_covered_outputs(spans, row.get("outputs"))
        if unknown:
            r.add("error", "E_SPAN_OUTPUT", where,
                  f"{len(unknown)} covered span(s) name draft(s) this row does "
                  f"not list in outputs (e.g. {', '.join(unknown[:3])}) — the "
                  "two halves of one row disagree about what the pass produced")

    for seg in without:
        r.add("warning", "W_SPAN_COVERAGE_MISSING", f"meta/ledger.jsonl ({seg})",
              "done segment has no span outcome report — nothing records what "
              "happened to the material it was assigned")

    r.spans = {"source": "attested", "note": SPAN_ATTESTED_NOTE,
               "done_segments": len(done), "with_spans": len(done) - len(without),
               "segments_without_spans": without,
               "covered": totals["covered"],
               "reviewed_empty": totals["reviewed_empty"],
               "dropped": totals["dropped"]}


def _check_span_contradiction(bundle: Bundle, r: Report):
    """Where the attested and the measured halves disagree. A span the worker
    declared `covered` — "I produced a concept from this" — whose file appears in
    `coverage.uncited` — "no concept cites this file" — is a claim the bundle
    itself contradicts. Neither check finds it alone: the ledger has no idea what
    the concepts cite, and the coverage check has no idea what was claimed.

    A WARNING at every strictness, on purpose. The honest alternative reading is
    common and legitimate: a worker may have folded a span's content into a
    concept that cites a sibling path, or cited the file at a different anchor
    than the one it was handed. The finding gives a reader something to judge; it
    is not a verdict, and it never blocks.

    One finding per (segment, path), never per span. A file handed out as forty
    line ranges would otherwise produce forty copies of one observation, which is
    how a useful signal becomes noise nobody reads.
    """
    from okfy.ledger import latest_span_outcomes
    if r.coverage is None:
        return  # nothing measured to contradict
    uncited = set(r.coverage.get("uncited") or [])
    if not uncited:
        return
    for seg, spans in sorted(latest_span_outcomes(bundle).items()):
        paths = {k.split("#", 1)[0] for k in (spans.get("covered") or {})}
        for path in sorted(paths & uncited):
            r.add("warning", "W_SPAN_COVERAGE_CONTRADICTION",
                  f"meta/ledger.jsonl ({seg})",
                  f"segment {seg} declares {path} covered, but no concept cites "
                  "it — the worker may have folded it into a concept citing a "
                  "sibling path, or the material may have been lost; the ledger "
                  "is the worker's report and the citation is the measurement")


def _output_exists(bundle: Bundle, output: str) -> bool:
    """Does this declared output resolve to something on disk right now?

    Two ways to resolve, both against the FILESYSTEM, never against the
    shape of the path string:

    - CONCEPT granularity: `bundle.get(output)` finds the exact `.md` file
      (e.g. `drafts/segment-02/access-table-data`, or an already-final
      `strategies/x`).
    - DIRECTORY granularity: `output` names a directory that still exists
      and holds at least one concept (e.g. `operations`, standing in for
      "these concepts were written under here") — the directory itself is
      never a concept, so its presence is judged by what is demonstrably
      inside it.

    v0.25 first tried a PATH-PREFIX rule instead of a filesystem check:
    anything shaped like `drafts/<segment>` (two path parts under `drafts/`)
    was exempted from drop accounting outright, on the theory that a sweep of
    the real bundles found `rayforce-api-okf` declaring outputs that way. Running the corrected check against that
    real bundle by path proved the prefix rule backwards: its nine declared
    outputs are five CATEGORY directories (`contracts`, `operations`,
    `types`, `topics`, `recipes` — 117 concepts between them, every one
    still present) plus four `drafts/segment-01..04` rows whose directories
    no longer exist at all, consolidated into the category directories with
    no `merge_map` ever recording where they went. A prefix rule silences
    exactly the wrong five and reports exactly the wrong four — it answers
    "what does the path look like" when the only question that matters is
    "does this exist". This function asks that question directly, for
    either granularity, against the bundle's actual files."""
    if bundle.get(output) is not None:
        return True
    d = (bundle.root / output).resolve()
    if not d.is_relative_to(bundle.root) or not d.is_dir():
        return False
    return next(d.rglob("*.md"), None) is not None


def _check_drops_unexplained(bundle: Bundle, r: Report):
    """W_DROPS_UNEXPLAINED: a declared output — a draft id some ledger row
    claims to have written, at either granularity (`_output_exists`) — that
    vanished with no recorded reason. Never blocks, at any strictness: a
    warning about missing bookkeeping, not a defect in the concepts.

    ACCOUNTED FOR: resolves on disk, or some row's `merge_map` (read
    ledger-wide, unioned) names the final that absorbed it. `dropped` is read
    the OPPOSITE way, per row: each row's own budget offsets only that row's
    own unaccounted outputs, never another's — a large count on one row must
    never mask a different row's genuinely vanished drafts. What remains
    after that per-row subtraction is the reported gap. A row whose
    `outputs` is not a list is reported malformed, not iterated character by
    character into bogus draft ids.

    CORRECTIONS: the ledger is append-only, so the way out is a LATER row
    whose `corrects` names an earlier row (`run_id`/`segment`) and the one
    `output` it accounts for, plus its own `dropped` explanation. Credited
    only through `ledger.check_corrects` (the writer's own shape
    validation — a hand-appended row shaped like something the writer would
    refuse is simply not credited, never a crash), and only when the
    `dropped` total is POSITIVE and the (run_id, segment, output) triple
    identifies EXACTLY ONE earlier row that STILL HAS an outstanding gap for
    that output — zero or more than one refuses the credit. Binding to the
    specific row, not the bare triple, lets a later unrelated pass legally
    redeclare the same output without an earlier correction leaking forward
    to excuse it.

    History (the A8 tightening of `corrects`) is in CHANGELOG.md's v0.27.0
    entry."""
    from okfy.ledger import check_corrects, read_rows
    try:
        purpose = bundle.purpose()
    except frontmatter.FrontmatterError:
        return  # layer 1's problem
    if purpose.get("exported"):
        return  # drafts/meta/ledger routinely do not survive a public export
    rows = read_rows(bundle)
    if not rows:
        return

    merge_map: dict[str, str] = {}
    for row in rows:
        mm = row.get("merge_map")
        if isinstance(mm, dict):
            merge_map.update({str(k): str(v) for k, v in mm.items()})

    def _row_accounting(row: dict):
        """This row's own (declared outputs, outputs not yet resolved via
        `merge_map`/`_output_exists`, own `dropped` total) — the exact same
        three numbers the final pass below needs, computed once so a
        correction can be matched against a row's OWN outstanding gap
        rather than merely against what it once declared. `None` for a row
        whose `outputs` is not a list (reported, not iterated, by the
        final pass)."""
        outputs = row.get("outputs")
        if not isinstance(outputs, list):
            return None
        declared = sorted({str(o) for o in outputs})
        unaccounted = [o for o in declared
                      if o not in merge_map and not _output_exists(bundle, o)]
        dropped = row.get("dropped")
        dropped_total = 0
        if isinstance(dropped, dict):
            # Booleans are ints in Python, but `ledger.check_dropped`
            # explicitly refuses a boolean count on the write path — the
            # reader must agree, not silently count `True` as 1.
            dropped_total = sum(v for v in dropped.values()
                                if isinstance(v, int) and not isinstance(v, bool))
        return declared, unaccounted, dropped_total

    info = [_row_accounting(row) for row in rows]

    # Credit is bound to the SPECIFIC earlier row a correction can only have
    # meant (its list index in `rows`), never to the (run_id, segment,
    # output) triple alone — see the CORRECTIONS section above.
    corrected: set[tuple[int, str]] = set()
    for i, row in enumerate(rows):
        c = row.get("corrects")
        if not isinstance(c, dict):
            continue
        # Reuse the WRITER's own shape validation — the same
        # `ledger.check_corrects` (and, nested inside it,
        # `ledger.check_dropped`) `ledger.add_row` runs before a correction
        # ever reaches disk — instead of re-implementing an isinstance
        # check that can silently accept a shape the writer refuses. A
        # hand-appended row can carry anything: a negative or boolean
        # dropped count, a blank reason key, a non-dict corrects block.
        # Every one of those raises here exactly as it does on write, and
        # an unreadable correction is simply not credited, never a crash
        # (v0.26 audit A8).
        try:
            normalized = check_corrects(c)
        except ValueError:
            continue
        # POSITIVE explanatory accounting: `check_corrects` only requires a
        # non-empty `dropped` block, which `{"irrelevant": 0}` satisfies —
        # a correction that explains zero losses explains nothing.
        if sum(normalized["dropped"].values()) < 1:
            continue
        # UNIQUE earlier transition: the correction names a (run_id,
        # segment, output), but that triple must resolve to exactly one
        # EARLIER row that STILL HAS an outstanding gap for that specific
        # output — not merely one that once declared it. A row whose own
        # `dropped` budget already fully offsets its declared outputs (see
        # `test_correction_for_row_a_does_not_silence_row_b`'s sibling
        # fixture: a second, independent row can legitimately reuse the
        # same run_id/segment/output and self-explain via its own
        # `dropped`) is not a live candidate — only a row this correction
        # would actually change counts. Zero live candidates is an unknown
        # target; more than one is ambiguous — both are refused rather than
        # credited to whichever matches.
        candidates = [j for j in range(i)
                     if info[j] is not None
                     and rows[j].get("run_id") == normalized["run_id"]
                     and rows[j].get("segment") == normalized["segment"]
                     and normalized["output"] in info[j][1]
                     and len(info[j][1]) - info[j][2] > 0]
        if len(candidates) == 1:
            corrected.add((candidates[0], normalized["output"]))

    total_gap = 0
    examples: list[str] = []
    for idx, row in enumerate(rows):
        outputs = row.get("outputs")
        if outputs is None:
            continue
        if info[idx] is None:
            where = f"{row.get('run_id')}/{row.get('segment')}"
            r.add("warning", "W_DROPS_UNEXPLAINED", "meta/ledger.jsonl",
                  f"ledger row {where} is malformed: `outputs` must be a "
                  f"list of draft ids, got {type(outputs).__name__} — "
                  "treated as reporting nothing rather than reasoned about "
                  "character by character; fix the row so outputs is a list")
            continue
        _declared, unaccounted, row_dropped_total = info[idx]
        row_unaccounted = [o for o in unaccounted if (idx, o) not in corrected]
        row_gap = len(row_unaccounted) - row_dropped_total
        if row_gap > 0:
            total_gap += row_gap
            for o in row_unaccounted:
                if len(examples) >= 2:
                    break
                if o not in examples:
                    examples.append(o)

    if total_gap > 0:
        examples_str = ", ".join(examples)
        r.add("warning", "W_DROPS_UNEXPLAINED", "meta/ledger.jsonl",
              f"{total_gap} declared draft(s) vanished with no recorded reason "
              f"(e.g. {examples_str}) — the ledger is append-only, so the "
              "original row cannot be edited to explain them; append a new "
              "row whose `corrects` block names the original run_id/segment "
              "and this output (with its own `dropped` explanation), or "
              "name the final that absorbed them in `merge_map`")


ANCHOR_LINE_RE = re.compile(r"^L(\d+)(?:-L(\d+))?$")
MD_EXTS = {".md", ".markdown"}


# --------------------------------------------------------------------------
# v0.25 quote check (phase 6, report item 2.1): whether an optional `quote:`
# — the literal words an agent read at a cited span — matches the span it
# names. The seven normalizations below, and nothing else, are the ones
# the release's measurement permitted, fixed before any quote was sampled: at most 2 of 50 genuine quotes may fail to be found, every
# failure attributable to one of these seven, and all 20 seeded wrong quotes
# must be caught.
#
# AMENDED (same file, "Amendment, declared before re-measuring", committed
# after the first 0/50 run): that first run found rule 7 dead — rule 2's
# whitespace collapse ran first and replaced every newline with a space
# before rule 7 could ever see a hyphen immediately followed by one. The
# 0/50 was real for what it measured, but the protocol's own genuine-quote
# construction (a byte-literal slice of the span) put the identical
# hyphen+newline on both sides of the comparison, so it canceled out and
# never exercised rule 7 either way. QUOTE_NORMALIZATION_RULES is now
# ORDERED BY APPLICATION, not by the threshold file's original numbering —
# rule 7 runs second, right after NFKC and before rule 2, so a hyphen+
# newline is joined while the newline still exists. The set of seven rules
# is unchanged; only the sequence changed, and only to make a rule that was
# declared to function actually function. Pinned as data — not re-derived
# from prose — so a test can assert against it directly
# (`core/tests/test_quote_match.py`).
# --------------------------------------------------------------------------
QUOTE_NORMALIZATION_RULES = (
    "1. Unicode NFKC",
    "7. hyphen immediately followed by U+000A removed with the newline",
    "2. run of whitespace (incl. tab, newline, U+00A0) -> single space",
    "3. strip leading/trailing whitespace",
    "4. typographic quotes/apostrophes U+2018/2019/201C/201D -> ASCII '/\"",
    "5. dashes U+2010-U+2015 -> ASCII '-'",
    "6. soft hyphen U+00AD removed",
)

_QUOTE_TYPOGRAPHIC = {"‘": "'", "’": "'", "“": '"', "”": '"'}
_QUOTE_DASH_TRANSLATE = {cp: "-" for cp in range(0x2010, 0x2016)}
_QUOTE_WS_RE = re.compile(r"\s+")
_QUOTE_DEHYPHENATE_RE = re.compile(r"-\n")


def normalize_quote(s: str) -> str:
    """Exactly the seven rules named in `QUOTE_NORMALIZATION_RULES`, applied
    in THAT (application) order — NFKC, then dehyphenation, then whitespace
    collapse, strip, typographic-quote mapping, dash mapping, soft-hyphen
    removal. Dehyphenation runs before the whitespace collapse specifically
    so the newline it looks for still exists when it runs — see the
    amendment note above. Applied identically to the quote and to the span
    before the substring check."""
    s = unicodedata.normalize("NFKC", s)
    s = _QUOTE_DEHYPHENATE_RE.sub("", s)
    s = _QUOTE_WS_RE.sub(" ", s)
    s = s.strip()
    for typo, ascii_ in _QUOTE_TYPOGRAPHIC.items():
        s = s.replace(typo, ascii_)
    s = s.translate(_QUOTE_DASH_TRANSLATE)
    s = s.replace("­", "")
    return s


def _quote_first_divergence(quote: str, span: str) -> str:
    """A short, human-readable description of where a normalized `quote`
    (known not to be a substring of normalized `span`) stops matching:
    grows the matched prefix as long as it stays a substring of `span`
    (monotonic — if quote[:k+1] is in span then so is quote[:k], at the same
    position), then reports what comes right after."""
    lo = 0
    while lo < len(quote) and quote[:lo + 1] in span:
        lo += 1
    matched = quote[:lo]
    tail = quote[lo:lo + 20]
    if not matched:
        return f"quote does not match the span from its first character: {tail!r}"
    return f"matched {matched!r}, then {tail!r} was not found next"


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


def heading_spans(text: str) -> dict[str, tuple[int, int]]:
    """Every heading slug mapped to the 1-based inclusive line span it owns.

    The span runs from the heading line to the line before the next heading of
    the SAME OR HIGHER level, or to end of file — so `## Risk` owns its `###`
    subsections and stops at the next `##`. That is what a reader means by "the
    part under this heading", and it is what a citation of `guide.md#risk`
    claims to be evidence for.

    Slug variants come from `_heading_slugs`'s dialects and all point at the
    same span: this exists to resolve an anchor to lines, not to referee
    sluggers. Where two headings produce the same slug the FIRST wins, because
    a duplicate slug is exactly what a reader's browser would jump to."""
    lines = text.splitlines()
    heads = []                      # (line_no, level, [slugs])
    for i, line in enumerate(lines, 1):
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m:
            heads.append((i, len(m.group(1)), _heading_slugs(m.group(0))))
    out: dict[str, tuple[int, int]] = {}
    for n, (start, level, slugs) in enumerate(heads):
        end = len(lines)
        for later_start, later_level, _ in heads[n + 1:]:
            if later_level <= level:
                end = later_start - 1
                break
        for s in slugs:
            out.setdefault(s, (start, max(end, start)))
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


def _check_quotes(bundle: Bundle, concepts, r: Report):
    """Optional `source_quotes:` (a concept-level mapping of one of its
    `sources:` ref strings to the literal words the agent read there) lets a
    citation carry proof, not just a pointer. This checks that proof.

    Deliberately a SEPARATE field from `sources:`, not a richer shape for
    each `sources:` entry: a dozen call sites across the core (merge_audit,
    sampling, cost, eval_metrics, update.py, sourcemap.py, this module's own
    `_check_sources`/`_check_anchors`) coerce every `sources:` entry with
    `str(s)` or split it on `#`, and none of them are touched by this
    feature — a bundle without `source_quotes:` is byte-identical in every
    code path that matters to it, and `sources:` itself never changes shape.
    This is what "optional forever, backwards compatible" means in practice,
    not just in the schema.

    A quote's span failing to RESOLVE at all (bad ref, unreadable file, no
    locally readable corpus) is `W_BAD_SOURCE`/`W_BAD_ANCHOR`/
    `W_ANCHOR_UNCHECKED`'s finding, not this one — this only fires once the
    span resolved and the (normalized) quote was not found inside it. Always
    a warning, never escalated by any `--strict-*` flag: the field is new,
    nothing here retroactively fails a bundle for not having verified a
    thing it never claimed to."""
    try:
        snap = bundle.get("meta/corpus")
        purpose = bundle.purpose()
    except frontmatter.FrontmatterError:
        return
    if snap is None or purpose.get("exported") or snap.meta.get("exported"):
        return
    corpus = Path(str(snap.meta.get("corpus") or ""))
    if not corpus.is_dir():
        return  # no local corpus — nothing to check a quote against
    root = corpus.resolve()
    from okfy.sourcemap import cited_span  # local: see _check_source_map
    # Per-run cache, keyed by resolved path, LOCAL to this one call — never a
    # module-level global, which would go stale between validate runs in the
    # same process. Many concepts routinely cite the same corpus file (the
    # common case: many drafts drawn from one source document), and without
    # this the file was read and re-split into lines once per QUOTE rather
    # than once per file.
    lines_cache: dict[str, list[str] | None] = {}
    for c in concepts:
        quotes = c.meta.get("source_quotes")
        if not isinstance(quotes, dict) or not quotes:
            continue
        srcs = c.meta.get("sources") or []
        srcs = {str(s) for s in (srcs if isinstance(srcs, list) else [srcs])}
        for ref, quote in quotes.items():
            ref = str(ref)
            if ref not in srcs or not isinstance(quote, str) or not quote.strip():
                continue  # a stray/malformed entry — not this check's finding
            # `lines_cache` threaded straight into `cited_span`: for a
            # line/ledger-anchored ref it does its own F11 bounds check by
            # reading through this same dict (see `sourcemap._cited_lines`),
            # so the read below is a cache hit, not a second read of the
            # file — the whole point of the cache this function already
            # built.
            path, start, end = cited_span(ref, root, lines_cache=lines_cache)
            if start is None:
                continue  # unresolved span: an anchor/source finding, not this one
            f = (root / path).resolve()
            if not f.is_relative_to(root) or not f.is_file():
                continue
            key = str(f)
            if key not in lines_cache:
                try:
                    lines_cache[key] = f.read_text(encoding="utf-8").splitlines(keepends=True)
                except (OSError, UnicodeDecodeError):
                    lines_cache[key] = None
            lines = lines_cache[key]
            if lines is None:
                continue
            # Not dead code: `cited_span`'s own bounds check covers the
            # bare-path and line/ledger-anchor branches (see its docstring),
            # but its char-anchor branch clamps rather than validates
            # ordering — a reversed anchor like `file.md#C100-C5` can still
            # come back with `end < start`. This check is the one place that
            # catches that case, so it stays; it is cheap, and removing it
            # would mean teaching `cited_span` to validate char-anchor
            # ordering too, which is a different fix than this one.
            if start < 1 or end < start or end > len(lines):
                continue  # invalid line range: W_BAD_ANCHOR's finding, not this one
            span = "".join(lines[start - 1:end])
            norm_quote = normalize_quote(quote)
            norm_span = normalize_quote(span)
            if norm_quote in norm_span:
                continue
            r.add("warning", "W_QUOTE_NOT_IN_SPAN", c.id,
                  f"quote for {ref} not found in the cited span ({path}#L{start}-"
                  f"L{end}) after normalization — "
                  f"{_quote_first_divergence(norm_quote, norm_span)} — fix the "
                  "quote to match the cited text verbatim (copy it again from "
                  "the span), or correct the anchor to the span it was "
                  "actually read from")


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


def _index_mode(bundle: Bundle) -> str:
    """"sharded" or "flat" (default), read from meta/package.json's `index`
    key — written by `okfy package --shard-index`, absent otherwise. Read
    here rather than by sniffing the `index/` directory so a leftover
    hand-copied file cannot flip the mode a validator checks against."""
    p = bundle.root / "meta" / "package.json"
    if not p.is_file():
        return "flat"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "flat"
    return "sharded" if data.get("index") == "sharded" else "flat"


def _shard_files(bundle: Bundle) -> list[Path]:
    d = bundle.root / "index"
    return sorted(d.glob("*.md")) if d.is_dir() else []


def _check_orphans(bundle, concepts, linked_ids, r: Report, strict=False):
    """strict (--strict-package): an unreachable concept is an error — agents
    following the consumption protocol through index.md must find everything.

    v0.24 (a): in sharded mode a concept listed in ANY `index/<dir>.md` shard
    counts as listed — the resident index.md itself only carries directory
    summary lines for sharded concepts, so reading it alone would call every
    one of them an orphan."""
    idx = bundle.root / "index.md"
    indexed = set()
    if idx.is_file():
        for target in LINK_RE.findall(idx.read_text(encoding="utf-8")):
            cid = resolve_link(bundle, idx, target)
            if cid:
                indexed.add(cid)
    if _index_mode(bundle) == "sharded":
        for f in _shard_files(bundle):
            for target in LINK_RE.findall(f.read_text(encoding="utf-8")):
                cid = resolve_link(bundle, f, target)
                if cid:
                    indexed.add(cid)
    level, code = ("error", "E_ORPHAN") if strict else ("warning", "W_ORPHAN")
    for c in concepts:
        if c.id.startswith("meta/"):
            continue
        if c.id not in indexed and c.id not in linked_ids:
            r.add(level, code, c.id, "not reachable from index.md or any concept")


INDEX_LINE_RE = re.compile(r"^- \[[^\]]*\]\(([^)\s]+)\)")


def _check_index_drift(bundle, concepts, r: Report):
    """okfy package renders every index line from its concept's description, so
    a line that no longer carries it was edited by hand (or the concept changed
    after packaging) and the index now tells agents something the concept does
    not say.

    v0.24 (a): in sharded mode the concept lines live in `index/<dir>.md`, not
    index.md itself (which carries only directory summaries) — checked the
    same way, file by file."""
    idx = bundle.root / "index.md"
    if not idx.is_file():
        return
    by_id = {c.id: c for c in concepts}

    def _scan(path: Path):
        for line in _index_body(path.read_text(encoding="utf-8")).splitlines():
            m = INDEX_LINE_RE.match(line)
            c = by_id.get(resolve_link(bundle, path, m.group(1))) if m else None
            desc = str(c.meta.get("description", "")).strip() if c else ""
            if desc and _norm(desc) not in _norm(line):
                r.add("warning", "W_INDEX_DRIFT", c.id,
                      f"index.md line for {c.id} no longer carries its description — "
                      "run okfy package to regenerate")
    _scan(idx)
    if _index_mode(bundle) == "sharded":
        for f in _shard_files(bundle):
            _scan(f)


def _package_is_stale(bundle: Bundle) -> bool:
    """True when the concept set has changed since `okfy package` last ran —
    the same signal `_check_package` compares (its own W_/E_STALE_PACKAGE),
    reused here (finding 29) so `_check_index_shard` can tell drift an
    ordinary accepted create/delete explains from real shard corruption."""
    p = bundle.root / "meta" / "package.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        recorded = data.get("fingerprint")
    except (OSError, ValueError):
        return True
    return recorded != package_fingerprint(bundle)


def _check_index_shard(bundle: Bundle, concepts, r: Report):
    """v0.24 (a) integrity, only meaningful in sharded mode: every non-meta
    concept must appear in EXACTLY ONE place — the resident index or exactly
    one `index/<dir>.md` shard — no shard may list a concept that does not
    exist, and no shard file may go unreferenced from the resident index's
    directory listing.

    v0.24 review fix (finding 29): `okfy review accept` never repackages, so
    an accepted create or delete makes the shard set stale in exactly the
    way `_check_package`'s fingerprint already detects — that is ordinary
    package staleness (flat mode reports it as W_/E_STALE_PACKAGE, plus
    orphan/dangling-link warnings that already fire independently; it must
    not ALSO turn into a hard, always-error E_INDEX_SHARD that reddens every
    `okfy validate` between an accept and the next repackage). Only
    corruption staleness cannot explain — a concept listed twice, a shard
    naming a concept missing even though the package is fresh, or a shard
    file structurally disconnected from index.md — stays E_INDEX_SHARD.

    v0.24 review fix (finding 30): a listing is only a line matching
    `INDEX_LINE_RE` (the `- [title](target)` entries package.py itself
    writes) — not every markdown link `LINK_RE` would find anywhere in the
    file, which double-counted a concept merely because another concept's
    `description` (or a plan `categories` value) happened to link to it."""
    if _index_mode(bundle) != "sharded":
        return
    WAY_OUT = "re-run `okfy package --shard-index`"
    idx = bundle.root / "index.md"
    idx_text = idx.read_text(encoding="utf-8") if idx.is_file() else ""
    shard_dir = bundle.root / "index"
    shard_paths = _shard_files(bundle)
    shard_refs = {unquote(t) for t in re.findall(r"\]\(index/([^)\s]+)\.md\)", idx_text)}
    by_id = {c.id: c for c in concepts if not c.id.startswith("meta/")}
    stale = _package_is_stale(bundle)

    for f in shard_paths:
        if f.stem not in shard_refs:
            r.add("error", "E_INDEX_SHARD", f"index/{f.name}",
                  f"shard file is not referenced from index.md — {WAY_OUT}")
    for name in sorted(shard_refs):
        if not (shard_dir / f"{name}.md").is_file():
            r.add("error", "E_INDEX_SHARD", "index.md",
                  f"index.md links index/{name}.md, which does not exist — {WAY_OUT}")

    def _ids(path: Path) -> set[str]:
        found = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            m = INDEX_LINE_RE.match(line)
            if not m:
                continue
            cid = resolve_link(bundle, path, m.group(1))
            if cid is not None:
                found.add(cid)
        return found

    resident_ids = _ids(idx) if idx.is_file() else set()
    per_shard: dict[str, set[str]] = {}
    for f in shard_paths:
        ids = _ids(f)
        per_shard[f.name] = ids
        if stale:
            continue  # a deleted-but-not-yet-repackaged concept explains this
        for cid in sorted(ids - set(by_id)):
            r.add("error", "E_INDEX_SHARD", f"index/{f.name}",
                  f"lists {cid!r}, which is not a concept in this bundle — {WAY_OUT}")

    for c in concepts:
        if c.id.startswith("meta/"):
            continue
        places = (1 if c.id in resident_ids else 0) + \
                 sum(1 for ids in per_shard.values() if c.id in ids)
        if places == 0 and stale:
            continue  # a created-but-not-yet-repackaged concept explains this
        if places != 1:
            r.add("error", "E_INDEX_SHARD", c.id,
                  f"appears in {places} place(s) across the resident index and "
                  f"its shards (want exactly 1) — {WAY_OUT}")


def _check_reserved_dir_concepts(bundle: Bundle, r: Report):
    """v0.24 (a): `index/` and `protocols/` are `bundle.RESERVED_DIRS` —
    `okfy package` writes and prunes everything under them, and
    `Bundle.iter_md_files` skips them unconditionally so nothing there ever
    reaches concept discovery. That is correct for generated output, but it
    means an owner who names a real concept category `protocols/` or
    `index/` gets no error: the concepts just silently stop being validated
    or retrieved. This check is the loud alternative — it reads the reserved
    directories directly (the one place in this module that has to, since
    `iter_md_files` will never yield these paths).

    Telling a concept from generated output is NOT "starts with `---`":
    `index/<dir>.md` shards open with `package.INDEX_HEAD`
    (`---\\nokf_version: "0.2"\\n---`), so a bare frontmatter-block test would
    flag every shard `okfy package --shard-index` ever writes. What no
    renderer in package.py ever puts in a reserved-dir file is a `type:` key
    — the same field `_check_type`'s E_TYPE requires of every real concept —
    so that is the signal used here: a `.md` file under a reserved directory
    whose frontmatter declares a non-empty `type:` looks like a concept that
    wandered somewhere it will never be seen again."""
    for name in sorted(RESERVED_DIRS):
        d = bundle.root / name
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.md")):
            text = p.read_text(encoding="utf-8")
            if not text.startswith("---"):
                continue
            try:
                meta, _ = frontmatter.parse(text)
            except frontmatter.FrontmatterError:
                continue  # E_FRONTMATTER's problem, not this check's
            t = meta.get("type")
            if isinstance(t, str) and t.strip():
                rel = p.relative_to(bundle.root)
                r.add("error", "E_RESERVED_DIR", rel,
                      f"{rel} looks like a concept, but {name}/ is reserved "
                      "for generated files — concepts there are invisible to "
                      "validation and retrieval. Move it to another "
                      f"directory (e.g. {name}-notes/)")


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
    # THE REPLAY, AND WHY IT NO LONGER SKIPS.
    #
    # This used to read "a moved corpus is not replayable — skip silently", and
    # that comment was honest about what it did and wrong about what it meant.
    # A moved corpus is exactly the moment the recorded L3 review stopped
    # describing this bundle, so the single most obvious symptom of a stale
    # review was turning the check OFF. An audit set the seed to
    # `definitely-not-current` and released clean.
    #
    # Two causes, one code, two messages — the remedies differ. A moved corpus
    # means re-run the sample and review what it picks; a changed selector means
    # the sampler itself no longer chooses the same concepts, and the old sample
    # is not the deterministic one for any corpus.
    from okfy.archetype import checks_digest
    from okfy.sampling import (SELECTOR_VERSION, _selector_seed,
                               sample_for_review, sampled_fingerprint)
    live_seed = _selector_seed(bundle)
    rec_sv = c.meta.get("selector_version")
    if str(c.meta.get("seed")) != live_seed:
        r.add(level, code("QUALITY_STALE"), c.id,
              f"the L3 review records seed {str(c.meta.get('seed'))!r} and the "
              f"corpus is now {live_seed!r} — the corpus moved after the review, "
              "so the recorded sample is not the one this bundle would select; "
              "re-run `okfy sample` and review what it picks")
    elif rec_sv != SELECTOR_VERSION:
        r.add(level, code("QUALITY_STALE"), c.id,
              f"the L3 review records selector_version {rec_sv!r} and the "
              f"sampler is now v{SELECTOR_VERSION} — the corpus has not moved "
              "but the selection rule has, so the recorded sample is not the "
              "deterministic one; re-run `okfy sample` and review what it picks")
    else:
        rerun = sample_for_review(
            bundle, fraction=float(c.meta.get("fraction", 0.1)),
            minimum=int(c.meta.get("minimum", 20)))
        missing = sorted(set(rerun["sampled"]) - set(sampled))
        if missing:
            r.add(level, code("QUALITY_SAMPLE"), c.id,
                  f"recorded sample misses deterministic selection: {missing}")

    # WHAT WAS REVIEWED, and WHAT IT WAS REVIEWED AGAINST. The seed pins the
    # corpus; these two pin the concepts' own bytes and the questions the
    # reviewer answered. Without them a verdict recorded on Monday still reads
    # `pass` after the concept is rewritten on Tuesday — the corpus never moved,
    # so nothing above notices.
    #
    # Absent is the pre-v0.21 artifact and is never an error at plain validate.
    # At release it is an error, because "this review does not record what it
    # read" and "this review is current" cannot both be asserted — and the
    # escape is the declaration that already exists for exactly this, rather
    # than a new mechanism: `provenance: legacy` in meta/purpose.md.
    legacy = str(bundle.purpose().get("provenance", "")).strip() == "legacy"
    for pin, want in (("sampled_fingerprint", sampled_fingerprint(bundle, sampled)),
                      ("checks_digest", checks_digest(archetype))):
        have = c.meta.get(pin)
        if have in (None, "", []):
            if not legacy:
                r.add(level, code("QUALITY_UNPINNED"), c.id,
                      f"the L3 review records no {pin} — it does not say what "
                      "it read, so a concept rewritten since cannot be told "
                      "from one that was not. Re-run `okfy sample` and record "
                      "the pins it reports, or declare `provenance: legacy` if "
                      "this bundle predates the field")
        elif str(have) != want:
            r.add(level, code("QUALITY_DRIFT"), c.id,
                  f"{pin} recorded {str(have)[:12]}… and computes to "
                  f"{want[:12]}… — what the review covered changed after it was "
                  "recorded, so its verdicts describe something else. Re-run "
                  "`okfy sample` and redo the L3 pass over what it selects")


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
