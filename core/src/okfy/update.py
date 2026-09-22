"""Deterministic incremental re-extraction support (ADR-0005): snapshot diff,
affected-concept reverse lookup, snapshot refresh. LLM re-extraction itself is
orchestrated by the /okfy:update plugin command."""
import datetime
import hashlib
import json
import tempfile
from fnmatch import fnmatch
from pathlib import Path

from okfy import frontmatter, memory
from okfy.bundle import Bundle
from okfy.gitenv import run_git
from okfy.init import _corpus_git_sha, _manifest

# `okfy.sourcemap` imports `ANCHOR_LINE_RE`/`heading_spans` from `okfy.validate`,
# and `okfy.validate` imports `_embedded_prefix`/`_source_path` from THIS module
# — a module-level `from okfy.sourcemap import ...` here would close the cycle
# mid-import (validate.py's own `_check_source_map` takes the same lazy-import
# route for the identical reason). So `cited_span`/`span_text` are imported
# locally, inside the two functions that use them.

E_DIFF_PARTIAL_LISTING = "E_DIFF_PARTIAL_LISTING"
# v0.25 audit F03: `okfy snapshot` refuses a corpus with uncommitted changes
# ahead of HEAD (registered here, raised by `okfy.commands.corpus.cmd_snapshot`
# — CLI-level, not by `refresh_snapshot` itself, which degrades to a no-op
# instead; see its docstring).
E_CORPUS_DIRTY = "E_CORPUS_DIRTY"
# v0.25 audit F04: a snapshot records a relationship between two COMMITTED
# states — the bundle's own concept set and the corpus it was extracted
# from. E_CORPUS_DIRTY (above) refuses when the CORPUS side is uncommitted;
# this is the same refusal for the BUNDLE side. Registered here, raised by
# `okfy.commands.corpus.cmd_snapshot`; `refresh_snapshot` itself degrades to
# a no-op instead — see its docstring for why this must be checked in the
# function too, not only at the CLI.
E_BUNDLE_DIRTY = "E_BUNDLE_DIRTY"
# v0.25 audit F04: `okfy snapshot` refuses while proposals filed for this
# update are still pending owner review — registered here, raised by
# `okfy.commands.corpus.cmd_snapshot`. Pending owner proposals are not a
# completed update (phase-5.md); declaring the corpus baseline current
# before they are accepted or rejected would let the affected concepts drop
# out of the NEXT `okfy diff` while nothing has actually been decided yet.
E_UPDATE_PROPOSALS_PENDING = "E_UPDATE_PROPOSALS_PENDING"
# v0.26 audit A3: `okfy snapshot` refuses while a still-affected concept's
# most recent meta/memory.jsonl event is a `reject` with nothing since to
# dispose of it — registered here, raised by
# `okfy.commands.corpus.cmd_snapshot` (see `unresolved_rejections`).
# Rejecting a proposed INTERPRETATION of a corpus change is not the same
# decision as deciding the change itself needs nothing: the documented
# workflow used to treat any reject as sufficient reason to advance the
# baseline (plugin/commands/update.md), which silently erased the very
# signal a `reject` test was named to preserve. `okfy dismiss` is the
# explicit owner disposition that clears it.
E_UPDATE_REJECTION_UNRESOLVED = "E_UPDATE_REJECTION_UNRESOLVED"

SOURCE_PINS = "meta/source-pins.json"
SOURCE_PINS_SCHEMA = "okfy-source-pins@1"

# v0.26 audit A2: sentinel default for the optional `rev` parameter on
# `corpus_diff`/`_classify_spans` below — distinct from `None`, which is
# itself the meaningful "not a git repository" value `_corpus_git_sha`
# returns and which callers must be able to pass through unchanged (a
# non-git corpus's diff/classify behaviour must not change at all; see
# their docstrings). `_UNSET` means "no revision was threaded in — resolve
# it yourself, exactly as before"; every EXISTING caller of either function
# (there are several, in tests and in `update_plan`/`sampling.py`) omits
# the argument entirely and keeps that self-resolving behaviour unchanged.
_UNSET = object()


def _snapshot(bundle: Bundle) -> dict:
    c = bundle.get("meta/corpus")
    if c is None:
        raise FileNotFoundError("meta/corpus.md missing — not an OKFy bundle?")
    if c.meta.get("exported"):
        raise ValueError("exported fusion — diff/update/snapshot are not "
                         "supported; re-export from the workspace instead")
    return c.meta


def _embedded_prefix(bundle: Bundle, corpus: Path) -> str | None:
    """For an --embed bundle living inside the corpus, the corpus-relative path
    prefix of the bundle's own files. These must never count as corpus changes
    (the bundle rides the corpus git repo, so git diff would otherwise report
    .okf/** as added). None for standalone bundles outside the corpus."""
    root = bundle.root.resolve()
    corpus = corpus.resolve()
    if root != corpus and root.is_relative_to(corpus):
        return root.relative_to(corpus).as_posix() + "/"
    return None


