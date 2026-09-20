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

For each concept in `affected` (batch ~8 concepts per subagent), split by two
fields `okfy diff` now reports alongside `affected`:

- **`protected[id]` is true**: the concept has a non-empty `verified` list, or
  an accepted proposal targeted it (`meta/memory.jsonl`, an `accept` event).
  NEVER rewrite it in place — doing so silently overwrites text an owner
  signed off on. Instead file the update through the normal proposal path, so
  `base_sha256` guards the owner's current text against a lost update:

  ```
  okfy propose <bundle> --as worker/<run-id> --target <id> --action update \
    --evidence external-source=<old_sha>..<new_sha> --from <updated-file.md>
  ```

  Use `okfy diff --json`'s `diff.old`/`diff.new` (git mode) for `<old_sha>` /
  `<new_sha>`; in manifest mode, name the corpus paths that changed instead.
  Leave the proposal for `/okfy:review` — do not accept your own proposal.
- **`protected[id]` is false**: rewrite in place as before — read the
  concept's CURRENT file, its `sources:` files in the corpus (at their new
  state), and the extraction plan; rewrite it preserving id, type, and link
  structure, updating substance that changed. Same rules as extraction
  workers: standalone content, canonical language, Russian aliases where the
  archetype requires them, real numbers from sources.

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
/okfy:extract stage 4, writing drafts to `drafts/update-NN/`; consolidate the
drafts into final concepts (dedup against EXISTING concepts by normalized
title + type, merge into them rather than duplicating); delete the drafts.

## 4. Retire stale candidates

For each id in `stale_candidates` (all sources gone from the corpus): show the
list to the user and ask for confirmation ONCE for the whole batch. On yes:
delete the concept files; scan remaining concepts for links pointing at the
deleted ids and remove or reroute those references. On no (keep for reference):
run `okfy stale <bundle> <id> --reason "sources removed from corpus"` — never
hand-edit the frontmatter, and retrieval marks the hit (it does not down-rank).

## 5. Validate + package + snapshot

1. `okfy repair-links <bundle>` (fix link drift introduced by the update).
2. `okfy index <bundle>` then `okfy validate <bundle>` — errors to zero,
   fixing in place as in /okfy:extract stage 6.
3. `okfy package <bundle>` and `okfy index <bundle>` again.
4. `okfy snapshot <bundle>` — pin the new corpus state (LAST, only after
   everything above succeeded; the diff must stay reproducible on failure).
   This also refreshes `meta/source-pins.json`, the baseline the NEXT
   `okfy diff`'s `spans`/`reextract` measure against.
5. `okfy log <bundle> "update: <N> refreshed, <M> new, <K> retired"`.
6. Commit. IMPORTANT: use `git -C <bundle> add .` (NOT `add -A`) — in embed
   bundles the repo is the corpus repo and `-A` would stage unrelated user
   changes. Commit message: `update: <N> refreshed, <M> new, <K> retired`.

## 6. Report

Tell the user: refreshed/new/retired counts, validation state, and whether
any `ambiguous`/`unresolved` links from repair-links need a human eye.
