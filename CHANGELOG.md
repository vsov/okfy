# Changelog

This changelog starts at v0.26.0. Earlier releases are not back-filled here —
their history is not something this file can verify.

## Unreleased

An audit of v0.29 found three issues in the shipped tool. The three fixes below do not change
how `okfy query` ranks a single bundle. The contributed retrieval change (the first of the
contributed subsections below) does: it moves the retrieval code digest (see there), and
the round-19 subsection moves it a second time.

### Round 23

- **R23-1:** `scripts/memory-recipe.sh` captures the selected directory without losing a trailing
  line feed. `$(...)` strips trailing newlines, so a selected work directory whose name ended in a
  line feed resolved to its sibling without one: with that sibling empty the recipe wrote into the
  wrong directory, and otherwise a valid empty directory was refused. The work directory, the
  script's own directory and the repository directory are now captured with a sentinel after the
  substitution, so each names the directory that was selected, the "inside the OKFy repository" guard
  compares the right paths, and a directory that cannot be entered is still refused as
  `cannot resolve`.
- **R23-2:** git output is decoded after its return code is read, and a commit id must be a full
  40- or 64-digit lowercase hex line. A corpus whose `.git` file names a path with a byte that is not
  text made `okfy snapshot --force` and `okfy validate` raise `UnicodeDecodeError`, which the CLI
  turned into a bare exit 2 with empty stdout. They now give what the same corpus gives when the path
  is ASCII: `okfy validate` reports `W_CORPUS_SHA` (`E_CORPUS_SHA` under `--strict-sources`) and
  the snapshot refresh refuses with `E_CORPUS_LISTING`. The `cat-file` probe in
  `okfy validate` reads the return code only.
- **R23-3:** the corpus manifest classifies each entry with one `os.stat`. A listed file below a
  path that is no longer a directory (ENOTDIR) or that is gone (ENOENT) is absent: skipped, and a
  dangling symlink is noted. Any other failure (EACCES, ELOOP) is collected as unreadable and
  refused with `E_CORPUS_LISTING` state `unreadable`, as in round 22; a non-regular entry is skipped
  unread. A listed self-referential symlink (ELOOP) was dropped silently and put in the skipped
  symlinks, and an unstattable listed entry raised `PermissionError` from `okfy init`, a traceback
  instead of the refusal.
- **R23-4:** staleness is keyed by a row's content, not by its `(src, rel, dst)` triple. In
  `okfy preedit` over a workspace, a stale proposed row that shared a triple with a fresh accepted
  row made the accepted row count as stale and drop out of the lookup, so a dangling endpoint of the
  accepted row went unreported and the answer was `complete: true`. A row is now excluded as stale
  only when its own canonical content (`row_fingerprint`, the sorted-key JSON of the row) equals a
  stale row's, so a proposed row can no longer change an accepted row's completeness through the
  freshness filter. One `row_fingerprint` serves `federate.active_rows` and the row-local `stale`
  flag of `okfy preedit`; `n_stale` still counts the accepted rows excluded as stale.
- **Smoke negatives:** `scripts/smoke.sh` runs three installed-artifact negatives against the wheels
  it installs: a stale proposed twin must not hide the accepted endpoint diagnostic (R23-4), a
  gitfile with a non-UTF-8 byte must give a JSON report naming `W_CORPUS_SHA` (R23-2), and an
  unstattable corpus entry must be refused without a traceback (R23-3; it prints SKIP when chmod does
  not deny). On the published `b941aa9` wheel each fails at its own assertion. The smoke now also
  restores permissions before it removes its temporary directory.

Contract changes in round 23:

- None. No code, state or reason was added: R23-2 and R23-3 use the existing `W_CORPUS_SHA`,
  `E_CORPUS_SHA` and `E_CORPUS_LISTING` (states `failed`, `partial`, `unreadable`), and R23-4 surfaces
  through the existing `invalid_relation` and `endpoint_*` reasons. `okfy preedit` keeps
  `coverage: declared_only` and `semantic: not_assessed`, has no `allowed` key, and a no-match is not
  permission.
- The federation code digest moved from `6545a557…` to `560b55bb…` (`federate.py`). Federated eval
  runs recorded before this change read stale by the existing contract; they were not rewritten. The
  retrieval digest is unchanged.
- Residuals: `update._corpus_dirty_paths` still decodes git output as text; `scripts/memory-recipe.sh`
  with `CDPATH` set and a relative argument is unchanged; the listing policy for a corpus that is a
  subdirectory of a repository is still deferred by the owner. Round 23 is not published.

### Round 22

- **R22-1:** a read failure is a refusal, not an omission. A git listing that succeeds does not
  prove the listed files can be read: `git ls-files` exits 0 with an empty stderr for a file
  whose permissions forbid reading, and `okfy init` and `okfy snapshot --force` recorded a
  baseline that silently lacked it. `okfy init` and `okfy snapshot` (with or without `--force`)
  now refuse with `E_CORPUS_LISTING`, state `unreadable`, naming the unreadable paths, before
  their first write; the init target is not created and the bundle is untouched. The same holds
  for an unreadable file or directory in a corpus that is walked. `--force` does not turn unread
  bytes into read ones. A healthy refresh after a wrong baseline works as before, and manifest-mode
  `okfy diff` keeps reporting the same condition as `E_DIFF_PARTIAL_LISTING`.
- **R22-2:** the `.git` marker at the corpus root is probed with `os.lstat`, which has three
  answers (new `gitenv.git_marker`: absent, present, unobservable). A dangling `.git` symlink was
  read as no marker and the corpus was walked, which put gitignored files into the manifest and
  the plan. Now a present marker (directory, gitfile, valid or dangling symlink) selects git and a
  git failure is `E_CORPUS_LISTING` state `failed`; a marker that cannot be observed (EACCES,
  ELOOP) is `failed` too, never a walk. No marker is still a walk, and a corpus that is a
  subdirectory of a repository is still walked (the owner deferred that policy).
  `okfy validate` now reports, instead of silently skipping, a corpus whose `.git` is dangling or
  unobservable: `W_CORPUS_SHA` (`E_CORPUS_SHA` under `--strict-sources`) on `meta/corpus.md`.
- **R22-3:** the emptiness guard of `scripts/memory-recipe.sh` decides by directory entries, not
  by listing text. `[ -z "$(ls -A ...)" ]` strips trailing newlines, so a work directory whose
  only entry is named `"\n"` read as empty and the recipe ran in it. The guard now appends the
  exit status of `ls` inside the substitution, where nothing is stripped: a failed listing is
  still `cannot list`, a bare status means empty, and any entry (a newline-only name, a dotfile,
  a dangling symlink) is `is not empty`. Both refusal messages are unchanged.
- **R22-4:** an unobservable `.git` marker is not an absent one. `okfy preedit` decided "no git at
  or above the root" with `os.path.lexists`, which is false both for a missing entry and for a
  `stat` that failed (EACCES on a root without search permission), so a committed corpus whose
  root could not be searched answered `head_git_sha: null`, `verified: true`, `complete: true`.
  The probe is now `gitenv.git_marker` per level: `present` and `unobservable` both mean git may
  be there, and only a root where every level is `absent` is non-git. Such a root is
  refused at the root with `E_PREEDIT_ROOT` (the message names the permissions repair) before any
  operand is resolved, never verified. A garbage HEAD under a readable root is still
  `root_unverifiable`.
- **R22-5:** `okfy preedit` input paths are resolved by one function that tells a missing future
  file from a failed observation. A symlink loop, a directory without search permission or a file
  used as a directory made non-strict `realpath` hand back the unresolved suffix, and the lookup
  accepted it as a lexical path inside the root with `complete: true`. These operands are now
  refused per path with `E_PREEDIT_PATH`, new reason `unresolvable_path`; a path that does not
  exist yet (also under a missing directory), a plain `..` and an internal symlink are
  unchanged, and `symlink_escape`, `ambiguous_dotdot` and `names_root` keep their precedence.
- **R22-6:** git's output is read as bytes. `git rev-parse HEAD` stderr holding bytes that are not
  text (a gitfile naming a path with a `0xff` byte) raised `UnicodeDecodeError` before the return
  code was looked at, so the root was neither `ok` nor `unverifiable`. Stderr is decoded with
  replacement for the note only, and stdout counts as a HEAD only when it is a 40- or 64-digit
  lowercase hex commit id; anything else under git is `root_unverifiable`.
- **R22-7:** the endpoint check of `okfy preedit` no longer depends on a pull. It ran only for
  rows reached through a `constrains` pull to a destination match, so an accepted
  `alpha constrains kb:modules/never-existed`, `alpha same-as missing` or `alpha conflicts missing`,
  with `alpha` matching the path, vanished and the answer was `complete: true`. One function
  (`_relevant_endpoint_problems`) now decides it: an accepted, fresh row is relevant when either
  endpoint, or any member of its same-as class, is a matched concept, and both of its endpoints are
  checked (`endpoint_missing:<side>` / `endpoint_unreadable:<side>`, `invalid_relation`). A row
  with no relevant endpoint, and any proposed row, still never affects completeness; the pull and
  its note are unchanged.
- **R22-8:** the personal-scope filter keeps its attribution when `rel` or `status` is malformed.
  A diagnostic for a non-string `rel` has `src`/`dst` None, so a row of a personal concept that
  does not apply to this project passed the filter and turned the answer incomplete with a
  file-level exclusion. The filter now reads the raw endpoints the file carried and runs before any
  diagnostic or exclusion is built: such a row leaves the result equal to the no-row baseline except
  for the aggregate personal-exclusion note, which names no ref. A row with a non-string endpoint
  stays unattributable and conservative (`diagnostic:malformed_ref`, incomplete).

Contract changes in round 22:

- `E_CORPUS_LISTING` has a third state, `unreadable`, and its `failed` state now also covers an
  unobservable or dangling `.git` marker at the corpus root. `okfy init` and `okfy snapshot`
  refuse it with exit 2 before writing. (R22-1, R22-2)
- `E_PREEDIT_PATH` has a ninth per-path reason, `unresolvable_path` (a failed physical
  resolution: ELOOP, EACCES, ENOTDIR). `E_PREEDIT_ROOT` now also covers a corpus root whose
  `.git` entry cannot be observed (no search permission); `root_unverifiable` also covers an
  unobservable ancestor level and git output that is not a commit id. (R22-4, R22-5, R22-6)
- `excluded_relations` reasons `endpoint_missing:<side>` / `endpoint_unreadable:<side>` now also
  name relevant `same-as` and `conflicts` rows, not only `constrains`, and a row with a malformed
  `rel`/`status` of an out-of-scope personal concept appears nowhere. (R22-7, R22-8)
- `W_CORPUS_SHA` (`E_CORPUS_SHA` under `--strict-sources`) on `meta/corpus.md` has a new trigger: a
  corpus whose `.git` marker is dangling or cannot be observed, which `okfy validate` used to skip.
  (R22-2)
- Relevance widening: an accepted `same-as` row (or `conflicts` row) with a dangling endpoint is now
  listed in `excluded_relations` and makes the answer incomplete (`invalid_relation`) whenever
  either endpoint, or any member of its same-as class, is a matched concept, whether or not a pull
  was built. Before round 22 only a row reached through a `constrains` pull was checked. (R22-7)
- Scope rule: the personal-scope filter decides from the raw endpoints the file carried, before any
  diagnostic is built, so a malformed `rel` or `status` no longer exposes an out-of-scope personal
  row. (R22-8)
- Recipe guard: `scripts/memory-recipe.sh` decides that its work directory is empty by entries; a
  directory holding only a newline-named entry is refused as `is not empty`. The two refusal
  messages are unchanged. (R22-3)