def _corpus_dirty_paths(corpus: Path) -> list[str]:
    """Paths `git status --porcelain` reports for the corpus repo — staged,
    unstaged, and untracked alike — or `[]` when the corpus has no local
    modifications. Callers only reach this once `_corpus_git_sha` has already
    confirmed the corpus has a resolvable HEAD; if `git status` itself fails
    for some other reason this degrades to `[]` too (nothing to refuse over —
    diagnosing "is this a git repo" is not this function's job)."""
    r = run_git(corpus, "status", "--porcelain", capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return [line[3:] for line in r.stdout.splitlines() if line.strip()]


def _materialize_pinned_paths(corpus: Path, rev: str, relpaths: set[str],
                              dest_root: Path) -> None:
    """Write the committed blob for every path in `relpaths` that exists at
    `rev` into `dest_root`, mirroring the corpus's own relative layout, so the
    existing filesystem-based readers (`okfy.sourcemap.cited_span`/
    `span_text`) can be reused UNCHANGED against PINNED content instead of the
    working tree — never restate that parser here (finding 18's docstring
    already makes this promise; this keeps it under git mode too).

    Exactly one `git show <rev>:./<relpath>` per path (bytes; callers decode
    as UTF-8 with the same tolerance a working-tree read already has — a
    `UnicodeDecodeError` routes to unpinned/span_changed exactly as before).
    The `./` prefix matters and is not cosmetic: `git show <rev>:<relpath>`
    resolves `<relpath>` from the REPOSITORY ROOT regardless of `-C`, which is
    the wrong answer whenever `corpus` is a SUBDIRECTORY of a larger repo (an
    `--embed` bundle's corpus is exactly this shape) — verified in the phase
    spec; `<rev>:./<relpath>` is the only form relative to `corpus` itself.

    A path absent at `rev` (`git show` exits non-zero: "fatal: path ... does
    not exist in <rev>") is simply left unmaterialized — every caller already
    treats a missing file as unpinned/span_changed via an ordinary
    `FileNotFoundError`, whether that file is missing from a real directory or
    from this scratch one. A path that would resolve outside `dest_root`
    (a `sources:` entry citing `../elsewhere`) is skipped the same way the
    corpus-relative source resolution already refuses to read outside the
    corpus."""
    dest_resolved_root = dest_root.resolve()
    for relpath in relpaths:
        dest = dest_root / relpath
        try:
            dest_resolved = dest.resolve()
        except OSError:
            continue
        if not dest_resolved.is_relative_to(dest_resolved_root):
            continue
        r = run_git(corpus, "show", f"{rev}:./{relpath}", capture_output=True)
        if r.returncode != 0:
            continue  # absent at this revision — leave unmaterialized
        try:
            dest_resolved.parent.mkdir(parents=True, exist_ok=True)
            dest_resolved.write_bytes(r.stdout)
        except OSError:
            continue


def corpus_diff(bundle: Bundle, *, rev: str | None = _UNSET) -> dict:
    """{"mode", "old", "new", "changed", "added", "removed",
    "skipped_dangling_symlinks"} — what changed in the corpus since the last
    snapshot, in git mode by comparing two committed trees, in manifest mode
    by comparing two file-hash listings.

    `rev` (v0.26 audit A2): the corpus revision to diff TO, already resolved
    by a caller running a larger multi-step operation (currently only
    `refresh_snapshot`) that needs every git-facing read in that operation to
    agree on one commit. Omitted (the default — every existing caller,
    `update_plan`, `sampling.py`, and every test that calls this directly),
    this resolves `_corpus_git_sha(corpus)` itself, exactly as before: an
    ordinary `okfy diff` answers against whatever HEAD is right now, which is
    correct for a standalone query. `None` is a legitimate explicit value (a
    non-git corpus, or one with no commits) and is passed through unchanged,
    never re-resolved — see `_UNSET`'s own comment for why a plain `None`
    default could not tell the two cases apart."""
    snap = _snapshot(bundle)
    corpus = Path(snap["corpus"])
    prefix = _embedded_prefix(bundle, corpus)

    def keep(p: str) -> bool:
        return prefix is None or not p.startswith(prefix)

    old_sha = snap.get("git_sha")
    new_sha = _corpus_git_sha(corpus) if rev is _UNSET else rev
    if old_sha and new_sha:
        # git mode: the listing comes from `git diff --name-status` against a
        # committed tree on both ends, so it is complete by construction — git
        # itself refuses to diff against a corpus it cannot read, there is no
        # "silently skipped an unreadable subtree" failure mode the way a bare
        # filesystem walk has. E_DIFF_PARTIAL_LISTING below is manifest-mode-only.
        # `--relative`: without it, `git -C corpus diff` still answers for the
        # WHOLE repo and prints paths relative to the REPO ROOT, not `corpus`
        # — invisible for every existing fixture here (corpus AT the repo
        # root, where the repo root IS corpus), but wrong for an `--embed`
        # bundle's corpus, which is a SUBDIRECTORY of the repo it documents
        # (v0.25 audit F03's path trap — see `_materialize_pinned_paths`
        # below for the matching fix to `git show`). `--relative` both scopes
        # the diff to `corpus` and reports paths corpus-relative, matching
        # what `sources:` entries and `keep()`/`affected_concepts` already
        # assume.
        #
        # v0.26 audit A2: the second endpoint is `new_sha` — a revision
        # resolved once, above — never the symbolic literal `"HEAD"`. Passing
        # `"HEAD"` here made this the WIDEST race window in the whole
        # operation: it re-resolves inside git itself, at the moment this
        # subprocess runs, after every Python-side read this function or its
        # caller has already done — a commit landing between `new_sha`'s
        # resolution above and this call would have made the listing
        # disagree with `new_sha` even within a single `corpus_diff` call,
        # never mind the rest of `refresh_snapshot`. `new_sha` is always a
        # concrete resolved sha here (never `None`, guarded by `if old_sha
        # and new_sha` above), so this substitution changes nothing about
        # what gets diffed in the ordinary case — only what it is pinned to.
        out = run_git(
            corpus, "diff", "--name-status", "--relative", "-M", old_sha, new_sha,
            capture_output=True, text=True, check=True).stdout
        changed, added, removed = [], [], []
        for line in out.splitlines():
            parts = line.split("\t")
            status = parts[0]
            if status.startswith("R"):          # rename: treat as remove+add
                removed.append(parts[1])
                added.append(parts[2])
            elif status == "A":
                added.append(parts[1])
            elif status == "D":
                removed.append(parts[1])
            else:                               # M, T, ...
                changed.append(parts[1])
        return {"mode": "git", "old": old_sha, "new": new_sha,
                "changed": sorted(p for p in changed if keep(p)),
                "added": sorted(p for p in added if keep(p)),
                "removed": sorted(p for p in removed if keep(p)),
                # Not applicable: git tracks a dangling symlink as ordinary
                # committed blob content, not a listing failure — there is
                # nothing here for this count to report (see manifest mode).
                "skipped_dangling_symlinks": 0}
    manifest_file = bundle.root / "meta" / "corpus-manifest.json"
    old = (json.loads(manifest_file.read_text(encoding="utf-8"))
           if manifest_file.is_file() else {})
    errors: list[str] = []
    skipped_symlinks: list[str] = []
    new = _manifest(corpus, errors=errors, skipped_symlinks=skipped_symlinks)
    if errors:
        first = sorted(errors)[0]
        raise ValueError(
            f"{E_DIFF_PARTIAL_LISTING}: the corpus listing is incomplete "
            f"({len(errors)} unreadable path(s), first: {first}) — a partial "
            "listing would report every unlisted file as removed and mark its "
            "concepts stale candidates. Fix the permissions or point "
            "meta/corpus at the right directory, then run okfy diff again")
    if old and not new:
        # The walk itself raised nothing (no permission errors, no missing
        # root — that case IS caught above via os.walk's onerror on a missing
        # top), but came back with zero files where the previous snapshot had
        # some. That is the same blast radius as a partial listing — every
        # previously-known file would read as removed — so it gets the same
        # refusal, not a false "everything was deleted".
        raise ValueError(
            f"{E_DIFF_PARTIAL_LISTING}: the new corpus listing is empty while "
            "the previous snapshot was not — a partial listing would report "
            "every unlisted file as removed and mark its concepts stale "
            "candidates. Check the corpus path in meta/corpus.md, then run "
            "okfy diff again")
    return {"mode": "manifest", "old": None, "new": None,
            "changed": sorted(p for p in old if p in new and old[p] != new[p] and keep(p)),
            "added": sorted(p for p in new if p not in old and keep(p)),
            "removed": sorted(p for p in old if p not in new and keep(p)),
            # v0.24: a dangling symlink (or other non-regular file: FIFO,
            # socket) is skipped, not a listing failure (finding 20) — v0.23's
            # rglob()+is_file() walk skipped these silently; this just makes
            # the count visible instead of reviving the old silence outright.
            "skipped_dangling_symlinks": len(skipped_symlinks)}


def _source_path(src: str) -> str:
    return str(src).split("#", 1)[0]


def affected_concepts(bundle: Bundle, files: list[str]) -> dict[str, list[str]]:
    """Reverse lookup: which concepts cite any of these corpus files in sources:."""
    fset = set(files)
    out: dict[str, list[str]] = {}
    for c in bundle.concepts():
        if c.id.startswith("meta/"):
            continue
        hits = sorted({_source_path(s) for s in (c.meta.get("sources") or [])
                       if _source_path(s) in fset})
        if hits:
            out[c.id] = hits
    return out


def _protected(bundle: Bundle, ids) -> dict[str, bool]:
    """True iff the concept has a non-empty `verified` frontmatter list OR
    meta/memory.jsonl has an `accept` event whose target is that concept.
    Nothing else — `log.md` is prose, not a decision record, and is never
    parsed for this."""
    accepted_targets = {row["target"] for row in memory.events(bundle)[0]
                        if row["event"] == "accept" and row.get("target")}
    out = {}
    for cid in ids:
        c = bundle.get(cid)
        verified = c.meta.get("verified") if c else None
        out[cid] = bool((isinstance(verified, list) and verified)
                        or cid in accepted_targets)
    return out


def unresolved_rejections(bundle: Bundle, affected) -> dict[str, dict]:
    """{concept_id: reject_row} for every id in `affected` whose most recent
    meta/memory.jsonl event is a `reject`, with nothing since to dispose of
    it — v0.26 audit A3. Rejecting a proposed INTERPRETATION of a corpus
    change ("this text is wrong") is a different decision from deciding the
    change itself needs no action, and the two must not collapse into one:
    `okfy.commands.corpus.cmd_snapshot` refuses to advance the baseline past
    any id this returns (see `E_UPDATE_REJECTION_UNRESOLVED`), unless the
    owner clears it — `okfy.proposals.dismiss` records that explicit
    disposition, and a fresh proposal on the same target being ACCEPTED
    clears it too, just by becoming the newer event.

    Folds the WHOLE ledger, keeping only the last row seen per target (file
    order is chronological order — the same assumption `_protected` above
    makes) — so a later `dismiss`, `accept`, or even another `reject`
    naturally supersedes whatever came before it for that target, without
    this function needing to know which kind of event outranks which.

    Reads `memory.events`, never `events_at_head`: an unreadable ledger line
    answers "cannot tell", and per this project's own guard rule
    (okfy-a-guard-needs-three-states) a WRITER may not refuse on "cannot
    tell" — only a positively read `reject` row, as the latest thing on
    record for that target, blocks anything here. A target absent from the
    readable ledger entirely is not reported either — absence of evidence
    is not evidence of an unresolved rejection."""
    latest: dict[str, dict] = {}
    for row in memory.events(bundle)[0]:
        target = row.get("target")
        if target in affected:
            latest[target] = row
    return {cid: row for cid, row in latest.items() if row["event"] == "reject"}


# --------------------------------------------------------------------------
# Span pins (v0.24, report-only). Built because the pooled anchored
# share over real bundles measured >= 30% (31.6%). ONE parser for anchors:
# `okfy.sourcemap.cited_span` — never restated here.
# --------------------------------------------------------------------------

def _unpinned_reason(source: str, corpus: Path | None) -> str:
    if corpus is None:
        return "corpus is not locally readable"
    path = _source_path(source)
    f = corpus / path
    try:
        f = f.resolve()
    except OSError:
        pass  # fall through to the not-found reason below
    if not (f.is_relative_to(corpus.resolve()) and f.is_file()):
        return "source file not found in the corpus"
    if "#" in source:
        return "anchor does not resolve (not a line range and no matching heading)"
    return "file unreadable as text"


def _anchor_kind(source: str) -> str:
    """Classify a cited source's anchor as "path" (no `#`, cites the whole
    file), "line" (`#L12-L40` or the ledger's dashless `#L12-40`), "char"
    (`#C12-40`), or "heading" (anything else after `#`) — WITHOUT touching the
    corpus. `_classify_spans` uses this to decide how a pin's span must be
    reclassified (finding 18): a bare path or heading anchor claims "the whole
    file" / "everything under this section", so growth of that span is itself
    a substantive change, and only a line/char anchor's FIXED window is the
    right thing to search for having moved. Mirrors `okfy.sourcemap.cited_span`
    is the one parser for the grammar; this reads the same three regexes,
    imported, rather than restating them."""
    from okfy.sourcemap import ANCHOR_CHAR_RE, LEDGER_LINE_RE
    from okfy.validate import ANCHOR_LINE_RE
    _, _, frag = str(source).partition("#")
    if not frag:
        return "path"
    if ANCHOR_LINE_RE.match(frag) or LEDGER_LINE_RE.match(frag):
        return "line"
    if ANCHOR_CHAR_RE.match(frag):
        return "char"
    return "heading"


def build_source_pins(bundle: Bundle, corpus: Path | None, *,
                      rev: str | None = _UNSET) -> dict:
    """{"schema", "pins": [...], "unpinned": [...]} for the CURRENT concept set
    against the CURRENT corpus — what `refresh_snapshot` writes to
    meta/source-pins.json. Every source `cited_span` resolves to a line range
    (line/char anchor, bare path, or a heading the corpus can resolve) becomes
    a pin tagged with its `kind` (`_anchor_kind`, read by `_classify_spans`);
    everything else is reported under `unpinned`, never dropped.

    A source resolving outside the corpus, or any read failure (missing file,
    binary/non-UTF-8, `corpus is None` while an anchor still resolved), routes
    to `unpinned` instead of raising — one cited file going away must not
    crash `okfy snapshot` or leave a half-refreshed snapshot.

    When `corpus` is a git repository with a resolvable HEAD, every byte read
    below comes from `rev`'s committed tree (`_materialize_pinned_paths`, a
    scratch mirror of the corpus layout), never the working tree — so the
    `git_sha` `refresh_snapshot` stamps can no longer disagree with the bytes
    it labels. No revision to capture (not a git repo, or no commits) falls
    back to the working tree, manifest mode's original semantics.

    `rev`: resolved by a caller running a larger operation (only
    `refresh_snapshot`) that needs every git-facing read in it to agree on
    one commit. Omitted, this resolves `_corpus_git_sha(corpus)` itself;
    `None` passes through unchanged (not a git repo, or no commits) — `_UNSET`.

    `cited_span`'s bounds check and the `span_text` hash right after it share
    one `lines_cache`, local to this call, so several citations into the same
    file cost one read of it, not one (or two) per citation."""
    from okfy.sourcemap import cited_span, span_text
    pins, unpinned = [], []

    read_corpus = corpus
    tmp = None
    if corpus is not None:
        resolved_rev = _corpus_git_sha(corpus) if rev is _UNSET else rev
        if resolved_rev is not None:
            relpaths = {_source_path(str(s))
                       for c in bundle.concepts() if not c.id.startswith("meta/")
                       for s in (c.meta.get("sources") or [])}
            tmp = tempfile.TemporaryDirectory(prefix="okfy-pinned-")
            read_corpus = Path(tmp.name)
            _materialize_pinned_paths(corpus, resolved_rev, relpaths, read_corpus)

    # Per-call cache, local to this one invocation (never a module-level
    # global — that goes stale between runs in the same process, the same
    # reason `_check_quotes` gives for keeping its own local). Follow-up to
    # v0.25 audit F11: `cited_span`'s line/ledger-anchor and bare-path
    # branches now read the corpus file to bounds-check a citation, where
    # before they read nothing — and this loop then called `span_text` right
    # after, which read the SAME file again to hash the span. Sharing one
    # cache across both calls (keyed by resolved path, same shape
    # `cited_span`/`span_text` already document) collapses that back to one
    # read per distinct file, for however many of its spans this bundle
    # cites.
    lines_cache: dict[str, list[str] | None] = {}

    try:
        for c in bundle.concepts():
            if c.id.startswith("meta/"):
                continue
            for s in (c.meta.get("sources") or []):
                s = str(s)
                path, start, end = cited_span(s, read_corpus, lines_cache=lines_cache)
                if corpus is None or start is None:
                    unpinned.append({"concept": c.id, "source": s,
                                     "reason": _unpinned_reason(s, read_corpus)})
                    continue
                f = read_corpus / path
                try:
                    f_resolved = f.resolve()
                except OSError:
                    f_resolved = f
                if not f_resolved.is_relative_to(read_corpus.resolve()):
                    unpinned.append({"concept": c.id, "source": s,
                                     "reason": "source file lies outside the corpus"})
                    continue
                try:
                    text = span_text(f, start, end, lines_cache=lines_cache)
                except (OSError, UnicodeDecodeError, TypeError) as e:
                    unpinned.append({"concept": c.id, "source": s,
                                     "reason": f"source file unreadable "
                                              f"({type(e).__name__})"})
                    continue
                sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                pins.append({"concept": c.id, "source": s, "file": path,
                            "start": start, "end": end, "sha256": sha,
                            "kind": _anchor_kind(s)})
    finally:
        if tmp is not None:
            tmp.cleanup()
    pins.sort(key=lambda p: (p["concept"], p["source"]))
    unpinned.sort(key=lambda p: (p["concept"], p["source"]))
    return {"schema": SOURCE_PINS_SCHEMA, "pins": pins, "unpinned": unpinned}


def _valid_pin_row(row) -> bool:
    """A pin row this module can safely index without KeyError/TypeError
    (finding 21): a dict with non-empty string `concept`/`source`, a string
    `sha256`, and integer (not bool) `start`/`end`."""
    return (isinstance(row, dict)
            and isinstance(row.get("concept"), str) and row["concept"]
            and isinstance(row.get("source"), str) and row["source"]
            and isinstance(row.get("sha256"), str) and row["sha256"]
            and isinstance(row.get("start"), int)
            and not isinstance(row.get("start"), bool)
            and isinstance(row.get("end"), int)
            and not isinstance(row.get("end"), bool))


def _valid_unpinned_row(row) -> bool:
    return (isinstance(row, dict)
            and isinstance(row.get("concept"), str) and row["concept"]
            and isinstance(row.get("source"), str) and row["source"])


def _load_source_pins(bundle: Bundle) -> dict:
    """The CURRENT meta/source-pins.json, degraded to "no usable pins"
    (schema-only, empty pins/unpinned) rather than raised on ANY corruption —
    unreadable file, invalid JSON, wrong schema, or (finding 21) well-formed
    JSON in the wrong SHAPE. A malformed row used to reach `_classify_spans`
    and crash it with a bare KeyError/TypeError; rows are now validated here,
    one at a time, so a single bad row degrades only that row to "no pin"
    instead of taking down `okfy diff` for the whole bundle. The way out is
    the same for every corruption class: `okfy snapshot` rewrites this file
    from the current corpus."""
    empty = {"schema": SOURCE_PINS_SCHEMA, "pins": [], "unpinned": []}
    p = bundle.root / SOURCE_PINS
    if not p.is_file():
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict) or data.get("schema") != SOURCE_PINS_SCHEMA:
        return empty
    # F13: `data.get("pins") or []` alone still hands a truthy non-list (a
    # well-formed document in the wrong SHAPE, e.g. `{"pins": 1}`) straight
    # to a `for` loop, raising an uncaught TypeError — exactly the crash
    # this function's own docstring promises never to produce. Guard the
    # container's TYPE first, same as `data` itself just above; only a list
    # is ever iterated, anything else degrades to empty like a missing key.
    raw_pins = data.get("pins")
    raw_pins = raw_pins if isinstance(raw_pins, list) else []
    raw_unpinned = data.get("unpinned")
    raw_unpinned = raw_unpinned if isinstance(raw_unpinned, list) else []
    return {"schema": SOURCE_PINS_SCHEMA,
            "pins": [r for r in raw_pins if _valid_pin_row(r)],
            "unpinned": [r for r in raw_unpinned if _valid_unpinned_row(r)]}


