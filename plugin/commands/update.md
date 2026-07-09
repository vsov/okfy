---
description: "Incremental re-extraction: corpus snapshot diff → re-extract affected concepts, extract new files, retire stale ones"
argument-hint: "<bundle-path>"
---
Bundle: $1. This is the incremental counterpart of /okfy:extract — never a
full re-run. Read `meta/extraction-plan.md` and `meta/purpose.md` first.

## 1. Diff

Run `okfy diff <bundle>`. If `affected`, `uncovered_new`, and `stale_candidates`
are all empty: report "bundle is up to date with the corpus" and STOP.

Present the numbers to the user (N affected concepts, M new files, K stale
candidates) before doing work.

## 2. Re-extract affected concepts

For each concept in `affected` (batch ~8 concepts per subagent): the subagent
reads the concept's CURRENT file, its `sources:` files in the corpus (at their
new state), and the extraction plan; it rewrites the concept in place,
preserving id, type, and link structure, updating substance that changed.
Same rules as extraction workers: standalone content, canonical language,
Russian aliases where the archetype requires them, real numbers from sources.

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
5. `okfy log <bundle> "update: <N> refreshed, <M> new, <K> retired"`.
6. Commit. IMPORTANT: use `git -C <bundle> add .` (NOT `add -A`) — in embed
   bundles the repo is the corpus repo and `-A` would stage unrelated user
   changes. Commit message: `update: <N> refreshed, <M> new, <K> retired`.

## 6. Report

Tell the user: refreshed/new/retired counts, validation state, and whether
any `ambiguous`/`unresolved` links from repair-links need a human eye.
