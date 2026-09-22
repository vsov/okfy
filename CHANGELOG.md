# Changelog

This changelog starts at v0.26.0. Earlier releases are not back-filled here —
their history is not something this file can verify.

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