def _classify_spans(bundle: Bundle, corpus: Path | None, diff: dict,
                    affected: dict[str, list[str]], *,
                    rev: str | None = _UNSET) -> dict:
    """{concept_id: {source: outcome}} for every affected concept's sources
    whose file actually CHANGED (a removed file's sources go through
    `stale_candidates`/`reextract` instead — no new content to search).
    `outcome` is "span_intact" / "span_changed" / "unpinned", or a dict
    wrapping one: `{"outcome": "span_moved", "moved_to": "L<a>-L<b>"}`, or
    `{"outcome": "span_changed", "ambiguous": True}` (old text now matches
    more than one place).

    Reclassification depends on the pin's `kind` (`_anchor_kind`, set by
    `build_source_pins`):

    - "path" (whole file): re-hash the CURRENT file and compare to the pin.
      Equal -> `span_intact`; otherwise `span_changed`. Never `span_moved` —
      nowhere else for "the whole file" to move to.
    - "heading": re-resolve the anchor by heading TEXT against the current
      file and hash that span — a reordered section is absorbed here (match
      is by heading text, not line number), not a separate "moved" case.
    - "line" / "char" (a fixed window, or an old pin with no `kind`): hash the
      OLD start/end window of the NEW file first; on a mismatch, search every
      same-length window of the new file for the old hash. 0 matches ->
      `span_changed`; >1 -> `span_changed` (`ambiguous`); exactly 1 ->
      `span_moved`.

    "the NEW/CURRENT file" means `rev`'s committed tree in git mode
    (`_materialize_pinned_paths`, same technique as `build_source_pins`),
    never the working tree — matching what `diff` already compared, so
    "what changed" and "what it now says" answer about the SAME revision.
    `rev` threads the same way as `corpus_diff`'s: resolved by a caller
    (only `refresh_snapshot`) needing this call to agree with the rest of
    its operation; omitted, this resolves `_corpus_git_sha(corpus)` itself.
    `None` passes through unchanged (not a git repository, or no commits)."""
    from okfy.sourcemap import cited_span, span_text
    old = _load_source_pins(bundle)
    pins_by_key = {(p["concept"], p["source"]): p for p in old.get("pins") or []}
    unpinned_keys = {(p["concept"], p["source"]) for p in old.get("unpinned") or []}
    changed_files = set(diff["changed"])

    read_corpus = corpus
    tmp = None
    if corpus is not None:
        resolved_rev = _corpus_git_sha(corpus) if rev is _UNSET else rev
        if resolved_rev is not None:
            tmp = tempfile.TemporaryDirectory(prefix="okfy-pinned-")
            read_corpus = Path(tmp.name)
            _materialize_pinned_paths(corpus, resolved_rev, changed_files, read_corpus)

    try:
        out: dict[str, dict] = {}
        for cid, hit_files in affected.items():
            if not (set(hit_files) & changed_files):
                continue  # every hit is a removed file — nothing to classify
            c = bundle.get(cid)
            if c is None:
                continue
            result: dict = {}
            for s in (c.meta.get("sources") or []):
                s = str(s)
                path = _source_path(s)
                if path not in changed_files:
                    continue
                key = (cid, s)
                if key in unpinned_keys or key not in pins_by_key:
                    result[s] = "unpinned"
                    continue
                pin = pins_by_key[key]
                old_sha = pin["sha256"]
                kind = pin.get("kind", "line")
                f = read_corpus / path if read_corpus else None

                if kind == "path":
                    if f is None:
                        result[s] = "unpinned"
                        continue
                    try:
                        text = f.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError, TypeError):
                        result[s] = "unpinned"
                        continue
                    new_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    result[s] = "span_intact" if new_sha == old_sha else "span_changed"
                    continue

                if kind == "heading":
                    if read_corpus is None:
                        result[s] = "unpinned"
                        continue
                    _, new_start, new_end = cited_span(s, read_corpus)
                    if new_start is None:
                        result[s] = "span_changed"  # the heading no longer resolves
                        continue
                    try:
                        text = span_text(f, new_start, new_end)
                    except (OSError, UnicodeDecodeError, TypeError):
                        result[s] = "unpinned"
                        continue
                    new_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    result[s] = "span_intact" if new_sha == old_sha else "span_changed"
                    continue

                # kind in ("line", "char"): fixed-window compare, then search.
                old_start, old_end = pin["start"], pin["end"]
                length = old_end - old_start + 1
                if f is None:
                    result[s] = "unpinned"
                    continue
                try:
                    lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
                except (OSError, UnicodeDecodeError, TypeError):
                    result[s] = "unpinned"
                    continue
                if old_start >= 1 and old_end <= len(lines):
                    window = "".join(lines[old_start - 1:old_end])
                    if hashlib.sha256(window.encode("utf-8")).hexdigest() == old_sha:
                        result[s] = "span_intact"
                        continue
                matches = []
                n = len(lines)
                for i in range(0, max(n - length + 1, 0)):
                    window = "".join(lines[i:i + length])
                    if hashlib.sha256(window.encode("utf-8")).hexdigest() == old_sha:
                        matches.append((i + 1, i + length))
                if len(matches) == 1:
                    a, b = matches[0]
                    moved_to = f"L{a}" if a == b else f"L{a}-L{b}"
                    result[s] = {"outcome": "span_moved", "moved_to": moved_to}
                elif len(matches) > 1:
                    result[s] = {"outcome": "span_changed", "ambiguous": True}
                else:
                    result[s] = "span_changed"
            if result:
                out[cid] = result
        return out
    finally:
        if tmp is not None:
            tmp.cleanup()


