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
   (The 10 **adversarial** queries come later, in step 4 — they need the schema
   to point at concepts, and asking for twenty questions at once gets ten good
   ones and ten filler.)
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
  **api-reference** (correctly USE an API — the caller's mirror of
  codebase-map: Operations, Types, Recipes, Contracts, Topics), or
  **research-synthesis** (the state of knowledge on a research question:
  atomic Findings with a confidence enum, Methods, Syntheses, OpenQuestions,
  SourceNotes — its cardinal sin is presenting contested as settled), or
  **regulatory-reference** (a frozen slice of primary legal sources:
  Provisions with authority/jurisdiction/status enums, Definitions,
  JurisdictionBoundaries, ComplianceProcedures, InterpretationNotes — its
  cardinal sins are citing superseded text as current and flattening the
  authority chain). If none fits, say so and stop — don't force it.
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
  test_queries (all 10), adversarial_queries (all 10).

  `adversarial_queries` is the second acceptance layer and it is REQUIRED for
  release. Ten test queries prove the bundle answers what it was built for; they
  cannot show what it answers confidently and wrongly. Interview for these
  separately, AFTER the schema is designed and BEFORE any extraction, and write
  each as a mapping:

  ```yaml
  adversarial_queries:
    - query: "Apply the narrow-based index test to a crypto index future."
      expect: not-covered
      why: "crypto is out of scope; a confident hit here is a false positive"
    - query: "How much cash do I need to put down to buy a single stock future?"
      expect: covered
      concept: provisions/type-form-and-use-of-margin
      why: "plain-English phrasing that never says the word margin"
  ```

  `expect: not-covered` means the bundle should signal that it does not cover
  this — a lexicon coverage note. `expect: covered` names the concept that must
  come back, and it must be a real concept id. `why` states the hypothesis; a
  query without one records an answer to a question nobody framed.

  Aim the ten at the ways retrieval actually fails: a topic adjacent to the
  corpus but outside it; the same out-of-scope topic under a SYNONYM (coverage
  guards are keyed to phrasings, not topics, so the synonym is the one that
  slips); a plain-English rephrasing of an in-scope question that avoids the
  corpus's own vocabulary; a keyword-shaped query rather than a sentence. Do NOT
  reuse a test query with different wording — that measures nothing new.
- Write `meta/lexicon.md` (`type: Lexicon`) from the lexicon pass.
- Write `meta/extraction-plan.md` (`type: ExtractionPlan`) with frontmatter:
  `archetype`, `archetype_version`, `types` (name → one-line extraction rule),
  `layout` (type → directory), `segmentation` (include/exclude/budget),
  `segments: []`; body: prose rationale — what the bundle will look like and why.
  `types` is **not optional and not decoration**: it is the closed set of concept
  types this bundle may contain, and `okfy validate --strict-schema` holds every
  concept to it. Write it even when you adapted nothing — then it is the
  archetype's `canonical_types` verbatim. Omitting it is what made a typo
  (`Strategyy`) indistinguishable from a deliberate custom type.
- Run `okfy validate <bundle> --no-archetype` — meta completeness must pass.
- Commit: `git -C <bundle> add . && git -C <bundle> commit -m "plan: purpose + extraction plan"`
- Present the plan to the user: types table, layout, segment count estimate,
  the 10 test queries, and the 10 adversarial queries with what each expects. Say exactly: **"Plan approved? Run `/okfy:extract <bundle-path>`
  to execute stages 4-6."** Do NOT start extraction in this session.
