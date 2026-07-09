---
description: "Purpose Interview → Survey → Schema design → Extraction Plan (stops for approval)"
argument-hint: "<corpus-path> [bundle-path]"
---
You are running OKFy stage 1-3 (of 6). Corpus: $1. Bundle path: $2 (if empty,
ask the user; suggest a sibling directory `<corpus-name>-okf`). The `okfy` CLI
must be installed (check `okfy --help`; if missing, tell the user:
`cd <okfy-repo>/core && uv tool install .`).

## 1. Purpose Interview

Interview the user, one question at a time, in their language:

1. Who/what consumes this bundle, doing what task? (→ purpose title + statement)
2. What decisions or outputs should the consuming model produce?
3. **Exactly 10 test queries** — real questions the bundle must answer.
   Push until you have 10 concrete ones; refuse vague entries ("stuff about X").
4. Canonical language (default: en — models read/write it best; user may override).
5. Write policy (default: proposals).
6. A short lexicon pass: ask for the user's habitual terms for the domain's
   central things (5-10 items max, only where their phrasing may diverge from
   the corpus vocabulary).

## 2. Init + Survey

- Run: `okfy init <bundle-path> --corpus <corpus-path> --language <lang>`
  (the CLI refuses paths inside the OKFy tool repo — bundles are private).
- Run: `okfy survey <corpus-path>` and study the JSON: file count, extensions,
  token estimate, samples. Read 3-5 representative files yourself for feel.

## 3. Schema design

- Pick the closest archetype: **decision-support** (knowledge that backs a
  human's decisions), **codebase-map** (navigate + safely change a codebase),
  or **api-reference** (correctly USE an API — the caller's mirror of
  codebase-map: Operations, Types, Recipes, Contracts, Topics). If none fits,
  say so and stop — don't force it.
- For a git CODE corpus, offer `--embed`: the bundle lives at `.okf/` inside
  the corpus repo, rides its PRs, write_policy defaults to `direct`
  (`okfy init --corpus <corpus> --embed`). Warn the user this writes into
  their working tree and get explicit consent before running it.
- Adapt it: propose concept types (start from canonical_types, add/drop with
  reasons), category layout, granularity (what merits its own concept?),
  glossary strategy (which terms, alias rules incl. cross-language),
  segmentation rules (include/exclude globs, budget — default 50k tokens).

## 4. Write the plan and stop

- Fill `meta/purpose.md`: title, statement body, language, write_policy,
  test_queries (all 10).
- Write `meta/lexicon.md` (`type: Lexicon`) from the lexicon pass.
- Write `meta/extraction-plan.md` (`type: ExtractionPlan`) with frontmatter:
  `archetype`, `archetype_version`, `types` (name → one-line extraction rule),
  `layout` (type → directory), `segmentation` (include/exclude/budget),
  `segments: []`; body: prose rationale — what the bundle will look like and why.
- Run `okfy validate <bundle> --no-archetype` — meta completeness must pass.
- Commit: `git -C <bundle> add . && git -C <bundle> commit -m "plan: purpose + extraction plan"`
- Present the plan to the user: types table, layout, segment count estimate,
  the 10 test queries. Say exactly: **"Plan approved? Run `/okfy:extract <bundle-path>`
  to execute stages 4-6."** Do NOT start extraction in this session.