def _span_outcome(entry) -> str:
    return entry["outcome"] if isinstance(entry, dict) else entry


def _reextract_ids(affected: dict[str, list[str]], diff: dict, spans: dict) -> list[str]:
    """Affected concepts with at least one span_changed/unpinned source, or a
    removed source — the set the update skill re-extracts instead of trusting
    `span_intact`/`span_moved` to mean the text still holds."""
    removed = set(diff["removed"])
    out = []
    for cid, hits in affected.items():
        if set(hits) & removed:
            out.append(cid)
            continue
        if any(_span_outcome(v) in ("span_changed", "unpinned")
              for v in spans.get(cid, {}).values()):
            out.append(cid)
    return sorted(out)


#  A stale pin's BASE shape (every pin has these — see `_valid_pin_row`)
# plus the two names `_reanchor_list` already surfaces explicitly by name
# below (`anchor_stale` itself, and `moved_to`). Anything else found on a
# stale pin is a LATER fact some other pass chose to record about that
# debt (currently only `stale_content_changed`, from `refresh_snapshot`'s
# A1.1 fix) — carried through generically rather than by adding a matching
# named field here every time one is invented. A hand-enumerated key tuple
# is exactly the shape that has silently dropped a just-added state in
# this codebase before (project memory: okfy-a-new-state-dies-in-the-
# printer, three instances in one phase) — `reanchor` is the reader that
# decides what the operator sees, so it cannot be the next instance.
_PIN_BASE_FIELDS = {"concept", "source", "file", "start", "end", "sha256",
                    "kind", "anchor_stale", "moved_to"}


