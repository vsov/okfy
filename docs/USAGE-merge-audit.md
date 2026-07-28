# Using the v0.10 additions: `merge-audit`, execution identity, dissent ledger

Step-by-step instructions for the three capabilities added in v0.10. Every command
below was executed at least once while the feature was built; none of the flags are
aspirational.

Conceptual background — what each mechanism is for and why it is shaped the way it is
— lives in [GUIDE.md §11](guide/GUIDE.md). This document is the operating manual.

---

## 1. Auditing what a consolidation dropped

### 1.1 The one-liner

```bash
okfy merge-audit ~/bundles/my-bundle
```

That is the whole thing for a bundle that has already been consolidated. The tool
reconstructs the merge groups from the ledger's `merge_map`, auto-detects the commit
before `drafts/` was deleted, recovers the drafts from it, and prints a report.

It **never fails your build**. Exit code 0 means the audit ran, not that it found
nothing. `release-check` is not affected by anything it reports.

### 1.2 Reading the output

```
merge-audit: /Users/you/bundles/my-bundle
state: ok (ref 5bbeef9)   groups: 33   with findings: 27

provisions/type-form-and-use-of-margin   [9 draft(s) held lost values]
  enum-collapse  drafts/segment-06/type-form-and-use-of-margin  authority: regulation
  lost-link      drafts/segment-14/equity-computed-under-account-margin-rules  definitions/security-future
  lost-date      drafts/segment-15/sf-acceptable-margin-deposits  2002

summary: lost-source 0 · enum-collapse 1 · lost-link 8 · lost-date 3
unverifiable: 0
```

The header line tells you three things. `state` is how the drafts were obtained;
`groups` is how many multi-draft merges exist at all; `with findings` is how many of
them have something to look at.

Read `unverifiable` first, every time. `unverifiable: 0` means every group was actually
inspected. Anything else prints `N group(s) NOT AUDITED` with a reason, and it means
the tool could not check those groups — which is **not** the same as finding nothing
wrong in them.

### 1.3 What to do with each finding kind

| finding | what it means | what to do |
|---|---|---|
| `lost-source` | a draft cited a source and the merged concept does not | Usually worth fixing. Check whether a sibling draft cited the same passage more precisely — if so, the drop was correct. Otherwise add the source back with `okfy refine`. |
| `enum-collapse` | two drafts disagreed on an archetype enum and the merge kept one silently | The most load-bearing kind. For `regulatory-reference` a disagreement about `authority` or `status` is exactly what the archetype exists to get right. Decide which value is correct, and consider whether the disagreement means the group should have been two concepts. |
| `lost-link` | a draft linked to a concept that still exists, and the merged concept does not | The merged concept is less navigable than its parts were. Add the link back if it carried meaning; ignore it if the merged text no longer discusses that topic. |
| `lost-date` | an ISO date, a year, or a percentage in a draft's frontmatter is gone from the merged concept's | Check for genericisation: "the 20% minimum" becoming "the required percentage" is sometimes correct (if 20% is superseded) and sometimes a real loss of temporal precision. |

### 1.4 Drilling into one group

```bash
okfy merge-audit ~/bundles/my-bundle --group provisions/type-form-and-use-of-margin
```

Use this when the summary flags a group and you want only its detail plus its notes.

### 1.5 Auditing a specific point in history

```bash
okfy merge-audit ~/bundles/my-bundle --ref 5bbeef92fbbe
```

An explicit `--ref` overrides the working tree even if drafts happen to be present —
if you name a ref, that is what gets audited. To find the right ref yourself:

```bash
git -C ~/bundles/my-bundle log --oneline --diff-filter=D -- 'drafts/*'
# the parent of the commit listed there is the pre-consolidation state
```

### 1.6 Machine-readable output

```bash
okfy merge-audit ~/bundles/my-bundle --json | jq '.by_kind'
okfy merge-audit ~/bundles/my-bundle --quiet     # summary only, no per-group detail
```

### 1.7 What a false positive looks like

The tool reports **candidates**, not defects. A `lost-source` where a sibling draft
cited the same file at a tighter line range is a correct merge. A `lost-date` where a
superseded rate was deliberately genericised is a correct merge. Anchor narrowing
(`raw/a.txt#L1-L9` collapsing into `raw/a.txt`) is reported as a *note*, not a finding,
for exactly this reason.

