---
description: "Owner-judged Eval Run: LLM-judge proposes verdicts, the owner disposes; only owner verdicts count for acceptance"
argument-hint: "<bundle-path>"
---
Bundle: $1. An Eval Run (`meta/eval.json`, ADR-0013) turns acceptance from an
agent's narrative into a replayable artifact stored in the bundle. The core
fills the deterministic half (query → expansion → top hits); YOU judge as the
LLM-judge; the OWNER disposes. Read `meta/purpose.md` first — its `test_queries`
and `adversarial_queries` are what gets replayed.

**Two suites, both mandatory for release.** `acceptance` replays the ten
questions the bundle was built to answer. `adversarial` replays ten questions
chosen because the bundle is most likely to answer them confidently and wrongly,
and each of those carries a DECLARED EXPECTATION — `expect: covered` with the
concept that proves it, or `expect: not-covered` — so the run records a
deterministic `met`/`unmet` beside your verdict. Run and judge both. Ten owner
passes on the acceptance suite alone prove only that ten phrasings chosen
alongside the bundle work.

RULE (ADR-0013, verbatim-ish): the LLM-judge *proposes*, the owner *disposes*.
Release acceptance counts ONLY owner verdicts; a result with an LLM verdict
alone is **provisional**. A bundle cannot self-certify. NEVER present a
provisional result as accepted — if the owner has not ruled, say so.

## 1. Run

`okfy eval run <bundle>` — appends a fresh acceptance run and prints, per query,
the expanded query and top hits. Verdicts start empty.

`okfy eval run <bundle> --suite adversarial` — the same for the adversarial
queries. Each result additionally carries `expect`, `outcome` (`met`/`unmet`) and
`outcome_detail`.

Run BOTH before judging: they are pinned to one retrieval fingerprint, so a run
of one suite followed by an edit and a run of the other leaves the first stale.

## 2. LLM-judge each query

For EACH query in the new run (0-indexed), open its top hits and judge whether
they actually answer the query:
- `okfy show <bundle> <concept-id>` for the top hits — read the substance, do
  not judge from titles.
- Decide pass | fail | partial, with concrete evidence (name the concept id and
  the fact that does/doesn't answer the query — never a vibe).
- `okfy eval verdict <bundle> latest <i> pass|fail|partial --llm --reason '...'`
  where the reason cites that evidence. Add `--suite adversarial` when judging
  that suite — `latest` is per suite.

On the adversarial suite the question is different: judge against the DECLARED
expectation, which the run already evaluated. `outcome: met` is the ordinary
case. For `outcome: unmet`, say which of two things is true — the bundle failed
the expectation (the finding the query was written to catch), or the expectation
itself was wrong. Never propose `pass` on an `unmet` outcome without saying
which.

## 3. Owner checkpoint (the only verdicts that count)

Present the FULL table to the owner — one row per query: `query | llm verdict |
reason`. Then collect the owner's verdict for each; the owner MAY override your
call:
- `okfy eval verdict <bundle> latest <i> pass|fail|partial --owner --note '...'`
  (`--suite adversarial` for the second table)

Do not skip queries the owner did not rule on — those stay provisional.

For the adversarial table, show `expect`, `outcome` and `outcome_detail` in the
row. An owner `pass` over an `unmet` outcome is legitimate — the expectation may
have been wrong — but it is an OVERRIDE of a criterion stated before the answer
was seen, and it must be presented as one. `release-check` reports the count of
unmet expectations in its notes.

## 4. Status + log

1. `okfy eval status <bundle>` and `okfy eval status <bundle> --suite
   adversarial` — owner-confirmed vs provisional vs pending, pass counts, and
   for the adversarial suite how many declared expectations were met. While any
   query lacks an owner verdict the run stays provisional.
2. `okfy log <bundle> "eval: run <run-id> — <owner-pass>/<of> owner pass"`.
3. Report to the owner: the run id, the effective (owner) result, and any
   queries still provisional or failing. State plainly whether the bundle is
   owner-accepted or only provisionally judged — never conflate the two.