### Round 21

- **R21-1:** `scripts/memory-recipe.sh` no longer lets an ambient `GIT_DIR`, `GIT_INDEX_FILE`,
  `GIT_WORK_TREE`, object-directory or common-dir variable redirect its git calls into another
  repository; it drops repository-location variables as `gitenv` does, before its first git call.
- **R21-2:** a work directory the recipe cannot list is refused before anything is written,
  instead of being taken for empty.
- **R21-3:** a git listing that exits 0 but wrote to stderr (an unreadable untracked subtree is
  only a warning) is a partial listing, not the whole corpus. The test is any stderr output,
  not English text. `okfy diff` in manifest mode therefore refuses with
  `E_DIFF_PARTIAL_LISTING` instead of reporting the unseen file as `removed`; `okfy init`,
  `okfy snapshot`, `okfy survey` and `okfy segment` refuse with the new `E_CORPUS_LISTING`
  before writing anything.
- **R21-4:** a corpus whose root holds `.git` is listed by git, and a failed listing (corrupt
  index, garbage HEAD, an empty or stray `.git`, no git executable) is the same coded refusal
  `E_CORPUS_LISTING`, with a repair path. It no longer falls back to a filesystem walk, which
  put gitignored files, and their digests, into the committed manifest. A corpus with no
  `.git` at its root, or a subdirectory of a repository, is walked exactly as before.
  `test_walk_falls_back_on_broken_git_dir` now asserts the refusal (renamed
  `test_walk_refuses_a_broken_git_dir_with_a_coded_error`): still no crash, no longer a wider scope.
- **R21-10:** `okfy preedit` (CLI and `okfy_preedit`, single bundle and workspace) no longer
  reads a HEAD it could not read as a successful non-git comparison. A root with a `.git` whose
  HEAD cannot be read (garbage HEAD, git not installed, an unborn repository) is
  `complete: false` with the new incomplete reason `root_unverifiable` and `root.verified:
  false`. A root with no `.git` at or above it is unchanged.
- **R21-5:** a reported `conflicts` entry takes its status, scope, reason, resolution, staleness
  and file from its own row. The lookup carried the resolution through a dictionary keyed by
  `(src, rel, dst)`, so a `proposed` row (or any other row with the same refs, in the same or
  another links file) could hand its resolution to an accepted conflict and flip it to
  `resolved`, depending on row order. Rows now travel as row-local entries; the triple-keyed
  `originals`, `file_of`, `hidden` and `stale_keys` are gone from `okfy preedit`.
- **R21-6:** a resolution resolves an operand only when its scope contains it: an equal entry,
  an ancestor directory entry, or `./`. A file scope no longer resolves a conflict over its
  parent directory (`out_of_scope`). Overlap still decides which operands a conflict `scope`
  reaches. New `pathscope.entry_contains`; sibling prefixes (`engine/` vs `engine2/`) never match.
- **R21-7:** a relevant accepted `constrains` row whose source concept is missing or unreadable,
  or whose destination or same-as class member is missing, is listed in `excluded_relations`
  (`endpoint_missing:<side>` / `endpoint_unreadable:<side>`) and the result is incomplete with
  `invalid_relation`. The summary note stays, but is no longer the only signal. A readable
  linked-only constraint (`path_scope: []`) is still pulled.
- **R21-8:** a crosswalk row whose `rel` or `status` is a list or a mapping is the named
  diagnostic `malformed_rel` / `malformed_status` from the validating reader instead of a
  `TypeError`, so `okfy preedit` and `okfy_preedit` answer `complete: false` with
  `invalid_relation`. The tolerant `load_rows` (federated query, `okfy workspace status`)
  skips such a row. No `except TypeError` was added to either surface.
- **R21-9:** the `path_scope` grammar rejects non-canonical entries: an empty segment
  (`src//auth/`, `src/auth//`) and a `.` segment (`src/./auth/`, `src/auth/.`). The lookup
  compares entries to normalised operands, so such an entry could never match and read as an
  all-clear. `okfy validate` reports `E_PATH_SCOPE`; the proposal and direct-write paths refuse
  it; a lookup on a bundle carrying one is `invalid_path_scope`, incomplete; a crosswalk
  `conflicts` row's `scope` (`invalid_scope`) and `resolution.scope` (`invalid_resolution`) are
  diagnosed by the same predicate. The single entry `./`, `[]`, spaces, NFC/NFD folding and
  duplicate detection are unchanged, and an owner's value is never rewritten.
- **R21-11:** `okfy preedit` checks the physical target of an operand as written, before the
  lexical fold of `..`. With `jump -> ../outside/child`, `jump/../secret.py` names
  `outside/secret.py`, not the root's `secret.py`; it is refused per path with
  `E_PREEDIT_PATH`, reason `symlink_escape` (the target is outside the root) or the new
  `ambiguous_dotdot` (inside the root but not the file the folded form names: a `..` crossed a
  symlink). Plain `a/../b.py` and an internal symlink `link/x.py` behave as before.

Contract changes in round 21 (every external contract the bullets above changed):

- New error code `E_CORPUS_LISTING`, with two states: `failed` (git exited non-zero, the `.git`
  at the root is empty or damaged, or git could not be run) and `partial` (git exited 0 but
  wrote to stderr). Raised by `okfy init`, `okfy snapshot`, `okfy survey` and `okfy segment`;
  the CLI exits 2. There is no filesystem-walk fallback for a root that holds `.git`. (R21-3, R21-4)
- `okfy diff` in manifest mode refuses a partial listing with the existing
  `E_DIFF_PARTIAL_LISTING`; its meaning is unchanged. (R21-3)
- New `okfy preedit` incomplete reason `root_unverifiable`, ordered after `snapshot_drift`, with
  `root.verified: false`. An unborn repository counts as unverifiable. (R21-10)
- `okfy preedit` per-path refusals (`E_PREEDIT_PATH`): `symlink_escape` is now judged on the
  physical target of the path as written, and `ambiguous_dotdot` is new. (R21-11)
- `path_scope` grammar: an empty segment or a `.` segment is invalid (`E_PATH_SCOPE`,
  `invalid_path_scope`, and for crosswalk rows `invalid_scope` / `invalid_resolution`). (R21-9)
- `okfy preedit` `excluded_relations` gains the reasons `endpoint_missing:<side>` and
  `endpoint_unreadable:<side>` (side `src` or `dst`); they fire `invalid_relation` and are not
  stale reasons. (R21-7)
- Crosswalk reader diagnostics gain `malformed_rel` and `malformed_status`; the tolerant
  `load_rows` skips such a row. (R21-8)
- Conflict resolution: `resolution_basis: out_of_scope` now means the resolution scope does not
  contain the operand (directed containment), and each conflict's status, resolution and
  staleness come from its own row. (R21-5, R21-6)
- `scripts/memory-recipe.sh`: repository-location `GIT_*` variables are dropped before the first
  git call, and a work directory it cannot list is refused (`cannot list <dir>`) instead of being
  taken for empty. (R21-1, R21-2)

### Pre-edit lookup

- **`path_scope`: where a concept applies, declared by the owner.** A concept may carry a list of
  corpus-relative paths: an entry ending in `/` is a recursive directory prefix (whole
  segments), an entry without it is one exact file, `./` is the whole root, `[]` means
  reviewed and applies nowhere, and an absent key means unmapped. There is no glob language.
  It is separate from `sources` (attribution) and from the personal `applies_to`.
  `okfy validate` checks the grammar on every non-meta concept that carries the key, and the
  proposal write paths refuse a malformed value before it lands. A direct-lane write
  (`okfy_write`) cannot set or change it (`E_DIRECT_OWNER_FIELD`, summary extended).
- **`preedit` policy block in `meta/purpose.md`.** `preedit: {governing_types: [...]}`
  names the concept types that govern. A match is `governing` only through `path_scope` or
  a `constrains` row, with a governing type, and (in a workspace) from a `constraints`
  member; everything else is `advisory`. A type may not also be in `direct.types`.
- **`okfy preedit <bundle|workspace> PATH... [--root R] [--limit N] [--offset K]` and the
  `okfy_preedit` MCP tool.** Read-only: one `okfy-preedit@1` object, identical on both
  surfaces, written to stdout or returned; nothing is written to the bundle and no index is
  built. The CLI exits 0 for any result and 2 for a refusal. The MCP server now has eight
  tools (it had seven).
- **Completeness is explicit, and is not permission.** Every result carries `complete`,
  `incomplete_reasons` (`unmapped`, `invalid_path_scope`, `truncated`, `snapshot_drift`,
  `root_unverifiable`, `refused_path`, `stale_relation`, `invalid_relation`, `unsupported_member`,
  `unreadable_concept`, `no_governing_policy`, `unresolved_conflict`) and `coverage:
  declared_only`. `complete: true` does not mean no rule exists or that the edit is allowed,
  and zero matches in a mapped bundle does not mean allowed. There is no `allowed` field.
  `limit`/`offset` page the matches, and a window that leaves a governing match out is
  `truncated`.
- **A `conflicts` crosswalk relation and a validating reader.** An owner-declared
  contradiction between two refs, with a required `reason` and an optional `scope` and
  `resolution`. `crosswalk.read_rows` reports a malformed row as a named diagnostic instead of
  dropping it; `load_rows` is unchanged, so `okfy query` and the federated query behave as
  before. The lookup reports declared conflicts with `semantic: not_assessed`: it does not
  judge whether two rules contradict. A stale `conflicts` row also covers its
  `resolution.decision`, and workspace export drops a row whose decision names an
  out-of-scope personal concept.
- **New codes:** `E_PATH_SCOPE`, `W_PATH_SCOPE_UNRESOLVED`, `E_PREEDIT_POLICY`,
  `E_PREEDIT_ROOT`, `E_PREEDIT_PATH`, `E_PREEDIT_PAGE`.
- **The federation code digest changed.** `crosswalk.py`, `federate.py` and `workspace.py`
  are in `federation_code_digest()` and all three changed, so recorded federated eval runs
  read stale against the new code: re-run `okfy eval run <workspace>` and repeat the owner
  checkpoint. The recorded workspace evals are historical; no run is rewritten. The retrieval
  code digest is unchanged (`bm25.py`, `index.py`, `lexicon.py`, `query.py` are untouched).
- **Docs and the agent protocol.** The User Guide (English and Russian) has a "Pre-edit
  lookup" section, and the stale tool enumerations in the MCP chapter now describe all eight
  tools. The codebase-map `AGENTS.md` template gains a short instruction (about 60 tokens in
  the rendered resident file): run `okfy preedit` before the first edit of a subsystem, stop
  and ask the owner when `complete` is false, and treat the result as context, not
  permission. The lookup is an advisory preflight, not enforcement.

### Fragment retrieval (opt-in)