def _reanchor_list(spans: dict, stale_pins: list[dict] | None = None) -> list[dict]:
    """[{"concept", "source", "moved_to", ...}] — a TODO list for fixing the
    anchor's TEXT (via `repair_anchors`/`okfy reanchor`), kept separate from
    `reextract` on purpose: a moved anchor means the cited text is unchanged
    and still needs no re-extraction, but its `#L..` anchor is now pointing
    at the wrong lines and needs to be rewritten to the current location.

    v0.25 audit F02: this used to come ONLY from `span_moved` outcomes in
    `spans` (finding 19b), which made it empty the moment the moved file
    dropped out of the current diff — even though `refresh_snapshot` had
    already flagged the pin `anchor_stale` and was carrying it forward
    unresolved. `refresh_snapshot` does NOT "refuse to silently re-pin a
    stale anchor" on its own; nothing clears `anchor_stale` except an
    explicit repair (see `repair_anchors`), so `reanchor` has to keep naming
    it independent of the diff too. `stale_pins` — the CURRENT
    meta/source-pins.json pins, as `_load_source_pins` returns them — is
    unioned in here for exactly that: every pin still carrying
    `anchor_stale` is reported, `moved_to` read straight off the pin (no new
    computation needed), deduplicated against any live `span_moved` entry
    for the same (concept, source) by preferring the live, freshly measured
    one.

    v0.26 audit A1 (orchestrator follow-up to A1.1): "not just the ONE flag
    the report happened to demonstrate" applies to THIS reader too. Every
    field a stale pin carries beyond its base shape (`_PIN_BASE_FIELDS`)
    rides along into the matching entry here — so `stale_content_changed`
    reaches `okfy diff`'s `reanchor` output, and so does whatever the NEXT
    fact recorded on a stale pin turns out to be, without this function
    needing to name it. On top of that pass-through, `needs_reextract` is
    added HERE, on the entry, when `stale_content_changed` is set: that flag
    on a raw pin states a FACT (this round's classification observed further
    drift); `needs_reextract` states the CONSEQUENCE a caller can act on
    without first knowing that `repair_anchors`'s own digest check (A1.3)
    is what would refuse a plain reanchor of that entry — the citation needs
    RE-EXTRACTION, not a mechanical anchor rewrite. Only entries built from
    `stale_pins` can carry it (a fresh, live `span_moved` this same round has
    no drift to report — see `refresh_snapshot`'s docstring for why a second
    move is not chased here either)."""
    out: dict[tuple[str, str], dict] = {}
    for cid, hits in spans.items():
        for s, entry in hits.items():
            if isinstance(entry, dict) and entry.get("outcome") == "span_moved":
                out[(cid, s)] = {"concept": cid, "source": s,
                                 "moved_to": entry["moved_to"]}
    for p in stale_pins or []:
        if p.get("anchor_stale"):
            key = (p["concept"], p["source"])
            extra = {k: v for k, v in p.items() if k not in _PIN_BASE_FIELDS}
            out.setdefault(key, {"concept": p["concept"], "source": p["source"],
                                 "moved_to": p.get("moved_to"), **extra})
    for entry in out.values():
        if entry.get("stale_content_changed"):
            entry["needs_reextract"] = True
    result = sorted(out.values(), key=lambda r: (r["concept"], r["source"]))
    return result


