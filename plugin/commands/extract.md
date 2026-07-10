---
description: "Stages 4-6: blind parallel extraction → consolidation → 4-layer validate → package"
argument-hint: "<bundle-path>"
---
Bundle: $1. Read `meta/extraction-plan.md` and `meta/purpose.md` first.
Resumable: skip any segment already `status: done`.
Commits: always `git -C <bundle> add .` (never `add -A`) — in embed bundles
the enclosing repo is the user's corpus repo and `-A` would stage their
unrelated changes.

Prompt versions (stamp these into every ledger row so provenance is
reproducible): worker drafts = `extract-worker@1`, consolidation =
`consolidate@1`. Bump the number here whenever you change the corresponding
prompt. The human label is not the real version: for worker rows the ledger
also records the digest of a core-built job artifact (see Stage 4) that
hashes the actual prompt text and inputs.

## Stage 4 — Extract (blind parallel Workers)

1. If `segments` is empty in the plan: run
   `okfy segment <bundle> --budget <plan budget> --include <globs> --exclude <globs>`.
2. Seed Glossary: from the plan's glossary strategy + survey, draft 10-30
   one-line term seeds (term + gloss). Keep in memory; pass to every worker.
3. For each pending segment, FIRST freeze the worker's contract:
   `okfy job <bundle> <segment-id> --prompt-file plugin/prompts/extract-worker.md`
   — the core writes `meta/jobs/<segment-id>.json` (schema `okfy-worker-job@1`:
   inputs with their `lines`/`chars` spans and content hashes, corpus snapshot,
   archetype, prompt SHA-256) and prints it with its `digest`. Then spawn a
   subagent (Task tool) with the prompt from `plugin/prompts/extract-worker.md`,
   placeholders filled, passing the job artifact's `inputs` list as the
   worker's AUTHORITATIVE input manifest — the worker reads exactly what the
   artifact says, nothing else:
   - `{path}` — the whole file;
   - `{path, lines: "A-B"}` — ONLY lines A-B (1-indexed, inclusive);
   - `{path, chars: "A-B"}` — a dense file with no blank-line boundaries
     (minified / single-line): ONLY the character window A-B (1-indexed,
     inclusive) of the file's text.
   Never let a worker read the whole file for a span entry — the segment
   budget exists precisely because these files don't fit. If the artifact
   contains a span form you don't recognize, STOP and report — do not guess.
   Run up to 4 concurrently. After each segment completes:
   - `okfy validate <bundle> --all` — draft frontmatter must parse (fix by
     re-running the worker on its segment if broken);
   - `okfy segment-status <bundle> <segment-id> done`
   - `git -C <bundle> add . && git -C <bundle> commit -m "extract: <segment-id>"`
   - `okfy ledger add <bundle> --run <run-id> --segment <segment-id>
     --inputs <corpus-paths-the-worker-used> --prompt-version extract-worker@1
     --outputs <draft-ids-written> --validation <pass|fail>
     --job <segment-id>` — one row per worker, after its drafts commit.
     `--job` takes the SEGMENT ID: the core loads the frozen artifact in
     `meta/jobs/<segment-id>.json` and computes the digest itself — never
     hand-copy a digest.

## Stage 5 — Consolidate

1. `okfy cluster <bundle>` → clusters of draft ids.
2. For each multi-draft cluster: read all members, write ONE merged concept at
   its final path (plan layout, e.g. `strategies/<name>.md`) — union of
   sources/aliases/tags, best content wins, contradictions resolved toward the
   more specific source. For singleton clusters: move draft to its final path.
3. Resolve links: make link targets point at final paths; leave genuinely
   missing targets dangling (spec tolerates).
4. Synthesize the glossary: every Seed Glossary term used by ≥1 concept gets a
   GlossaryTerm concept with cross-language aliases.
5. Delete `drafts/` contents. Commit: `consolidate: <N> concepts from <M> drafts`.
6. After the consolidation commit, ledger the transition with the draft→final
   merge map: `okfy ledger add <bundle> --run <run-id> --segment consolidate
   --inputs <draft-ids-consumed> --prompt-version consolidate@1
   --outputs <final-concept-ids> --validation <pass|fail>
   --merge-map "<draft>=<final>,<draft2>=<final>"`.

## Stage 6 — Validate + package

1. Layers 1-2: `okfy index <bundle>` then `okfy validate <bundle> --strict-sources`
   — MUST exit 0. New bundles are born strict: broken `sources` paths are errors
   here, not warnings. Fix errors (missing fields/sections, dead sources) and
   re-run until green.
2. Layer 3 (purpose fitness): `okfy sample <bundle>` → returns JSON
   `{selector_version, seed, sampled, reasons, notes}` — a risk-oriented
   deterministic sample (changed sources, stale, rare types, weak coverage
   first). For each sampled id, `okfy show` it and judge against the
   archetype's `purpose_checks` (read them from the archetype yaml). Fix
   failures in place, commit fixes.
   PERSIST the pass as an artifact, not a story: write `meta/purpose-fitness.md`
   with frontmatter `type: PurposeFitness`, `date`, `prompt_version`, and —
   copied verbatim from the sample output — `selector_version`, `seed`,
   `sampled`, plus the `fraction`/`minimum` used if non-default. The verdicts
   are MACHINE-READABLE frontmatter, not prose: `rows:` — one entry per
   sampled id × check id, each `{concept_id, check_id, verdict:
   pass|fail|n/a, evidence: <one line>}`. The body is a human rendering
   (what was checked, what was fixed) — never the source of truth. Commit it.
   Then `okfy validate <bundle> --strict-quality` MUST exit 0 — the validator
   demands every sampled id × check row, unique, with a real verdict and
   non-empty evidence, and (while the corpus hasn't moved) replays the
   selector. L3 is replayable evidence like the eval, not an agent action
   that evaporates with the transcript.
3. Layer 4 (consumption smoke test) — run the **/okfy:eval** flow, NOT a prose
   check: `okfy eval run <bundle>` (deterministic hits), LLM-judge each query
   (`okfy eval verdict ... --llm`), then take the owner through the checkpoint
   (`... --owner`). This produces a replayable Eval Run in `meta/eval.json`.
   Acceptance is the owner's call at that checkpoint — an LLM-only pass is
   provisional and must never be reported as accepted.
4. `okfy package <bundle>` then `okfy index <bundle>` (refresh after doc gen).
   Packaging records a fingerprint of the concept set in `meta/package.json`;
   any concept change after this point makes the package stale.
5. FINAL GATE — the full strict validation, after packaging:
   `okfy validate <bundle> --strict-sources --strict-quality
   --strict-provenance --strict-package` MUST exit 0. This cross-checks the
   whole release: sources and anchors real, purpose-fitness complete, every
   job artifact/frozen prompt/ledger digest consistent, every concept
   reachable from index.md, package fresh. If it fails, fix and re-run —
   repackage if concepts changed.
6. `okfy log <bundle> "extract: <N> concepts, smoke <K>/10 (eval <run-id>)"`;
   final commit `reextract: complete — <K>/10 smoke queries pass (eval <run-id>)`.
7. Report to the user: concept counts by type, validation summary, and the Eval
   Run result (owner-confirmed vs provisional per `okfy eval status`) with any
   failing/gapping queries and suggested fixes: refine targets or corpus gaps.
   MVP acceptance: ≥8/10 owner PASS.
