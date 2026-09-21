# Changelog

This changelog starts at v0.26.0. Earlier releases are not back-filled here —
their history is not something this file can verify.

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

### Also measured this release

Running `okfy release-check` end-to-end against a real bundle (on a scratch
copy, never the bundle itself) confirmed that machine-checkable work and
owner sign-off are genuinely separate gates: closing every machine-checkable
predicate does not move a bundle closer to release, because
`release-check`'s fingerprint includes the newly-required adversarial query
set, which invalidates any owner verdicts recorded before that work existed.
A bundle cannot reach release without a human acting after the last machine
change.
