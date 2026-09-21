"""Owner-mutator verbs (ADR-0007 refinement loop, ADR-0013 reviewed staleness):
agents propose, the owner reviews/refines/flags staleness. accept/refine/reject
and set_stale/clear_stale are the sanctioned mutators — they commit with
--no-verify because they ARE the authority the pre-commit hook defers to."""
import contextlib
import datetime
import fcntl
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from okfy import frontmatter, memory
from okfy.actor import check_actor, check_owner_actor, owner_actor, utc_now
from okfy.bm25 import tokenize
from okfy.bundle import DRAFT_DIR, PROPOSAL_DIR, RESERVED_DIRS, SKIP_DIRS, Bundle, Concept
from okfy.cluster import _alias_keys, _jaccard, _title_key
from okfy.gitenv import run_git
from okfy.package import append_log
from okfy.sourcemap import cited_span
from okfy.transcript_lint import _SEGMENT
from okfy.validate import (LINK_RE, Report, _check_archetype, _index_mode, _norm,
                           _shard_files, _source_checker, archetype_applies,
                           ledger_prefix_check, resolve_link, review_due_date)

ACTIONS = {"create", "update", "delete", "supersede", "flag", "gap"}
# v0.24 (a): a flag's --type — closed, like ACTIONS itself: an unrecognised
# kind is a refusal, not a new category the owner discovers by accident.
FLAG_TYPES = ("contradicts-source", "merged-entities", "out-of-date",
             "coverage-gap", "other")
# What a proposal says its claim rests on. Closed: an unrecognised kind is a
# refusal, not a new category. Only `agent-inference` may omit `ref` — a test
# run, an owner decision and an external source each have something to point
# at, and a claim to one of them without the pointer is the agent's self-report
# wearing a better label.
EVIDENCE_KINDS = ("test-run", "owner-decision", "external-source", "agent-inference")
E_EVIDENCE = "E_PROPOSAL_EVIDENCE"


E_BASE_MOVED = "E_PROPOSAL_BASE_MOVED"
E_INJECTION = "E_PROPOSAL_INJECTION"
E_SECRET = "E_PROPOSAL_SECRET"
E_REJECTED = "E_PROPOSAL_REJECTED"
E_DUPLICATE = "E_PROPOSAL_DUPLICATE"
W_UNBASED = "W_PROPOSAL_UNBASED"
W_NEAR = "W_PROPOSAL_NEAR"
W_DISTINCT_OVERLAP = "W_DISTINCT_ALIAS_OVERLAP"

# v0.24 lifecycle actions
E_DELETE_EXPECTED = "E_DELETE_EXPECTED"
E_PROPOSAL_LANE = "E_PROPOSAL_LANE"
ORIGINS = ("proposal-accept", "owner-refine", "revert")

# v0.24 intake kinds: flag, gap, patch
E_PROPOSAL_GAP_CAP = "E_PROPOSAL_GAP_CAP"
E_GAP_ROW_EXISTS = "E_GAP_ROW_EXISTS"
E_PATCH_SHAPE = "E_PATCH_SHAPE"
E_PATCH_COUNT = "E_PATCH_COUNT"
E_PROPOSAL_SOURCE = "E_PROPOSAL_SOURCE"
GAP_CAP = 10

# v0.25 (R2): an `action` outside the closed vocabulary used to raise a bare
# `ValueError` (no `E_` prefix) — the MCP adapter's `_CODED` match never
# fired, so this reached an agent as a raw ToolError instead of the
# adapter's promised {error, message, way_out}. See h_propose (handlers.py).
E_PROPOSAL_ACTION = "E_PROPOSAL_ACTION"

# v0.24 (a): `okfy propose --batch` — validate-all-first, write nothing on
# any refusal unless --partial. One code for anything wrong with an entry's
# own shape (bad JSON, unknown key, not an object) BEFORE it ever reaches the
# real propose() gates — a gate refusal from `_prepare_proposal` keeps ITS
# OWN code (E_PROPOSAL_DUPLICATE, E_PROPOSAL_INJECTION, ...), reported the
# same way.
E_BATCH_ENTRY = "E_BATCH_ENTRY"
# Keys mirror propose()'s own keyword parameters, minus `actor` (an entry may
# not declare its own — `--as` applies to the whole batch, see propose_batch's
# docstring) and `observed` (adapter-only, never CLI/batch-filed). `content`
# stands in for `meta`+`body`, exactly like the CLI's `--from`.
BATCH_ENTRY_KEYS = {"action", "target", "note", "content", "evidence", "extends",
                    "reopen", "distinct_from", "new_id", "supersedes", "reverts",
                    "flag_type", "query", "patch"}
_CODE_PREFIX_RE = re.compile(r"^([EW]_[A-Z][A-Z0-9_]*):\s*(.*)$", re.S)

# v0.24 (a): the adapter session trace. `observed` may ONLY arrive through
# the `propose()` keyword — never through the proposal CONTENT's own
# frontmatter, which the author (an agent) controls and could otherwise use
# to claim a read/search history that never happened.
E_PROPOSAL_OBSERVED_FORGED = "E_PROPOSAL_OBSERVED_FORGED"

# Typed evidence-ref grammar (d): a ref matching one of these prefixes is
# checked inside the bundle; anything else (an external CI id, a commit, a
# bare URL) is honestly labelled `unchecked` rather than guessed at — the
# donor's mistake was treating every unresolvable ref as `not-found`, which
# would mislabel the common case (proposals.py's own `test-run` examples are
# external CI ids, not eval run ids).
_TYPED_REF_RE = re.compile(r"^(eval|concept|proposal|log):(.+)$")

NEAR_THRESHOLD = 0.6

# v0.25: the declared-distinct/alias-overlap cutoff, fixed BEFORE the
# real-bundle sweep that measures how many existing `distinct_from` claims
# would fire ran. 0.6 is not a fresh
# choice — it is the number `cluster_drafts` (okfy.cluster) already uses to
# decide that two DRAFT concepts are near-duplicates of each other. A
# `--distinct-from` pair scoring at or above it is exactly the pair the
# clusterer would merge; picking any other cutoff here would leave the core
# holding two different opinions about sameness at once.
DISTINCT_OVERLAP_THRESHOLD = 0.6


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Markdown emphasis markers and trailing sentence punctuation, stripped so a
# rejected claim cannot walk back in re-cased, re-wrapped or re-bolded.
# Punctuation BETWEEN digits (and every other symbol) is kept on purpose:
# folding "1.5" and "15" to the same fingerprint would refuse an unrelated
# number because it happens to share digits with a rejected one.
_TRAIL_PUNCT = ".!?…;:"
_REMOVABLE_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])")


def match_sha256(body: str) -> str:
    """Sha256 of `body` (frontmatter excluded — callers pass the concept body,
    the same slice `content_sha256` hashes) after folding away rewording that
    changes no meaning: Unicode compatibility form, case, markdown emphasis,
    and whitespace/trailing-punctuation noise. Two texts that differ only in
    those ways hash the same; two texts that differ in wording, in a digit,
    or in punctuation between digits do not."""
    s = unicodedata.normalize("NFKC", body).casefold()
    s = s.replace("*", "").replace("`", "")
    s = _REMOVABLE_UNDERSCORE.sub("", s)
    lines = []
    for line in s.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip().rstrip(_TRAIL_PUNCT)
        if line:
            lines.append(line)
    s = " ".join(lines).rstrip(_TRAIL_PUNCT)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def normalize_query(q: str) -> str:
    """v0.24 (b): NFKC, casefold, collapse whitespace, strip trailing
    punctuation — deliberately narrower than `match_sha256` (no markdown or
    underscore folding): a gap's dedup key is a user's plain question, not a
    concept body, and the two ARE allowed to disagree — "a_b" and "ab" are
    different unanswered questions."""
    s = unicodedata.normalize("NFKC", q or "").casefold()
    s = re.sub(r"[ \t\n\r\f\v]+", " ", s).strip()
    return s.rstrip(_TRAIL_PUNCT)


def query_hash(q: str) -> str:
    return hashlib.sha256(normalize_query(q).encode("utf-8")).hexdigest()


@contextlib.contextmanager
def review_lock(bundle: Bundle):
    """One accept or reject at a time per bundle. The base check and the write
    must be one step: checked apart, two accepts from the same base can both
    pass the check and the second still erases the first.

    The lock lives beside the retrieval cache, which is already gitignored —
    an untracked lock under proposals/ would dirty every bundle's worktree."""
    from okfy.index import index_path
    lock = index_path(bundle).parent / "review.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield lock
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def content_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _ledger_rewritten_refusal(check, context: str) -> str:
    """Shared refusal text for `propose`'s rejected-content gate and
    `accept`'s pre-commit check (v0.25 F05) — both catch the exact condition
    `okfy validate` names E_LEDGER_REWRITTEN (`okfy.validate.
    ledger_prefix_check`), worded in the same voice and pointing at the same
    repair. `context` names WHY this caller refuses; the code, the divergence
    location and the repair steps are the one shared part."""
    return (
        f"E_LEDGER_REWRITTEN: {check.relpath} diverges from its committed "
        f"HEAD version at byte offset {check.offset} (row {check.row}) — "
        f"{context}. Repair: restore the file from git (`git show "
        f"HEAD:{check.relpath}`), then re-append the corrected decision as a "
        "NEW row — the ledger never edits a row in place — then try again")


def _flag_reject_hashes(flag_type: str | None, target: str | None,
                        query: str | None, note: str) -> tuple[str, str]:
    """A flag's rejected-content tombstone is keyed on (target-or-normalized-
    query, flag_type, match hash of the note) — NOT on the note's bytes
    bundle-wide. Without the scope prefix, rejecting one flag's note
    tombstones every future flag anywhere that happens to reuse a similar
    short note (a different target, a different flag_type, a different
    actor). `content_sha256`/`match_sha256` still do the real fingerprinting
    of the note text; the scope is folded in ahead of it so two flags in
    different scopes never collide."""
    # `match_sha256` folds whitespace/markdown/case at the true START and END
    # of whatever string it is given — folding it on `scope + note` directly
    # would leave a stray leading space from the note's OWN padding stuck in
    # the MIDDLE of the composite (right after the scope), no longer at an
    # edge `.strip()` reaches. Folding the note alone first, exactly as every
    # other match_sha256(note) call in this module does, then hashing that
    # fingerprint together with the (already-normalized) scope keeps the two
    # concerns — the note's own rewording tolerance, and the flag's scope —
    # independent.
    scope = f"{flag_type or ''}\x1f{target or normalize_query(query or '')}\x1f"
    return content_sha256(scope + note), content_sha256(scope + match_sha256(note))


def check_evidence(evidence) -> dict | None:
    """`{kind, ref}`, and now optionally a third key: `quote`, the literal
    words the agent read at `ref` — the same optional field `sources:`
    carries via `source_quotes:` (`okfy.validate._check_quotes`), offered
    here too since evidence is itself a citation. `quote` is CARRIED, not
    independently verified at propose time: `ref` here is a typed ref
    (`eval:`/`concept:`/`proposal:`/`log:`) or an untyped id/URL/commit, not
    necessarily a corpus-relative `path#L10-L20` `cited_span` can resolve —
    `ref_state` already only checks the four typed prefixes and calls
    everything else `unchecked`, honestly, rather than guessing. A quote
    without a ref to check it against is unconditionally rejected, the same
    reasoning the CLI `--evidence <kind>=<ref>` flag has no syntax for."""
    if evidence is None:
        return None
    how = "pass it as `okfy propose --evidence <kind>=<ref>`"
    if not isinstance(evidence, dict):
        raise ValueError(f"{E_EVIDENCE}: evidence must be a mapping with kind and ref — {how}")
    unknown = sorted(set(evidence) - {"kind", "ref", "quote"})
    if unknown:
        raise ValueError(f"{E_EVIDENCE}: unknown evidence key(s) {unknown}, only "
                         f"kind, ref and quote — {how}")
    kind = evidence.get("kind")
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"{E_EVIDENCE}: kind {kind!r} is not one of "
                         f"{list(EVIDENCE_KINDS)} — {how}")
    ref = str(evidence.get("ref") or "").strip()
    if kind != "agent-inference" and not ref:
        raise ValueError(f"{E_EVIDENCE}: {kind} evidence needs a ref (a run id, "
                         f"commit, decision or URL) — `--evidence {kind}=<ref>`")
    quote = evidence.get("quote")
    if quote is not None and not isinstance(quote, str):
        raise ValueError(f"{E_EVIDENCE}: quote must be a string — {how}")
    quote = quote.strip() if isinstance(quote, str) else ""
    if quote and not ref:
        raise ValueError(f"{E_EVIDENCE}: a quote needs a ref to check it "
                         f"against — {how}")
    out = {"kind": kind, "ref": ref} if ref else {"kind": kind}
    if quote:
        out["quote"] = quote
    return out


