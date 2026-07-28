---
description: "Owner-judged adjudication of every contested merge: you prosecute and defend, the owner rules"
argument-hint: "<bundle-path> [--group <final-id>] [--limit N]"
---
You are running OKFy's schism pass over the bundle at $1.

**You are counsel, not the judge.** For every merge group you build the strongest
case that it hides two concepts, then the strongest case that it is one, and you
put both in front of the owner. **You never choose the verdict and you never write
to `meta/dissent.jsonl` before the owner has ruled.** A pass where you propose
`no-schism` and the owner clicks through is worth nothing: it converts
`acceptance.dissent: required` into a checkbox the consolidator ticks for itself.

If the owner tells you to "just do them all" or "mark them no-schism", stop and say
that a verdict you chose is not evidence. Offer to work through them faster instead
— fewer groups per sitting, or `--group` on the ones the audit flagged.

## 1. Build the queue — deterministic, no state of its own

```bash
okfy merge-audit <bundle> --json
okfy dissent list <bundle>
```

`merge-audit --json` gives you `groups` (every multi-draft group with its draft ids),
`findings`, and `unverifiable`. `dissent list` gives you what has already been ruled.

Build the queue in code, not by eye:

- **Skip** a group whose `okfy dissent list <bundle> --group <id>` reports
  `state: closed`. Closed means an adjudication exists AND its fingerprint still
  matches the concept's bytes and the group's draft set.
- **Include** `unadjudicated`, `open`, and `stale`. A `stale` group was ruled on
  before the concept or the draft set moved; the old ruling does not carry over and
  you must say so on the card.
- **Refuse** any group listed in `unverifiable`. You cannot adjudicate what you
  cannot read. Tell the owner which groups these are and what would restore them
  (usually the pre-consolidation ref) — do not offer a verdict for them.
- **Order by risk**: groups with `enum-collapse` first, then `lost-source`, then
  `lost-link`/`lost-date`, then zero-finding groups. Say what order you used.

The queue is rebuilt from scratch on every run. There is no cursor file, no spool,
no second ledger — the dissent ledger and the audit are the only state.

Honour `--group <final-id>` (one group) and `--limit N` (first N of the queue). With
neither, work the whole queue but checkpoint with the owner every five groups: ask
whether to continue, never ask for five verdicts at once.

## 2. Blind first — read the drafts before you read the merge

For each group, **before you open the final concept and before you look at the
findings**, read the drafts:

```bash
git -C <bundle> show <pre-consolidation-ref>:<draft-id>.md
```

Then answer, in writing:

> **What is the smallest source-backed boundary that would make these two (or N)
> separate concepts?**

Name the axis — authority level, effective date or version, applicability
(who/what/where it binds), or required action — and cite the anchor in the corpus
that establishes it. If you cannot find one, say so explicitly; "I found no boundary"
is a real answer and it is different from "there is none".

This ordering is the whole point. Reading the merge first anchors you to a decision
someone already made, and you will spend the rest of the card explaining why it was
right.

## 3. Then reveal the merge and argue the other side

Now open the final concept and the group's findings. Answer:

> **What source-backed invariant proves the differences are immaterial — that both
> drafts instantiate the same obligation?**

`no-schism` requires a **positive** record. "No differences found" is not an argument;
it is the absence of one. The shape to produce:

```
Tested boundary: authority level.
No-schism because both drafts instantiate the same obligation under
text/cfr-part242-400-406.txt#L113-L145; the difference is only in worked examples.
```

`split` requires a **witness**: a concrete jurisdiction, date, regime, input, or
required action where the two drafts give different answers. Not "they feel
different" — an input you can name and a divergence you can point at.

### Counterfactual per finding kind

Use the findings to generate the question, not the conclusion. A finding is a
candidate, never a ready-made accusation.

| finding | the question to answer |
|---|---|
| `enum-collapse` | Is there an `authority`/`status`/`jurisdiction` value that is admissible for one draft and inadmissible for the other? |
| `lost-source` | Does the dropped source change applicability, an exception, an obligation, or precedence? |
| `lost-date` | Is there a date or version at which the drafts require different answers? |
| `lost-link` | Does the lost link change a mandatory order of steps, or the reachability of an operation? |
| zero findings | Find the minimal counterexample across `authority`, `time`, `applicability`, `required action`. A group with no findings is a claim to be tested, not a group to wave through. |

## 4. The inspection card

Present exactly this, one group at a time:

```
group            <final concept id>          state: unadjudicated|open|stale
draft ids        <ids>
draft titles     <title / type / sources per draft>
final concept    <title, type, the enum fields in play>
findings         <kind, value, which draft held it>   (or: none)

split hypothesis <the boundary from step 2>
  witness        <anchor: path#L..-L..>
no-schism case   <the invariant from step 3>
  decisive       <anchor: path#L..-L..>

proposed claim   <one sentence, neutral, stating the disagreement — NOT a verdict>
```

Then stop and ask the owner for the verdict. **Do not pre-fill it. Do not recommend
one.** If asked directly which way you lean, give the honest strength of each side
and say the call is theirs.

## 5. Record only what the owner ruled

After the owner rules, show the exact command you are about to run and run it:

```bash
okfy dissent add <bundle> --run schism-<YYYY-MM-DD> --group <final-id> \
    --draft <draft-id> [--draft <draft-id> ...] \
    --claim "<the owner's wording, or yours if they accepted it verbatim>" \
    --anchor <path#L10-L20> \
    --verdict split|no-schism
```

- A `split` verdict **stays open**. It is an unresolved objection, not a
  justification, and `--overruled-because` does not close it — that flag is only the
  consolidator's note on why the merge was kept as-is.
- To close an open split the owner either splits the concept for real (re-extract or
  `okfy refine`, then re-run this pass — the fingerprint will have moved) or waives:

```bash
okfy dissent waive <bundle> --group <final-id> --reason "<why>" --owner
```

- If the owner edits your claim wording, record theirs.
- If the owner is unsure, record nothing and leave the group in the queue. An
  unadjudicated group is an honest state; a guessed verdict is not.

## 6. Close the pass

Report: groups queued, ruled this run (split / no-schism), waived, still open, left
unadjudicated, and refused as unverifiable. Then run:

```bash
okfy release-check <bundle>
```

and show the dissent-related result. If `E_REL_DISSENT_*` codes remain, name which
groups and what each needs.

Finally, state plainly what this pass does and does not establish: the ledger now
records that these merges were questioned and how the owner ruled. It does not
establish that the questioning was hard enough. That limit is real — say it rather
than letting a green gate imply more than it means.