If you find the report noisy on your corpus, that is information worth acting on —
say which kind and which field, because both current exclusions (`aliases` from the
literal scan, never-live link targets) were added after measuring noise on real
bundles rather than guessing.

---

## 2. Recording who ran an extraction

### 2.1 Write the attestation file

Create a small JSON file — anywhere, it does not live in the bundle:

```json
{
  "model": "claude-opus-5",
  "provider": "anthropic",
  "sampling": {"temperature": 0},
  "harness_version": "claude-code/2.1"
}
```

All four keys are required. Unknown keys are refused, and so is a blank value.

**Where the values come from: the agent, and nowhere else.** There is no reliable
channel through which the core, or you, can read the model actually serving a run —
so `/okfy:extract` asks the agent to describe itself and records the answer verbatim.
Read every field as *"the agent reported this"*. Where the agent does not know a value
it writes `unknown-to-agent`; that is the correct entry, and it is strictly better than
a plausible guess, which would read as recorded fact.

### 2.2 Attach it when freezing the job

```bash
okfy job ~/bundles/my-bundle segment-01 \
    --prompt-file /path/to/worker-prompt.md \
    --execution-file /path/to/exec.json
```

The block lands in `meta/jobs/segment-01.json` and is covered by the job digest, so
swapping the model changes the digest and is visible in the ledger.

Without `--execution-file`, `okfy job` behaves exactly as it always did and produces a
byte-identical artifact.

### 2.3 Require it for new extractions

```bash
okfy validate ~/bundles/my-bundle --strict-execution
```

Without the flag, a job artifact lacking the block produces `W_EXEC_MISSING` and the
bundle stays green. With it, the same artifact produces `E_EXEC_MISSING` and validation
exits 1.

A block that exists but has a missing or blank field is `E_EXEC_FIELD` at **both**
levels — a half-filled attestation reads as complete and is not.

### 2.4 Do not retrofit it

Turn `--strict-execution` on for extractions you are running now. Do **not** go back and
add `execution` blocks to bundles that were built before v0.10: you would be writing
down a claim about an execution nobody recorded, which is fabrication rather than
provenance. Those bundles warn, stay green, and that is the honest state.

### 2.5 What this does and does not prove

It proves the harness *claimed* a particular model. It does not prove the claim is true.
The core is agent-neutral by design — it
never talks to a provider and cannot observe what ran. A harness that reports the wrong
model passes this check. What you get is a recorded, digested, diffable claim, which is
strictly more than the nothing you had before, and strictly less than proof.

---

## 3. Recording how a contested merge was resolved

The dissent ledger is for the case where `merge-audit` surfaces something real and you
want the decision to survive, instead of re-litigating it on every future audit.

### 3.1 Record an adjudication

The supported way to do this is `/okfy:schism <bundle>`, which walks the queue, makes
you state the strongest case for splitting *before* it shows you the merge, then the
strongest case against, and records only the verdict you give it. The raw command
below is what that pass ends up running; use it directly only when you are adjudicating
a single group by hand.

```bash
okfy dissent add ~/bundles/my-bundle \
    --run schism-2026-07-28 \
    --group provisions/type-form-and-use-of-margin \
    --draft drafts/segment-06/type-form-and-use-of-margin \
    --draft drafts/segment-14/equity-computed-under-account-margin-rules \
    --claim "the two drafts describe obligations at different authority levels" \
    --anchor text/cfr-part242-400-406-customer-margin.txt#L113-L145 \
    --verdict split \
    --overruled-because "one clause with two worked examples; merged deliberately"
```

`--verdict no-schism` means the drafts really did describe one thing. `--verdict split`
means they did not — and a split **stays open**. It is an unresolved objection, not a
justification, so it needs no reason at the moment of recording; `--overruled-because`
is available as the consolidator's note on why the merge was kept, and it annotates
without resolving anything. Only an owner waiver, or actually splitting the concept,
closes it. A later `no-schism` row does not: the party that recorded the merge cannot
also dismiss the objection to it.

`no-schism` needs a **positive** record, not the absence of a complaint:

```
Tested boundary: authority level.
No-schism because both drafts instantiate the same obligation under
text/cfr-part242-400-406.txt#L113-L145; the difference is only in worked examples.
```

`split` needs a **witness** — a concrete jurisdiction, date, regime, input, or required
action where the drafts give different answers.

`--anchor` should point at the source lines the claim rests on. A vote without evidence
is cheap; requiring the anchor is what makes a lazy pass cost something.

### 3.2 Review what is on the record

```bash
okfy dissent list ~/bundles/my-bundle
okfy dissent list ~/bundles/my-bundle --group provisions/type-form-and-use-of-margin
```

With `--group` it also prints the group's state: `unadjudicated`, `open`, `stale`, or
`closed`.

### 3.3 Close an open split

```bash
okfy dissent waive ~/bundles/my-bundle \
    --group provisions/type-form-and-use-of-margin \
    --reason "reviewed against 17 CFR 242.404: one obligation, two examples" \
    --owner
```

`--owner` is required. Waiving is your decision, and the flag is the acknowledgement.

Every row — waiver or not — pins an `adjudication_fingerprint` over the merged
concept's bytes **and** the sorted ids of the drafts that fed it. Edit the concept, or
let a later run add a draft to the group, and the ruling goes `stale` and the group
reopens. Binding to the concept alone was not enough: a group that grew a new draft
would still have read as closed by a ruling that never saw it.

### 3.4 Turn the release gate on (optional, per bundle)

Add to `meta/purpose.md`:

```yaml
acceptance:
  dissent: required
```

Only then does `okfy release-check` consult the ledger, with four codes:

| code | meaning |
|---|---|
| `E_REL_DISSENT_UNADJUDICATED` | a multi-draft merge group has no dissent row at all |
| `E_REL_DISSENT_OPEN` | a `split` was recorded and never resolved |
| `E_REL_DISSENT_STALE` | an adjudication no longer matches what it ruled on — the concept's bytes or the group's draft set moved since |
| `E_REL_DISSENT_UNVERIFIABLE` | the contract is required but the record cannot be checked at all: no `merge_map` in the ledger, so the groups cannot be reconstructed; or the pre-consolidation drafts cannot be recovered, so every ruling is unfalsifiable |

The escape hatch, for a bundle where you want the ledger but not the blocking:

```yaml
acceptance:
  dissent: required
  allow_open_dissent: true
```

It excuses the first two codes only. `_STALE` and `_UNVERIFIABLE` still block, because
they do not mean "a verdict you would rather not act on" — they mean the record no
longer describes what is in the bundle, or cannot be read at all.

Bundles that do not declare `acceptance.dissent` are completely unaffected — this is
why turning it on is a per-bundle decision rather than a global upgrade.

### 3.5 The limit, stated plainly

This gate verifies that adjudication **happened**. It cannot verify that it was
rigorous. Someone stamping `no-schism` on every group passes it completely, and
`release-check` prints that caveat in its own notes rather than letting a green result
imply more than it means. Treat it as evidence that the question was asked.

---

## 4. Probing a bundle adversarially

`/okfy:challenge <bundle>` runs the pass that found five reproducible failures in a
bundle already holding owner-confirmed 10/10. It authors questions from `meta/purpose.md`
without reading the concept index first, runs them, and hands you candidates.

It has **no write authority**. It cannot change your bundle, your `test_queries`, your
lexicon, or your eval. Everything it produces is a suggestion you act on by hand.

When it confirms a bypass, you have three routes, in increasing cost:

1. **A core regression test.** Cheapest, changes no acceptance surface, and it is what
   the pass is for.
2. **A lexicon fix** — usually adding `not-covered` rows for the *synonyms* of an
   out-of-scope topic, since the guard is keyed to phrasings rather than topics.
3. **Promotion into `meta/purpose.md` `test_queries`.** Expensive by construction:
   `retrieval_fingerprint` covers `test_queries`, so adding one invalidates your
   recorded eval run (`E_REL_EVAL_STALE`) and forces a full re-run with fresh owner
   verdicts on all ten. That cost is deliberate — there are no free additions to the
   acceptance surface — but it is yours to choose to pay.