def _validate_patch(patch) -> list[dict]:
    """v0.24 (c): `[{old, new, count=1}]`, checked here so the shape error is
    the same whether the JSON came from `--patch-file` or the MCP `patch`
    parameter. Applying the hunks (and the count check) is a separate step —
    this only rejects a shape that could never apply."""
    if not isinstance(patch, list) or not patch:
        raise ValueError(f"{E_PATCH_SHAPE}: patch must be a non-empty list of "
                         "{old, new, count} objects")
    out = []
    for h in patch:
        if not isinstance(h, dict):
            raise ValueError(f"{E_PATCH_SHAPE}: each hunk must be an object "
                             "with old/new/count")
        unknown = sorted(set(h) - {"old", "new", "count"})
        if unknown:
            raise ValueError(f"{E_PATCH_SHAPE}: unknown hunk key(s) {unknown}, "
                             "only old, new, count")
        old = h.get("old")
        if not isinstance(old, str) or not old:
            raise ValueError(f"{E_PATCH_SHAPE}: hunk `old` must be a non-empty string")
        new = h.get("new")
        if not isinstance(new, str):
            raise ValueError(f"{E_PATCH_SHAPE}: hunk `new` must be a string")
        count = h.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError(f"{E_PATCH_SHAPE}: hunk `count` must be a positive integer")
        out.append({"old": old, "new": new, "count": count})
    return out


def _apply_patch(body: str, hunks: list[dict]) -> str:
    """Hunks applied IN ORDER to `body`. Each `old` must occur exactly `count`
    times in the body AS IT STANDS when that hunk is applied (i.e. after
    earlier hunks) — the same rule `str.replace(old, new, count)` enforces,
    just checked first so the message names the real count."""
    for i, h in enumerate(hunks):
        n = body.count(h["old"])
        if n != h["count"]:
            raise ValueError(
                f"{E_PATCH_COUNT}: hunk {i}: expected {h['count']} occurrence(s) "
                f"of the text, found {n} — re-read the concept with `okfy show`, "
                "fix the hunk or set count")
        body = body.replace(h["old"], h["new"], h["count"])
    return body


def _kebab(s: str) -> str:
    out = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return out or "proposal"


# v0.24: one shared grammar for every concept id an agent can SUPPLY as a
# write target — create's --target, supersede's --new-id, --extends — reusing
# the exact segment shape transcript_lint.py already enforces for citations
# (lower-kebab segments) rather than inventing a second dialect. A segment
# that fails this regex can never spell `..`, an empty path piece, or
# anything but ASCII lower-kebab, so those are refused for free.
_ID_SEGMENT_RE = re.compile(rf"^{_SEGMENT}$")
# The bundle's own reserved/generated top levels (bundle.RESERVED_DIRS,
# `okfy package`-owned) and dot-dirs (bundle.SKIP_DIRS), plus the three
# OKF-owned top levels concept discovery itself special-cases: `meta/` (bundle
# config, never a concept), `proposals/` (open proposals — a concept "in"
# proposals/ would read back as an open proposal, not an accepted one) and
# `drafts/` (pre-review extraction output, skipped by iter_md_files unless
# asked for).
RESERVED_ID_ROOTS = {"meta", PROPOSAL_DIR, DRAFT_DIR} | RESERVED_DIRS | SKIP_DIRS


def _validate_write_id(bundle: Bundle, cid: str | None, *, flag: str) -> str:
    """Refuses an id an agent supplied as a WRITE target (create's --target,
    supersede's --new-id, --extends) unless it is relative, contains no `..`,
    no leading `/` or `~`, no backslashes, no empty segment, and every
    segment is a lower-kebab slug — then refuses it again if its first
    segment names a reserved/generated directory (`E_RESERVED_DIR`, the same
    code `okfy validate` raises for a concept that landed there by hand), and
    a last time if the resolved `<id>.md` path would not sit inside
    `bundle.root` at all. Returns `cid` unchanged so call sites can chain it."""
    raw = str(cid or "")
    if not raw or raw != raw.strip():
        raise ValueError(f"--{flag} must be a non-empty concept id with no "
                         f"leading/trailing whitespace: {cid!r}")
    if raw.startswith(("/", "~")) or "\\" in raw or ".." in raw:
        raise ValueError(
            f"--{flag} must be a relative concept id — no leading / or ~, no "
            f"backslashes, no `..`: {raw!r}")
    segments = raw.split("/")
    if any(not _ID_SEGMENT_RE.match(s) for s in segments):
        raise ValueError(
            f"--{flag} must be `/`-separated lower-kebab segments (letters, "
            f"digits, single hyphens, no empty segment): {raw!r}")
    first = segments[0]
    if first in RESERVED_ID_ROOTS:
        raise ValueError(
            f"E_RESERVED_DIR: --{flag} {raw} — {first}/ is reserved for "
            "generated or system files (meta/, proposals/, drafts/, "
            "index/<dir>.md, protocols/memory.md); a concept there is "
            "invisible to validation and retrieval. Choose another directory "
            f"(e.g. {first}-notes/)")
    resolved = (bundle.root / f"{raw}.md").resolve()
    if not resolved.is_relative_to(bundle.root):
        raise ValueError(f"--{flag} must resolve inside the bundle: {raw!r}")
    return raw


def _assert_write_inside_bundle(bundle: Bundle, path: Path) -> None:
    """Last-line defence at WRITE time: propose-time already refuses a
    malformed id (`_validate_write_id`), but a proposal FILE can in
    principle be placed or hand-edited between filing and `review accept` —
    a write that would land outside `bundle.root` must never happen
    silently, so accept re-checks the resolved path right before writing."""
    if not path.resolve().is_relative_to(bundle.root):
        raise ValueError(f"refusing to write outside the bundle: {path}")


def _collect_scan_strings(meta: dict, body: str, *, query: str | None = None,
                          note: str | None = None, reopen: str | None = None,
                          distinct_from: dict[str, str] | None = None,
                          reverts: str | None = None, target: str | None = None,
                          new_id: str | None = None,
                          patch_hunks: list[dict] | None = None) -> list[str]:
    """Every author-controlled string that could end up PERSISTED somewhere —
    the proposal file, `meta/memory.jsonl`, or (at accept) `log.md` — for the
    injection and secret gates to scan. Walks `meta` RECURSIVELY and collects
    every string VALUE (and every dict key) rather than scanning
    `frontmatter.serialize`'s YAML dump: PyYAML folds long plain scalars at
    ~80 columns, and the line-based scanners would miss a phrase whose fold
    happens to land inside it. `body` is included whole (already the right
    slice — for flag/gap it IS `note`). Every other free-text field a
    proposal can carry is added explicitly: `note`, `reopen`, `distinct_from`
    reasons (and their keys, the concept ids they name), `reverts`, `target`,
    `new_id`, `query`, and a patch hunk's `new` text — the replacement text a
    patch installs into the concept body."""
    out: list[str] = []

    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for k, vv in v.items():
                if isinstance(k, str):
                    out.append(k)
                walk(vv)
        elif isinstance(v, (list, tuple, set)):
            for vv in v:
                walk(vv)
        # numbers/bools/None/etc: nothing to scan

    walk(meta)
    if body:
        out.append(body)
    for extra in (query, note, reopen, reverts, target, new_id):
        if extra:
            out.append(str(extra))
    if distinct_from:
        for k, v in distinct_from.items():
            out.append(str(k))
            out.append(str(v))
    if patch_hunks:
        for h in patch_hunks:
            if isinstance(h, dict) and isinstance(h.get("new"), str) and h["new"]:
                out.append(h["new"])
    return out


def _commit(bundle: Bundle, paths: list[str], message: str) -> None:
    """Sanctioned mutator commit: --no-verify past the policy hook. Silently
    skipped when the bundle has no own git repo (embed bundles ride the corpus
    PR flow instead). Paths are staged one by one with check=False: a deleted
    never-tracked file (e.g. an uncommitted proposal being consumed) is a fatal
    pathspec for `git add` and must not abort staging of the other paths."""
    if not (bundle.root / ".git").exists():
        return
    for p in paths:
        run_git(bundle.root, "add", "-A", "--", p,
               check=False, capture_output=True)
    # Nothing staged (e.g. rejecting a never-committed proposal touched no
    # tracked file) is benign — skip. A real commit failure must NOT be
    # swallowed: the mutation is already on disk, and leaving it uncommitted
    # would trap the next commit against the policy hook.
    staged = run_git(bundle.root, "diff", "--cached", "--quiet",
                     capture_output=True)
    if staged.returncode == 0:
        return
    run_git(bundle.root, "commit", "-q", "--no-verify", "-m", message,
           check=True, capture_output=True)


def propose(bundle: Bundle, meta: dict, body: str, target: str | None = None,
            action: str = "update", note: str = "", *, actor: str,
            evidence: dict | None = None, extends: str | None = None,
            reopen: str | None = None,
            distinct_from: dict[str, str] | None = None,
            new_id: str | None = None, supersedes: str | None = None,
            reverts: str | None = None, flag_type: str | None = None,
            query: str | None = None, patch: list[dict] | None = None,
            observed: dict | None = None) -> Path:
    """File a change into proposals/. Thin wrapper over the two halves
    `okfy propose --batch` (v0.24 (a)) needed split apart: `_prepare_proposal`
    does every check and computes the exact bytes to write WITHOUT writing
    anything, `_materialize_proposal` does the write + the memory-ledger
    record. A single propose() call is just prepare-then-materialize with
    nothing shared with any other entry.

    `actor` is required and has no default: a
    proposal whose author is a guess records nothing worth keeping.

    `extends` turns a create into an update of that existing concept — the way
    out of E_PROPOSAL_DUPLICATE. `reopen` states what changed since an identical
    (or reworded-identical) text was rejected or deleted — the way out of
    E_PROPOSAL_REJECTED. `distinct_from` (create only) declares that a
    near-duplicate concept found by `_gate` is NOT the same thing, stated as
    `{concept_id: reason}` — it suppresses W_PROPOSAL_NEAR for the ids it
    covers without blocking anything.

    `new_id` (action `supersede` only) is the id of the successor concept
    `body`/`meta` describe — the OLD concept named by `target` keeps existing,
    flagged stale, and gains `superseded_by` at accept.

    `supersedes` (a proposal id, any action) is the proposal LANE: it replaces
    an open proposal with this new one instead of leaving both open, but only
    when the two proposals agree on WHO and WHAT — same actor, same target.
    Attested, not verified: `--as` is a declaration, and the lane check trusts
    it the same way everything else here does.

    `reverts` (action `update` only) is a git sha this proposal reverts to —
    recorded on the proposal and surfaced as `origin: revert` at accept,
    distinguishing an owner-directed rollback from an ordinary accepted edit.

    v0.24 intake kinds, three more ways to bring something to the owner
    besides a full create/update/delete:

    `action="flag"` — "this is wrong", without pretending to have the fix.
    `flag_type` (required, one of FLAG_TYPES) and either `target` or `query`
    (at least one). The proposal's BODY is `note`. At accept nothing is
    written but the ledger and the ack; nothing else changes.

    `action="gap"` — "the bundle could not answer this". `query` (required)
    is the user's own wording; the proposal's BODY is `note` (why it
    matters). Rejection and open-duplicate dedup are keyed on the NORMALIZED
    query (`normalize_query`/`query_hash`), not on the note's bytes — two
    different notes about the same unanswered question are the same gap.
    Default accept disposition appends one `not-covered` lexicon row keyed to
    the filed phrase verbatim (`E_GAP_ROW_EXISTS` if that term already has a
    row). Capped at GAP_CAP open gap proposals per actor (`E_PROPOSAL_GAP_CAP`).

    `patch` (action `update` only) is `[{old, new, count=1}]`, applied IN
    ORDER to the CURRENT target's body (frontmatter untouched) — `meta`/`body`
    are ignored when `patch` is given; the resulting body is materialized into
    the proposal exactly as a normal update would have, so injection scan,
    secret scan, tombstones, archetype checks and review rendering all work
    unchanged. `E_PATCH_SHAPE` for a malformed patch, `E_PATCH_COUNT` when a
    hunk's declared count does not match the target's current body.

    `observed` (v0.24 (a)) is the MCP adapter's own session trace — attested
    by the adapter PROCESS, never by the author. It may ONLY arrive through
    this keyword (`E_PROPOSAL_OBSERVED_FORGED` if the CONTENT tries to carry
    it) and is stored verbatim at `meta["proposal"]["observed"]`. The CLI
    never passes it, so a CLI-filed proposal has no `observed` key at all.

    v0.25 F05: prepare-then-materialize runs under `review_lock`, the same
    mutation lock `accept`/`reject` already hold — the ledger-rewrite check
    `_gate` runs inside `_prepare_proposal` must be atomic with the write
    `_materialize_proposal` does right after it, or a concurrent writer could
    rewrite meta/memory.jsonl in the window between the two."""
    with review_lock(bundle):
        prepared = _prepare_proposal(
            bundle, meta, body, target, action, note, actor=actor, evidence=evidence,
            extends=extends, reopen=reopen, distinct_from=distinct_from, new_id=new_id,
            supersedes=supersedes, reverts=reverts, flag_type=flag_type, query=query,
            patch=patch, observed=observed)
        return _materialize_proposal(bundle, prepared)


