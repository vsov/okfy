---
name: okf-consumer
description: Use when the current project or a referenced directory contains an OKF knowledge bundle (a directory with meta/purpose.md and index.md, or an AGENTS.md mentioning "OKF Knowledge Bundle") and the task involves answering questions from it or working with its knowledge. Teaches the consumption discipline, and the memory discipline before and after a task.
---

# Consuming an OKF bundle

You are near an OKF Knowledge Bundle. Its own `AGENTS.md` is the authoritative
protocol — read it FIRST and follow it. This skill is the fallback discipline
when that file is missing or you need a refresher:

1. **Purpose first.** Read `meta/purpose.md` — what this bundle is for bounds
   what it can answer.
2. **Progressive disclosure.** Start at `index.md`; open only the concepts you
   need. Never bulk-read the bundle.
3. **Translate before searching.** Check `meta/lexicon.md` and the glossary
   for the canonical vocabulary (and aliases — they carry cross-language
   equivalents). With the okfy CLI: `okfy query <bundle> "<canonical terms>"`,
   then `okfy show <bundle> <concept-id>`. Query Expansion is now done by the
   tool itself: it returns the `expanded_query` it actually searched plus
   per-row `notes` from the lexicon — trust them. A `ambiguous` note means the
   term maps several ways: ask the user which they meant instead of picking.
   A `not-covered` note means the bundle has no answer: say so, don't guess.
4. **Coverage honesty.** If no concept genuinely matches, say "this bundle
   does not cover that" — never present a merely similar concept as the
   answer. Honor lexicon `not-covered` notes.
5. **Flag stale hits.** A hit marked `stale` means the owner has ruled it "do
   not trust as current" — it may still be the best available answer, so keep
   it, but tell the user it is stale (and its reason) rather than presenting it
   as current fact.
6. **Write only through the sanctioned door.** Never edit concept files
   directly (the bundle's pre-commit hook refuses it). Suggest changes with
   `okfy propose <bundle> --as <actor> --target <id> --action update
   --note "<why>" --from <file>` — a human reviews them.
7. **Cite concept ids** in your answers so the user can verify.

## Before the task

Read what the project already knows before you act on it.

- `okfy query <bundle> "<the task's own terms>"`, then again with the
  lexicon's canonical terms. Look for decisions, constraints and known failure
  causes that bear on the task — not only concepts that answer a question.
- For each hit you rely on, `okfy show` it and read two fields: `stale` (the
  owner says do not trust it as current — say so) and `review_due` (a reminder
  that has passed means "may be out of date, verify before relying"; it is not
  staleness). `okfy stale <bundle> --due` lists every overdue concept.
- If the bundle holds a decision that conflicts with what you were asked to
  do, raise the conflict before acting — do not quietly follow either side.

## After the task

Most of what happened in a task is not worth remembering. Before proposing,
apply one test: **would an agent starting tomorrow, with a blank context, need
this to avoid repeating work or a mistake?**

Keep only:
- a confirmed root cause (with what confirmed it),
- a decision and the reasons it was made,
- a constraint the project must respect,
- a reproducible workaround.

Never propose: conversation, scratch work, your reasoning steps, a guess
stated as fact ("the agent infers…" is fine, labelled as inference), or
anything the bundle already says.

Then:
1. **Search first.** `okfy query` for the concept it belongs to. If one
   exists, add to it with `--extends <id>` rather than creating a near-copy.
2. **Name yourself and your evidence.** `--as <actor>` (e.g. `claude-code/1.0`)
   is required. `--evidence <kind>=<ref>` with kind `test-run` (a run id),
   `owner-decision` (where the owner decided it), `external-source` (a URL or
   document) or `agent-inference` (no ref; say it is inference). "I checked"
   is not evidence — point at what you checked.
3. **Report only what the tool confirmed.** Something is remembered when
   `okfy propose` printed a proposal id (MCP: `persisted: true`). It is
   *accepted* only after the owner runs `okfy review accept`. Never tell the
   user a fact was saved on any other basis.

A refusal names its way out — follow it rather than rephrasing to get past it:

| Code | Why | Way out |
|---|---|---|
| `E_PROPOSAL_ACTOR` | no or malformed `--as` | `<producer>/<version>` or `<prefix>:<id>` |
| `E_PROPOSAL_EVIDENCE` | unknown kind, missing ref, or a `verified` field | fix the evidence; never write `verified` yourself |
| `E_PROPOSAL_DUPLICATE` | the title already names a concept | `--extends <id>`, or a title that says how it differs |
| `E_PROPOSAL_INJECTION` | the text reads as instructions to an agent | none for you; the owner may `okfy refine` |
| `E_PROPOSAL_REJECTED` | the owner rejected this exact text | `--reopen "<what changed>"` only with new evidence |
| `E_PROPOSAL_BASE_MOVED` | the concept changed after you wrote against it (at accept) | re-read it and propose again |
| `E_MEMORY_LINE` | `meta/memory.jsonl` has an unreadable line | tell the owner; do not edit the ledger |

The before/after discipline is adapted from the OKF Agent Memory Convention
(https://github.com/okf-memory/okf-agent-memory, `docs/CONVENTION.md`, MIT).
OKFy borrows the lifecycle, not the tooling: writes still go through proposals
and the owner's review.