def anchored_source_share(bundle: Bundle) -> float | None:
    """Share of the CURRENT meta/source-pins.json's sources that are pinned
    (pins / (pins + unpinned)). None when the bundle has never been snapshotted
    with span pins (no source-pins.json yet)."""
    p = bundle.root / SOURCE_PINS
    if not p.is_file():
        return None
    data = _load_source_pins(bundle)
    total = len(data.get("pins") or []) + len(data.get("unpinned") or [])
    return (len(data.get("pins") or []) / total) if total else None


def update_plan(bundle: Bundle) -> dict:
    diff = corpus_diff(bundle)
    affected = affected_concepts(bundle, diff["changed"] + diff["removed"])

    plan = bundle.plan()
    seg = (plan.meta.get("segmentation") or {}) if plan else {}
    include = seg.get("include") or []
    exclude = seg.get("exclude") or []

    def in_scope(p: str) -> bool:
        if include and not any(fnmatch(p, g) for g in include):
            return False
        return not any(fnmatch(p, g) for g in exclude or [])

    covered: set[str] = set()
    stale_candidates: list[str] = []
    removed_set = set(diff["removed"])
    for c in bundle.concepts():
        if c.id.startswith("meta/"):
            continue
        srcs = {_source_path(s) for s in (c.meta.get("sources") or [])}
        covered |= srcs
        if srcs and srcs <= removed_set:
            stale_candidates.append(c.id)

    snap = bundle.get("meta/corpus")
    corpus = Path(str(snap.meta.get("corpus") or "")) if snap else None
    corpus = corpus if corpus and corpus.is_dir() else None
    spans = _classify_spans(bundle, corpus, diff, affected)
    # F02: `reanchor` must keep naming an unresolved anchor across every
    # later `okfy diff`/`okfy snapshot`, not just the one call whose OWN
    # diff caught the movement — see `_reanchor_list`.
    stale_pins = _load_source_pins(bundle).get("pins") or []

    # Report suggestion only — never the persisted `stale` flag (ADR-0013:
    # staleness is a reviewed owner decision via `okfy stale`, not automatic).
    return {
        "diff": diff,
        "affected": affected,
        "protected": _protected(bundle, affected),
        "spans": spans,
        "reextract": _reextract_ids(affected, diff, spans),
        "reanchor": _reanchor_list(spans, stale_pins),
        "uncovered_new": sorted(p for p in diff["added"]
                                if in_scope(p) and p not in covered),
        "stale_candidates": sorted(stale_candidates),
    }