def _prepare_proposal(bundle: Bundle, meta: dict, body: str, target: str | None = None,
                      action: str = "update", note: str = "", *, actor: str,
                      evidence: dict | None = None, extends: str | None = None,
                      reopen: str | None = None,
                      distinct_from: dict[str, str] | None = None,
                      new_id: str | None = None, supersedes: str | None = None,
                      reverts: str | None = None, flag_type: str | None = None,
                      query: str | None = None, patch: list[dict] | None = None,
                      observed: dict | None = None,
                      sibling_titles: list[str] | None = None,
                      used_paths: set | None = None,
                      sibling_gap_open_count: int = 0) -> dict:
    """Every check `propose()` runs, and every byte it would write, computed
    WITHOUT touching disk — no directory created, no file written, no memory
    event recorded. Returns a dict `_materialize_proposal` writes verbatim.

    `sibling_titles` and `used_paths` exist only for `propose_batch`: earlier
    entries in the SAME batch are not yet on disk when a later entry is
    validated, so the ordinary duplicate-title and same-path checks (which
    read the live bundle) cannot see them on their own. `sibling_titles` is
    the normalized titles of earlier OK create/supersede entries (extends the
    E_PROPOSAL_DUPLICATE check); `used_paths` is the proposal paths earlier OK
    entries in the batch already claimed (extends the on-disk collision loop
    below) — without it, two updates to the same target filed in one batch
    would compute the identical, still-nonexistent path and the second would
    silently clobber the first at materialize time. `sibling_gap_open_count`
    is how many earlier OK gap entries in this same batch, by this same
    actor, `_gate`'s on-disk GAP_CAP count cannot see yet."""
    if extends is not None:
        extends = _validate_write_id(bundle, extends, flag="extends")
        if bundle.get(extends) is None:
            raise ValueError(f"--extends target does not exist: {extends}")
        target, action = extends, "update"
    if action not in ACTIONS:
        raise ValueError(f"{E_PROPOSAL_ACTION}: bad action {action!r} — "
                         f"use one of {sorted(ACTIONS)}")
    if action in {"update", "delete", "supersede"} and not target:
        raise ValueError(f"action {action!r} requires a target concept id")
    if action == "flag" and not (target or (query and str(query).strip())):
        raise ValueError("action 'flag' requires --target or --query")
    if action == "flag" and not flag_type:
        raise ValueError("action 'flag' requires --type "
                         f"(one of {list(FLAG_TYPES)})")
    if flag_type is not None and flag_type not in FLAG_TYPES:
        raise ValueError(f"bad --type {flag_type!r} (use: {list(FLAG_TYPES)})")
    if action == "gap" and not (query and str(query).strip()):
        raise ValueError("action 'gap' requires --query")
    if patch is not None and action != "update":
        raise ValueError("--patch-file is only valid with --action update — "
                         "a patch IS an update, applied to the current body")
    if reverts is not None and action != "update":
        raise ValueError("--reverts is only meaningful with --action update — "
                         "a revert IS an update, recorded with the sha it reverts to")
    if action == "supersede":
        if not new_id:
            raise ValueError("action 'supersede' requires --new-id <concept-id>")
        # v0.24: one shared id grammar for every write target an agent can
        # name (see `_validate_write_id`) — `new_id` used to be checked for
        # only a `meta/` prefix, which let `../x`, an absolute path, or
        # `index/x`/`proposals/x`/`drafts/x` all through to `accept`.
        new_id = _validate_write_id(bundle, new_id, flag="new-id")
        if bundle.get(new_id) is not None:
            raise ValueError(f"--new-id already exists: {new_id}")
    # v0.24: a `create`'s --target IS the concept id it will materialize at
    # on accept — validated with the same shared grammar `new_id`/`extends`
    # use, so `../x`, an absolute path, and every reserved directory are
    # refused here, at propose time, with the same code validate.py raises
    # for a concept that already landed there by hand (E_RESERVED_DIR).
    if action == "create" and target:
        target = _validate_write_id(bundle, target, flag="target")
    actor = check_actor(actor)

    hunks = None
    if patch is not None:
        existing_for_patch = bundle.get(target)
        if existing_for_patch is None:
            raise ValueError(f"update target does not exist: {target}")
        hunks = _validate_patch(patch)
        # v0.24: drop accept-written keys BEFORE the forged-verification
        # check below — every concept `review accept` has ever written
        # carries `verified`, and a patch that copies it in would trip that
        # gate on content the AUTHOR never supplied (accept re-derives
        # `verified` from the target's own history regardless). A proposal
        # CONTENT that itself declares `verified` (the non-patch --from/meta
        # path) still hits the gate untouched.
        meta = {k: v for k, v in existing_for_patch.meta.items() if k != "verified"}
        body = _apply_patch(existing_for_patch.body, hunks)

    if action in {"flag", "gap"}:
        body = note

    old_proposal = None
    old_env: dict = {}
    if supersedes is not None:
        old_proposal = _load_proposal(bundle, supersedes)
        old_env = old_proposal.meta.get("proposal") or {}
        old_actor = (old_proposal.meta.get("generated") or {}).get("by")
        old_target = old_env.get("target")
        diffs = []
        if old_actor != actor:
            diffs.append(f"actor ({old_actor!r} filed it, {actor!r} is filing now)")
        if old_target != target:
            diffs.append(f"target ({old_target!r} vs {target!r})")
        if diffs:
            raise ValueError(
                f"{E_PROPOSAL_LANE}: {supersedes} differs in {'; '.join(diffs)} — "
                "file a separate proposal, or ask the owner to reject the old one")

    meta = dict(meta)
    if "verified" in meta:
        raise ValueError(f"{E_EVIDENCE}: a proposal cannot carry `verified` — "
                         "verification is written only by `okfy review accept`")
    # v0.24 (a): `observed` (the adapter's session trace) may only arrive
    # through this function's own keyword. A proposal's CONTENT is author
    # (agent) controlled — if it carries `observed`/`read_set` at the top
    # level, or `proposal.observed` (the exact shape this function itself
    # writes below), that is a forged claim of a read/search history, not a
    # real one, and is refused outright rather than silently dropped.
    if "observed" in meta or "read_set" in meta or (
            isinstance(meta.get("proposal"), dict) and "observed" in meta["proposal"]):
        raise ValueError(
            f"{E_PROPOSAL_OBSERVED_FORGED}: proposal content carries `observed` "
            "or `read_set` — remove the key, it is written by the adapter "
            "process, not by the author")
    ev = check_evidence(evidence if evidence is not None else meta.get("evidence"))
    meta.pop("evidence", None)
    if ev is not None:
        meta["evidence"] = ev
    near, sources_state = _gate(bundle, meta, body, target, action, reopen,
                                distinct_from, actor=actor, flag_type=flag_type,
                                query=query, sibling_titles=sibling_titles,
                                note=note, reverts=reverts, new_id=new_id,
                                hunks=hunks, supersedes=supersedes,
                                sibling_gap_open_count=sibling_gap_open_count)
    meta["generated"] = {"by": actor, "at": utc_now()}
    if action == "delete":
        meta.setdefault("type", "Proposal")
        meta.setdefault("title", f"Delete {target}")
        meta.setdefault("description", note or f"proposal to delete {target}")
    elif action == "flag":
        meta.setdefault("type", "Proposal")
        meta.setdefault("title", f"Flag: {target or query}")
        meta.setdefault("description", note or "flagged issue")
    elif action == "gap":
        meta.setdefault("type", "Proposal")
        meta.setdefault("title", f"Gap: {query}")
        meta.setdefault("description", note or "unanswered query")
    meta["proposal"] = {"action": action, "target": target, "note": note,
                        "created": datetime.date.today().isoformat()}
    if near:
        meta["proposal"]["near"] = near
        if distinct_from:
            meta["proposal"]["distinct_from"] = dict(distinct_from)
    if action == "supersede":
        meta["proposal"]["new_id"] = new_id
    if reverts:
        meta["proposal"]["reverts"] = str(reverts).strip()
    if flag_type is not None:
        meta["proposal"]["flag_type"] = flag_type
    if query is not None:
        meta["proposal"]["query"] = query
    if hunks is not None:
        meta["proposal"]["patch"] = hunks
    if sources_state is not None:
        meta["proposal"]["sources_state"] = sources_state
    if observed is not None:
        # Stored verbatim — the adapter process is the only writer of this
        # value, checked above. A CLI-filed proposal never passes it, so the
        # key stays absent (not false, not null) on that path.
        meta["proposal"]["observed"] = observed
    # The bytes this change was written against. accept refuses if they moved.
    tfile = bundle.root / f"{target}.md" if target else None
    if action in {"update", "delete", "supersede"} and tfile is not None and tfile.is_file():
        meta["proposal"]["base_sha256"] = _file_sha256(tfile)
    pdir = bundle.root / PROPOSAL_DIR
    base = _kebab(target or str(meta.get("title", "")))
    path = pdir / f"{base}.md"
    n = 2
    while path.exists() or (used_paths and path in used_paths):
        path = pdir / f"{base}-{n}.md"
        n += 1
    # A gap's dedup key is the NORMALIZED QUERY, not the note's bytes — see
    # `normalize_query`. A flag's is scoped (target-or-query, flag_type,
    # note) — see `_flag_reject_hashes`. Every other action keys on the body
    # as before.
    if action == "gap":
        propose_chash, propose_mhash = query_hash(query), None
    elif action == "flag":
        propose_chash, propose_mhash = _flag_reject_hashes(flag_type, target, query, body)
    elif action == "delete":
        propose_chash, propose_mhash = content_sha256(body), None
    else:
        propose_chash = content_sha256(body)
        propose_mhash = match_sha256(body)
    return {"path": path, "meta": meta, "body": body, "action": action,
           "target": target, "actor": actor, "reopen": reopen, "new_id": new_id,
           "supersedes": supersedes, "old_proposal": old_proposal, "old_env": old_env,
           "propose_chash": propose_chash, "propose_mhash": propose_mhash}


def _materialize_proposal(bundle: Bundle, prepared: dict) -> Path:
    """The write half: everything `_prepare_proposal` computed but did not
    touch disk for. `okfy propose --batch` calls this once per entry that
    passed validation (all of them, or the ok subset under --partial); a
    plain `propose()` call always materializes what it just prepared.

    Filesystem writes happen FIRST, the memory-ledger records LAST: the
    ledger is append-only and cannot be cleanly un-written, so a ledger
    failure instead unlinks the new proposal FILE this call just wrote and
    re-raises — this entry leaves nothing behind, ledger or file. `old_proposal`'s
    unlink uses `missing_ok=True` defensively — `propose_batch` refuses a
    second entry that would consume the same `supersedes` id before any entry
    materializes (see its `claimed_supersedes` tracking), so this should
    never actually be missing, but a write half that trusts that from a
    second, unrelated code path is the one that stayed broken."""
    path = prepared["path"]
    path.parent.mkdir(exist_ok=True)
    path.write_text(frontmatter.serialize(prepared["meta"], prepared["body"]),
                    encoding="utf-8")
    old_proposal = prepared["old_proposal"]
    old_content = None
    if old_proposal is not None:
        old_content = content_sha256(old_proposal.body)
        old_proposal.path.unlink(missing_ok=True)
    try:
        reopen = prepared["reopen"]
        memory.record(bundle, "propose", actor=prepared["actor"],
                      proposal=bundle.concept_id(path), target=prepared["target"],
                      action=prepared["action"], content_sha256=prepared["propose_chash"],
                      base_sha256=prepared["meta"]["proposal"].get("base_sha256"),
                      reopen=reopen.strip() if reopen else None,
                      match_sha256=prepared["propose_mhash"],
                      new_id=prepared["new_id"] if prepared["action"] == "supersede"
                      else None)
        if old_proposal is not None:
            by_proposal = bundle.concept_id(path)
            memory.record(bundle, "superseded", actor=prepared["actor"],
                          proposal=prepared["supersedes"],
                          target=prepared["old_env"].get("target"),
                          action=prepared["old_env"].get("action", "create"),
                          content_sha256=old_content, by_proposal=by_proposal)
    except Exception:
        # This entry's OWN new file must not outlive its own failed ledger
        # write — `propose_batch`'s rollback only knows about paths an
        # earlier, fully-COMPLETED call to this function returned, so a
        # failure in between must clean up after itself. `old_proposal`'s
        # unlink above cannot be undone the same way — documented as a known
        # limit of this rollback, not silently pretended away.
        path.unlink(missing_ok=True)
        raise
    return path


