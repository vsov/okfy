---
name: okf-consumer
description: Use when the current project or a referenced directory contains an OKF knowledge bundle (a directory with meta/purpose.md and index.md, or an AGENTS.md mentioning "OKF Knowledge Bundle") and the task involves answering questions from it or working with its knowledge. Teaches the consumption discipline.
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
   `okfy propose <bundle> --target <id> --action update --note "<why>"
   --from <file>` — a human reviews them.
7. **Cite concept ids** in your answers so the user can verify.
