---
description: "Owner-judged Eval Run: LLM-judge proposes verdicts, the owner disposes; only owner verdicts count for acceptance"
argument-hint: "<bundle-path>"
---
Bundle: $1. An Eval Run (`meta/eval.json`, ADR-0013) turns acceptance from an
agent's narrative into a replayable artifact stored in the bundle. The core
fills the deterministic half (query → expansion → top hits); YOU judge as the
LLM-judge; the OWNER disposes. Read `meta/purpose.md` first — its `test_queries`
are what gets replayed.

RULE (ADR-0013, verbatim-ish): the LLM-judge *proposes*, the owner *disposes*.
Release acceptance counts ONLY owner verdicts; a result with an LLM verdict
alone is **provisional**. A bundle cannot self-certify. NEVER present a
provisional result as accepted — if the owner has not ruled, say so.

## 1. Run

`okfy eval run <bundle>` — appends a fresh run and prints, per query, the
expanded query and top hits. Verdicts start empty.

## 2. LLM-judge each query

For EACH query in the new run (0-indexed), open its top hits and judge whether
they actually answer the query:
- `okfy show <bundle> <concept-id>` for the top hits — read the substance, do
  not judge from titles.
- Decide pass | fail | partial, with concrete evidence (name the concept id and
  the fact that does/doesn't answer the query — never a vibe).
- `okfy eval verdict <bundle> latest <i> pass|fail|partial --llm --reason '...'`
  where the reason cites that evidence.

## 3. Owner checkpoint (the only verdicts that count)

Present the FULL table to the owner — one row per query: `query | llm verdict |
reason`. Then collect the owner's verdict for each; the owner MAY override your
call:
- `okfy eval verdict <bundle> latest <i> pass|fail|partial --owner --note '...'`

Do not skip queries the owner did not rule on — those stay provisional.

## 4. Status + log

1. `okfy eval status <bundle>` — reports owner-confirmed vs provisional vs
   pending, and pass counts. While any query lacks an owner verdict the run
   stays provisional.
2. `okfy log <bundle> "eval: run <run-id> — <owner-pass>/<of> owner pass"`.
3. Report to the owner: the run id, the effective (owner) result, and any
   queries still provisional or failing. State plainly whether the bundle is
   owner-accepted or only provisionally judged — never conflate the two.