def _split_code(message: str) -> tuple[str | None, str]:
    """Splits a `"E_PROPOSAL_DUPLICATE: rest of the message"`-shaped string
    into `("E_PROPOSAL_DUPLICATE", "rest of the message")` — every ValueError
    this module raises is worded that way — or `(None, message)` when there
    is no recognisable code prefix. Used only to shape `propose_batch`'s
    per-entry report; never changes what is raised."""
    m = _CODE_PREFIX_RE.match(message)
    return (m.group(1), m.group(2)) if m else (None, message)


# v0.24: value-shape checks BEFORE any of these ever reach `_prepare_proposal`
# — a non-string `note`/`target`/`action` used to reach `_kebab`, a set
# membership test, or `str.encode()` deep inside `_gate` and raise a raw
# AttributeError/TypeError instead of a coded, per-entry refusal. `content`
# and `evidence`/`distinct_from`/`patch` are shape-checked by their own
# existing parsers (`frontmatter.parse`, `check_evidence`, `_validate_patch`)
# and already report cleanly, so they are not duplicated here.
_BATCH_STRING_KEYS = {"action", "target", "note", "reopen", "query", "new_id",
                     "supersedes", "reverts", "flag_type", "extends"}


def _parse_batch_entry(raw: str, lineno: int) -> dict:
    """One JSONL line -> `_prepare_proposal` kwargs (minus `actor`, which the
    whole batch shares — see `propose_batch`). Anything that could never be
    filed, however small, is `E_BATCH_ENTRY` naming the real file line
    number — a malformed line has no `index` yet in the caller's sense, but
    it does have a line."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{E_BATCH_ENTRY}: line {lineno}: not valid JSON ({e.msg})")
    if not isinstance(obj, dict):
        raise ValueError(f"{E_BATCH_ENTRY}: line {lineno}: entry must be a JSON object")
    unknown = sorted(set(obj) - BATCH_ENTRY_KEYS)
    if unknown:
        raise ValueError(f"{E_BATCH_ENTRY}: line {lineno}: unknown key(s) {unknown} "
                         f"(allowed: {sorted(BATCH_ENTRY_KEYS)})")
    for k in _BATCH_STRING_KEYS:
        v = obj.get(k)
        if v is not None and not isinstance(v, str):
            raise ValueError(f"{E_BATCH_ENTRY}: line {lineno}: `{k}` must be a "
                             f"string, got {type(v).__name__}")
    content = obj.get("content")
    if content is not None:
        try:
            meta, body = frontmatter.parse(content)
        except frontmatter.FrontmatterError as e:
            raise ValueError(f"{E_BATCH_ENTRY}: line {lineno}: `content` {e}")
    else:
        meta, body = {}, ""
    kwargs = {k: v for k, v in obj.items() if k != "content"}
    return {"meta": meta, "body": body, **kwargs}


def _batch_flag_key(flag_type, target, query, note) -> tuple:
    """Same dedup key `_gate`'s open-flag-duplicate check uses (flag_type +
    target-or-normalized-query + note's match hash), reused so `propose_batch`
    can recognise two entries in the SAME batch that cover the same flag —
    neither is on disk yet for the on-disk check to see."""
    want_query = normalize_query(query) if query else None
    return (flag_type, target or None, want_query, match_sha256(note or ""))


def propose_batch(bundle: Bundle, lines: list[str], *, actor: str,
                  dry_run: bool = False, partial: bool = False) -> dict:
    """v0.24 (a): `okfy propose --batch <file.jsonl>`. VALIDATE ALL FIRST —
    every entry runs the same parsing + `_gate` checks `propose()` itself
    runs, with nothing written to disk. Then:

    - `--dry-run`: never writes, whatever the verdicts are.
    - Any entry refused, no `--partial`: writes NOTHING (all-or-nothing),
      exit is the caller's job (`ok: False` in the returned report).
    - Any entry refused, `--partial`: files every entry that passed, still
      reports `ok: False` overall.
    - No entry refused: files everything (unless `--dry-run`).

    Returns `{"entries": [{"index", "ok", "code", "message", "target"}, ...],
    "filed": [proposal_id, ...], "ok": bool}` — `filed` lists only what was
    actually written (empty under `--dry-run`, or under an all-or-nothing
    refusal without `--partial`).

    `actor` applies to the WHOLE batch; an entry naming its own `actor` key
    is refused (`E_BATCH_ENTRY` — not a recognised key, see
    `BATCH_ENTRY_KEYS`) rather than silently overridden or silently honoured.

    Blank lines are skipped and not counted as entries (ordinary JSONL
    practice); a file with no non-blank lines returns `entries: []`,
    `filed: []`, `ok: True` — "0 entries" is success, not a refusal.

    v0.24: validation also models the BATCH'S OWN effects, not just the
    on-disk state — an earlier OK entry in this same batch is tracked the
    same way `sibling_titles`/`used_paths` already track create/path
    collisions: which proposal id an earlier entry's `supersedes` already
    claimed, which gap queries and flag keys an earlier entry already opened,
    and how many gap opens by this actor this batch has already committed to
    (folded into `_gate`'s GAP_CAP count via `sibling_gap_open_count`) — so a
    batch of duplicate gaps, duplicate flags, or two entries superseding the
    same proposal is refused entry-by-entry instead of writing all of them
    (or crashing partway through materializing them).

    v0.25 F05: the whole validate-all-then-write-all body runs under
    `review_lock`, same as `propose()` — this is the SAME write path, just N
    entries at once, and its wider validate/write window is the more exposed
    of the two to a concurrent ledger rewrite landing in between."""
    with review_lock(bundle):
        return _propose_batch(bundle, lines, actor=actor, dry_run=dry_run, partial=partial)


def _propose_batch(bundle: Bundle, lines: list[str], *, actor: str,
                   dry_run: bool = False, partial: bool = False) -> dict:
    # `--as` is one value for the whole batch — checked once, the same way a
    # single propose() call checks it, rather than failing identically on
    # every entry.
    actor = check_actor(actor)
    entries: list[dict] = []
    prepared_ok: list[dict] = []
    sibling_titles: list[str] = []
    used_paths: set = set()
    claimed_supersedes: set = set()
    sibling_gap_queries: set = set()
    sibling_flag_keys: set = set()
    sibling_gap_open_count = 0
    index = 0
    for lineno, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        report = {"index": index, "ok": False, "code": None, "message": None,
                  "target": None}
        index += 1
        try:
            parsed = _parse_batch_entry(raw, lineno)
        except ValueError as e:
            report["code"], report["message"] = _split_code(str(e))
            entries.append(report)
            continue
        report["target"] = parsed.get("target")
        batch_action = parsed.get("action", "update")
        supersedes = parsed.get("supersedes")
        if supersedes and supersedes in claimed_supersedes:
            report["code"], report["message"] = _split_code(
                f"{E_PROPOSAL_LANE}: {supersedes} is already claimed by an "
                "earlier entry in this same --batch file — only one entry may "
                "supersede a given proposal; file the second as a separate "
                "proposal instead")
            entries.append(report)
            continue
        if batch_action == "gap" and str(parsed.get("query") or "").strip():
            qh = query_hash(parsed["query"])
            if qh in sibling_gap_queries:
                report["code"], report["message"] = _split_code(
                    f"{E_DUPLICATE}: an earlier entry in this same --batch file "
                    "already covers this query — file it once")
                entries.append(report)
                continue
        if batch_action == "flag":
            key = _batch_flag_key(parsed.get("flag_type"), parsed.get("target"),
                                  parsed.get("query"), parsed.get("note", ""))
            if key in sibling_flag_keys:
                report["code"], report["message"] = _split_code(
                    f"{E_DUPLICATE}: an earlier entry in this same --batch file "
                    "already covers this — file it once")
                entries.append(report)
                continue
        try:
            prepared = _prepare_proposal(
                bundle, parsed["meta"], parsed["body"],
                target=parsed.get("target"), action=batch_action,
                note=parsed.get("note", ""), actor=actor,
                evidence=parsed.get("evidence"), extends=parsed.get("extends"),
                reopen=parsed.get("reopen"), distinct_from=parsed.get("distinct_from"),
                new_id=parsed.get("new_id"), supersedes=supersedes,
                reverts=parsed.get("reverts"), flag_type=parsed.get("flag_type"),
                query=parsed.get("query"), patch=parsed.get("patch"),
                sibling_titles=sibling_titles, used_paths=used_paths,
                sibling_gap_open_count=sibling_gap_open_count)
        except ValueError as e:
            report["code"], report["message"] = _split_code(str(e))
            entries.append(report)
            continue
        report["ok"] = True
        report["target"] = prepared["target"]
        entries.append(report)
        prepared_ok.append(prepared)
        used_paths.add(prepared["path"])
        if prepared["action"] in ("create", "supersede"):
            title = str(prepared["meta"].get("title", "")).strip()
            if title:
                sibling_titles.append(_norm(title))
        if supersedes:
            claimed_supersedes.add(supersedes)
        if prepared["action"] == "gap":
            sibling_gap_queries.add(query_hash(parsed.get("query") or ""))
            sibling_gap_open_count += 1
        if prepared["action"] == "flag":
            sibling_flag_keys.add(_batch_flag_key(
                parsed.get("flag_type"), prepared["target"], parsed.get("query"),
                parsed.get("note", "")))

    ok = all(e["ok"] for e in entries)
    filed: list[str] = []
    if not dry_run and (ok or partial):
        filed_paths: list[Path] = []
        try:
            for prepared in prepared_ok:
                path = _materialize_proposal(bundle, prepared)
                filed_paths.append(path)
                filed.append(bundle.concept_id(path))
        except Exception as e:
            # Everything validation could see is refused before this point,
            # so reaching here is a bug, not an agent mistake. Roll back the
            # NEW proposal files this call itself wrote (never an old
            # proposal an earlier, already-completed entry legitimately
            # consumed by superseding it) rather than leave the batch
            # half-materialized with no report at all.
            for p in filed_paths:
                p.unlink(missing_ok=True)
            raise ValueError(
                f"E_BATCH_MATERIALIZE: batch write failed after filing "
                f"{len(filed_paths)} of {len(prepared_ok)} proposal(s) "
                f"({type(e).__name__}: {e}) — rolled back: none of this "
                "batch's new proposals were left on disk. This is a bug "
                "(validation should have caught it) — file the entries one "
                "at a time to find which one fails, and report it") from e
    return {"entries": entries, "filed": filed, "ok": ok}


def near_warning(near_ids: list[str], distinct_from: dict[str, str] | None = None) -> str | None:
    """The W_PROPOSAL_NEAR text for a stored `near` list, or None when there is
    nothing to warn about — no near ids, or every one of them already has a
    `distinct_from` reason. Never a refusal; callers that surface it (the CLI,
    the MCP adapter) keep exiting 0."""
    distinct_from = distinct_from or {}
    if not near_ids or all(nid in distinct_from for nid in near_ids):
        return None
    ids = ", ".join(near_ids)
    return (f'{W_NEAR}: close to {ids} — if it is the same thing use --extends '
            '<id>; if not, say why with --distinct-from <id>="reason"')


def distinct_overlap_warnings(bundle: Bundle, meta: dict,
                              distinct_from: dict[str, str] | None) -> list[str]:
    """W_DISTINCT_ALIAS_OVERLAP: one warning per `--distinct-from <id>="..."`
    claim whose target still looks like the proposed concept once title and
    aliases are compared the way `cluster.py` compares them — title-key
    against title-key, and the WHOLE SET of alias-keys against the other's
    whole set of alias-keys (each alias kept as one atomic key, never broken
    apart into pooled tokens), taking the higher of the two jaccard scores.
    `--distinct-from` suppresses W_PROPOSAL_NEAR by asserting two concepts
    are different things; when the overlap says retrieval would conflate
    them anyway, that suppression is hiding a real collision rather than
    resolving one, and the owner should see that even though the
    near-duplicate warning itself stayed silent.

    An earlier version pooled title AND alias tokens into one bag before
    scoring it (v0.25 first draft). `cluster.py`'s own module docstring
    documents MEASURING that exact pooling on real bundles and rejecting it:
    on the 363-, 304- and 117-concept bundles, "any shared alias" was
    overwhelmingly false — 337 pairs in one bundle bridged by nothing more
    than a shared category word, 311 in another by one cited authority
    string. A single shared category/authority alias between two otherwise
    unrelated concepts pooled straight into the same false-positive shape
    here: a category alias sitting in an otherwise small token bag was
    enough to push a pooled jaccard score over the cutoff for two concepts
    that share nothing else. Comparing whole alias-KEYS (one concept's set
    of alias-keys against the other's, same as comparing whole title-keys)
    instead of pooled tokens means one alias in common among several does
    not, by itself, decide the score — the same discipline `cluster.py`
    already applies by keeping aliases out of its own title bag.

    Never a refusal — same shape as `near_warning`, computed the same way at
    propose time from the caller's own (meta, distinct_from), not from what
    ended up persisted on the proposal (which only stores `distinct_from`
    when `near` was non-empty for a create/supersede — a narrower condition
    than "this specific id overlaps enough to matter").

    Reuses cluster.py's own machinery (`_jaccard`, `_title_key`,
    `_alias_keys`) rather than a new tokenizer or a new similarity measure."""
    if not distinct_from:
        return []
    mine_title = _title_key(meta)
    mine_aliases = _alias_keys(meta)
    out = []
    for did in sorted(distinct_from):
        target = bundle.get(did)
        if target is None:
            continue  # _gate already refuses an unknown --distinct-from id
        theirs_title = _title_key(target.meta)
        theirs_aliases = _alias_keys(target.meta)
        overlap = max(_jaccard(mine_title, theirs_title),
                      _jaccard(mine_aliases, theirs_aliases))
        if overlap >= DISTINCT_OVERLAP_THRESHOLD:
            out.append(
                f"{W_DISTINCT_OVERLAP}: declared distinct from {did}, but "
                f"title/alias tokens overlap {overlap:.2f} (cutoff "
                f"{DISTINCT_OVERLAP_THRESHOLD}) — retrieval would conflate "
                "them despite the claim; drop the --distinct-from claim if "
                "they really are the same thing, or rename/narrow the "
                "aliases (`okfy refine`) so retrieval can tell them apart")
    return out


def ref_state(bundle: Bundle, ref: str) -> str:
    """Tri-state label for one evidence ref — never a lookup that guesses, and
    never a refusal. Only a TYPED ref (`eval:`, `concept:`, `proposal:`,
    `log:`) is checked inside this bundle, and only then does the label mean
    `resolved` (found) or `not-found` (typed, but missing). Every other ref —
    an external CI id, a commit, a bare URL, exactly what `test-run` examples
    look like in this project — is `unchecked`: the core cannot see it, which
    is honest, not a verdict against it."""
    ref = str(ref or "").strip()
    if not ref:
        return "unchecked"
    m = _TYPED_REF_RE.match(ref)
    if m is None:
        return "unchecked"
    kind, val = m.group(1), m.group(2)
    if kind == "eval":
        from okfy.evaluation import _find_run, load_evals  # local: evaluation imports proposals
        try:
            _find_run(load_evals(bundle), val)
        except KeyError:
            return "not-found"
        return "resolved"
    if kind == "concept":
        return "resolved" if bundle.get(val) is not None else "not-found"
    if kind == "proposal":
        c = bundle.get(val)
        if c is not None and val.startswith(f"{PROPOSAL_DIR}/"):
            return "resolved"
        evs, _problems = memory.events(bundle)
        return "resolved" if any(row.get("proposal") == val for row in evs) else "not-found"
    if kind == "log":
        log = bundle.root / "log.md"
        if not log.is_file():
            return "not-found"
        text = log.read_text(encoding="utf-8")
        return "resolved" if f"## {val}" in text else "not-found"
    return "unchecked"  # unreachable: _TYPED_REF_RE only matches the 4 kinds above


def evidence_state(bundle: Bundle, meta: dict) -> dict:
    """`{kind: ref_state}` for a concept's declared evidence, or `{}` when it
    has none (or the kind, like agent-inference, carries no ref to check)."""
    ev = meta.get("evidence")
    if not isinstance(ev, dict) or not ev.get("kind"):
        return {}
    return {ev["kind"]: ref_state(bundle, ev.get("ref", ""))}


def near_duplicates(bundle: Bundle, meta: dict, exclude: str | None = None) -> list[str]:
    """Concepts close enough to `meta` that the owner should see them before a
    duplicate lands — never a refusal (W_PROPOSAL_NEAR).

    Reuses cluster.py's already-measured rule rather than a new scorer: same
    `type` AND title-token Jaccard >= NEAR_THRESHOLD, OR an existing concept's
    title equals one of the PROPOSED aliases (normalized like `_gate`'s
    existing E_PROPOSAL_DUPLICATE check). Alias-to-alias is deliberately not
    compared — cluster.py's own measurement found that noisy (556/847 pairs on
    two real bundles). `meta/` concepts are exempt, same as the duplicate
    check. `exclude` (a `supersede`'s own OLD concept) drops one more id: a
    successor is expected to be near-identical to its predecessor, and that is
    not the warning this exists to raise."""
    title = str(meta.get("title", "")).strip()
    if not title:
        return []
    want_type = meta.get("type")
    want_toks = set(tokenize(title))
    aliases = meta.get("aliases") or []
    aliases = aliases if isinstance(aliases, list) else [aliases]
    proposed_aliases = {_norm(a) for a in aliases if str(a).strip()}
    out = []
    for c in bundle.concepts():
        if c.id.startswith("meta/") or c.id == exclude:
            continue
        same_type_near = bool(want_toks) and c.meta.get("type") == want_type and \
            _jaccard(want_toks, set(tokenize(str(c.meta.get("title", ""))))) >= NEAR_THRESHOLD
        existing_title = _norm(c.meta.get("title", ""))
        alias_hit = bool(existing_title) and existing_title in proposed_aliases
        if same_type_near or alias_hit:
            out.append(c.id)
    return sorted(set(out))


def nearest(bundle: Bundle, meta: dict, n: int = 3) -> list[dict]:
    """v0.24 (b): the core's own retrieval, run over a proposal's OWN text
    (title + aliases + description) — top `n` non-meta hits, computed live at
    `propose` (action `create`) and again by `review list`/`show`. Pure
    information: never a refusal, never composed into any gate, and never
    stored in the proposal file — a stale `nearest` list would be worse than
    none, so it is always recomputed against the live bundle.

    Calls `okfy.query.query` as a library function; `query.py` itself is
    unmodified (the v0.24 hard invariant)."""
    from okfy.query import query as _query
    title = str(meta.get("title") or "").strip()
    aliases = meta.get("aliases") or []
    aliases = aliases if isinstance(aliases, list) else [aliases]
    description = str(meta.get("description") or "").strip()
    text = " ".join(p for p in (title, " ".join(str(a) for a in aliases), description) if p)
    if not text.strip():
        return []
    hits = _query(bundle, text, n=n)["results"]
    return [{"id": h["id"], "score": h["score"]} for h in hits[:n]]


# v0.24 (c): the exact five states `fresh()` may report — never fewer, never
# more. See `fresh`'s docstring for what each one means.
FRESH_STATES = ("unchanged", "changed", "now-stale", "unchanged-stale", "deleted")


def fresh(bundle: Bundle, ids: dict) -> dict:
    """v0.24 (c): read-only freshness check for a caller's remembered
    `{id: sha256}` pairs against the live bundle — never writes, never calls
    git (a stale-check that itself mutates anything, or blocks on git, is not
    one an agent can call freely mid-session).

    Per id, exactly one of `FRESH_STATES`:
    - `deleted` — the concept no longer exists.
    - `unchanged` — file bytes match and the concept is not `stale`.
    - `changed` — file bytes differ and the concept is not `stale`.
    - `unchanged-stale` — file bytes match, but the concept is now `stale`
      (an owner decision recorded since the caller's sha, not a content edit).
    - `now-stale` — file bytes differ AND the concept is `stale`.
    """
    out = {}
    for cid, sha in ids.items():
        c = bundle.get(cid)
        if c is None:
            out[cid] = "deleted"
            continue
        current = _file_sha256(c.path)
        stale = bool(c.meta.get("stale"))
        if current == sha:
            out[cid] = "unchanged-stale" if stale else "unchanged"
        else:
            out[cid] = "now-stale" if stale else "changed"
    return out


def _open_flags_and_gaps(bundle: Bundle, action: str):
    for c in bundle.concepts(include_proposals=True):
        if not c.id.startswith(f"{PROPOSAL_DIR}/"):
            continue
        env = c.meta.get("proposal") or {}
        if env.get("action") == action:
            yield c, env


def _check_proposal_sources(bundle: Bundle, meta: dict) -> str | None:
    """v0.24 (d): every `sources:` entry, resolved with the same grammar
    `okfy validate` uses (`sourcemap.cited_span`), must name a file the
    bundle's corpus checker recognises — `E_PROPOSAL_SOURCE` otherwise.
    Returns `None` when there is nothing to check (no `sources` at all),
    `"unchecked"` when no checker is available for this bundle (a
    memory-only bundle must stay writable), else `"checked"`."""
    srcs = meta.get("sources") or []
    srcs = srcs if isinstance(srcs, list) else [srcs]
    if not srcs:
        return None
    exists = _source_checker(bundle)
    if exists is None:
        return "unchecked"
    for s in srcs:
        path, _start, _end = cited_span(str(s), None)
        if not exists(path):
            raise ValueError(
                f"{E_PROPOSAL_SOURCE}: {s} is not a file of this bundle's corpus — "
                "`okfy show <id>` to copy a real anchor from a concept you are "
                "extending, or file with --evidence external-source=<url> and no "
                "corpus source")
    return "checked"


def _gate(bundle: Bundle, meta: dict, body: str, target, action: str, reopen,
          distinct_from: dict[str, str] | None = None, *, actor: str | None = None,
          flag_type: str | None = None, query: str | None = None,
          sibling_titles: list[str] | None = None, note: str = "",
          reverts: str | None = None, new_id: str | None = None,
          hunks: list[dict] | None = None, supersedes: str | None = None,
          sibling_gap_open_count: int = 0) -> tuple[list[str], str | None]:
    """Refusals, cheapest first, each naming its way out — then one warning
    (W_PROPOSAL_NEAR) that is never a refusal. Returns `(near, sources_state)`:
    the near-duplicate ids found for a create (empty otherwise), and the
    `sources:` check outcome (`None` when nothing to check) — both for
    `propose` to persist."""
    from okfy import secrets
    from okfy.injection import scan_values
    label = f"{PROPOSAL_DIR}/{target or meta.get('title', '')}"
    # v0.24: every author-controlled string that could end up PERSISTED
    # anywhere (the proposal file, meta/memory.jsonl, or log.md at accept) —
    # every frontmatter VALUE walked recursively (never the YAML dump, which
    # folds long scalars and would hide a phrase split by the fold), the
    # body, and every other free-text field: note, reopen, distinct_from
    # reasons, reverts, query, target/new_id, a patch hunk's `new`. One
    # collection point, one scan call — every action (create/update/delete/
    # supersede/flag/gap/patch), --batch, and the MCP path all share it.
    strings = _collect_scan_strings(meta, body, query=query, note=note,
                                    reopen=reopen, distinct_from=distinct_from,
                                    reverts=reverts, target=target, new_id=new_id,
                                    patch_hunks=hunks)
    found = scan_values(label, strings)
    if found:
        f = found[0]
        raise ValueError(
            f"{E_INJECTION}: {len(found)} injection finding(s) — {f.rule} at line "
            f"{f.line}: {f.excerpt}. Agent-written memory is refused, not filed; "
            "if the text is legitimate the owner can write it with `okfy refine`")

    secret_found = secrets.scan_values(strings)
    if secret_found:
        rule, line = secret_found[0]
        raise ValueError(
            f"{E_SECRET}: {len(secret_found)} secret-shaped value(s) — rule {rule} "
            f"at line {line}. Memory is committed to git and re-sent every turn; "
            "remove the value and refer to where it lives (env var name, vault "
            "path), then propose again")

    events, problems = memory.events(bundle)
    if problems:
        raise ValueError(
            f"{problems[0]}; the rejected-content gate cannot trust "
            f"{memory.MEMORY_FILE} while it has unreadable lines — `okfy validate` "
            "lists each one; the owner repairs the ledger, then propose again")

    # v0.25 F05: parsing cleanly is not the same as being trustworthy — a
    # ledger with a rejection surgically deleted still parses. The SAME
    # byte-prefix predicate `okfy validate` uses (`ledger_prefix_check`),
    # right beside the parseability check above. `rewritten`: the WORKING
    # TREE can no longer answer "was this rejected?" either way — a `None`
    # below could be a genuine negative or a scrubbed one — so every
    # rejected-hash lookup in this function falls back to the COMMITTED HEAD
    # version (`events_at_head`) instead: the exact trust boundary
    # `ledger_prefix_check` itself already draws (its HONESTY LABEL: proves
    # nothing about a rewrite that was itself already committed, only ever
    # compares the working tree against HEAD). A hit found ONLY that way
    # means the working copy is hiding a standing decision — refused as
    # E_LEDGER_REWRITTEN, not E_PROPOSAL_REJECTED, naming the real problem.
    # `unverifiable` (no git repo, or this file never committed yet) changes
    # nothing here — every non-git bundle, and every bundle proposing for the
    # first time, keeps using the working tree exactly as before.
    ledger_check = ledger_prefix_check(bundle, memory.MEMORY_FILE)
    reject_lookup_events = events
    if ledger_check.outcome == "rewritten":
        head_events, head_problems = memory.events_at_head(bundle)
        if head_problems:
            # Nothing trustworthy left to fall back to either.
            raise ValueError(_ledger_rewritten_refusal(
                ledger_check,
                "the rejected-content gate cannot trust "
                f"{memory.MEMORY_FILE}: its committed HEAD version is itself "
                "unreadable, so even the fallback this gate uses for a "
                "rewritten working copy has nothing to stand on"))
        reject_lookup_events = head_events

    reopened = bool(reopen and str(reopen).strip())
    if action == "gap":
        # v0.24 (b): keyed on the NORMALIZED QUERY, not the note's bytes — two
        # different notes about the same unanswered question are the same gap.
        qh = query_hash(query)
        prior = memory.rejected_hashes(bundle, from_events=reject_lookup_events).get(qh)
        if prior is not None and not reopened:
            if ledger_check.outcome == "rewritten":
                raise ValueError(_ledger_rewritten_refusal(
                    ledger_check,
                    "the working copy no longer shows the rejection by "
                    f"{prior['actor']} on {str(prior['at'])[:10]} that HEAD "
                    f"still has ({prior.get('reason') or 'no reason given'}) "
                    "— re-proposing this query unreopened over a scrubbed "
                    "rejection repeats a closed decision instead of reopening it"))
            raise ValueError(
                f"{E_REJECTED}: this query was rejected by {prior['actor']} on "
                f"{str(prior['at'])[:10]} ({prior.get('reason') or 'no reason given'}) "
                "— re-proposing it unchanged repeats a closed decision. If something "
                'changed, say what: `okfy propose ... --reopen "<new evidence>"`')
    elif action != "delete":
        # v0.24: a flag's tombstone is keyed on (target-or-normalized-query,
        # flag_type, match hash of the note) — see `_flag_reject_hashes` —
        # not on the note's bytes bundle-wide, which used to tombstone every
        # future flag anywhere that reused a similar short note.
        if action == "flag":
            chash, mhash = _flag_reject_hashes(flag_type, target, query, body)
        else:
            chash, mhash = content_sha256(body), match_sha256(body)
        prior = memory.rejected_hashes(bundle, from_events=reject_lookup_events).get(chash)
        if prior is not None and not reopened:
            if ledger_check.outcome == "rewritten":
                raise ValueError(_ledger_rewritten_refusal(
                    ledger_check,
                    "the working copy no longer shows the rejection by "
                    f"{prior['actor']} on {str(prior['at'])[:10]} that HEAD "
                    f"still has ({prior.get('reason') or 'no reason given'}) "
                    "— re-proposing this exact text unreopened over a scrubbed "
                    "rejection repeats a closed decision instead of reopening it"))
            raise ValueError(
                f"{E_REJECTED}: this exact text was rejected by {prior['actor']} on "
                f"{str(prior['at'])[:10]} ({prior.get('reason') or 'no reason given'}) "
                "— re-proposing it unchanged repeats a closed decision. If something "
                'changed, say what: `okfy propose ... --reopen "<new evidence>"`')
        tomb = memory.rejected_match_hashes(bundle, from_events=reject_lookup_events).get(mhash)
        if tomb is not None and not reopened:
            when = str(tomb["at"])[:10]
            if ledger_check.outcome == "rewritten":
                verb = "delete" if tomb["event"] == "accept" else "rejection"
                raise ValueError(_ledger_rewritten_refusal(
                    ledger_check,
                    f"the working copy no longer shows the {verb} by "
                    f"{tomb['actor']} on {when} that HEAD still has — "
                    "re-proposing this text (reworded or not) unreopened over "
                    "a scrubbed decision repeats a closed decision instead of "
                    "reopening it"))
            if tomb["event"] == "accept":            # accepted delete: a tombstone
                raise ValueError(
                    f"{E_REJECTED}: this text (reworded or not) was deleted by "
                    f"{tomb['actor']} on {when} — re-proposing it repeats a closed "
                    'decision. If something changed, say what: `okfy propose ... '
                    '--reopen "<new evidence>"`')
            raise ValueError(
                f"{E_REJECTED}: this text, reworded, was rejected by {tomb['actor']} "
                f"on {when} ({tomb.get('reason') or 'no reason given'}) — "
                "re-proposing it repeats a closed decision. If something changed, "
                'say what: `okfy propose ... --reopen "<new evidence>"`')

    if action == "flag":
        note_mh = match_sha256(body)
        want_query = normalize_query(query) if query else None
        for c, env in _open_flags_and_gaps(bundle, "flag"):
            if env.get("flag_type") != flag_type:
                continue
            same_target = bool(target) and env.get("target") == target
            same_query = want_query is not None and \
                normalize_query(env.get("query") or "") == want_query
            if not (same_target or same_query):
                continue
            if match_sha256(c.body) != note_mh:
                continue
            raise ValueError(
                f"{E_DUPLICATE}: an open flag already covers this — {c.id} — "
                "review it instead of filing another")

    if action == "gap":
        qh = query_hash(query)
        for c, env in _open_flags_and_gaps(bundle, "gap"):
            # v0.24: the proposal THIS one supersedes is excluded — it nets
            # to zero (one gap replaces itself) and is gone by the time this
            # one materializes, so it must not read as "an open gap already
            # covers this query" against its own replacement.
            if c.id == supersedes:
                continue
            if query_hash(env.get("query") or "") != qh:
                continue
            raise ValueError(
                f"{E_DUPLICATE}: an open gap already covers this query — {c.id} — "
                "review it instead of filing another")
        # v0.24: same exclusion for the cap count — `--supersedes` of one's
        # OWN open gap must actually be the way out the refusal below names,
        # not another way into the same refusal. `sibling_gap_open_count`
        # folds in gap entries `propose_batch` already OK'd earlier in this
        # same batch, invisible to `_open_flags_and_gaps` until materialized.
        n_open = sum(1 for c, env in _open_flags_and_gaps(bundle, "gap")
                    if c.id != supersedes
                    and (c.meta.get("generated") or {}).get("by") == actor)
        n_open += sibling_gap_open_count
        if n_open >= GAP_CAP:
            raise ValueError(
                f"{E_PROPOSAL_GAP_CAP}: {actor} already has {n_open} open gap "
                f"proposal(s) (max {GAP_CAP}) — the owner clears the queue with "
                "`okfy review list` / accept / reject, or withdraw one with "
                "--supersedes")

    if distinct_from:
        for did in distinct_from:
            if bundle.get(did) is None:
                raise ValueError(f"--distinct-from target does not exist: {did}")

    # Title against existing titles AND aliases; proposed aliases are never
    # compared. Measured on the real bundles before this gate was written: title=title 0-5 pairs per
    # bundle, existing-alias=title 0-39, but alias=alias 556 on rayforce-api-okf
    # and 847 on sec-cftc-sfp-okf — aliases hold categories and authorities, so
    # that branch would refuse ordinary proposals.
    if action in {"create", "supersede"} and str(meta.get("title", "")).strip():
        want = _norm(meta["title"])
        for c in bundle.concepts():
            # target is excluded on purpose: a `supersede`'s successor is
            # explicitly allowed to reuse the OLD concept's own title/aliases —
            # that is the common case, not a duplicate.
            if c.id.startswith("meta/") or c.id == target:
                continue
            as_ = ("title" if _norm(c.meta.get("title", "")) == want else
                   "alias" if want in {_norm(a) for a in c.meta.get("aliases") or []}
                   else None)
            if as_:
                raise ValueError(
                    f"{E_DUPLICATE}: {meta['title']!r} already names {c.id} (as its "
                    f"{as_}) — to add to that concept pass `--extends {c.id}`; if "
                    "this is a different thing, give it a title that says how")
        # v0.24 (a) `--batch`: earlier entries in the SAME batch are not on
        # disk yet, so the loop above cannot see them — this is the same
        # exact-title check, extended to the batch's own siblings.
        if sibling_titles and want in sibling_titles:
            raise ValueError(
                f"{E_DUPLICATE}: {meta['title']!r} duplicates another entry "
                "earlier in this same --batch file — give it a distinct title, "
                "or file them one at a time with --extends")

    sources_state = _check_proposal_sources(bundle, meta)

    if action in {"create", "supersede"}:
        near = near_duplicates(bundle, meta, exclude=target if action == "supersede" else None)
        return near, sources_state
    return [], sources_state


def _proposal_row(bundle: Bundle, c: Concept) -> dict:
    """One `review list`/`review show` row. `base_moved` and `evidence_state`
    are RE-computed live, never trusted from what `propose` stored: a
    `concept:` ref or a target's bytes can go stale between filing and
    review."""
    env = c.meta.get("proposal") or {}
    action = env.get("action", "create")
    target = env.get("target")
    issues: list[str] = []
    target_exists = bool(target) and bundle.get(target) is not None
    if action in {"update", "delete", "supersede"}:
        if not target:
            issues.append("missing target for " + action)
        elif not target_exists:
            issues.append(f"target does not exist: {target}")
    if action == "create" and target and target_exists:
        issues.append(f"create target already exists: {target}")
    if action == "supersede":
        new_id = env.get("new_id")
        if not new_id:
            issues.append("missing new_id for supersede")
        elif bundle.get(new_id) is not None:
            issues.append(f"new_id already exists: {new_id}")
    if action != "delete" and not str(c.meta.get("type", "")).strip():
        issues.append("proposed concept has no type")
    base = env.get("base_sha256")
    base_moved = (None if not base or not target_exists
                  else base != _file_sha256(bundle.root / f"{target}.md"))
    # v0.24 (b): recomputed live, same as `base_moved` and `evidence_state` —
    # only meaningful for a create (see `nearest`'s docstring), `[]` otherwise
    # so the key stays a stable shape across every row.
    row = {"id": c.id, "action": action, "target": target,
           "target_exists": target_exists, "base_moved": base_moved,
           "note": env.get("note", ""), "created": env.get("created"),
           "near": env.get("near") or [],
           "distinct_from": env.get("distinct_from") or {},
           "evidence_state": evidence_state(bundle, c.meta),
           "flag_type": env.get("flag_type"), "query": env.get("query"),
           "sources_state": env.get("sources_state"),
           "nearest": nearest(bundle, c.meta) if action == "create" else [],
           "valid": not issues, "issues": issues}
    # v0.24 (a): a summary only here — `queries`/`searched_before_propose`/
    # `target_shown` — ABSENT (not present-and-false) for a CLI-filed
    # proposal, which never carries `observed` at all. `review show` renders
    # the fuller per-entry view (see `show_proposal`).
    obs = env.get("observed")
    if obs:
        row["observed"] = {"queries": obs.get("queries"),
                           "searched_before_propose": obs.get("searched_before_propose"),
                           "target_shown": obs.get("target_shown")}
    return row


def list_proposals(bundle: Bundle) -> list[dict]:
    out = [_proposal_row(bundle, c) for c in bundle.concepts(include_proposals=True)
          if c.id.startswith(f"{PROPOSAL_DIR}/")]
    # Grouped by target (proposals against the same concept sit together),
    # then created, then id as a deterministic same-day tiebreak.
    return sorted(out, key=lambda d: (d["target"] or "", d["created"] or "", d["id"]))


def _load_proposal(bundle: Bundle, proposal_id: str) -> Concept:
    c = bundle.get(proposal_id)
    if c is None or not proposal_id.startswith(f"{PROPOSAL_DIR}/"):
        raise KeyError(f"proposal not found: {proposal_id}")
    return c


def compute_impact(bundle: Bundle, concept_id: str, exclude_proposal: str | None = None) -> dict:
    """What accepting a `delete` (or looking past a `supersede`'s old concept)
    would touch — read-only, computed live, never cached. Six artifact kinds:
    `inbound_links` (concepts whose body links to it — same resolution
    `validate._check_links`/`index.concept_links` already use), `lexicon_rows`
    (meta/lexicon.md rows whose `maps_to` names it), `expectations`
    (purpose.md `test_queries`/`adversarial_queries` entries naming it —
    substring match for the plain-string acceptance suite, the structured
    `concept` field for adversarial), `index_line` (linked from index.md),
    `open_proposals` (other open proposals targeting it) and
    `verified_linkers` (how many inbound linkers carry a non-empty
    `verified`)."""
    inbound_links: list[str] = []
    verified_linkers = 0
    for c in bundle.concepts():
        if c.id == concept_id:
            continue
        hit = any(resolve_link(bundle, c.path, t) == concept_id
                  for t in LINK_RE.findall(c.body))
        if hit:
            inbound_links.append(c.id)
            if c.meta.get("verified"):
                verified_linkers += 1
    inbound_links.sort()

    lexicon_rows = []
    lex = bundle.get("meta/lexicon")
    if lex is not None:
        for row in (lex.meta.get("rows") or []):
            if not isinstance(row, dict):
                continue
            maps_to = row.get("maps_to")
            targets = maps_to if isinstance(maps_to, list) else ([maps_to] if maps_to else [])
            if concept_id in targets:
                lexicon_rows.append({"term": row.get("term"), "status": row.get("status")})

    expectations = []
    purpose = bundle.purpose()
    for qtext in purpose.get("test_queries") or []:
        if isinstance(qtext, str) and concept_id in qtext:
            expectations.append({"suite": "acceptance", "query": qtext})
    for row in purpose.get("adversarial_queries") or []:
        if isinstance(row, dict) and row.get("concept") == concept_id:
            expectations.append({"suite": "adversarial", "query": row.get("query", "")})

    index_line = False
    idx = bundle.root / "index.md"
    if idx.is_file():
        index_line = any(resolve_link(bundle, idx, t) == concept_id
                         for t in LINK_RE.findall(idx.read_text(encoding="utf-8")))
    # v0.24 (a) --shard-index: in sharded mode the resident index.md carries
    # only directory summary lines — a sharded concept's own line lives in
    # `index/<dir>.md` — so the check above alone reports `False` for EVERY
    # sharded concept. Mirrors `validate._check_orphans`/`_check_index_drift`,
    # which were already taught this; this reader was not.
    if not index_line and _index_mode(bundle) == "sharded":
        for f in _shard_files(bundle):
            if any(resolve_link(bundle, f, t) == concept_id
                  for t in LINK_RE.findall(f.read_text(encoding="utf-8"))):
                index_line = True
                break

    open_proposals = sorted(row["id"] for row in list_proposals(bundle)
                            if row["id"] != exclude_proposal and row["target"] == concept_id)

    return {"inbound_links": inbound_links, "lexicon_rows": lexicon_rows,
            "expectations": expectations, "index_line": index_line,
            "open_proposals": open_proposals, "verified_linkers": verified_linkers}


def _observed_view(bundle: Bundle, observed: dict) -> dict:
    """`review show`'s live rendering of a stored `observed` block: for each
    `read_set` entry, whether the concept's CURRENT file sha still equals the
    one recorded when it was shown (`moved`), or the concept is gone
    (`deleted`) — computed fresh every call, never trusted from what the
    adapter recorded at propose time."""
    out = dict(observed)
    read_set = []
    for entry in observed.get("read_set") or []:
        cid, sha = entry.get("id"), entry.get("sha256")
        row = dict(entry)
        path = bundle.root / f"{cid}.md"
        if not path.is_file():
            row["deleted"] = True
        else:
            row["moved"] = _file_sha256(path) != sha
        read_set.append(row)
    out["read_set"] = read_set
    return out


def show_proposal(bundle: Bundle, proposal_id: str) -> dict:
    """`review show`: the proposal's row, plus a read-only `impact` block for
    `delete` and `supersede` — the two actions that make an existing concept
    stop being current. When the proposal carries `observed` (v0.24 (a),
    adapter-filed only), the summary `review list` shows is replaced here by
    the fuller per-entry rendering (`_observed_view`)."""
    c = _load_proposal(bundle, proposal_id)
    row = _proposal_row(bundle, c)
    if row["action"] in {"delete", "supersede"} and row["target"]:
        row = dict(row, impact=compute_impact(bundle, row["target"],
                                              exclude_proposal=proposal_id))
    obs = (c.meta.get("proposal") or {}).get("observed")
    if obs:
        row = dict(row, observed=_observed_view(bundle, obs))
    return row


def accept(bundle: Bundle, proposal_id: str, archetype=None, *,
          as_actor: str | None = None, channel: str = "api") -> str:
    """`as_actor` (v0.24 (c)): optional `--as <actor>` — defaults to
    `owner_actor(bundle)` exactly as before this existed; an explicit value is
    honoured only when it is a `human:` actor (`E_OWNER_ACTION_REQUIRED`
    otherwise — see `actor.check_owner_actor`). `channel` is MEASURED, never
    a flag the caller asserts: the CLI passes `tty`/`non-tty` from
    `sys.stdin.isatty()` at the CLI layer; every other (library) caller gets
    the default `api`. Recorded on the ledger's `accept` row."""
    with review_lock(bundle):
        return _accept(bundle, proposal_id, archetype, as_actor=as_actor, channel=channel)


def _accept(bundle: Bundle, proposal_id: str, archetype=None, *,
           as_actor: str | None = None, channel: str = "api") -> str:
    c = _load_proposal(bundle, proposal_id)
    env = c.meta.get("proposal") or {}
    action = env.get("action", "create")
    owner = check_owner_actor(
        bundle, as_actor, verb="review accept",
        retry=f"okfy review accept {bundle.root} {proposal_id}")

    # v0.25 F05: every `_commit` call below deliberately commits with
    # --no-verify — accept writes the FINAL concept file, which the bundle's
    # own `write_policy: proposals` pre-commit hook must refuse from an agent
    # and must allow from the owner's sanctioned command here. But bypassing
    # the hook also bypasses the ledger check the hook would have run:
    # without this, an accept that has nothing to do with an already-
    # REWRITTEN meta/memory.jsonl would still fold its diverged working-tree
    # bytes into HEAD the instant this call's commit touches the file —
    # permanently erasing the evidence `okfy validate` was reporting, the
    # exact laundering this release closes. The fix is narrower than
    # refusing the whole accept: `_commit_paths` below drops
    # `memory.MEMORY_FILE` from every commit THIS call makes while it is
    # REWRITTEN — the concept write, proposal removal and log still commit
    # normally, and the event THIS accept itself appends still lands in the
    # working-tree file (via `memory.record`, independent of git), just not
    # folded into HEAD — so the SAME divergence `ledger_prefix_check` found
    # stays exactly where `okfy validate` can still see it, until the owner
    # restores the file and commits it. `unverifiable` (no git repo, or this
    # file never committed yet) changes nothing — every non-git bundle, and
    # every bundle whose ledger is not yet committed, commits exactly as
    # before this check existed.
    ledger_check = ledger_prefix_check(bundle, memory.MEMORY_FILE)

    def _commit_paths(paths: list[str]) -> list[str]:
        if ledger_check.outcome != "rewritten":
            return paths
        return [p for p in paths if p != memory.MEMORY_FILE]

    # A flag/gap filed on `--query` alone has no real target concept — the
    # proposal-id-derived fallback (kebab of its own title) would otherwise
    # look like one.
    target = env.get("target") or (
        c.id.removeprefix(f"{PROPOSAL_DIR}/") if action not in ("flag", "gap") else None)
    existing = bundle.get(target)

    # Exempt by construction: a proposal filed before v0.23 carries no base, so
    # there is nothing to compare. It is accepted and the log says so; it is
    # never refused, and a PRESENT base that no longer matches always is.
    unbased = ""
    if action in {"update", "delete", "supersede"} and existing is not None:
        base = env.get("base_sha256")
        if base is None:
            unbased = (f" {W_UNBASED}: filed before base_sha256 existed, accepted without a "
                       "lost-update check; re-file with `okfy propose` to get one")
        elif base != _file_sha256(existing.path):
            raise ValueError(
                f"{E_BASE_MOVED}: {target} changed after {proposal_id} was "
                "written against it — accepting would erase that change. Re-read "
                f"the concept, file a fresh proposal (`okfy propose <bundle> "
                f"--target {target} --action {action} --as <actor>`), then "
                f"`okfy review reject <bundle> {proposal_id}`")

    origin = "revert" if env.get("reverts") else "proposal-accept"

    if action == "flag":
        # v0.24 (a): "this is wrong", without pretending to have the fix.
        # Acknowledged means acknowledged — no concept is written, only the
        # proposal file removed and the ledger row.
        c.path.unlink()
        memory.record(bundle, "accept", actor=owner, proposal=proposal_id,
                      target=target, action=action, content_sha256=content_sha256(c.body),
                      origin=origin, channel=channel)
        append_log(bundle, f"review: accept flag {target or env.get('query', '')} "
                           f"({env.get('note', '')}){unbased}")
        _commit(bundle, _commit_paths([f"{proposal_id}.md", "log.md", memory.MEMORY_FILE]),
                f"review: accept flag {proposal_id}")
        return target or ""

    if action == "gap":
        # v0.24 (b): default disposition — accept-as-not-covered. Exactly one
        # lexicon row, term = the filed phrase verbatim, in the row shape
        # `lexicon.py` reads; refused if that term already has a row.
        term = str(env.get("query") or "").strip()
        if not term:
            raise ValueError("gap proposal has no query")
        lex = bundle.get("meta/lexicon")
        rows = list(lex.meta.get("rows") or []) if lex is not None else []
        # v0.24: compared the same way `lexicon.expand` MATCHES — casefolded
        # — not byte-exact. A byte-exact compare let `Vega Hedge` past a
        # standing `vega hedge` row: `expand` lowercases both sides, so the
        # new row either silently never fires (same-length tie, first row
        # wins) or shadows the old one, depending on row order — either way
        # the accept commits a row that contradicts one already there.
        term_key = term.strip().lower()
        if any(isinstance(r, dict) and str(r.get("term") or "").strip().lower() == term_key
              for r in rows):
            raise ValueError(
                f"{E_GAP_ROW_EXISTS}: a lexicon row for {term!r} already exists — "
                "reject the proposal, the row is already there")
        # v0.24: `purpose.md` only requires `language` to be PRESENT
        # (validate.META_REQUIRED), not to be a string — a multilingual
        # `language: [ru, en]` purpose validates cleanly. `lexicon.row_problems`
        # (and therefore `load_rows`) rejects a non-string `language`, so
        # writing it through verbatim would take every later `query()` call
        # down with a ValueError the moment this accept's commit lands. A
        # non-string (or blank) value falls back to "en" instead of being
        # copied through — the gap itself is still real and still worth
        # recording; only the malformed `purpose.md` value is not.
        purpose_language = bundle.purpose().get("language")
        row_language = purpose_language if isinstance(purpose_language, str) and \
            purpose_language.strip() else "en"
        new_row = {"term": term, "language": row_language,
                  "maps_to": [], "canonical_terms": [], "status": "not-covered",
                  "note": env.get("note", "")}
        rows = rows + [new_row]
        if lex is not None:
            lex_meta, lex_body, lex_path = dict(lex.meta, rows=rows), lex.body, lex.path
        else:
            lex_meta = {"type": "Lexicon", "title": "Lexicon", "rows": rows}
            lex_body, lex_path = "Rows.\n", bundle.root / "meta/lexicon.md"
        lex_path.parent.mkdir(parents=True, exist_ok=True)
        lex_path.write_text(frontmatter.serialize(lex_meta, lex_body), encoding="utf-8")
        c.path.unlink()
        memory.record(bundle, "accept", actor=owner, proposal=proposal_id,
                      target=None, action=action, content_sha256=content_sha256(c.body),
                      origin=origin, channel=channel)
        append_log(bundle, f"review: accept gap {term!r} ({env.get('note', '')}){unbased}")
        _commit(bundle, _commit_paths(["meta/lexicon.md", f"{proposal_id}.md", "log.md",
                                       memory.MEMORY_FILE]),
                f"review: accept gap {term!r}")
        return "meta/lexicon"

    if action == "delete":
        if existing is None:
            raise ValueError(f"delete target does not exist: {target}")
        # Read-only, computed against the live bundle — an expectation added
        # after this proposal was filed still blocks it.
        impact = compute_impact(bundle, target, exclude_proposal=proposal_id)
        if impact["expectations"]:
            first = impact["expectations"][0]
            raise ValueError(
                f"{E_DELETE_EXPECTED}: {target} is named by the "
                f"{first['suite']} expectation {first['query']!r} — edit the "
                "expectation in meta/purpose.md first — a suite that expects "
                "a deleted concept can never pass — then accept again")
        # The deleted CONCEPT's body, not the proposal's own (often empty)
        # body — this is the tombstone `_gate` checks re-creations against.
        deleted_match = match_sha256(existing.body)
        existing.path.unlink()
        c.path.unlink()
        memory.record(bundle, "accept", actor=owner, proposal=proposal_id,
                      target=target, action=action, content_sha256=content_sha256(c.body),
                      match_sha256=deleted_match, origin=origin, channel=channel)
        append_log(bundle, f"review: accept delete {target} ({env.get('note', '')})"
                           f"{unbased}")
        _commit(bundle, _commit_paths(
                    [f"{target}.md", f"{proposal_id}.md", "log.md", memory.MEMORY_FILE]),
                f"review: accept delete {target}")
        return target

    if action == "supersede":
        # Not refused by expectations (unlike delete): the old concept STAYS
        # in the bundle, flagged stale — an expectation naming it still finds
        # it, just marked as no longer current.
        if existing is None:
            raise ValueError(f"supersede target does not exist: {target}")
        new_id = env.get("new_id")
        if not new_id:
            raise ValueError("supersede proposal has no new_id")
        if bundle.get(new_id) is not None:
            raise ValueError(f"--new-id already exists: {new_id}")
        new_meta = dict(c.meta)
        new_meta.pop("proposal", None)
        if not str(new_meta.get("type", "")).strip():
            raise ValueError("proposed concept has no type")
        new_meta["supersedes"] = target
        if archetype is not None and archetype_applies(new_id):
            r = Report()
            probe = Concept(new_id, bundle.root / f"{new_id}.md", new_meta, c.body)
            _check_archetype(probe, archetype, r)
            if not r.ok:
                raise ValueError("; ".join(f"{f.code}: {f.message}" for f in r.errors))
        proposed_by = (new_meta.get("generated") or {}).get("by") \
            if isinstance(new_meta.get("generated"), dict) else None
        entry = {"by": owner, "at": utc_now(),
                 "content": content_sha256(c.body)}
        if proposed_by:
            entry["proposed_by"] = proposed_by
        new_meta["verified"] = [entry]          # a new concept: no prior history

        new_path = bundle.root / f"{new_id}.md"
        # Last-line defence: propose-time already validated `new_id` with
        # `_validate_write_id` (relative, no `..`, no reserved first
        # segment), but a proposal file can in principle be hand-edited
        # between filing and accept — the resolved write path is re-checked
        # right here, not trusted from the proposal alone.
        _assert_write_inside_bundle(bundle, new_path)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(frontmatter.serialize(new_meta, c.body), encoding="utf-8")

        # The OLD concept stays — reviewed staleness (ADR-0013), via the same
        # fields set_stale writes, plus the successor link.
        old_meta = dict(existing.meta)
        old_meta["superseded_by"] = new_id
        old_meta["stale"] = True
        old_meta["stale_reason"] = f"superseded by {new_id}"
        old_meta["stale_since"] = datetime.date.today().isoformat()
        existing.path.write_text(frontmatter.serialize(old_meta, existing.body),
                                 encoding="utf-8")

        c.path.unlink()
        memory.record(bundle, "accept", actor=entry["by"], proposal=proposal_id,
                      target=target, action=action, content_sha256=entry["content"],
                      new_id=new_id, origin=origin, channel=channel)
        append_log(bundle, f"review: accept supersede {target} -> {new_id} "
                           f"({env.get('note', '')}){unbased}")
        # ONE commit: old concept, new concept, the consumed proposal, log,
        # ledger. index.md is untouched — same as every other accept; it is
        # regenerated by `okfy package`, never written by review accept.
        _commit(bundle, _commit_paths([f"{target}.md", f"{new_id}.md", f"{proposal_id}.md",
                                       "log.md", memory.MEMORY_FILE]),
                f"review: accept supersede {target} -> {new_id}")
        return new_id

    if action == "update" and existing is None:
        raise ValueError(f"update target does not exist: {target}")
    if action == "create" and existing is not None:
        raise ValueError(f"create target already exists: {target}")

    meta = dict(c.meta)
    meta.pop("proposal", None)
    if not str(meta.get("type", "")).strip():
        raise ValueError("proposed concept has no type")
    # Same predicate the validator uses. Applying the archetype schema to a
    # `meta/*` target refused every proposal against purpose.md, corpus.md or
    # the extraction plan for a missing `description` that `okfy validate` does
    # not require — which locked a `write_policy: proposals` bundle out of the
    # one flow its own pre-commit hook tells the owner to use.
    if archetype is not None and archetype_applies(target):
        r = Report()
        probe = Concept(target, bundle.root / f"{target}.md", meta, c.body)
        _check_archetype(probe, archetype, r)
        if not r.ok:
            raise ValueError("; ".join(f"{f.code}: {f.message}" for f in r.errors))

    # Authorship survives an update: the concept's first author stays in
    # `generated`, and who proposed THIS text is recorded on the verification
    # that accepted it. Each verification names the bytes it verified, so a
    # later edit leaves it historical instead of silently transferring it.
    proposed_by = (meta.get("generated") or {}).get("by") \
        if isinstance(meta.get("generated"), dict) else None
    prior: list = []
    if existing is not None:
        if isinstance(existing.meta.get("generated"), dict):
            meta["generated"] = existing.meta["generated"]
        if isinstance(existing.meta.get("verified"), list):
            prior = list(existing.meta["verified"])
    entry = {"by": owner, "at": utc_now(),
             "content": content_sha256(c.body)}
    if proposed_by:
        entry["proposed_by"] = proposed_by
    meta["verified"] = prior + [entry]

    tpath = bundle.root / f"{target}.md"
    _assert_write_inside_bundle(bundle, tpath)   # see the supersede branch above
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(frontmatter.serialize(meta, c.body), encoding="utf-8")
    c.path.unlink()
    memory.record(bundle, "accept", actor=entry["by"], proposal=proposal_id,
                  target=target, action=action, content_sha256=entry["content"],
                  origin=origin, channel=channel)
    append_log(bundle, f"review: accept {action} {target} ({env.get('note', '')})"
                       f"{unbased}")
    _commit(bundle, _commit_paths(
                [f"{target}.md", f"{proposal_id}.md", "log.md", memory.MEMORY_FILE]),
            f"review: accept {action} {target}")
    return target


def reject(bundle: Bundle, proposal_id: str, reason: str = "", *,
          as_actor: str | None = None, channel: str = "api") -> None:
    """`as_actor`/`channel`: see `accept`'s docstring — same v0.24 (c) rules,
    the same owner-only verb."""
    with review_lock(bundle):
        _reject(bundle, proposal_id, reason, as_actor=as_actor, channel=channel)


def _reject(bundle: Bundle, proposal_id: str, reason: str = "", *,
           as_actor: str | None = None, channel: str = "api") -> None:
    c = _load_proposal(bundle, proposal_id)
    env = c.meta.get("proposal") or {}
    owner = check_owner_actor(
        bundle, as_actor, verb="review reject",
        retry=f"okfy review reject {bundle.root} {proposal_id}")
    c.path.unlink()
    reject_action = env.get("action", "create")
    # A gap's tombstone is keyed on the NORMALIZED QUERY (see propose()'s
    # matching content_sha256 for the `propose` event), not the note's bytes.
    # A flag's is scoped (target-or-query, flag_type, note) — see
    # `_flag_reject_hashes` — not the note's bytes bundle-wide.
    if reject_action == "gap":
        reject_chash, reject_mhash = query_hash(env.get("query", "")), None
    elif reject_action == "flag":
        reject_chash, reject_mhash = _flag_reject_hashes(
            env.get("flag_type"), env.get("target"), env.get("query"), c.body)
    else:
        reject_chash = content_sha256(c.body)
        reject_mhash = match_sha256(c.body) if reject_action != "delete" else None
    memory.record(bundle, "reject", actor=owner, proposal=proposal_id,
                  target=env.get("target"), action=reject_action,
                  content_sha256=reject_chash, reason=reason or None,
                  match_sha256=reject_mhash, channel=channel)
    append_log(bundle, f"review: reject {proposal_id} ({reason})")
    _commit(bundle, [f"{proposal_id}.md", "log.md", memory.MEMORY_FILE],
            f"review: reject {proposal_id}")


def refine(bundle: Bundle, concept_id: str, text: str, message: str = "") -> None:
    """Owner-directed direct edit (ADR-0007 channel 1). Validates and commits
    past the policy hook — this verb IS the sanctioned direct door.

    v0.24: recorded to the memory ledger like any other content write, so
    `origin: owner-refine` distinguishes it from a proposal's `accept`
    without inventing a new event kind — a refine has no proposal behind it,
    but it is exactly as much a content write as one that does."""
    existing = bundle.get(concept_id)
    if existing is None:
        raise KeyError(f"concept not found: {concept_id}")
    meta, body = frontmatter.parse(text)            # raises FrontmatterError
    if not str(meta.get("type", "")).strip():
        raise ValueError("refined concept has no type")
    existing.path.write_text(text, encoding="utf-8")
    memory.record(bundle, "accept", actor=owner_actor(bundle), proposal=None,
                  target=concept_id, action="update", content_sha256=content_sha256(body),
                  origin="owner-refine")
    append_log(bundle, f"refine: {concept_id}" + (f" — {message}" if message else ""))
    _commit(bundle, [f"{concept_id}.md", "log.md", memory.MEMORY_FILE],
            message or f"refine: {concept_id}")


STALE_FIELDS = ("stale", "stale_reason", "stale_since")


def overdue(bundle: Bundle) -> list[dict]:
    """Concepts whose `review_due` has passed, most overdue first. A listing,
    never a flag: nothing here writes `stale` or anything else."""
    today = datetime.date.today()
    out = []
    for c in bundle.concepts():
        d = review_due_date(c.meta.get("review_due")) if "review_due" in c.meta else None
        if d is not None and d < today:
            out.append({"id": c.id, "review_due": d.isoformat(),
                        "days_overdue": (today - d).days})
    return sorted(out, key=lambda x: (-x["days_overdue"], x["id"]))


def _rewrite_meta(bundle: Bundle, concept: Concept, meta: dict, message: str) -> None:
    concept.path.write_text(frontmatter.serialize(meta, concept.body), encoding="utf-8")
    append_log(bundle, message)
    _commit(bundle, [f"{concept.id}.md", "log.md"], message)


def set_stale(bundle: Bundle, concept_id: str, reason: str) -> None:
    """Reviewed staleness (ADR-0013): 'do not trust this as current'. Set only
    by an owner decision — automation merely reports Concepts as *affected*."""
    c = bundle.get(concept_id)
    if c is None:
        raise KeyError(f"concept not found: {concept_id}")
    if not reason.strip():
        raise ValueError("stale requires a reason — it is a reviewed decision")
    meta = dict(c.meta)
    meta["stale"] = True
    meta["stale_reason"] = reason
    meta["stale_since"] = datetime.date.today().isoformat()
    _rewrite_meta(bundle, c, meta, f"stale: {concept_id} — {reason}")


def clear_stale(bundle: Bundle, concept_id: str) -> None:
    c = bundle.get(concept_id)
    if c is None:
        raise KeyError(f"concept not found: {concept_id}")
    if not any(f in c.meta for f in STALE_FIELDS):
        raise KeyError(f"concept not stale: {concept_id}")
    meta = {k: v for k, v in c.meta.items() if k not in STALE_FIELDS}
    _rewrite_meta(bundle, c, meta, f"stale: clear {concept_id}")