def refresh_snapshot(bundle: Bundle, *, force: bool = False) -> dict:
    """Rewrite meta/corpus-manifest.json, meta/source-pins.json and
    meta/corpus.md to the CURRENT corpus. Returns `{"refreshed": True}` on an
    ordinary write, or `{"refreshed": False, "reason": "corpus dirty" |
    "bundle dirty", "dirty": <n>, "dirty_paths": [...]}` on either dirty
    no-op below — a caller that ignores the return value is choosing to treat
    "skipped" the same as "refreshed", not being denied the distinction.

    Everything is computed in memory FIRST — manifest, pins, corpus.md text —
    and all three files are written only after every computation has already
    succeeded, so a source citing a file that goes missing mid-computation
    can never leave the three files describing different corpus states
    (`build_source_pins` routes that case to `unpinned` instead of raising).

    By default (`force=False`), a DIRTY corpus is a no-op (nothing read
    beyond `git status`, nothing written): silently ADVANCING the pinned
    baseline while the owner is mid-edit would swallow an already-committed
    change one uncommitted revert away from becoming invisible to the next
    `okfy diff`. A DIRTY BUNDLE whose write-policy hook is installed
    (`.git/hooks/pre-commit`, from `okfy package`) is likewise a no-op
    (`reason: "bundle dirty"`) — otherwise, advancing the baseline while an
    update's own concept edit is still uncommitted would erase the diff
    signal if the hook then refuses that commit. A bundle without the hook
    has no such risk and is not checked. `okfy snapshot` (the CLI) REFUSES
    either dirty case outright, with a registered code, before calling this
    function; `--force` there maps to `force=True` here. A corpus or bundle
    that is not a git repository, or has no commits yet, has no "dirty"
    concept to check.

    One revision, `rev`, is resolved ONCE at entry and threaded into the
    dirty check, `build_source_pins`, `corpus_diff`, `_classify_spans`, and
    the `git_sha` this function writes, so every git-facing read in the
    operation names the same commit — `corpus_diff` takes `rev` as an
    explicit endpoint rather than the symbolic `"HEAD"`, which would
    otherwise re-resolve inside git at the last possible moment.

    RETAIN, not refuse: if the corpus advances DURING this call, the
    captured `rev` — not whatever is current when this function returns — is
    what gets written; the next diff simply sees the intervening commit as
    an ordinary pending change. `{"refreshed": True}` means "written against
    the revision current when this call started," not "against whatever is
    current now."

    Outgoing pins are classified against the current corpus before being
    overwritten (`_classify_spans`). A pin already flagged `anchor_stale`
    survives BYTE-FOR-BYTE for as long as the bundle still cites that exact
    (concept, source) key, decided in its OWN pass over the OLD pins ahead
    of and independent of this round's diff or freshly-built pins — so
    neither "this file isn't in today's diff" nor "the stale destination
    drifted again" can replace the debt with an unverified pin. It gains
    `stale_content_changed` only when this round's classification of the
    old window comes back `span_changed` — informational, never a reason to
    alter `anchor_stale`/`moved_to`/`sha256`. Nothing here ever clears
    `anchor_stale`; the only way out is `repair_anchors`, which changes the
    citation string (and so the pin's key) by construction. A pin not
    already stale that classifies as `span_moved` gains `anchor_stale`/
    `moved_to` for the first time; one that classifies as `span_changed`
    takes the fresh pin, flagged `repinned_after_change`.

    History of the defects this shape closes (A1, A2) is in CHANGELOG.md's
    v0.27.0 entry, not here."""
    c = bundle.get("meta/corpus")
    if c.meta.get("exported"):
        raise ValueError("exported fusion — diff/update/snapshot are not "
                         "supported; re-export from the workspace instead")

    hook_installed = (bundle.root / ".git" / "hooks" / "pre-commit").is_file()
    if not force and hook_installed and _corpus_git_sha(bundle.root) is not None:
        bundle_dirty = _corpus_dirty_paths(bundle.root)
        if bundle_dirty:
            return {"refreshed": False, "reason": "bundle dirty",
                    "dirty": len(bundle_dirty), "dirty_paths": bundle_dirty}

    corpus_raw = Path(c.meta["corpus"])
    corpus = corpus_raw if corpus_raw.is_dir() else None

    # v0.26 audit A2: resolve the corpus's revision ONCE, here, for the rest
    # of this call. `_corpus_git_sha` on a path that is not a directory (or
    # not a git repository, or a git repository with no commits) returns
    # `None` exactly as it always has — this capture changes nothing about
    # what "no revision" means, only how many times it gets asked for.
    rev = _corpus_git_sha(corpus_raw)

    if corpus is not None and not force:
        if rev is not None:
            dirty = _corpus_dirty_paths(corpus)
            if dirty:
                return {"refreshed": False, "reason": "corpus dirty",
                        "dirty": len(dirty), "dirty_paths": dirty}

    # Build the CURRENT pins right after the dirty check passes, from the
    # SAME `rev` captured above (v0.26 audit A2) — no independent
    # `_corpus_git_sha` call happens inside this call any more.
    new_pins_doc = build_source_pins(bundle, corpus, rev=rev)

    # Classify the outgoing pins against the current corpus (finding 19a).
    # A genuine listing failure (E_DIFF_PARTIAL_LISTING, or the new-listing-
    # went-empty case) must not block `okfy snapshot` — refreshing the
    # snapshot from a fresh walk is itself the recovery path for corpus
    # drift — it just means no anchor-stability information is available
    # this round, same as a bundle's very first snapshot.
    try:
        diff = corpus_diff(bundle, rev=rev)
        affected = affected_concepts(bundle, diff["changed"] + diff["removed"])
        old_outcomes = _classify_spans(bundle, corpus, diff, affected, rev=rev)
    except ValueError:
        old_outcomes = {}

    new_manifest = _manifest(corpus_raw)
    old_pins_by_key = {(p["concept"], p["source"]): p
                       for p in _load_source_pins(bundle).get("pins") or []}

    # v0.26 audit A1.1/A1.2: the CURRENT set of (concept, source) citations
    # the bundle actually makes right now — `bundle.concepts()`'s own
    # `sources:`, never `new_pins_doc`, which only names what
    # `build_source_pins` could BUILD (a citation to a since-deleted file
    # never gets a pin there at all; see A1.2 above). This is what decides
    # whether a stale key is still owed a repair or has been resolved by a
    # citation-string rewrite (`repair_anchors`).
    live_keys: set[tuple[str, str]] = set()
    for cn in bundle.concepts():
        if cn.id.startswith("meta/"):
            continue
        for s in (cn.meta.get("sources") or []):
            live_keys.add((cn.id, str(s)))

    final_pins: list[dict] = []
    handled_keys: set[tuple[str, str]] = set()

    # Pass 1 — debt first, driven by the OLD pins, ahead of and independent
    # of anything `new_pins_doc`/this round's diff says (A1.1/A1.2, see the
    # docstring above). Every old pin still carrying `anchor_stale`, for a
    # key the bundle still cites, survives byte-for-byte; nothing here can
    # replace it with a repin, a `span_changed` outcome only adds the
    # `stale_content_changed` note.
    for key, old_pin in old_pins_by_key.items():
        if not old_pin.get("anchor_stale") or key not in live_keys:
            continue
        kept = dict(old_pin)
        entry = old_outcomes.get(key[0], {}).get(key[1])
        outcome = _span_outcome(entry) if entry is not None else None
        if outcome == "span_changed":
            kept["stale_content_changed"] = True
        final_pins.append(kept)
        handled_keys.add(key)

    # Pass 2 — ordinary repinning, for every key pass 1 did not already
    # claim as outstanding debt: finding 19a's `span_moved`/`span_changed`
    # classification of the OUTGOING pin, or a fresh pin with nothing to
    # preserve.
    for pin in new_pins_doc["pins"]:
        key = (pin["concept"], pin["source"])
        if key in handled_keys:
            continue
        entry = old_outcomes.get(pin["concept"], {}).get(pin["source"])
        outcome = _span_outcome(entry) if entry is not None else None
        if outcome == "span_moved" and key in old_pins_by_key:
            kept = dict(old_pins_by_key[key])
            kept["anchor_stale"] = True
            kept["moved_to"] = entry["moved_to"]
            final_pins.append(kept)
        elif outcome == "span_changed" and key in old_pins_by_key:
            taken = dict(pin)
            taken["repinned_after_change"] = True
            final_pins.append(taken)
        else:
            final_pins.append(pin)
    new_pins_doc = {**new_pins_doc, "pins": final_pins}

    new_meta = dict(c.meta)
    # v0.26 audit A2: the SAME `rev` captured at entry, never a fresh
    # `_corpus_git_sha(corpus_raw)` call here — that second, independent
    # call is exactly what let a commit landing mid-operation stamp
    # OLD-content pins with a NEW commit's sha (see the docstring above).
    new_meta["git_sha"] = rev
    new_meta["extracted_at"] = datetime.date.today().isoformat()
    manifest_text = json.dumps(new_manifest, indent=0, sort_keys=True)
    pins_text = json.dumps(new_pins_doc, indent=2, sort_keys=True,
                           ensure_ascii=False) + "\n"
    corpus_md_text = frontmatter.serialize(new_meta, c.body)

    # Only now write — every payload above already succeeded.
    (bundle.root / "meta" / "corpus-manifest.json").write_text(
        manifest_text, encoding="utf-8")
    (bundle.root / "meta" / "source-pins.json").write_text(
        pins_text, encoding="utf-8")
    c.path.write_text(corpus_md_text, encoding="utf-8")
    return {"refreshed": True}


