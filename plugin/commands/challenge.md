---
description: "Adversarial candidate queries against a bundle — read-only, no authority, owner promotes"
argument-hint: "<bundle-path>"
---
You are running OKFy's adversarial challenge pass against the bundle at $1.

**This command has NO WRITE AUTHORITY.** You must not create, edit, or delete anything
inside the bundle. You must not touch `meta/purpose.md`, `meta/eval.json`,
`meta/lexicon.md`, or any concept. You must not run any `okfy` verb that writes
(`propose`, `review`, `refine`, `stale`, `eval verdict`, `ledger add`, `package`,
`index`, `job`). The only okfy verbs you may run are `query`, `show`, `links`,
`validate`, `release-check`, and `merge-audit` — all read-only.

Your output is a list of candidate questions and traps for the owner to judge. It is
never a concept, never a verdict, never an eval row. Nothing you produce enters the
bundle without an explicit owner action taken afterwards, by hand.

## Why this exists

A bundle's ten `test_queries` are written by whoever built it, so they test what that
person already thought to test. Measured on an accepted bundle (owner-confirmed 10/10,
`release-check ok: true`), adversarially-authored questions found five reproducible
failures — every one of them in the gap between two passing queries. The two most
instructive: a `not-covered` lexicon guard fired for the exact phrase that had been
tested and stayed silent for a plain synonym of the same out-of-scope topic; and the
bundle answered at its highest confidence of the whole run about a product that appears
nowhere in it.

## 1. Author blind

Read **only** `meta/purpose.md` — its title, statement, declared out-of-scope list, and
the archetype's purpose checks. **Do not read `index.md` or any concept while
authoring.** A question written after reading the answers proves nothing.

State explicitly in your output that you authored blind, and at what point you began
reading concepts.

## 2. Five frames, four candidates each

Run each frame independently; do not let one frame's output shape another's.

| frame | mandate |
|---|---|
| **regulator** | Where will it cite superseded or historical text as the live rule? |
| **newcomer** | Where will the asker's plain vocabulary miss the bundle's terms? |
| **analogist** | Where will it extrapolate across a declared boundary — product, jurisdiction, venue, time? |
| **hurry** | Where is the first plausible hit wrong, or where does one keyword drag in a whole wrong cluster? |
| **historian** | Where will it answer without an as-of date, or without naming what superseded what? |

Composition requirements:

- **≥4 negative-space candidates** (`expect: not-covered`), drawn from the topics
  `purpose.md` declares out of scope. For these a confident top hit is the **failure**.
  At least two must be *synonyms* of a declared out-of-scope topic rather than its exact
  wording — that is the class that found the real bypass.
- **≥4 temporal or precedence candidates**, and at least one must phrase a pinned concept
  in **plain English that avoids the pinned term** — the other class that found a real
  bypass.
- No candidate may duplicate an existing `test_queries` entry.

Each candidate names the specific failure mode it targets, not just a topic.

## 3. Run every candidate

`okfy query <bundle> "<candidate>" -n 4` for all of them. Record the top hits with their
scores and any `note:` lines the tool emits. Then, and only then, read the concepts you
need in order to judge the outcomes.

## 4. Classify with evidence, not assertion

- `bypass-confirmed` — a reproducible wrong or out-of-slice **confident** top hit.
  Define "confident" from the run's own score distribution (e.g. the upper half), and say
  what threshold you used. Verify mechanically before claiming: does the corpus mention
  the product at all? does the correct concept exist and simply rank too low? did the
  same topic under its tested phrasing emit a `not-covered` note when this phrasing did
  not? Record the exact query string and top hit id so the case can be replayed verbatim.
- `handled` — answered correctly, or correctly signalled not-covered.
- `inconclusive` — the outcome depends on how a consuming model reads the hits; say why
  it could not be decided mechanically.

Be strict. Zero bypasses on an accepted bundle is a legitimate, publishable result. Do
not inflate the count, and do not tune the bundle to make a candidate pass or fail.

## 5. Hand the result to the owner

Present the candidate table, the verbatim evidence for each `bypass-confirmed`, and a
count. Then say exactly what the owner's options are:

- **Confirmed bypass → a core regression test.** This is the cheap route and it changes
  no acceptance surface. It is what this pass is for.
- **Promotion into `meta/purpose.md` `test_queries` is owner-only, and it is expensive.**
  `retrieval_fingerprint` covers `test_queries`, so adding one invalidates the recorded
  eval run (`E_REL_EVAL_STALE`) and forces a full re-run with fresh owner verdicts on all
  ten. That cost is a feature — there are no free additions to the acceptance surface —
  but the owner must choose to pay it. **Never edit `test_queries` yourself.**
- **A lexicon fix** (adding a `not-covered` row for a topic's synonyms) is also an owner
  edit. Propose the exact rows; do not write them.

Finish by stating plainly what you did not check and what a second pass should target.
