---
description: "Incremental re-extraction: corpus snapshot diff → re-extract affected concepts, extract new files, retire stale ones"
argument-hint: "<bundle-path>"
---
Bundle: $1. This is the incremental counterpart of /okfy:extract — never a
full re-run. Read `meta/extraction-plan.md` and `meta/purpose.md` first.

## 1. Diff

Run `okfy diff <bundle> --json`. If `affected`, `uncovered_new`, and
`stale_candidates` are all empty: report "bundle is up to date with the corpus"
and STOP.

Present the numbers to the user (N affected concepts, M new files, K stale
candidates) before doing work. Note `protected` and `reextract` (v0.24) —
they change what you do in step 2, below.

## 2. Re-extract affected concepts

Read `meta/purpose.md`'s `write_policy` first — it governs EVERY write in
stages 2-4, not only protected ones. v0.25 audit F04: the bundle's generated
commit hook (`okfy package`) refuses ANY staged concept `.md` outside
`proposals/drafts/index/protocols` under `write_policy: proposals`,
regardless of `protected[id]` — that field never entered the hook's own
test. A direct rewrite of an "unprotected" concept under `proposals` policy
is exactly F04: it produces a commit the bundle's own hook then refuses, and
by the time you find out, the corpus baseline may already have moved on
(see step 5's snapshot-timing fix).

- **`write_policy: proposals`** (the default): for EVERY concept in
  `affected` (batch ~8 concepts per subagent), protected or not, file the
  re-extraction through the proposal path, so `base_sha256` guards the
  current text against a lost update:

  ```
  okfy propose <bundle> --as worker/<run-id> --target <id> --action update \
    --evidence external-source=<old_sha>..<new_sha> --from <updated-file.md>
  ```

  Use `okfy diff --json`'s `diff.old`/`diff.new` (git mode) for `<old_sha>` /
  `<new_sha>`; in manifest mode, name the corpus paths that changed instead.
  Build `<updated-file.md>` the same way a direct rewrite would: read the
  concept's CURRENT file, its `sources:` files in the corpus (at their new
  state), and the extraction plan; rewrite it preserving id, type, and link
  structure, updating substance that changed. Same rules as extraction
  workers: standalone content, canonical language, Russian aliases where the
  archetype requires them, real numbers from sources. Leave the proposal for
  `/okfy:review` — do not accept your own proposal. `protected[id]` still
  matters for review, even though it no longer changes how you FILE the
  change: a **true** entry means the concept has a non-empty `verified` list
  or an accepted proposal already targeted it — flag that for the owner,
  since the review is overwriting text someone signed off on, not merely
  re-extracting.
- **`write_policy: direct`**: rewrite in place — read the concept's CURRENT
  file, its `sources:` files in the corpus (at their new state), and the
  extraction plan; rewrite it preserving id, type, and link structure,
  updating substance that changed. Same rules as extraction workers:
  standalone content, canonical language, Russian aliases where the
  archetype requires them, real numbers from sources. `protected[id]` is
  informational only under this policy — the owner opted into direct edits,
  hook or no hook.

**`spans`/`reextract` (v0.24, report-only, present only once `okfy snapshot`
has written `meta/source-pins.json` at least once).** `okfy diff` also
classifies, per affected concept, whether each cited span's bytes are
`span_intact` (unchanged), `span_moved` (found verbatim elsewhere in the file —
a pure reflow, e.g. lines inserted above it), `span_changed` (the cited text
itself is gone), or `unpinned` (no anchor to check against). `reextract` is the
subset of `affected` worth spending a worker on: concepts with a
`span_changed`/`unpinned` source, or a source in a REMOVED file. A concept that
is `affected` only because its cited lines shifted (`span_moved`, or every
source `span_intact`) measurably did not change — skip re-extraction for it
(no `okfy` command needed; just do not queue it) unless `--strict-sources` or
similar makes you distrust the measurement. This is diagnosis, same as
`affected` — `okfy diff` never re-anchors an anchor itself, so nothing here is
authoritative until you look.

`affected` is diagnosis, not truth (ADR-0013). Split it at the review: a
concept whose substance no longer holds but is kept for reference gets an owner
staleness decision — `okfy stale <bundle> <id> --reason "..."` marks it "do not
trust as current" while keeping it visible in retrieval (never auto-flip it, and
never demote its score). Concepts you actually re-extract above are re-verified
instead — a fresh extraction supersedes staleness, so do not stale those.

## 3. Extract uncovered new files

If `uncovered_new` is non-empty: group the files into segments per the plan's
`segmentation` (budget/globs), then run extraction workers exactly as in
/okfy:extract stage 4, writing drafts to `drafts/update-NN/` (drafts/ is
always a direct write under EITHER policy — it is one of the hook's own
sanctioned prefixes, same as `proposals/`). Consolidate the drafts into final
concepts as in /okfy:extract stage 5 (dedup against EXISTING concepts by
normalized title + type, merge into them rather than duplicating):

- **`write_policy: proposals`**: a newly-consolidated final concept is the
  SAME kind of write section 2 just routed through proposals — the hook
  makes no distinction between "re-extracted" and "brand new". File each one:
  `okfy propose <bundle> --as worker/<run-id> --action create --from
  <concept-file.md>` (or `--action update --target <id>` when consolidation
  merges into an EXISTING concept rather than creating a new one). Leave for
  `/okfy:review`.
- **`write_policy: direct`**: write the final concept file(s) directly, as
  before.

