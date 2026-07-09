---
description: "Lexicon interview: capture the user's vocabulary and map it onto bundle terms"
argument-hint: "<bundle-path>"
---
Bundle: $1. The Lexicon (`meta/lexicon.md`, type: Lexicon) maps the USER's
habitual phrasing onto bundle vocabulary; consuming agents read it before
query expansion (ADR-0004 — this is a pillar of vector-free retrieval).

1. Read the current `meta/lexicon.md` if present, and the bundle's glossary
   index (`okfy query <bundle> --type GlossaryTerm ""` is unreliable for
   listing — read index.md's glossary section instead).

   MIGRATION (ADR-0013): frontmatter rows are now the source of truth; the body
   table is a human rendering of them. If `meta/lexicon.md` has a prose table
   but NO frontmatter rows, migrate before anything else — this is a judgment
   task, not a mechanical convert. Parse the prose table and draft one row per
   entry with fields `term / language / maps_to / canonical_terms / status /
   note`: default `status: accepted`; use `ambiguous` where the prose hedges or
   maps to several concepts without a clear pin; use `not-covered` for entries
   the prose marks as an explicit gap (the old "NOT COVERED" rows). Show the
   drafted rows to the owner and get confirmation BEFORE writing anything.
2. Interview the user, one question at a time, in their language: which terms
   do THEY habitually use for the domain's central things where their phrasing
   may diverge from the bundle's vocabulary? 5-10 rows max. For each: their
   term → meaning → target bundle concepts/vocabulary. Probe with examples
   from their own past queries if any are recorded in meta/purpose.md.
3. Terms that name things the bundle does NOT cover get an explicit
   "NOT COVERED" row (coverage honesty — see the AGENTS.md rule).
4. Rewrite `meta/lexicon.md`: write the frontmatter `rows` (the source of
   truth) — merge with existing rows, never silently drop one — then regenerate
   the human-readable body table FROM those rows so the two never diverge.
   meta/ is protected by the pre-commit hook, so
   commit deliberately: `git -C <bundle> add meta/lexicon.md && git -C <bundle>
   commit --no-verify -m "lexicon: interview update"` — this command IS the
   owner-sanctioned channel.
5. Show the final table to the user.