- **Source passages for the top three hits, behind a flag.** `okfy query <bundle> <text> --passages` and `okfy_query(..., passages=true, passage_tokens=N)` add a `fragment` (`concept_id`, `path`, `sha256` of the raw file bytes, 1-based `lines`, `heading_path` (the headings in force at the extract's first line, `[]` before the first heading), `spans_sections` (`true` when a heading line lies inside the extract after its first line; absent otherwise), exact `extract`) and a `passage_state` (`quoted`, `truncated`, or `none_selected` with `passage_reason`). The selector is `core/src/okfy/passages.py`. Off by default; without the flag the output is unchanged, and with it the CLI prints one JSON object instead of the bare list.
- **A budget on fragments only.** `--passage-tokens` / `passage_tokens` is an integer from 100 to 8000, default 1200, and a hard limit on the summed fragment tokens, counted with the method named in `passage_budget.method`. The whole-response size is an estimate. Hit lists, lexicon and not-covered notes, stale marks and the existing `max_tokens` budget are never dropped to fit it.
- **Two refusals, both before any file is read.** `E_PASSAGES_WORKSPACE` for a workspace target, since a passage reads one bundle's concept files, and `E_PASSAGE_BUDGET` for a budget that is not an integer from 100 to 8000.
- **Ranking and federation are unchanged.** `bm25.py`, `index.py`, `lexicon.py`, `query.py` and `federate.py` are byte-unchanged, so `retrieval_code_digest()`, the federated query and the recorded evals are not affected. A fragment shares query terms with the question; it does not prove the answer.
- **Economy is unproven.** Whether passages save tokens has not been measured; a live comparison is pending, so this release makes no cost claim.

### Round 20

- **`okfy migrate ids` refuses a file with other hard links.** A concept page hard-linked
  to a file outside the bundle is neither a symlink nor outside the bundle by path, so the
  migration rewrote the shared inode and gave the outside file an `okf_id`. Every target
  (`meta/purpose.md` included) with `st_nlink > 1` is now refused with `E_MIGRATE_TARGET`
  (exit 2) in the same preflight, before the first write. The inode is not detached; copy
  the file to make it independent, then migrate. A plain copy of the same bytes still
  migrates and leaves the original untouched.
- **A `meta/purpose.md` symlink is refused by its own path.** The check read the Purpose
  through `Bundle.get`, which resolves links, so `purpose.md -> other.md` inside `meta/`
  looked like an ordinary file and the target was rewritten. The preflight now tests the
  lexical `meta/purpose.md`. `Bundle.get` is unchanged.
- **`okfy diff` / `okfy update` in git mode read paths from git's NUL format.** The corpus
  diff parsed the human `git diff --name-status` output, which C-quotes a path with
  non-ASCII, tab, LF or quote characters (the default `core.quotePath=true`), so the quoted
  text was taken for a file name. When a directory such as `документы/` became an embedded
  bundle, the removal list came back empty, no concept was marked affected, and `changed`
  held a quoted marker path. It now reads `--name-status -z` (status, path, and both paths
  of a rename), and the committed-tree listing is decoded from bytes so a CR in a name
  survives. The result is the same under `core.quotePath=true` and `false` on the same
  commits. Untracked or uncommitted marker files still change nothing.
- **A declared `manifest_digest` with its manifest gone no longer lets proposals claim
  `sources_state: checked`.** Round 19 made strict validate stop checking sources in that
  state, but `_source_checker`, which `propose`, `write_direct` and the coverage check
  also use, still fell back to the live corpus tree of an embedded bundle, so a proposal
  citing a file that was never in the frozen manifest was filed as `checked`. The rule now
  lives in `_source_checker` itself: when `meta/corpus.md` declares a non-null
  `manifest_digest` and `meta/corpus-manifest.json` is missing, a directory, or unreadable,
  there is no basis, so `propose` records `sources_state: unchecked` and `write_direct`
  skips the source check (no new refusal; a source the live tree lacks is no longer
  refused in that state either). A manifest that exists but is malformed or not a mapping
  keeps its `E_SOURCE_MANIFEST` / `W_SOURCE_MANIFEST` report. Strict validate still reports
  `E_MANIFEST_DIGEST` once and omits the `sources` summary. A present manifest, a legacy
  bundle with no declaration, memory-only and exported bundles behave as before.
  `manifest_digest: null` is recorded as a legacy opt-out, the same as an absent key: it
  declares nothing and takes the legacy branch (no new validation, no migration).
- **A ledger row with U+0085, U+2028 or U+2029 in a string is one row, not several.** The
  reader of `meta/memory.jsonl` (`events` for the working tree and `events_at_head` for the
  committed blob, one shared parser) cut the file with `str.splitlines()`, which also breaks
  on those characters; `json.dumps(ensure_ascii=False)` writes them raw. So
  `write_direct(..., observed={"by": "client\u2028name"})` reported `persisted: true`, the
  file held one valid row, and the reader returned no rows and two `E_MEMORY_LINE` problems
  (the next direct write then refused). Rows are now framed by LF only, one trailing CR is
  dropped so a CRLF ledger still parses, and a problem names the LF line number, the same
  numbering `ledger_prefix_check` and git use. A row without a final LF, and a real torn row
  (`E_MEMORY_LINE`, write refused before any change), behave as before.
- **`meta/ledger.jsonl` and `meta/dissent.jsonl` are read by LF too.** Their readers
  (`ledger.read_rows`, `dissent.read_rows`) had the same `splitlines()` framing as the memory
  ledger, and crashed with `JSONDecodeError` on a row whose string carried a raw U+0085,
  U+2028 or U+2029, which their own writers emit (`ensure_ascii=False`). CRLF rows still read.
- **`okfy index --usage --journal` reads the okfy-mcp journal by LF too.** `okfy-mcp` writes
  journal rows with `ensure_ascii=False`; a query carrying a raw U+0085, U+2028 or U+2029
  was split into fragments, so the row was lost and counted in `skipped_lines`. A torn
  row is still one skipped line.
- **The docs no longer say a signed JWT stays valid until its `exp` regardless of its key.**
  The Round 19 entry under "okfy-mcp HTTP authorization", the `okfy-mcp` README and the
  guide (English and Russian) said so; the runtime does not. The same unexpired token is
  answered `200` before the issuer retires its signing key and `401` once the JWKS set is
  re-read. One text now stands everywhere: a token is accepted only while it is signed by a
  key in the issuer's JWKS as cached here and before its `exp`; retiring a key ends
  acceptance of every token that key signed once the cached set is re-read (at most 300 s,
  or sooner when a token names an unknown `kid`; newer PyJWT re-reads for that at most once
  every 30 s); there is no per-token revocation, so to cut off one token without retiring
  its key, wait for its `exp` and keep access tokens short-lived. No code change.
- **The Round 19 HTTP unit fixture runs on PyJWT 2.15, not only 2.13.** It served JWKS
  through `urllib.request.urlopen` alone and assumed an immediate re-read on an unknown
  `kid`, so on 2.15 it hit the network seam `build_opener().open` and tripped over the
  30 s re-read cooldown. The fixture now serves both seams, drives PyJWT's own clocks (as
  `scripts/smoke.sh` does) and fails any other network attempt. A new test pins that the
  same unexpired token gets `200` before the key is retired and `401` after the set is
  re-read, while a token of the current key gets `200`. Tests only; the dependency range
  is unchanged.

### Stricter behaviour

- **A workspace `acceptance` policy is a closed, typed mapping.** `release-check` on a
  workspace used to replace an `acceptance` that was not a mapping (a list, a string) with
  `{}`, and ignored unknown keys, so a misspelled `min_owner_pas: 10` silently judged the
  workspace against the default bar of 8. Now `acceptance` absent or `null` keeps the
  defaults; otherwise only `min_owner_pass` and `min_adversarial_pass` are allowed, each an
  integer, and anything else is `E_REL_WS_ACCEPTANCE_INVALID` naming the key or the shape.
  A workspace that declared such a policy can fail now where it passed before.
- **A non-string verdict is a malformed eval record, not a crash.** An `owner_verdict` or
  `llm_verdict` that is a list or a mapping raised `TypeError` out of both release
  predicates. It is now `E_REL_EVAL_INVALID` (bundle) or `E_REL_WS_EVAL_INVALID` (workspace)
  with `ok: false`, from the API and the CLI alike.

### okfy-mcp HTTP authorization

- **A repeated `okfy:write` no longer survives `--oidc-write-group`.** Outside the group the
  server removed only the first `okfy:write` from the token's scope string, so a token whose
  `scope` repeats it (`okfy:read okfy:write okfy:write`) kept a write scope and reached
  `okfy_write` and `okfy_propose`. Every copy is removed now. A group member, and a token
  with one `okfy:write`, behave as before.
- **A signing key the issuer stops publishing is no longer trusted for the life of the
  process.** The JWKS client kept an unbounded per-key cache, with no expiry. It now relies on
  the bounded JWKS-set cache only (300 s, and a token naming an unknown `kid` re-reads the
  set; newer PyJWT releases allow that re-read at most once every 30 s), so a retired key is
  refused once the set is re-read. Individual token revocation is still not offered, and the
  docs now say so. (An earlier wording of this entry said a JWT the issuer already signed
  stays valid until its `exp`, independent of its key; that was wrong, see Round 20.)

### Direct write and `migrate ids`: filesystem and ledger

- **A planted temp file can no longer redirect a direct write.** The atomic write used a
  predictable temp name (`.<file>.tmp`) opened with a plain `open`, so a symlink planted
  there (to `meta/purpose.md`, or to a file outside the bundle) received the write, and the
  cleanup on failure then unlinked it. The temp is now this call's own: a random name of
  fixed length (`.okfy-<16 hex>.tmp`, so a long concept file name never pushes it past the
  file-name limit) created with `O_EXCL|O_NOFOLLOW` in the same directory, fsynced, then renamed over the
  target. It still ends in `.tmp`, so concept discovery never sees one, and the file keeps
  its mode (a new file gets what a plain create would). Only the call's own temp is removed.
- **`okfy migrate ids` checks every file before it writes the first.** A concept page that
  was a symlink to a file outside the bundle had that file rewritten, after the pages before
  it were already migrated. Every page the run would write (`meta/purpose.md` included) is
  now checked first, and one that is a symlink or resolves outside the bundle is refused
  with the new `E_MIGRATE_TARGET` (exit 2, nothing written). Pages that already carry an
  `okf_id` are not touched, and a second run still writes nothing.
- **A direct write refuses on an unreadable ledger instead of recording into it.** With an
  unparseable line in `meta/memory.jsonl` (a torn `{"interrupted":`), `okfy write` succeeded
  and its row was lost. It is now refused with `E_MEMORY_LINE`, the code `okfy propose`
  already used, before the concept, `log.md`, the ledger or git change. The byte-prefix
  check (`E_LEDGER_REWRITTEN`) stays; both run.
- **A ledger whose last row has no final newline no longer swallows the next row.** Every
  caller appends through `memory.record`, which now writes the missing newline in the same
  append as the new row, so the event is a line of its own and reads back. A ledger that
  already ends in a newline is appended to exactly as before.

### Faster federation

- `federated_query` loads each member's index once per query instead of M² + 2M times
  (24 loads for four members). The answers were byte-identical to the previous release's
  (before the stopword change below). Measured on synthetic workspaces only, the median
  time per query fell by 1.2× to 5.2×.
- `federate.py` is part of the federation code digest, so recorded federated eval runs now
  read stale against the new code: re-run `okfy eval run <workspace>` and repeat the owner
  checkpoint. No recorded run is rewritten.

### Codes

- **New:** `E_MIGRATE_TARGET` (`okfy migrate ids` would write through a symlink, outside
  the bundle, or into a file with other hard links).
- **Widened, same names:** `E_MEMORY_LINE` also stops a direct write;
  `E_REL_WS_ACCEPTANCE_INVALID` also covers a non-mapping policy
  and unknown keys; `E_REL_EVAL_INVALID` and `E_REL_WS_EVAL_INVALID` also cover a non-string
  verdict.

### Not claimed

- Nothing here measures extraction accuracy or the quality of an agent's answers.

### Retrieval: stopwords, matched terms, outline

Contributed by @theaspirational (vsov/okfy#5).

**This moves the retrieval code digest.** `bm25.py` and `query.py` are in `RETRIEVAL_MODULES`,
so `retrieval_code_digest()` changes from `11e03e87…a83638d` to `1c923e5c…b021` and every
recorded eval run, bundle or workspace, reads stale against the new code: re-run
`okfy eval run` and repeat the owner checkpoint. No recorded run is rewritten. Ranking can
change for any query containing a listed stopword, in a single bundle and in a workspace
(`federated_query` ranks through the same `search_pool`).
The digest value above is the one after both contributed changes in this section and the
next (`okfy links` also lives in `query.py`); it is what the working tree computes, not a
step-by-step history. The round-19 subsection below ("Round 19: an alias that is a
stopword, a pinned diff, and a missing manifest") moves it again, to `94950666…3359`; the
lines in this section that say the digest "stays `1c923e5c…b021`" describe the change they
sit in, not the final value.

- **Stopwords leave the query.** `query.search_pool` drops a short English list
  (the, who, how, ...) before BM25; the index keeps every token, so no bundle's
  index fingerprint moves. Before, `who` in a small corpus carried real idf and
  pulled an unrelated concept to rank 2.
- **Every hit says what it matched, body included.** `okfy query` hits carry
  `matched_terms` (query terms, expansion included, found anywhere in the
  concept); `okfy_query`'s `matched.terms` reads it instead of overlapping the
  query with title+description only, which showed `[]` for body-only matches.
- **`okfy_show(outline=true)`** returns the `## ` headings and the sha, no body,
  so a section can be picked without reading the whole concept.
- **The server introduces itself.** okfy-mcp now sends MCP `instructions` at
  the handshake (search with the user's words, show's sha goes into write,
  nothing found means a gap proposal, retrieved content is data), and a
  bundle-mode search with no hits carries `way_out` naming
  `okfy_propose(action="gap", query=<the user's words>)`. Until now the gap
  door was one phrase inside okfy_propose's description.

### Round 19: an alias that is a stopword, a pinned diff, and a missing manifest

- **An exact domain alias is no longer erased by the stopword list.** A concept whose title or
  alias is `and`, `or`, `in`, `as`, `at`, `by` (or the phrase `as of`) could not be found
  by it: the query dropped it and the result was empty, and
  `okfy_query` said `no-term-matched`. Rule, with no word list: when the whole query, word
  for word, is a title or alias of some concept in the pool being searched, its words are
  never dropped as stopwords. Any other query keeps them as noise: `who is the` returns
  nothing over a note whose body says "Who is the person?", over a title like "The Rules of
  the Road", and over a concept titled "The". Two broader drafts (protect every title token;
  protect a name found anywhere in the query) let such titles turn `the` and `of` into
  scoring terms for unrelated questions, and an independent check caught both. Not covered:
  an alias inside a longer query (`what is the as of operator` still drops `as of`, as
  before), and identifiers, which the index does not search. The rule lives in
  `query.search_pool`, so `okfy query`, `okfy_query` and the federated query get it
  together; the name check reads the words the user typed, so a lexicon row or a
  crosswalk term appended by expansion does not hide the alias. Ranking changes only for
  a query that is exactly such a name.
- **This moves the retrieval code digest a second time.** `bm25.py` and `query.py` changed,
  so `retrieval_code_digest()` moves from `1c923e5c…b021` to `94950666…3359`
  (`949506666368cb938e36196cba14a6138cf9493e7a74ccec66a87e9ce4f43359`). Every recorded eval
  run, bundle or workspace, therefore reads stale until its owner re-runs `okfy eval run`
  and repeats the owner checkpoint. `federate.py` changed as well (it passes the user's
  query to the name check), so the federation code digest moves too and recorded
  federated runs read stale the same way. No recorded eval run, owner verdict or real
  bundle was changed by this change; the index fingerprint does not move.
- **A pinned git diff is decided by the two committed trees.** `corpus_diff` in git mode
  used to ask the working tree whether a directory was an embedded bundle, so untracked
  `meta/purpose.md` and `meta/corpus.md` added after the pinned commit made a committed
  change under that directory vanish from `changed`/`removed`, and `update_plan` with it.
  Now a path is corpus on a side iff it exists in that side's committed tree and no
  ancestor directory in that tree is an embedded bundle (both markers committed, with the
  types `init` gives them). Corpus on both sides and modified: `changed`. Corpus on the new
  side only: `added`, including a directory that stopped being a bundle. Corpus on the old
  side only: `removed`, including a directory that became a bundle (its files left the
  corpus). Corpus on neither side: not reported. Files git does not list as modified, but
  whose directory changed role, are found from the two tree listings and reported the same
  way. Untracked or uncommitted marker changes alter nothing. Manifest mode (a corpus
  that is not a git repo) is unchanged and still reads the working tree.
- **A missing manifest no longer disables the declared digest.** When `meta/corpus.md`
  declares `manifest_digest`, a `meta/corpus-manifest.json` that is missing, unreadable (a
  directory, no permission) or wrong is `E_MANIFEST_DIGEST`; before, only a present but
  damaged file was. A snapshot that never declared a digest is unchanged. The sources check
  does not run without the manifest, standalone or embedded (an embedded bundle no longer
  falls back to the live corpus tree there), so the report has no `sources` summary and
  claims no pass. A bundle that fails this way now validates red until `okfy snapshot` is
  re-run.

### Stable page identity: `bundle_uid`, `okf_id`, `okfy migrate ids`

Contributed by @theaspirational (vsov/okfy#4).

A concept id is its path, so it changes when a page moves. Two frontmatter keys do not.

- **`bundle_uid`** in `meta/purpose.md` names the bundle; `okfy init` writes it.
  **`okf_id`** in each concept page outside `meta/` names the page and travels with the file
  through a rename. `okfy review accept` gives a created or superseding page a fresh `okf_id`
  and keeps the page's own, and purpose.md's `bundle_uid`, through an update; `okfy refine`
  refuses text that changes either. Reserved pages (`index.md`, `log.md`, the root docs,
  `meta/*`) carry none: they are addressed as `bundle_uid` plus their path.
- **`okfy migrate ids <bundle>`** backfills both into a bundle made before ids existed, as
  uuid5s derived from history every full clone shares, so two clones migrating offline
  agree; a rerun writes nothing; an id once written is never recomputed. A shallow clone
  that does not reach the creating commit derives different ids: migrate from a full clone.
  It commits past the policy hook, like `okfy stale`.
- **An older bundle stays green.** A missing id is a warning only (`W_BUNDLE_UID_MISSING`,
  `W_OKF_ID_MISSING`); `E_BUNDLE_UID`, `E_OKF_ID` (present but blank or not a string) and
  `E_OKF_ID_DUPLICATE` (two pages share one) are errors, and none of them can fire on a
  bundle that has no such key.
- **Any URI scheme is an external link.** `[x](other-tool://node/page.md)` used to resolve as
  a bundle path and warn `W_DANGLING_LINK`; it is now external, and `okfy links` and
  `okfy_links` return an `external` list beside `out` and `backlinks`.
- **A bundle embedded anywhere in a corpus is not corpus.** It is recognised by its
  `meta/purpose.md` and `meta/corpus.md` (with their `type`), not by a dot-folder name, and is
  left out of `okfy survey`, the snapshot manifest and `okfy diff`; `okfy init --embed` no
  longer lists its own bundle. A corpus that held a non-dot embedded bundle will see those
  files leave its manifest on the next `okfy snapshot`.
- **`meta/corpus.md` records `manifest_digest`**, one sha256 over `meta/corpus-manifest.json`,
  written by `okfy init` and `okfy snapshot`; `okfy validate` refuses a mismatch with
  `E_MANIFEST_DIGEST`. A snapshot made before this key keeps validating.
- **Retrieval:** the index is untouched, so no bundle's index fingerprint moves. The code
  digest moves because `okfy links` is in `query.py` (see the digest note above); no ranking
  changes.

New codes: `W_BUNDLE_UID_MISSING`, `W_OKF_ID_MISSING`, `E_BUNDLE_UID`, `E_OKF_ID`,
`E_OKF_ID_DUPLICATE`, `E_MANIFEST_DIGEST`.

### Direct-write lane: types the owner opens are written without review

Contributed by @theaspirational (vsov/okfy#6).

Until now an agent had one way to write: file a proposal and wait for the owner.
That is right for knowledge and wrong for session notes, observations and logs,
where several agents working at once need to see each other's writes now. This
adds a second lane the owner opens per concept type. Nothing changes for a bundle
that does not open it, and no version is bumped here: the number is the
maintainer's.

#### Stricter behaviour — read this before upgrading

- **`direct` in `meta/purpose.md` is now a checked key.** A bundle that already
  carried a `direct:` key of its own fails `okfy validate`: `E_DIRECT_KEY` (not a
  mapping, or a key other than `types`), `E_DIRECT_TYPE` (`types` is not a
  non-empty list of names, or names a type outside the plan's declared `types`)
  and `E_DIRECT_POLICY` (declared while `write_policy` is not `proposals`, where
  the block would silently do nothing). All three are errors at every strictness
  level, like `E_WRITE_POLICY`: it is a trust boundary. A listed type outside the
  archetype's canonical list, in a bundle that declares no `types`, is
  `W_DIRECT_TYPE` and becomes `E_DIRECT_TYPE` under `--strict-schema`.
- **"An MCP tool call can never touch a final concept" is now true per bundle, not
  per adapter.** It holds exactly as before for every bundle without a `direct`
  block, and for `okfy_propose` always. A bundle that lists `direct.types` has
  chosen otherwise for those types.

#### The lane

- `meta/purpose.md`, under `write_policy: proposals`, may declare
  `direct: {types: [note, session-log]}`. `write_policy` keeps its two literals and
  the pre-commit hook is unchanged: a direct write commits through the same
  `_commit` every sanctioned writer uses.
- `okfy.proposals.write_direct(bundle, concept_id, *, actor, expect_sha256,
  text=None, patch=None, note="", observed=None, archetype=None)` writes a
  concept whose type is listed, at once. The lane is decided by the concept's
  current type on an update and by the proposed type on a create; `meta/` and the
  other reserved directories are never in it, so the policy cannot rewrite itself.
- Concurrency is optimistic and adds no lock. `expect_sha256` is the sha256 of the
  file the author read (the digest `okfy_show` already returns), or `absent` to
  create. The check and the write are one step under the existing `review_lock`;
  the file is read once and the digest and the text both come from that read; the
  write is a temp file and a rename. Two writers holding the same sha256: one
  lands, the other is refused `E_WRITE_BASE_MOVED` with the current sha256. A hand
  edit takes no lock: one made before that read is refused the same way, one made
  between the read and the rename is lost. That window is the honest limit of
  compare-then-rename.
- What a proposal would be refused for is refused here, by the same functions: the
  injection and secret scans (now `_refuse_scan`, shared with the propose gate),
  `sources:` outside the corpus, and the archetype schema `review accept` applies
  (`E_DIRECT_INVALID`). The rejected-content and duplicate-title gates belong to
  the review queue and do not run.
- A direct write cannot change `type` (`E_DIRECT_RETYPE`) or what an owner decision
  wrote: `verified`, `generated`, `stale`/`stale_reason`/`stale_since`,
  `supersedes`/`superseded_by` (`E_DIRECT_OWNER_FIELD`). They are carried over from
  the file. A concept edited this way after a verification warns
  `W_VERIFIED_SUPERSEDED`, as after a `refine`.
- Page identity stays the core's (vsov/okfy#4 meets this lane; added when integrating).
  A direct update keeps the page's own `okf_id` whatever the text carries (a rewrite
  that dropped, forged or copied one does not re-identify the page, and cannot raise
  `E_OKF_ID_DUPLICATE`), and a direct create gets a fresh one, as `review accept` does.
  Without this a created page had none (`W_OKF_ID_MISSING`) and a full-content update
  could replace it.
- The ledger row is an `accept` with `proposal: null`, `origin: agent-direct`, the
  agent as actor and `base_sha256`. Readers of `accept` rows see it as a content
  write: `release-check` counts it among the changes since the last package and
  `okfy diff` lists the concept as protected. It is not a decision, though:
  `okfy.update.unresolved_rejections` and `okfy dismiss` now read one shared
  fold (`okfy.memory.latest_decisions`) that skips it, so an agent's write
  neither disposes of an owner's rejection nor stops the owner dismissing it.
- `okfy write <bundle> <concept_id> --as <actor> --expect <sha256|absent>
  (--from <file> | --patch-file <hunks.json>) [--note …]` is the CLI door. It
  refuses; it does not fall back.

#### MCP

- `okfy_write(concept_id, actor, expect_sha256, content?, patch?, note?,
  evidence?)` (`actor` is optional in the tool schema since the HTTP transport
  below; a call with no verified token and no actor is still refused). The agent does not need to know the policy: a listed type is written
  at once (`channel: "direct"`, with the new file's `sha256`); any other type, and
  any bundle with no lane, is filed through the `okfy_propose` path
  (`channel: "proposal"`) and only if the target is still at `expect_sha256`
  (`okfy.proposals.propose_checked`: check and filing under one lock).
  `persisted: true` in both cases. A refusal is `{error, message, way_out}`;
  `E_WRITE_BASE_MOVED` also carries `current_sha256`.
- A direct write's ledger row keeps four counters from the server's own session
  trace: `by`, `queries`, `target_shown` and `base_shown`, the last being whether
  this session was shown the concept at the very sha256 it wrote against. An agent
  that takes `current_sha256` from a refusal and writes without reading is
  therefore visible in the ledger. It is not prevented: like every actor string
  here, this is a record, not authentication.
- `okfy_show`'s docstring points at `okfy_write`. No existing response shape
  changed.
- A direct **create** of a type the plan does not declare is refused
  `E_UNKNOWN_TYPE` (reused with validate's meaning) before the lane check. It
  used to miss the lane and, over MCP, become a proposal invisible to search
  that validate rejects the moment it is accepted.
- `okfy package` adds one block to `AGENTS.md`, between `<!-- okfy:direct -->`
  markers, naming the listed types and `okfy write`. It is rendered only for a
  bundle with a well-formed `direct` block, so every other bundle's `AGENTS.md`
  is byte-identical, and the memory block and its 60-token bound are untouched.
  Without it the memory block's "never written to concepts directly" was false
  for exactly the types the owner had opened.

#### New codes

`E_DIRECT_POLICY`, `E_DIRECT_KEY`, `E_DIRECT_TYPE`, `W_DIRECT_TYPE`,
`E_DIRECT_DISABLED`, `E_DIRECT_LANE`, `E_DIRECT_TARGET`, `E_DIRECT_RETYPE`,
`E_DIRECT_OWNER_FIELD`, `E_DIRECT_INVALID`, `E_WRITE_CONTENT`,
`E_WRITE_BASE_MOVED`, `E_WRITE_WORKSPACE`. Reused with their meaning unchanged: `E_UNKNOWN_TYPE`,
`E_PROPOSAL_ACTOR`, `E_PROPOSAL_INJECTION`, `E_PROPOSAL_SECRET`,
`E_PROPOSAL_SOURCE`, `E_PROPOSAL_TRUNCATED`, `E_FRONTMATTER`, `E_PATCH_SHAPE`,
`E_PATCH_COUNT`, `E_RESERVED_DIR`.

#### Changed outside the lane — the injection scan

- **The injection scan of an agent-written value joins hard wraps only**
  (`okfy.injection.scan_values`, used by `okfy propose` and `okfy write` alike).
  The second pass used to collapse every newline of a value, which turned a
  whole concept body into one line; the co-occurrence rule for exfiltration,
  written for one line, then matched an outbound verb, the word `password` and
  a URL anywhere in the document. Measured on a 48-concept bundle of working
  notes: 8 bodies refused, 5 of them for words in different paragraphs or in
  different items of one list, each with the entire body as the excerpt. A
  blank line and the start of a list item, heading, quote or table row are now
  line boundaries. A phrase split by a hard wrap is still caught. The owner
  accepted the price when integrating this change. Exactly what the scan no
  longer sees, measured against the previous scan (585 phrase/separator cases, no
  case where the new scan reports something the old one did not):
  - `instruction-override` and `identity-reassignment`: a phrase cut by a blank
    line (or a whitespace-only line) between two of its words. Cut by a list
    marker, quote or heading it was not caught before either.
  - `exfiltration`: a verb, a secret term and a destination spread over several
    paragraphs, list items, quotes, headings or table rows. A verb and a STRONG
    secret (`.env`, `api key`, ...) on one line, and verb + secret + URL on one
    line, are caught as before.
  An author who wants to evade a phrase rule can now insert a blank line; the rules
  were never topic-complete (see `okfy.injection`), and `okfy refine` remains the
  owner's door for text that is legitimate. `tests/test_injection.py` pins the
  boundary in both directions.

#### Not changed, and not claimed

- `okfy refine` still writes whatever parses and has a `type`: no injection or
  secret scan, no archetype check, no lock, no base check, a plain `write_text`.
  It is the owner's door and was left alone; the direct lane does not go through
  it and does not inherit any of that.
- `protocols/memory.md` does not mention the lane; the packaged `AGENTS.md` does
  (see above), and an MCP agent also learns it from the tool description.
- One commit per direct write. A write touching several concepts at once is still
  a set of proposals.
- A concept created directly is found by `okfy query` at once and is not in
  `index.md` until the next `okfy package`, exactly like an accepted create.
- **Retrieval is untouched.** None of `bm25.py`, `index.py`, `lexicon.py`, `query.py`
  changed, so `retrieval_code_digest()` stays `1c923e5c…b021`, the value in the
  retrieval note above, and no recorded eval run reads stale because of this change.
- **The v0.30 pilot arm still has no write tool.** `okfy_write` is a seventh server
  tool, and the client offers every registered tool unless a deny rule names it: the
  contract test (`adapters/mcp/tests/test_client_contract.py`) found it offered to arm
  OKFy. The runbook's `--disallowedTools` and settings `deny` list now name it beside
  `okfy_propose`, the contract test calls it and sees it refused, and a pin in
  `core/tests/test_doc_pins.py` fails if any server tool outside the five read tools
  is missing from those lists. The v0.28 harness's preflight tolerates `okfy_write`
  in the server's `tools/list` as an unoffered tool; the tools a run is offered are
  unchanged.
- The key is called `direct`, and `write_policy: direct` already exists (an embedded
  bundle whose gate is the host repository's code review). The two are different
  things: `write_policy: direct` opens every type, `direct: {types: [...]}` opens
  some under `write_policy: proposals`. A `direct:` block next to
  `write_policy: direct` is refused `E_DIRECT_POLICY`.
- This adds `core/tests/test_direct_write.py` and
  `adapters/mcp/tests/test_write_tool.py`. The public export ships no tests by
  policy, so on the public repository CI's conditional unit step skips; here, in the source
  tree, it runs the full core, mcp and normalize suites, these two files among them.

### okfy-mcp over HTTP: one shared token, or identities from an OpenID issuer

Contributed by @theaspirational (vsov/okfy#7), on top of the direct-write lane
above. Nothing changes for stdio apart from the one schema detail named below, and no
version is bumped here.

- **`okfy-mcp serve <path> --http --token-file <file>`** serves the same seven
  tools over streamable HTTP (`/mcp`, stateless, JSON responses) to many clients
  at once, behind one bearer token. The token is read from a file, never a flag;
  a missing, unreadable or short (< 32 characters) token refuses to start
  (`E_MCP_HTTP_TOKEN`), and a request without the header is `401` with the
  documented envelope (`E_MCP_UNAUTHORIZED`) before the MCP layer sees it. Plain
  HTTP: loopback by default, TLS and anything finer than one token are a reverse
  proxy's job (`--public-url` names the host the proxy serves). No `Session`
  trace over HTTP: one process serves every client, so `observed` is absent
  rather than attesting one client's reads to another's write.
- **`--http --oidc-issuer <url> --oidc-audience <client id>`** is the other
  access model (exclusive with `--token-file`): JWT access tokens from an OpenID
  issuer (Authentik, Keycloak, ...), RS256 only, checked against the keys the
  token's own `iss` publishes, `aud`, `exp`, `nbf`; discovery is read once at
  start and a wrong issuer refuses to start (`E_MCP_OIDC_ISSUER`). Both flags
  repeat. `okfy:read` is needed to call anything; `okfy:write` for the two
  writing tools (`E_MCP_FORBIDDEN`, nothing written). `--oidc-write-group` makes
  `okfy:write` count only for users whose `groups` claim lists the group;
  `--oidc-read-group` closes the bundle to everyone else (`403`), so one issuer
  can serve several bundles. The token names the **actor** of `okfy_write` and
  `okfy_propose`: its `okfy_actor` claim when the issuer sets one, else `sub`
  (`oidc:<sub>` when that is not already an OKF actor string); the tools' `actor`
  parameter is now optional and ignored under a token. The first place an actor
  string here is a verified identity, not a declaration. The MCP SDK's own
  middleware answers 401/403 in the OAuth shape and publishes the
  protected-resource metadata (`okfy:read`, `okfy:write`, `offline_access` as
  `scopes_supported`) so a client can find the issuer.
- **`--oidc-issuer-prefix <url>`** trusts every issuer whose URL lies under the
  prefix as it turns up (the shape an Authentik gives MCP clients that registered
  themselves): discovery on first sight, signature checked, **audience not**.
  Residual risk, stated plainly: without the audience check nothing ties such a
  token to this bundle except the scope names `okfy:read` / `okfy:write`. Anyone
  the issuer lets register a client under that prefix and log a user in, and any
  account holder of the issuer who can do so for themselves, obtains a token that
  passes; it reads, and writes if the issuer grants the scope and no
  `--oidc-write-group` applies. Use it with `--oidc-read-group`. The `okfy_actor`
  and `sub` claims, `groups` and the scopes are the issuer's word: OKFy
  verifies the signature, it does not audit the issuer.
- **One change reaches stdio**: `actor` is no longer in the `required` list of the
  `okfy_propose` and `okfy_write` tool schemas (the schema is one for every
  transport). A call with no verified token and no actor is still refused,
  `E_PROPOSAL_ACTOR`; the requirement moved from the schema to the call, and
  `test_propose_memory.py` pins that. The tool descriptions say it.
- **Not claimed.** The shared token file's permissions and the token's entropy
  are not checked. There is no TLS, no rate limit and no clock-skew allowance
  (`exp` is exact). Blocking discovery and key fetches run in worker threads with
  a 10 s timeout; an issuer that answers slowly can still occupy them.

#### Found and fixed while integrating it

A read of the transport turned up seven defects, each pinned by a test that
failed first (`adapters/mcp/tests/test_http_hardening.py`):

- The prefix was compared as text, and a token's `iss` was fetched before any
  signature check, so `<prefix>../x`, `%2e%2e`, `?` and `#` made the server send
  a GET to the issuer's host at a path outside the prefix (blind: the issuer's
  discovery document has to state itself as exactly that `iss` before anything
  is trusted, and the host and scheme were always the prefix's). Only plain
  path segments below the prefix are fetched now.
- A failed discovery and a `kid` the issuer does not publish were fetched again
  on every anonymous request; both are remembered for 60 s (at most 1024 entries).
- A `groups` claim that was a string was matched as a substring (`owners`
  matched `owners-admins`), and a non-list claim raised an unhandled error. A lone
  string is one group now, anything else names none.
- Under `--oidc-issuer`, a tool call that carried no verified token (the request
  context lost) fell back to the client's own `actor` with no scope check. It is
  refused now (`E_MCP_UNAUTHORIZED`).
- A token identity must be one printable line of at most 255 characters
  (`E_PROPOSAL_ACTOR`): the core's actor grammar let control characters through
  and its `$` also matched before a trailing newline, and the string lands in a
  ledger row and a commit message. The core now holds every actor to that bar
  (see the next section), so the check here is the early, token-naming one.
- The log line for a refused token now carries `repr()` of the reason, which
  includes the caller's own `kid`; the shared token's check passes only
  `lifespan` and checked `http` scopes (a websocket scope used to pass through).

#### Dependencies and codes

`adapters/mcp/pyproject.toml` declared only `okfy` and `mcp`, while the transport
imports PyJWT (with its `crypto` extra), httpx, anyio, uvicorn, starlette and
pydantic directly. They are declared now, with bounds that restate what `mcp`
requires; `uv.lock` records the new edges and moves no version.
`tests/test_dependencies.py` fails when an import under `src/` has no declared
dependency, or a declared one no import.

New codes: `E_MCP_HTTP_TOKEN`, `E_MCP_UNAUTHORIZED`, `E_MCP_OIDC_ISSUER`,
`E_MCP_FORBIDDEN`; `E_PROPOSAL_ACTOR` is reused for a token identity that is not
an OKF actor. The transport adds no tool: the pilot arm's deny list and its pin
are unchanged and still hold, and the retrieval code digest stays `1c923e5c...b021`.

### Commits name their writer: author and an `Okfy-Actor` trailer

Contributed by @theaspirational (vsov/okfy#8). No version is bumped here.

- **Every commit that writes a ledger row names its writer where git looks.**
  The message gains an `Okfy-Actor: <actor>` trailer, read back with
  `git log --format='%(trailers:key=Okfy-Actor,valueonly)'`. For any actor but
  a `human:` one the commit's author is the actor too (`claude:vasyl
  <claude-vasyl@okfy.invalid>`), so `git shortlog`, `git log --author` and
  tools that group by author tell agents apart. A `human:` actor is the owner
  working through their own git identity, which stays the author; the
  committer is always whoever runs the process. Before, a hub serving many
  agents committed everything under one name and the writer was only in
  `meta/memory.jsonl` and `log.md`. Older commits are unchanged; the ledger
  still covers them. In practice the agent is the author of the direct-write
  lane's commits (`direct: ...`); `review accept`, `reject`, `dismiss` and
  `refine` are the owner's verbs, carry the owner's `human:` role in the
  trailer and keep the owner's git identity as author. Commits that write no
  ledger row (`eval`, `ledger`, `dissent`, workspace release) carry neither.
- **An actor is now one printable line of at most 255 characters, for every
  caller (`E_PROPOSAL_ACTOR`, nothing written).** The actor string reaches a git
  author name and a trailer, and the grammar alone did not hold it: `\S` lets
  control characters through (a NUL made `git commit` fail after the concept file
  and the ledger row were already written; an escape sequence went into the
  message verbatim), and `$` also matched before a trailing newline. `ACTOR_RE`
  is anchored with `\A...\Z` and `check_actor` refuses non-printable characters
  and anything over 255. This tightens the stdio and shared-token transports as
  well, where the actor is the client's own declaration. `_commit` repeats the
  check as a backstop: an actor that reaches it any other way (a hand-edited
  ledger row) has non-printable characters replaced by `?` and is cut at 255, so
  it can start no second trailer and cannot break the commit. The author's email
  is built from the actor's word characters only and always ends `@okfy.invalid`;
  `<` and `>` are dropped from the author name.
- **Nothing that reads history changes.** No check in the core reads a commit's
  author or committer: the append-only ledger check compares file bytes, the
  merge audit and the anchor checker read `%H` and blobs, and `git_env()` still
  keeps `GIT_AUTHOR_*` / `GIT_COMMITTER_*` (the `--author` option outranks the
  author variables; the committer stays the process's). The retrieval code digest
  is unchanged (`1c923e5c...b021`).
- **Not claimed.** An actor is still a declaration, so an author line is the
  string the writer sent, not an authenticated identity (except under
  `--oidc-issuer`, where it is the token's). A `human:` actor sent through the
  direct-write lane is committed under the process's own git identity, as every
  write was before.

### The corpus manifest respects `.gitignore`

Contributed by @vbanas (vsov/okfy#9).

- **A gitignored file no longer enters `meta/corpus-manifest.json`.** A corpus whose root
  holds `.git` is listed with `git ls-files --cached --others --exclude-standard`, the
  listing the plan (`segment._walk`) already read, so a private file or a build directory
  stays out of the manifest, `okfy diff` and `manifest_digest`. Hidden paths and embedded
  bundles are dropped as before.
- **Same gate as the plan (added when integrating).** A corpus that is a subdirectory of a
  repo, or has no git, is walked as before, gitignored files included, because the plan
  walks it that way too; a corpus inside an ignored directory is therefore not listed empty.
  A non-UTF-8 file name is a key, not a crash.
- **One git listing for the plan and the manifest.** Both read `segment.git_listing`, so
  they cannot disagree on which corpora are git corpora. `okfy survey` gains a `listing`
  field (`git` or `walk`) naming the rule it used. The survey no longer raises on a file
  name that is not UTF-8: such a name cannot be cited from a concept's frontmatter, so it
  is reported under `skipped.undecodable` (escaped) instead of planned. The README and the
  User Guide said every git corpus is walked with `git ls-files`; they now say a
  subdirectory of a repository is walked directly.
- **One-time change for existing bundles.** On a repo-root corpus with gitignored files, the
  next `okfy diff` reports those files as removed and the next snapshot refresh drops them,
  so `manifest_digest` changes once. On macOS, a name stored decomposed (NFD) is now keyed
  as git reports it (precomposed), as the plan already keyed it. The retrieval and
  federation code digests are unchanged.

## v0.29.0 — 2026-09-30

An audit of v0.28 found two gaps in the shipped tool and four in a local experiment
harness. This release closes the two in the tool. Nothing changes how `okfy query`
ranks: the retrieval digest is unchanged.

### Stricter behaviour — read this before upgrading

- **A hand-edited navigation file now reads stale.** `okfy validate` and
  `okfy_overview` used to call the navigation view fresh whenever a re-render of the
  concepts matched the recorded `navigation_fingerprint`; a hand edit of the file a
  reader is actually served (`index.md`, or an `index/<dir>.md` shard) was invisible.
  Freshness now compares three objects and is `fresh` only when all agree: the
  recorded fingerprint, the live re-render, and the generated files on disk, hashed by
  the same rule that hashed what `okfy package` wrote. An edited, deleted or added
  shard, a shard that is not valid UTF-8 or not a regular file, and a hand-edited
  flat `index.md` are all `stale` (`W_STALE_NAVIGATION`, `E_STALE_NAVIGATION` under
  `--strict-package`), with a reason that says the files were edited after packaging.
  The way out is `okfy package` again, which rewrites them and keeps the index mode.
  A bundle that validated clean before can therefore warn now if someone had edited a
  generated file by hand.
- **Workspace `release-check` re-derives the federated eval it certifies.** The
  bundle gate has re-derived every recorded answer since v0.21; the workspace gate
  only checked the record's fingerprint and owner verdicts, so a hand-edited federated
  record passed. It now replays the latest run of each suite with the real federated
  query and compares exactly: `query_options`, the queries against the workspace's own
  suite, `expanded_query` per member, `top_hits` (id, member, role, score, `via`),
  `notes`, and for the adversarial suite `expect`, `concept`, `why`, `outcome` and
  `outcome_detail`. A difference is `E_REL_WS_EVAL_REPLAY` or
  `E_REL_WS_ADVERSARIAL_REPLAY`; a replay that runs out of its time budget stops
  before the next query and reports `E_REL_WS_REPLAY_INCOMPLETE` with the number left
  uncompared. Owner and LLM verdicts are judgements and are not compared. The replay
  runs only when the record's retrieval schema and fingerprint already match, so
  records from an earlier era are unchanged: they stay reported stale, not replayed.
  A latest run that is not a valid eval record is `E_REL_WS_EVAL_INVALID`, reported
  before anything walks it.
- **A workspace acceptance bar outside `1..N` now fails `release-check`.** A workspace
  whose `acceptance.min_owner_pass` or `min_adversarial_pass` was `0` or negative
  (or above the number of that suite's queries) passed the gate even when every
  owner verdict was `fail`: the gate only checked that the value was an integer,
  while a bundle's `validate` already refused it as `E_ACCEPTANCE_RANGE`. The workspace
  gate now reports `E_REL_WS_ACCEPTANCE_INVALID` for a bar the owner set outside
  `1..N`, `N` being that suite's own query count. The default bar is not
  range-checked, as on the bundle side. The way out is a bar inside the range.

### MCP and validate

- `okfy_overview`'s `stale` flag is computed from the very bytes the response returns
  (the file is read once), so the flag and the payload cannot describe different reads.
  Its shape is unchanged: `stale: true` with `stale_reason` and `way_out`,
  `stale: null` with `stale_reason` when freshness cannot be told, no key when fresh.
  `stale: null` now also covers a generated file that could not be read.
- `okfy.package.navigation_state(bundle, served=None)` takes an optional map of
  bundle-relative path to the bytes a caller is about to return.
- Generated navigation is written as LF bytes on every platform, so the on-disk hash
  is the same wherever a bundle was packaged.

### Codes

- **Added:** `E_REL_WS_EVAL_REPLAY`, `E_REL_WS_ADVERSARIAL_REPLAY`,
  `E_REL_WS_REPLAY_INCOMPLETE`.
- **Widened, same names:** `E_STALE_NAVIGATION` and `W_STALE_NAVIGATION` also mean a
  generated file was edited, deleted or added after packaging;
  `E_REL_WS_EVAL_INVALID` also covers a malformed latest run;
  `E_REL_WS_ACCEPTANCE_INVALID` also covers a bar outside `1..N`.

### Not claimed

- No claim that category descriptions help an agent. The paired experiment is still
  unrun: it waits for the owner's approved tasks, answer keys and signed thresholds.
- `README.md`, `AGENTS.md` and `protocols/` are generated too but are outside the
  navigation fingerprint; a hand edit of them is not reported by this check.
- The experiment harness is local development tooling and not part of the shipped tool;
  its own fixes are not product behaviour and are not listed here.
- Nothing here measures extraction accuracy.

## v0.28.0 — 2026-09-29

Makes the existing `overview → shard → concept` navigation usable by the two real
consumers (a file-reading agent and an MCP agent), lets an owner say what each
directory is for, keeps the chosen index mode across updates, and closes two
federation identity defects. Nothing changes how `okfy query` ranks: the
retrieval digest is unchanged, and a category edit moves neither the retrieval
fingerprint nor any query output. A paired agent experiment is prepared for the
owner and has not been run; this release makes no claim that descriptions help
an agent.

### Stricter behaviour — read this before upgrading

- **`okfy package` now keeps the recorded index mode.** A plain `okfy package`
  on a bundle whose `meta/package.json` says `index: sharded` stays sharded; it
  used to silently drop the shards, which is what the update workflow's plain
  package step did. Scripts that relied on a plain package meaning flat must
  pass the new `--flat-index`. `--shard-index` together with `--flat-index` is
  refused with `E_PACKAGE_MODE_CONFLICT`, before anything is written. The
  command prints the resulting `index` mode and where it came from.
- **`okfy_overview` no longer returns a bare `truncated` flag with no way on.**
  The marker is gone; a long file is served in exact character pages with
  `next_offset`. `max_chars < 1` and a negative `offset` are refused
  (`E_OVERVIEW_PAGE`) instead of clamped.
- **Workspace retrieval fingerprints changed shape (`okfy-ws-retrieval@2`).** They
  now fold the workspace `project_key` and each personal concept's `applies_to`,
  because both change what federated search returns. Every federated eval run
  recorded before this release is therefore reported stale by `release-check`; it
  is not rewritten, and it does not become current by re-tagging.
- **The persisted federated eval record keeps what was answered.** Stored
  `top_hits` used to be cut to the first 2n knowledge and constraint hits and
  dropped personal and pulled hits; up to 3n hits plus every pulled constraint
  are now kept, so a run graded on a pulled constraint can be replayed.
- **`package()` returns the index mode** it used, for callers that inspect the
  result.
- **Generated navigation cannot gain structure from owner text.** Every value the
  renderer puts on one generated line (concept titles and descriptions, type and
  purpose-title headings, directory names, category descriptions, the README and
  `AGENTS.md` title) has control characters — newlines, tabs, U+2028/9 — replaced
  by a space, and a category description cannot open a markdown link (`](`
  becomes `] (`). None of the 8 real bundles measured has such a character, so
  their generated bytes do not change.

### New

- **`okfy_overview(shard="<dir>", offset=<n>)`** reads a generated shard listed
  in the resident index and pages any overview. The response carries `sha256` of
  the bytes read once, `total_chars`, `truncated` and `next_offset`. A shard name
  must be a link target of the resident index and resolve to a regular file
  inside `index/` that carries the generated marker; workspaces, flat bundles,
  symlinks, traversal and typed listings are refused with `E_OVERVIEW_SHARD_*`.
  `okfy_show` on a shard id refuses with `E_SHOW_IS_SHARD` and names the reader.
  No new tool: the tool-count pin is unchanged.
- **Navigation freshness.** `meta/package.json` records `navigation_fingerprint`,
  a hash of the rendered view's bytes, so any input the renderer reads (categories,
  concept titles and descriptions, the demote set, mode, title) moves it. Validation
  reports `E_STALE_NAVIGATION` (strict) or `W_STALE_NAVIGATION`; a manifest written
  before this release is `W_NAVIGATION_UNVERIFIABLE`, never an error. `okfy_overview`
  carries a `stale` key (null when it cannot tell).
- **Optional plan `categories`.** One line per top-level directory, copied into the
  resident index of a sharded bundle after the concept count, never generated.
  `W_CATEGORY_SHAPE`, `W_CATEGORY_UNKNOWN_DIR`, `W_CATEGORY_DANGLING_ID` and
  `W_CATEGORY_MULTILINE` (control character, or over 200 characters) are
  warnings at every strictness level. `okfy package --flat-index` is the tested way
  back: shards removed, every concept byte-identical, query output identical.
- **Codes added:** `E_OVERVIEW_SHARD_UNKNOWN`, `_PATH`, `_MISSING`, `_NOT_GENERATED`,
  `_FLAT`, `_WORKSPACE`, `_TYPE`, `E_OVERVIEW_PAGE`, `E_SHOW_IS_SHARD`,
  `E_PACKAGE_MODE_CONFLICT`, `E_STALE_NAVIGATION`, `W_STALE_NAVIGATION`,
  `W_NAVIGATION_UNVERIFIABLE` and the four `W_CATEGORY_*` above.

### Not done, on purpose

- No router, no summaries service, no embeddings, no new dependency. A rule fixed
  before measuring found no retrieval defect on the one corpus examined, and a
  second rule found no case where segment cuts separate a rule from its exception;
  `segment.py` is unchanged.
- The paired experiment that would show whether category descriptions lower an
  agent's reading cost at non-inferior quality is prepared and waits for the
  owner's approved tasks, answer keys and signed thresholds. Until then the
  harness prints only "exploratory — awaiting owner".

## v0.27.0 — 2026-09-22

An external audit of v0.26 reproduced ten defects (A1–A10). Each one is a case
where a v0.26.0 fix closed the exact counterexample the earlier audit had
presented but left a neighbouring case open — an ordering it didn't check, a
grammar half it didn't share, a second read path it didn't wrap. This release
closes all ten, together with a repair to the agent memory pilot's evidence
and a new extraction rule for claims phrased as absolute.

### Stricter behaviour — read this before upgrading

Several commands now refuse input they previously accepted silently. Any of
these can break an existing script or workflow that relied on the old,
looser behaviour.

- **`okfy snapshot` refuses while a rejected update is unresolved (A3).**
  Following the documented update procedure — reject a proposed
  interpretation of a corpus change, then run ordinary `okfy snapshot` —
  used to advance the baseline and erase the diff signal, even though
  rejecting the *proposed text* is not a decision that the underlying
  *source change* needs no action. Snapshot now refuses with
  `E_UPDATE_REJECTION_UNRESOLVED` whenever a still-affected concept's latest
  memory event is a bare `reject`. Resolve it with a fresh proposal that
  gets accepted, or with the new `okfy dismiss <bundle> <concept-id>
  --reason "..."` verb, which records that the source change itself needs
  no action (and writes its own `dismiss` memory event) without reopening
  the rejected text.
- **A staged-but-uncommitted ledger rewrite can no longer ride into a
  sanctioned commit (A5).** The review mutators (`accept`, `reject`,
  `dismiss`, `refine`, `ledger add`) already refused an uncommitted rewrite
  of the append-only ledger, but `git commit` with no pathspec commits the
  *whole* index — a rewrite something else had already staged before the
  mutator ran still shipped inside that mutator's own commit and then read
  as intact afterwards. The rewrite check (`E_LEDGER_REWRITTEN`) now also
  looks at the git index, not just the committed and working copies, and is
  re-run immediately before the commit itself, not only at the mutator's
  entry.
- **More MCP refusals return the documented error envelope, this time by
  construction (A9).** `okfy_show` called with more than 10 `concept_ids`
  now refuses with `E_SHOW_TOO_MANY_IDS` in the normal `{error, message,
  way_out}` shape. More generally, any refusal a tool handler raises as a
  plain `ValueError` or `KeyError` — not only the ones a previous audit
  round happened to name — is now caught at the adapter's one dispatch
  boundary and wrapped as `E_MCP_REFUSAL`, with the original message
  preserved, instead of escaping uncoded.
- **A malformed `applies_to` entry is excluded from scope for its grammar,
  not only its shape (A7).** `okfy validate` already rejected an
  `applies_to` entry that wasn't a legal project key (wrong case, an
  embedded space, and so on), but federated search and export only checked
  that entries were non-blank strings, so a concept `validate` called
  malformed could still be a live search hit and still ship in an export.
  Both now run the same complete shape-and-grammar predicate.
- **`ledger add` refuses a correction that can't be bound to one specific
  loss (A8).** A `corrects` block used to be credited to any ledger row
  matching its bare `run_id`/`segment`, even one that never declared the
  named output, or more than one row that did — crediting the wrong loss,
  or an ambiguous one. A correction is now accepted only when it binds to a
  unique earlier row that actually declared the named output; zero or more
  than one candidate is refused rather than guessed at. `okfy validate`
  applies the same binding when it reads the ledger, and also re-runs the
  writer's shape check and requires a positive dropped count, so a
  hand-appended `{irrelevant: 0}` no longer clears `W_DROPS_UNEXPLAINED`.

### Fixed

- **A stale anchor's flag could still disappear before repair, in two more
  cases (A1).** A pin already flagged `anchor_stale` lost that flag if its
  destination drifted again before the flag was acted on, or if the cited
  source file was deleted outright — the carry-forward logic only revisited
  pins the current corpus scan could still find. Carry-forward is now a
  first pass over the OLD pins and the CURRENT live citations, decided
  before anything about a fresh corpus scan runs, so a stale pin survives
  until `okfy reanchor` actually repairs it; a location whose text changed
  again in the meantime now also carries `needs_reextract`, so a caller
  knows re-extraction, not just re-pinning, is needed.
- **A snapshot's several reads of the corpus could span more than one
  commit (A2).** Reading "the corpus's current revision" was resolved
  independently, several git calls apart, across one snapshot operation. A
  commit landing in the middle could stamp pins read from the OLD content
  with the NEW commit's SHA, and the next diff would then compare
  new-against-new and report nothing pending — the intervening commit
  became invisible. A snapshot now resolves the revision exactly once and
  threads it through every git-facing call in the operation.
- **`okfy reanchor` could repair a citation to the wrong text (A1, in
  repair rather than in snapshot).** A move recorded by an earlier diff
  was trusted indefinitely: repair rewrote the citation to the recorded
  destination as long as the *old* text still matched, with no check that
  the destination's *own* text hadn't changed since. Before rewriting,
  repair now re-hashes the originally pinned digest against the destination
  at a captured revision, and skips with a reason instead of certifying a
  location whose text has moved on.
- **`transcript-lint` no longer invents a search from a quoted shell
  separator (A4).** A command like `printf '%s\n' ';' okfy query <bundle>
  alpha` only prints its arguments, but a naive dequoting pass could mistake
  the printed `';'` for a real separator and read the printed words as a
  second, real command. The command reader now runs a second, non-POSIX
  pass in parallel and only treats a separator-shaped token as a real split
  point when both passes agree it arrived bare; a command whose split is
  ambiguous is now reported as unknown rather than guessed at.
- **MCP multi-show works again on a valid CRLF concept file (A6).** The
  read-once-for-both-digest-and-body fix from the previous release dropped
  the newline translation the old two-read path got for free, so a CRLF
  file's frontmatter no longer parsed. The digest is still computed over
  the untouched raw bytes; only the returned text is now normalized for
  parsing.
- **A memory line with an unusable timestamp is reported, not silently
  dropped (A10).** A `meta/memory.jsonl` row with an empty or unusable `at`
  field parsed as valid JSON and reached the time-window comparison, where
  it matched no window and vanished from both the result list and the
  problem list. Such a row is now reported as an `E_MEMORY_LINE` problem,
  the same as a row with a missing key or an unknown event, instead of
  disappearing.

### Extraction

`extract-worker` and `glean-worker` are now `@3`. A claim phrased as
must / must not / always / never / maximum / minimum / only / required is a
claim about what the code enforces, not about what a sentence in the corpus
says — a worker must now back one with either a traced caller path
(file:line, from the entry point a user actually calls down to the
enforcing code) or a minimal executed example, and record which one it
used as evidence. When the documentation and the code disagree and the
worker can't resolve it, the draft records both sides under a `## Doc-vs-code
conflict` heading instead of stating either side as settled fact. The
consolidation stage is instructed to file each such heading through the
existing review lane as a `contradicts-source` flag, so an owner rules on
it rather than a concept quietly shipping one side as a contract. These are
instructions to the extracting agent, not a check the core enforces: nothing
in `okfy validate` verifies that a traced path was actually followed.

### Agent memory pilot: comparative conclusion withdrawn

The v0.26.0 agent pilot's comparative conclusion — that the compiled bundle
finished behind both a plain source read and a hand-written guidance file —
is withdrawn. The 72-run experiment did not execute under its own
registered conditions: every run inherited this project's live instructions
and an accumulating memory file that changed mid-experiment rather than the
promised memory-free fresh context, the registered per-run token ceiling
was never stated to any of the 72 runs, and each arm's instructions were
read from a file rather than installed as the system prompt. The published
usage comparison also double-counted streamed usage blocks sharing one
message id; corrected, the compiled bundle's median cost is *lower* than
the source-only arm's, not 1.04× it. Two of the original task adjudications
do not survive an independent re-read against the pinned source. None of
this reinstates the bundle's original favorable framing either — no
comparative advantage, in either direction, is established by this run.

**What survives:** on one task, the compiled bundle served an incorrect
rule with a named citation and no hedge, on a question the pinned source
already settled the opposite way at the bundle's own extraction revision —
this bundle can hand an agent a confident wrong answer, with a citation.
The rule was not outdated by a later code change: the documentation said
one thing and the code did another, and extraction recorded the
documentation's side as fact. A properly isolated, budget-enforced rerun is needed before any
comparative claim about the bundle's value can be made.

## v0.26.0 — 2026-09-21

An external audit of v0.25 reproduced sixteen defects (F01–F15, with F12
splitting into F12a and F12b) and revisited four findings from an earlier
audit round (R1–R4). This release closes all sixteen and all four.

### Stricter behaviour — read this before upgrading

Two commands now refuse input they previously accepted silently. Either can
break an existing script or workflow that relied on the old, looser
behaviour.

- **`okfy snapshot` refuses a dirty corpus in git mode (F03).** In git mode,
  snapshot used to list changed files between two commits with `git diff`,
  then read the *cited bytes* from the corpus's working tree, then stamp
  those bytes with the newer commit's SHA — three steps spanning two
  different revisions. A committed edit followed by an uncommitted revert
  read as unchanged, and the snapshot recorded a SHA next to content that
  SHA never actually described. Snapshot now reads committed bytes only, and
  if the corpus has uncommitted changes it refuses with `E_CORPUS_DIRTY`,
  naming up to ten dirty paths. Pass `--force` to snapshot the last commit
  anyway and ignore the uncommitted changes.

- **`okfy eval metrics`'s `--k` now raises instead of returning nothing
  useful (F15).** `--k=-5` (or `--k=0`, or a non-integer) used to exit 0 and
  silently compute a meaningless or negative recall-at-k baseline. It's
  refused now, both at the CLI (`--k` must be a comma-separated list of
  positive integers) and in the underlying `eval_metrics(..., ks=...)`
  library call, so a library caller gets the same refusal a CLI user does.
  A boolean is rejected too, even though Python treats `bool` as a subclass
  of `int` — `ks=(True,)` is almost always an accident, not a deliberate
  `k=1`.

  (Note: this finding's fix lives in `okfy eval metrics`'s `--k` flag, not
  in `okfy eval compare` — `eval compare` takes no `k`/`ks` argument at
  all.)

### Sharing and export

- **Exporting a bundle no longer ships content it should have excluded
  (F01).** When a member bundle was nested under `mem/` inside an export,
  every rule that only checked root-level paths silently stopped firing.
  An unaccepted proposal scoped to a different project could ride along as
  ordinary, searchable knowledge, and the personal-scope report undercounted
  what actually shipped. Export now works from an allowlist — each member
  contributes only what it's actually supposed to (its real concepts, plus
  its `meta/`) — so nothing else has a way to ride along.
- **A malformed personal-scope value is now excluded consistently (F10).**
  `okfy validate` already rejected a concept whose `applies_to` contained a
  malformed entry (e.g. `['*', 123]`). Federated search and export used to
  filter out just the bad entry and match on what was left, so the same
  concept that `validate` calls an error was still a live search hit and
  still shipped in exports through a personal workspace member. Both
  readers now agree: malformed means out of scope, everywhere.

### Anchors, ledger, and citations

- **A repaired anchor's flag no longer disappears on its own (F02).** A
  moved citation anchor used to keep its "stale" flag only while the most
  recent diff still reported the move; the next unrelated snapshot could
  drop the flag and quietly take a fresh pin from whatever text had drifted
  into the old location, erasing the record that the anchor was ever wrong.
  The flag now persists across any number of snapshots until `okfy reanchor`
  actually repairs the citation.
- **Ledger integrity is now checked on write, not just on read (F05).**
  `okfy validate` already detected an uncommitted rewrite of the (supposedly
  append-only) ledger. `okfy propose` did not run that same check — it only
  confirmed the ledger still parsed, which a surgically-edited rewrite does
  perfectly. `propose` (and `propose_batch`) now run the same check `accept`
  and `reject` do, and hold the same write lock, before writing.
- **Ledger rows can now record a correction (F09).** There was previously no
  legal way to explain, after the fact, why a run's declared output never
  showed up — the ledger is append-only, so editing the original row was
  refused, but the warning telling you to explain it demanded exactly that
  edit. A new row can now carry a `corrects` block naming an earlier row and
  one of its outputs, plus an explanation; it's rejected up front if it
  names a row or output that doesn't actually exist.
- **Citations now distinguish "verified" from "merely well-formed" (F11).**
  A citation that resolved to a missing file, line 0, a reversed range, or a
  range past end-of-file used to be reported the same as one that actually
  checks out — anything that merely parsed as a citation was treated as
  valid. Citations are now `verified` (checked against real corpus bytes) or
  `unverifiable` (no bytes to check against), and `transcript-lint`, `eval
  compare`, and `eval qrels` all show the new state instead of hiding it.
- **`okfy changes` no longer reports a broken ledger as "nothing happened"
  (F12b).** A ledger containing nothing but invalid JSON used to exit 0 with
  a count of 0 — indistinguishable from a bundle where nothing has ever
  happened. It now reports the rows it could read and names the lines it
  couldn't, and exits non-zero whenever there are unreadable lines.
- **A malformed source-pins file no longer crashes (F13).** A pins document
  shaped as `{"pins": 1}` used to raise `TypeError` instead of degrading the
  way its own documented contract promised. It now checks the shape it
  actually needs before reading it.

### Date handling

- **Event date-window filters now match padded and unpadded dates the same
  way (F12a).** A window bound like `2026-9-01` used to be validated but
  then returned unnormalized, so it sorted differently from `2026-09-01`
  under the tool's (lexicographic) comparison and could silently miss
  events recorded on that exact date. Both spellings, and both `T2:00:00Z` /
  `T02:00:00Z`, now select the same events.

### The `/okfy:update` procedure

- **The update procedure, the write policy, and `okfy snapshot`'s timing now
  agree (F04).** The update instructions used to tell an agent to rewrite
  unprotected concepts directly, while the bundle's own write-policy hook
  refused any staged concept file outside `proposals/`, and `okfy snapshot`
  could run — and advance the baseline — before the commit meant to make a
  change official, even if that commit later failed. The update procedure
  now routes every concept change through `proposals/`, commits before it
  snapshots, and `okfy snapshot` gained two more refusals: `E_BUNDLE_DIRTY`
  (the bundle's own git tree is uncommitted) and
  `E_UPDATE_PROPOSALS_PENDING` (this update's own proposals are still
  open).

### MCP adapter

- **`okfy_show` no longer returns a body and a digest describing different
  edits of the same file (F06).** It used to read a concept twice — once to
  resolve and parse it, once more to compute the digest — so an edit landing
  between those two reads could hand back the old text under the new file's
  digest, and a follow-up freshness check would then wrongly report nothing
  had changed. It now reads each concept's bytes exactly once and derives
  both the body and the digest from that one read.
- **A query's reported hit count now matches what was actually shown
  (F14).** The session record and the usage journal used to count a query's
  surfaced results differently — the journal missed the extra concept a
  federated result can auto-pull in, so a query that surfaced two concepts
  to the agent could log one in the journal. Both now share one count.
- **More MCP refusals return the documented `{error, message, way_out}`
  shape (R2).** Two refusals the audit reported were bare and uncoded;
  fixing them prompted a sweep of six neighbouring bare guards, all of which
  turned out to be reachable in practice. All eight now return a coded
  refusal.

### Auditability of agent activity

- **`transcript-lint` no longer mistakes text that merely mentions an okfy
  command for the command itself (F07).** It used to join a shell segment's
  words back into a string and pattern-match it, so `printf '%s\n' 'okfy
  query ...'` counted as a real search — the command being run is `printf`,
  not `okfy`. It now classifies by command position instead, and a shell
  segment it can't parse is reported as unknown rather than silently
  skipped or silently counted either way.
- **`okfy eval compare` no longer reports "no differences" when the thing
  being compared changed (F08).** It used to rank both compared runs using
  only one run's declared target, so changing the target between two runs
  could report identical rankings for what were, in fact, two different
  questions. Each run's own target, rank, and outcome are now reported
  separately, with an explicit note when the comparison isn't like-for-like.

### Documentation and CI

- **`docs/OKF-COMPATIBILITY.md` no longer claims stricter compliance than
  OKFy actually has (R1).** The Trust Signals row for OKF v0.2 §5.2 claimed
  a stricter match; OKFy's `generated` field is set once, at proposal time,
  and is never updated on later edits, which is a real deviation from the
  spec's definition, not a stricter version of it. The doc now states the
  deviation and points readers at `verified[-1].proposed_by` for "who last
  changed the verified text" instead.
- **CI's lockfile checks now actually fail when a lockfile is missing
  (R3).** Each check was guarded by "run this only if the file exists,"
  so deleting a lockfile passed CI instead of failing it. All three
  declared lockfiles are now required to exist before the check runs.
- **A documentation guard can no longer be satisfied by a hidden, correct
  example standing in for a broken, visible one (R4).** A test that checks
  every doc's `okfy propose` example for a required flag used to scan raw
  markdown text, so a complete example sitting inside an HTML comment could
  satisfy the check on behalf of a visibly broken, uncommented example right
  next to it. HTML comments are now stripped before the check runs.

### Agent memory pilot: no change shipped from it

A preregistered, 72-run pilot compared three memory conditions on the same
24 tasks: no memory, a hand-written `AGENTS.md`, and the compiled OKFy
bundle. The bundle-backed agent finished **last**: 19 passes / 2 fails,
against 22/0 for the hand-written file and 20/3 for no memory at all. Of
seven preregistered claims, four were refuted and only two were confirmed
(one was inconclusive). The bundle also produced the pilot's only harmful
stale answer — on the one task built in advance to test whether a
compiled-but-outdated bundle would be cited with unwarranted confidence, it
was. Nothing in this release changed as a result of this pilot; it is
reported here as a negative result, not acted on.

**2026-09-22 note:** the comparative framing above ("finished last", the
count of refuted/confirmed claims) does not hold as originally stated — the
experiment ran nonconforming to its own registered conditions and two of
its task adjudications do not survive independent re-reading. See the
v0.27.0 section above. The harmful answer on the one task built to test
it stands, but not as described below: the bundle's rule was not made stale
by later drift — it was already wrong when the bundle was extracted.

### Also measured this release

Running `okfy release-check` end-to-end against a real bundle (on a scratch
copy, never the bundle itself) confirmed that machine-checkable work and
owner sign-off are genuinely separate gates: closing every machine-checkable
predicate does not move a bundle closer to release, because
`release-check`'s fingerprint includes the newly-required adversarial query
set, which invalidates any owner verdicts recorded before that work existed.
A bundle cannot reach release without a human acting after the last machine
change.

**2026-09-22 note:** a follow-up audit found several statements above did not
hold as broadly as stated. Each has since been corrected in source; this note
records which v0.26.0 claim was wrong and does not rewrite the text above.

- "A repaired anchor's flag no longer disappears on its own (F02)" — false in
  two more cases: a stale pin whose destination changed again before repair,
  and a stale pin whose cited source file was deleted (finding A1).
- "Ledger integrity is now checked on write, not just on read (F05)" — a
  ledger rewrite already staged by something else before a sanctioned mutator
  ran could still ship in that mutator's own commit (finding A5).
- "Both readers now agree: malformed means out of scope, everywhere (F10)" —
  true for shape only; the grammar half of `applies_to` validity was not
  shared between the validator and the federation reader (finding A7).
- "All eight now return a coded refusal (R2)" — four more uncoded refusals
  reachable through registered MCP tools were found afterward (finding A9).