Delete the drafts once consolidated, either way — `drafts/` never holds
final content, and this delete is itself always a direct write (still inside
the hook's sanctioned `drafts/` prefix, whatever the policy).

## 4. Retire stale candidates

For each id in `stale_candidates` (all sources gone from the corpus): show the
list to the user and ask for confirmation ONCE for the whole batch.

- On yes, **`write_policy: proposals`**: a delete is a final-concept
  mutation too — the hook does not treat "removing a concept" any
  differently from "rewriting" one. File `okfy propose <bundle> --as
  worker/<run-id> --target <id> --action delete --evidence
  external-source="sources removed from corpus"` for each id, and file any
  necessary link fixes in concepts that reference a retired id as their own
  `--action update` proposal. Leave for `/okfy:review` — the concept (and
  the dangling links) still exist until the owner accepts.
- On yes, **`write_policy: direct`**: delete the concept files directly;
  scan remaining concepts for links pointing at the deleted ids and remove
  or reroute those references.
- On no (keep for reference), either policy: run `okfy stale <bundle> <id>
  --reason "sources removed from corpus"` — never hand-edit the
  frontmatter. `okfy stale` commits its own change (`--no-verify`,
  ADR-0007) the moment it runs, so it is unaffected by write_policy or the
  hook either way; retrieval marks the hit (it does not down-rank).

## 5. Validate + package + snapshot

1. Link/anchor repair, mechanical text corrections that never touch concept
   CONTENT: `okfy repair-links --dry-run <bundle>` and `okfy reanchor
   --dry-run <bundle>` (report what would change).
   - **`write_policy: direct`**: re-run BOTH without `--dry-run` — they
     write the fixes directly, as before.
   - **`write_policy: proposals`**: `okfy repair-links`/`okfy reanchor`
     write concept files directly and do NOT self-commit (unlike `okfy
     stale`/`okfy refine`/`okfy review accept`) — so their output lands in
     the SAME commit step 5.5 below makes, and the hook refuses it exactly
     like any other direct concept edit. It is never given a command-based
     exemption: "this diff came from `okfy repair-links`" is not something a
     pre-commit hook can verify after the fact (phase-5.md's ruling). So
     under this policy, do NOT run them without `--dry-run`. If the dry run
     reports anything, file each fix as an ordinary update proposal instead
     — `okfy propose <bundle> --as worker/<run-id> --target <id> --action
     update --evidence agent-inference="link/anchor repair" --from
     <patched-file.md>`, building `<patched-file.md>` from the dry run's
     report — and leave it for `/okfy:review`, same as every other mutation
     in this stage.
2. `okfy index <bundle>` then `okfy validate <bundle>` — errors to zero,
   fixing in place as in /okfy:extract stage 6.
3. `okfy package <bundle>` and `okfy index <bundle>` again.
4. `okfy log <bundle> "update: <N> refreshed, <M> new, <K> retired"`.
5. Commit. IMPORTANT: use `git -C <bundle> add .` (NOT `add -A`) — in embed
   bundles the repo is the corpus repo and `-A` would stage unrelated user
   changes. Commit message: `update: <N> refreshed, <M> new, <K> retired`.
   Under `write_policy: proposals` this commit carries only what the hook
   already allows — `drafts/`, `proposals/`, the regenerated root docs, and
   (v0.25 audit F04) `meta/corpus.md` when its `git_sha`/`extracted_at` are
   the only lines that changed — so it is expected to succeed even though
   none of this update's actual concept mutations (steps 2-4 above) have
   landed yet; they are still pending review as proposals.
6. `okfy snapshot <bundle>` — pin the new corpus state, but ONLY AFTER the
   commit above succeeded (never before — a refused commit must not be
   allowed to advance the baseline out from under the diff it was supposed
   to still show; v0.25 audit F04). This also refreshes
   `meta/source-pins.json`, the baseline the NEXT `okfy diff`'s
   `spans`/`reextract` measure against.
   - **`write_policy: direct`**: run it now, right after the commit.
   - **`write_policy: proposals`**: run it only once nothing from THIS
     update is still pending review. If step 1-4 filed any proposals,
     `okfy snapshot` refuses (`E_UPDATE_PROPOSALS_PENDING`) — pending owner
     proposals are not a completed update. Tell the user: "N proposal(s)
     filed for this update; run `/okfy:review`, then re-run `okfy snapshot
     <bundle>` once every one of them is accepted or rejected." (If nothing
     was actually proposed — no affected concepts, no new files, nothing to
     retire — there is nothing pending and `okfy snapshot` proceeds
     immediately, same as under `direct`.) `okfy snapshot` also refuses
     (`E_BUNDLE_DIRTY`) if the bundle's own working tree is uncommitted at
     the time it runs — always run it right after a successful commit, not
     mid-edit.
   - Once `okfy snapshot` actually refreshes the pin, commit again — it
     changed meta/corpus.md/-manifest.json/source-pins.json, which are not
     part of the commit in step 5 above. The hook's corpus.md carve-out
     (v0.25 audit F04) allows exactly this follow-up commit under
     `proposals` policy too.

## 6. Report

Tell the user: refreshed/new/retired counts, validation state, whether any
`ambiguous`/`unresolved` links from repair-links/reanchor need a human eye,
and — under `write_policy: proposals` — how many proposals were filed for
this update and that `okfy snapshot` will refuse (`E_UPDATE_PROPOSALS_PENDING`)
until `/okfy:review` clears every one of them.