def repair_anchors(bundle: Bundle, *, only: set[tuple[str, str]] | None = None,
                   apply: bool = True) -> dict:
    """The only thing that clears an `anchor_stale` pin (`okfy reanchor`).
    For every `{"concept", "source", "moved_to"}` entry `update_plan(...)
    ["reanchor"]` reports (optionally narrowed to `only`, a set of
    (concept, source) pairs), rewrites that concept's own frontmatter
    `sources:` entry from the stale citation to `<path>#<moved_to>` and
    writes the concept file directly — a mechanical text correction, like
    `repair_links`, not a content change, so it bypasses the proposal
    workflow. The cited TEXT is unchanged by definition of `span_moved`;
    only the anchor moves.

    Changing the citation string changes its (concept, source) key, so the
    next `okfy snapshot` no longer matches the stale pin and takes a fresh
    one for the new citation instead — repair is real because of that key
    change, this function never edits meta/source-pins.json directly.

    Never partial: a missing concept, an old citation no longer verbatim in
    `sources` (a concurrent edit), or no `moved_to` on record, is reported
    under `skipped` with a `reason` and left untouched. `apply=False`
    (`okfy reanchor --dry-run`) reports what WOULD be repaired without
    writing, including this function's own refusals below.

    Before rewriting, the ORIGINAL pinned digest for (concept, source) is
    re-hashed at `moved_to` in the corpus at a captured revision `rev` (git
    mode: via `_materialize_pinned_paths`, never the mutable working tree,
    so a commit landing mid-call cannot disagree with what this reports; no
    revision to capture falls back to the working tree). A mismatch (the
    destination was edited since the move), an unresolvable `moved_to`, an
    unreadable destination or corpus, or no pinned digest on record, each skip with their
    own `reason` — `moved_to` names a LOCATION, not a still-current
    guarantee that the originally cited text is what lives there now."""
    from okfy.sourcemap import cited_span, span_text

    plan_reanchor = update_plan(bundle)["reanchor"]
    if only is not None:
        plan_reanchor = [e for e in plan_reanchor
                         if (e["concept"], e["source"]) in only]

    pinned_by_key = {(p["concept"], p["source"]): p
                     for p in _load_source_pins(bundle).get("pins") or []}

    corpus_doc = bundle.get("meta/corpus")
    corpus_raw = (Path(corpus_doc.meta["corpus"])
                 if corpus_doc is not None and corpus_doc.meta.get("corpus")
                 else None)
    corpus = corpus_raw if corpus_raw is not None and corpus_raw.is_dir() else None
    # v0.26 audit A1.3: captured ONCE, like `refresh_snapshot`'s own `rev` —
    # see that function's A2 docstring for why a second, independent
    # `_corpus_git_sha` call partway through an operation is exactly the
    # race this pattern exists to close.
    rev = _corpus_git_sha(corpus_raw) if corpus_raw is not None else None

    read_corpus = corpus
    tmp = None
    if corpus is not None and rev is not None:
        needed_paths = {e["source"].split("#", 1)[0] for e in plan_reanchor}
        tmp = tempfile.TemporaryDirectory(prefix="okfy-pinned-")
        read_corpus = Path(tmp.name)
        _materialize_pinned_paths(corpus, rev, needed_paths, read_corpus)

    try:
        repaired, skipped = [], []
        for entry in plan_reanchor:
            moved_to = entry.get("moved_to")
            c = bundle.get(entry["concept"])
            old_source = entry["source"]
            sources = list(c.meta.get("sources") or []) if c is not None else []
            if c is None or not moved_to or old_source not in sources:
                skipped.append({**entry, "reason":
                    "concept missing, no moved_to on record, or the old "
                    "citation no longer appears verbatim in sources (a "
                    "concurrent edit)"})
                continue

            path_prefix = old_source.split("#", 1)[0]
            new_source = f"{path_prefix}#{moved_to}"

            key = (entry["concept"], old_source)
            pin = pinned_by_key.get(key)
            if pin is None or not isinstance(pin.get("sha256"), str):
                skipped.append({**entry, "reason":
                    "no pinned digest on record for this citation — cannot "
                    "verify what text the move is supposed to preserve"})
                continue
            if read_corpus is None:
                skipped.append({**entry, "reason":
                    "corpus is not locally readable — cannot verify the "
                    "destination text"})
                continue
            _, new_start, new_end = cited_span(new_source, read_corpus)
            if new_start is None:
                skipped.append({**entry, "reason":
                    f"moved_to destination {moved_to!r} does not resolve "
                    "against the corpus"})
                continue
            dest_file = read_corpus / path_prefix
            try:
                dest_text = span_text(dest_file, new_start, new_end)
            except (OSError, UnicodeDecodeError, TypeError) as e:
                skipped.append({**entry, "reason":
                    f"destination text unreadable ({type(e).__name__})"})
                continue
            dest_sha = hashlib.sha256(dest_text.encode("utf-8")).hexdigest()
            if dest_sha != pin["sha256"]:
                skipped.append({**entry, "reason":
                    "destination text no longer matches the originally "
                    "pinned digest — it was edited after the move was "
                    "recorded; re-extract instead of repairing"})
                continue

            sources[sources.index(old_source)] = new_source
            if apply:
                new_meta = dict(c.meta)
                new_meta["sources"] = sources
                c.path.write_text(frontmatter.serialize(new_meta, c.body),
                                  encoding="utf-8")
            repaired.append({**entry, "new_source": new_source})
        return {"repaired": repaired, "skipped": skipped}
    finally:
        if tmp is not None:
            tmp.cleanup()
