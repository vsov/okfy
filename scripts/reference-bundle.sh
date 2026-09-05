#!/usr/bin/env bash
# Build the reference bundle: a synthetic bundle that passes the WHOLE release
# contract, from the installed `okfy` CLI and nothing else.
#
# WHY THIS IS A SHELL SCRIPT. The unit suite does not ship in the export, so a
# pytest fixture proving the contract holds would prove it only on this machine.
# This runs where the export runs. It is the published repository's only
# end-to-end evidence that the contract is satisfiable at all — an external audit
# of v0.19 found that not one bundle in existence passed it, and a contract no
# artifact meets is a specification, not a gate.
#
# WHAT IT IS NOT. Every byte here is synthetic: the corpus is three invented
# files about an invented instrument, and the owner verdicts are written by this
# script. They are FIXTURE DATA, not a record of anyone's judgement, and they
# prove exactly one thing — that the machine-checkable half of the contract can
# be satisfied end to end. They say nothing about whether a real extraction is
# any good. Never point this at a real corpus and never run it inside a real
# bundle.
#
#   bash scripts/reference-bundle.sh <workdir>
#
# Leaves <workdir>/widget-okf behind for the caller to inspect. Exits non-zero
# the moment any step or the final release-check fails.
set -euo pipefail

WORK="${1:-}"
[ -n "$WORK" ] || { echo "usage: reference-bundle.sh <workdir>" >&2; exit 2; }
mkdir -p "$WORK"
WORK="$(cd "$WORK" && pwd)"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE="$HERE/reference-corpus"
[ -d "$FIXTURE" ] || { echo "missing fixture corpus: $FIXTURE" >&2; exit 2; }

# The fixture is COPIED out of the repo before any bundle points at it. Two
# reasons, both load-bearing. A bundle records its corpus path, so a bundle built
# here would otherwise carry a path into a checkout that will not exist on the
# next machine. And the L3 sample seed is the corpus's git sha: with the fixture
# read in place that sha is OKFy's own HEAD, so the recorded sample would go
# stale on every unrelated commit and the replay check would silently skip.
# The RAW tree and the normalized corpus are separate directories, because
# that is the shape a converted corpus actually has and it is what makes
# `normalization.raw_root` mean anything. Passthrough is the converter: it needs
# nothing installed, so this stays runnable in CI, and the raw and normalized
# bytes being identical is honest for a Markdown corpus rather than a shortcut.
RAW="$WORK/raw"
rm -rf "$RAW"
mkdir -p "$RAW"
cp "$FIXTURE"/*.md "$RAW/"

CORPUS="$WORK/corpus"
rm -rf "$CORPUS"

BUNDLE="$WORK/widget-okf"
[ -e "$BUNDLE" ] && { echo "refusing: $BUNDLE already exists" >&2; exit 2; }

# A CI runner has no git identity and every okfy verb that writes also commits.
export GIT_AUTHOR_NAME="okfy reference" GIT_AUTHOR_EMAIL="ref@example.invalid"
export GIT_COMMITTER_NAME="okfy reference" GIT_COMMITTER_EMAIL="ref@example.invalid"

step() { printf '\n=== %s\n' "$1"; }
fail() { echo "REFERENCE FAIL: $*" >&2; exit 1; }

step "normalize the raw tree into the corpus"
# The adapter, not `cp`. A source map written by hand would agree with the
# validator by construction; one written by the producer is the only thing that
# shows the two halves of this contract still fit together.
okfy-normalize "$RAW" "$CORPUS" >/dev/null || fail "okfy-normalize"
[ -f "$CORPUS/source-map.jsonl" ] || fail "the adapter wrote no source map"
grep -q '"granularity": "whole-document"' "$CORPUS/source-map.jsonl" \
  || fail "the source map does not state its granularity"

step "init"
okfy init "$BUNDLE" --corpus "$CORPUS" --language en >/dev/null || fail "okfy init"

# --- the interview's output, supplied as data ------------------------------
# `okfy init` writes a skeleton; purpose.md and the extraction plan are the
# Purpose Interview's job, and there is no CLI verb that authors them. That is
# by design — they are judgement, not mechanism — so the script supplies them
# the way an interview would, as files.
step "purpose + plan"
cat > "$BUNDLE/meta/purpose.md" <<'EOF'
---
type: Purpose
title: Widget options desk reference
language: en
write_policy: proposals
acceptance:
  dissent: required
  min_owner_pass: 10
  min_adversarial_pass: 10
test_queries:
  - when do I sell a widget straddle
  - what closes a short widget straddle
  - why is gamma the risk in a short straddle
  - what is the exit rule at twenty-one days
  - how does a widget collar pay for itself
  - when should I use a collar instead of a straddle
  - what does rolling a collar depend on
  - what does gamma measure
  - what does delta measure
  - what is implied volatility actually quoting
adversarial_queries:
  - query: what is the rate of change of delta
    expect: covered
    concept: glossary/gamma
    why: names the definition without using the term, so it tests the lexicon rather than a string match
  - query: which structure caps my upside to pay for downside
    expect: covered
    concept: strategies/widget-collar
    why: describes the trade by its economics, using none of the concept's own words
  - query: what should I do about a short straddle into earnings
    expect: covered
    concept: strategies/widget-straddle
    why: the rule is stated as a prohibition in the source, and a retrieval that only matches entry criteria will miss it
  - query: what makes an option position blow up near expiry
    expect: covered
    concept: glossary/gamma
    why: colloquial phrasing of the second-derivative risk, the kind a user actually types
  - query: what volatility number goes into the model
    expect: covered
    concept: glossary/implied-volatility
    why: distinguishes the quoted input from realised movement, which the corpus deliberately separates
  - query: what is the widget dividend schedule
    expect: not-covered
    why: plausibly adjacent to a derivatives desk and absent from the corpus, so a confident answer would be fabrication
  - query: how do I hedge a widget position with futures
    expect: not-covered
    why: same domain, different instrument, nothing in the corpus supports it
  - query: what is the margin requirement for a short straddle
    expect: not-covered
    why: the obvious next question after the entry rule, and the corpus never addresses it
  - query: how do I price a widget swaption
    expect: not-covered
    why: an instrument the bundle has never seen, phrased with the vocabulary it does know
  - query: what is the firm's position limit on widgets
    expect: not-covered
    why: policy rather than technique; the bundle must not answer from an unrelated concept
---

Answer a widget options trader's entry, exit and risk questions from the desk's
own written material, and say plainly when the material does not cover the
question.
EOF

# The normalization block, spliced INTO the frontmatter rather than appended,
# because it carries a path only this run knows and the heredoc above is quoted
# so it cannot interpolate one. Appending would put the key in the body, where
# nothing reads it — a setting the owner believes they made, which is the exact
# failure mode this release closes elsewhere.
#
# `source_map: required` is the strict setting: an absent sidecar now blocks.
# `raw_root` is what makes the raw half of every row recomputable rather than
# merely carried.
awk -v raw="$RAW" '
  BEGIN { fm = 0; done = 0 }
  /^---$/ { fm++
            if (fm == 2 && !done) {
              print "normalization:"
              print "  source_map: required"
              print "  raw_root: " raw
              done = 1
            } }
  { print }' "$BUNDLE/meta/purpose.md" > "$BUNDLE/meta/purpose.tmp"
mv "$BUNDLE/meta/purpose.tmp" "$BUNDLE/meta/purpose.md"
grep -q "^  source_map: required$" "$BUNDLE/meta/purpose.md" \
  || fail "the normalization block was not spliced into the frontmatter"

# The sidecar belongs to the BUNDLE, not to the corpus: leaving it in $CORPUS
# would offer a .jsonl to every verb that walks the corpus tree.
mv "$CORPUS/source-map.jsonl" "$BUNDLE/meta/source-map.jsonl" \
  || fail "could not install the source map"
cat > "$BUNDLE/meta/extraction-plan.md" <<'EOF'
---
type: ExtractionPlan
title: Widget desk extraction
archetype: decision-support
archetype_version: 1
types:
  Strategy: one concept per tradeable structure
  GlossaryTerm: one concept per term of art
layout:
  Strategy: strategies/
  GlossaryTerm: glossary/
segments: []
---

Synthetic plan for the reference bundle.
EOF

okfy segment "$BUNDLE" --budget 50000 >/dev/null || fail "okfy segment"
grep -q "segment-01" "$BUNDLE/meta/extraction-plan.md" || fail "segment wrote no segments"

step "job artifact"
cat > "$WORK/worker-prompt.md" <<'EOF'
You are an extraction worker. Read only the spans assigned to you and write one
draft concept per tradeable structure and per term of art. Cite the source file
for every draft. Report what you did with every span you were given.
EOF
# The executor identity. v0.21 composes `--strict-execution` into release, so a
# job artifact that does not say WHO ran it cannot be released — the workflow
# has prescribed this for new extractions since v0.10 and the release predicate
# simply was not asking. It is an attestation, not a measurement: the core
# cannot observe a model, so it checks that the harness declared one. This
# builder's declaration is honest about what actually produced these drafts.
cat > "$WORK/execution.json" <<'EXEC'
{"model": "none (fixture text written by scripts/reference-bundle.sh)",
 "provider": "none",
 "sampling": "n/a — the drafts are committed fixture text, not generated",
 "harness_version": "reference-bundle.sh@0.21"}
EXEC
okfy job "$BUNDLE" segment-01 --prompt-file "$WORK/worker-prompt.md" \
  --execution-file "$WORK/execution.json" >/dev/null \
  || fail "okfy job"

# --- the worker pass: drafts, committed, then consolidated -----------------
# Consolidation deletes the drafts; the dissent gate recovers them from history,
# so they must be committed BEFORE they are removed or every adjudication is
# unfalsifiable. This is the shape `/okfy:extract` actually leaves behind.
step "drafts"
mkdir -p "$BUNDLE/drafts/segment-01"
cat > "$BUNDLE/drafts/segment-01/straddle-entry.md" <<'EOF'
---
type: Strategy
title: Widget Straddle
aliases: [short straddle, straddle, at-the-money straddle]
description: Sell an at-the-money straddle when widget implied volatility is extreme.
tags: [volatility, short-premium]
sources: [straddle.md]
---

## Setup

Sell the at-the-money call and the at-the-money put on the same widget expiry
when implied volatility sits in the top decile of its trailing year.

## Risk

The position is short volatility and profits when realised movement is smaller
than the premium collected.

## Exit

Fifty percent of maximum profit.
EOF
cat > "$BUNDLE/drafts/segment-01/straddle-exit.md" <<'EOF'
---
type: Strategy
title: Widget Straddle
description: Exit and prohibition rules for a short widget straddle.
tags: [volatility, short-premium]
sources: [straddle.md]
---

## Setup

The short widget straddle, once open.

## Risk

Gamma near expiry: a widget pinning the strike leaves an exploding delta and no
time to hedge it.

## Exit

Fifty percent of maximum profit or twenty-one days to expiry, whichever comes
first. Never hold the position through a scheduled earnings release.
EOF
cat > "$BUNDLE/drafts/segment-01/collar.md" <<'EOF'
---
type: Strategy
title: Widget Collar
aliases: [collar, protective collar, costless collar]
description: Hold the widget, buy a put below spot, sell a call above it.
tags: [hedging]
sources: [collar.md]
---

## Setup

Hold the widget, buy a protective put below spot, sell a call above it. The sold
call pays for the bought put, so the structure is close to costless at the price
of capping the upside.

## Risk

Long volatility on the downside and short volatility on the upside. The risk is
opportunity cost, not gamma.

## Exit

Roll the collar when either leg reaches ten percent of its original extrinsic
value.
EOF
cat > "$BUNDLE/drafts/segment-01/gamma.md" <<'EOF'
---
type: GlossaryTerm
title: Gamma
aliases: [gamma, second derivative, rate of change of delta]
description: The rate of change of delta with respect to the underlying price.
sources: [greeks.md]
---

The second derivative of the option price with respect to the underlying. It is
largest at the money and grows without bound as expiry approaches.
EOF
cat > "$BUNDLE/drafts/segment-01/delta.md" <<'EOF'
---
type: GlossaryTerm
title: Delta
aliases: [delta, first derivative, hedge ratio]
description: The rate of change of an option's price with respect to the underlying.
sources: [greeks.md]
---

The first derivative of the option price with respect to the underlying, quoted
between minus one and one.
EOF
cat > "$BUNDLE/drafts/segment-01/implied-volatility.md" <<'EOF'
---
type: GlossaryTerm
title: Implied Volatility
aliases: [implied volatility, IV, implied vol]
description: The volatility input that makes a model return the traded price.
sources: [greeks.md]
---

A quoted number, not a measurement of the future: the volatility that makes a
pricing model reproduce the option's traded price.
EOF
git -C "$BUNDLE" add -A
git -C "$BUNDLE" commit -q -m "extract: 6 drafts from segment-01"

step "ledger: the worker pass"
cat > "$WORK/spans.json" <<'EOF'
{"covered": {"straddle.md": ["drafts/segment-01/straddle-entry",
                             "drafts/segment-01/straddle-exit"],
             "collar.md": ["drafts/segment-01/collar"],
             "greeks.md": ["drafts/segment-01/gamma",
                           "drafts/segment-01/delta",
                           "drafts/segment-01/implied-volatility"]},
 "reviewed_empty": {}, "dropped": {}}
EOF
okfy ledger add "$BUNDLE" --run run-1 --segment segment-01 \
  --inputs "straddle.md,collar.md,greeks.md" \
  --prompt-version worker@1 \
  --outputs "drafts/segment-01/straddle-entry,drafts/segment-01/straddle-exit,drafts/segment-01/collar,drafts/segment-01/gamma,drafts/segment-01/delta,drafts/segment-01/implied-volatility" \
  --validation ok --job segment-01 --spans-file "$WORK/spans.json" >/dev/null \
  || fail "okfy ledger add (worker pass)"

step "consolidation"
mkdir -p "$BUNDLE/strategies" "$BUNDLE/glossary"
cat > "$BUNDLE/strategies/widget-straddle.md" <<'EOF'
---
type: Strategy
title: Widget Straddle
aliases: [short straddle, straddle, at-the-money straddle]
description: Sell an at-the-money straddle when widget implied volatility is extreme.
tags: [volatility, short-premium]
sources: [straddle.md]
---

## Setup

Sell the at-the-money call and the at-the-money put on the same widget expiry
when implied volatility sits in the top decile of its trailing year. The
position is short volatility: it profits when realised movement is smaller than
the premium collected.

## Risk

[Gamma](../glossary/gamma.md) near expiry. A widget that pins the strike leaves
the position with an exploding delta and no time to hedge it.

## Exit

Fifty percent of maximum profit or twenty-one days to expiry, whichever comes
first. Never hold a short straddle through a scheduled earnings release.
EOF
cat > "$BUNDLE/strategies/widget-collar.md" <<'EOF'
---
type: Strategy
title: Widget Collar
aliases: [collar, protective collar, costless collar]
description: Hold the widget, buy a put below spot, sell a call above it.
tags: [hedging]
sources: [collar.md]
---

## Setup

Hold the widget, buy a protective put below spot, sell a call above it. The sold
call pays for the bought put, so the structure is close to costless at the price
of capping the upside. Use it when a position must be carried through a known
event and the downside is unacceptable.

## Risk

Long volatility on the downside and short volatility on the upside. The risk is
opportunity cost, not [gamma](../glossary/gamma.md).

## Exit

Roll the collar when either leg reaches ten percent of its original extrinsic
value.
EOF
cat > "$BUNDLE/glossary/gamma.md" <<'EOF'
---
type: GlossaryTerm
title: Gamma
aliases: [gamma, second derivative, rate of change of delta]
description: The rate of change of delta with respect to the underlying price.
sources: [greeks.md]
---

The rate of change of [delta](delta.md) with respect to the underlying price:
the second derivative of the option price. It is largest at the money and grows
without bound as expiry approaches.
EOF
cat > "$BUNDLE/glossary/delta.md" <<'EOF'
---
type: GlossaryTerm
title: Delta
aliases: [delta, first derivative, hedge ratio]
description: The rate of change of an option's price with respect to the underlying.
sources: [greeks.md]
---

The first derivative of the option price with respect to the underlying price,
quoted between minus one and one.
EOF
cat > "$BUNDLE/glossary/implied-volatility.md" <<'EOF'
---
type: GlossaryTerm
title: Implied Volatility
aliases: [implied volatility, IV, implied vol]
description: The volatility input that makes a model return the traded price.
sources: [greeks.md]
---

The volatility input that makes a pricing model reproduce an option's traded
price. It is a quoted number, not a measurement of the future.
EOF
rm -rf "$BUNDLE/drafts"
git -C "$BUNDLE" add -A
git -C "$BUNDLE" commit -q -m "consolidate: 5 concepts from 6 drafts"

okfy ledger add "$BUNDLE" --run run-1 --segment consolidation \
  --inputs "straddle.md,collar.md,greeks.md" \
  --prompt-version consolidate@1 \
  --outputs "strategies/widget-straddle,strategies/widget-collar,glossary/gamma,glossary/delta,glossary/implied-volatility" \
  --validation ok \
  --merge-map "drafts/segment-01/straddle-entry=strategies/widget-straddle,drafts/segment-01/straddle-exit=strategies/widget-straddle,drafts/segment-01/collar=strategies/widget-collar,drafts/segment-01/gamma=glossary/gamma,drafts/segment-01/delta=glossary/delta,drafts/segment-01/implied-volatility=glossary/implied-volatility" \
  >/dev/null || fail "okfy ledger add (consolidation)"

step "dissent"
okfy dissent add "$BUNDLE" --run run-1 --group strategies/widget-straddle \
  --draft drafts/segment-01/straddle-entry \
  --draft drafts/segment-01/straddle-exit \
  --claim "entry criteria and the exit/prohibition rules could be two concepts" \
  --anchor "straddle.md#L1-L14" \
  --verdict no-schism \
  --overruled-because "both drafts state conditions on one position's lifecycle; splitting them would make the exit rule unreachable from the entry question" \
  >/dev/null || fail "okfy dissent add"

step "segment done"
okfy segment-status "$BUNDLE" segment-01 done >/dev/null || fail "okfy segment-status"

# --- the lexicon: the retrieval contract ------------------------------------
# `not-covered` rows are how a bundle says "I do not answer this" instead of
# returning its best-scoring irrelevant concept. They are also the ONLY thing
# that can make an adversarial `not-covered` expectation come out `met`, so
# without them the reference bundle would need ten owner passes over five unmet
# expectations — a rubber stamp, and precisely the failure the adversarial suite
# exists to expose. Note that these rows are PHRASE-KEYED: they fire on the term
# as written, and a synonym walks straight past them.
step "lexicon"
cat > "$BUNDLE/meta/lexicon.md" <<'EOF'
---
type: Lexicon
title: Widget desk lexicon
rows:
  - term: gamma
    status: accepted
    maps_to: [glossary/gamma]
    canonical_terms: [gamma, delta]
  - term: implied volatility
    status: accepted
    maps_to: [glossary/implied-volatility]
    canonical_terms: [implied volatility]
  - term: collar
    status: accepted
    maps_to: [strategies/widget-collar]
    canonical_terms: [collar]
  - term: straddle
    status: accepted
    maps_to: [strategies/widget-straddle]
    canonical_terms: [straddle]
  - term: dividend
    status: not-covered
    note: the desk material covers option structure and greeks, never the widget's cash flows
  - term: futures
    status: not-covered
    note: same desk, different instrument; nothing here supports a futures hedge
  - term: margin
    status: not-covered
    note: margin is set by the clearer and is not in this material
  - term: swaption
    status: not-covered
    note: an instrument this bundle has never seen
  - term: position limit
    status: not-covered
    note: firm policy rather than trading technique
---

Terms this bundle answers to, and the terms it explicitly does not.
EOF

step "index + package"
okfy index "$BUNDLE" >/dev/null || fail "okfy index"
okfy package "$BUNDLE" >/dev/null || fail "okfy package"

# --- L3: the purpose-fitness artifact --------------------------------------
# `okfy sample` picks the concepts to review deterministically and reports the
# seed it selected under; the review itself is judgement and has no CLI verb, so
# the verdicts are written here as fixture data like the eval verdicts below.
# The seed is read back from the tool rather than hardcoded: a recorded seed that
# does not match the live one makes validate skip the replay check silently, so
# hardcoding it would quietly delete the strongest thing this artifact proves.
step "purpose-fitness (L3)"
okfy sample "$BUNDLE" > "$WORK/sample.json" || fail "okfy sample"
SEED="$(grep -o '"seed": "[^"]*"' "$WORK/sample.json" | cut -d'"' -f4)"
SELECTOR="$(grep -o '"selector_version": [0-9]*' "$WORK/sample.json" \
            | grep -o '[0-9]*$')"
[ -n "$SEED" ] && [ -n "$SELECTOR" ] || fail "could not read seed from okfy sample"
# v0.21: the artifact also records WHAT was reviewed and WHAT AGAINST. Both are
# read back from `okfy sample` for the same reason the seed is — recomputing a
# digest here would restate a definition that exists once in core, and a
# restated definition is one that drifts. `E_QUALITY_UNPINNED` is what an
# artifact without them gets, and `E_QUALITY_DRIFT` is what one whose concepts
# moved afterwards gets.
FPRINT="$(grep -o '"sampled_fingerprint": "[^"]*"' "$WORK/sample.json" \
          | cut -d'"' -f4)"
CHECKS="$(grep -o '"checks_digest": "[^"]*"' "$WORK/sample.json" | cut -d'"' -f4)"
[ -n "$FPRINT" ] && [ -n "$CHECKS" ] \
  || fail "okfy sample did not report the L3 pins (sampled_fingerprint / checks_digest)"
# Five concepts, two archetype checks each. If `okfy sample` ever selects a
# different set these rows go missing and validate says so by name.
for id in glossary/delta glossary/gamma glossary/implied-volatility \
          strategies/widget-collar strategies/widget-straddle; do
  grep -q "\"$id\"" "$WORK/sample.json" \
    || fail "sample no longer selects $id — the L3 rows below would be stale"
done
{
  echo "---"
  echo "type: PurposeFitness"
  echo "title: Widget desk purpose fitness"
  echo "date: $(date -u +%Y-%m-%d)"
  echo "prompt_version: l3@1"
  echo "selector_version: $SELECTOR"
  echo "seed: $SEED"
  echo "sampled_fingerprint: $FPRINT"
  echo "checks_digest: $CHECKS"
  echo "fraction: 0.1"
  echo "minimum: 20"
  echo "sampled:"
  echo "  - glossary/delta"
  echo "  - glossary/gamma"
  echo "  - glossary/implied-volatility"
  echo "  - strategies/widget-collar"
  echo "  - strategies/widget-straddle"
  echo "rows:"
  cat <<'ROWS'
  - concept_id: glossary/delta
    check_id: standalone-content
    verdict: pass
    evidence: states the derivative and its quoted range without referring out
  - concept_id: glossary/delta
    check_id: decision-ready
    verdict: n/a
    evidence: GlossaryTerm, not a Strategy or Playbook
  - concept_id: glossary/gamma
    check_id: standalone-content
    verdict: pass
    evidence: defines the term and its behaviour near expiry in the concept body
  - concept_id: glossary/gamma
    check_id: decision-ready
    verdict: n/a
    evidence: GlossaryTerm, not a Strategy or Playbook
  - concept_id: glossary/implied-volatility
    check_id: standalone-content
    verdict: pass
    evidence: separates the quoted input from realised movement without a pointer
  - concept_id: glossary/implied-volatility
    check_id: decision-ready
    verdict: n/a
    evidence: GlossaryTerm, not a Strategy or Playbook
  - concept_id: strategies/widget-collar
    check_id: standalone-content
    verdict: pass
    evidence: structure, risk and roll rule are all present in the body
  - concept_id: strategies/widget-collar
    check_id: decision-ready
    verdict: pass
    evidence: roll trigger is a number (ten percent of original extrinsic value)
  - concept_id: strategies/widget-straddle
    check_id: standalone-content
    verdict: pass
    evidence: entry, risk and exit stated without reference to the source file
  - concept_id: strategies/widget-straddle
    check_id: decision-ready
    verdict: pass
    evidence: entry is a decile threshold, exit is fifty percent or twenty-one days
ROWS
  echo "---"
  echo
  echo "Synthetic L3 review for the reference bundle."
} > "$BUNDLE/meta/purpose-fitness.md"

# --- acceptance: both suites, ten owner verdicts each -----------------------
# Run LAST. The retrieval fingerprint covers the index, the lexicon rows and the
# test queries, so a run recorded before any of them settles is stale evidence
# the moment they move — which is exactly what the audit caught on real bundles.
step "eval: acceptance suite"
okfy eval run "$BUNDLE" -n 5 >/dev/null || fail "okfy eval run (acceptance)"
for i in 0 1 2 3 4 5 6 7 8 9; do
  okfy eval verdict "$BUNDLE" latest "$i" pass --owner \
    --note "synthetic fixture verdict, not a record of anyone's judgement" \
    >/dev/null || fail "okfy eval verdict (acceptance $i)"
done

step "eval: adversarial suite"
okfy eval run "$BUNDLE" -n 5 --suite adversarial >/dev/null \
  || fail "okfy eval run (adversarial)"
for i in 0 1 2 3 4 5 6 7 8 9; do
  okfy eval verdict "$BUNDLE" latest "$i" pass --owner --suite adversarial \
    --note "synthetic fixture verdict, not a record of anyone's judgement" \
    >/dev/null || fail "okfy eval verdict (adversarial $i)"
done

step "release-check"
set +e
okfy release-check "$BUNDLE" > "$WORK/release.json"
RC=$?
set -e
cat "$WORK/release.json"
[ "$RC" -eq 0 ] || fail "release-check exited $RC"
grep -q '"ok": true' "$WORK/release.json" || fail "release-check did not return ok"

# --- the gate is not vacuous -----------------------------------------------
# A green run proves the contract is satisfiable. It does not prove the gate can
# still say no — a release-check that returned ok for anything would pass every
# line above. So break the copy in one place the contract names, and require the
# red. `awk` rather than an okfy verb because there is deliberately no CLI for
# un-recording an owner verdict: verdicts are append-only evidence.
# --- the deliberate breaks --------------------------------------------------
# A green artifact proves the contract is SATISFIABLE. It does not prove the
# contract is doing anything, and a builder that only ever reports green would
# keep reporting green after a gate was accidentally deleted. So every gate this
# bundle relies on is broken on purpose, on a copy, and required to go red with
# its OWN code — never merely a nonzero exit, because `release_check` composes a
# dozen gates into one boolean and redness proves nothing about any single one.
#
# Each break also asserts it EDITED SOMETHING first. A mutation that silently
# matched nothing (a BSD/GNU sed difference, a field that moved) would leave the
# assertion below passing over an untouched file — which is the exact shape of
# false evidence this whole release is about.
break_setup() {
  BROKEN="$WORK/broken-okf"
  rm -rf "$BROKEN"
  cp -R "$BUNDLE" "$BROKEN"
}

# $1 = file that must differ, $2 = expected code, $3 = what was broken
break_expect() {
  cmp -s "$BUNDLE/$1" "$BROKEN/$1" \
    && fail "the break edited nothing ($3) — the assertion below would be vacuous"
  set +e
  okfy release-check "$BROKEN" > "$WORK/broken.json"
  BRC=$?
  set -e
  cat "$WORK/broken.json"
  [ "$BRC" -ne 0 ] || fail "$3 and release-check still exited 0"
  grep -q '"ok": false' "$WORK/broken.json" || fail "$3 and release-check still returned ok"
  grep -q "$2" "$WORK/broken.json" || fail "$3 was not reported as $2"
  rm -rf "$BROKEN"
}

step "break 1: one owner verdict removed"
break_setup
awk 'BEGIN { done = 0 }
     { if (!done && sub(/"owner_verdict": "pass"/, "\"owner_verdict\": null")) done = 1
       print }' "$BUNDLE/meta/eval.json" > "$BROKEN/meta/eval.json"
break_expect meta/eval.json E_REL_EVAL_POLICY "one owner verdict removed"

# The gate the audit's first P0 asked for. The fingerprint still matches — the
# environment did not move — so the only thing that changed is the record the
# owner judged, which is precisely the substitution that used to release clean.
step "break 2: a recorded eval result edited after the verdict"
break_setup
awk 'BEGIN { done = 0 }
     { if (!done && sub(/"expanded_query": "/, "\"expanded_query\": \"fabricated ")) done = 1
       print }' "$BUNDLE/meta/eval.json" > "$BROKEN/meta/eval.json"
break_expect meta/eval.json E_REL_EVAL_REPLAY "an eval result edited after the owner verdict"

# The audit's second P0. A stale seed used to turn the L3 check OFF.
step "break 3: the L3 review's seed made stale"
break_setup
awk 'BEGIN { done = 0 }
     { if (!done && sub(/^seed: .*$/, "seed: definitely-not-current")) done = 1
       print }' "$BUNDLE/meta/purpose-fitness.md" > "$BROKEN/meta/purpose-fitness.md"
break_expect meta/purpose-fitness.md E_REL_VALIDATE "the L3 seed made stale"
# ...and specifically as staleness, not as some other validation error.
break_setup
awk 'BEGIN { done = 0 }
     { if (!done && sub(/^seed: .*$/, "seed: definitely-not-current")) done = 1
       print }' "$BUNDLE/meta/purpose-fitness.md" > "$BROKEN/meta/purpose-fitness.md"
okfy validate "$BROKEN" --strict-quality > "$WORK/broken-validate.txt" 2>&1 || true
grep -q "E_QUALITY_STALE" "$WORK/broken-validate.txt" \
  || { cat "$WORK/broken-validate.txt"; fail "a stale L3 seed was not reported as E_QUALITY_STALE"; }
rm -rf "$BROKEN"

# The executor identity, rebuilt WITHOUT it rather than edited: `job_digest`
# covers the whole artifact, so hand-editing the execution block out would fail
# as a tampered digest (`E_PROV_JOB_DIGEST`) — a different defect with a
# different code, and the assertion would pass while proving nothing about
# executor identity. Re-running the verb produces a digest-consistent artifact
# that simply has no executor. The ledger row's recorded digest then no longer
# matches, which is honest and expected: the artifact really did change.
step "break 4: the executor identity removed"
break_setup
okfy job "$BROKEN" segment-01 --prompt-file "$WORK/worker-prompt.md" >/dev/null \
  || fail "okfy job (rebuild without execution)"
okfy validate "$BROKEN" --strict-execution > "$WORK/broken-exec.txt" 2>&1 || true
grep -q "E_EXEC_MISSING" "$WORK/broken-exec.txt" \
  || { cat "$WORK/broken-exec.txt"; fail "a job with no executor identity was not reported"; }
break_expect meta/jobs/segment-01.json E_REL_VALIDATE "the executor identity removed"

# --- the v0.22 gates ---------------------------------------------------------
# Six more breaks, one per trust boundary this release closed. Same rule as
# above: each names its OWN code, and each proves it edited something first.
#
# These are also the answer to the audit's request for a published black-box
# trust-boundary suite. The published repository ships src without tests by the
# owner's publication policy, so `pytest` cannot be the external check — but
# this script IS published, runs against an installed wheel, and every assertion
# below is one an outside reader can run and watch fail.

# A verdict outside the enum, not a verdict REMOVED. Break 1 sets one to null,
# which is legal and fails on policy; this sets one to a value no era of this
# tool could write, which fails on the record's schema. Different defect,
# different code — and before v0.22 a malformed nested record did not produce a
# finding at all: it raised AttributeError out of release-check.
step "break 5: an eval verdict outside the enum"
break_setup
awk 'BEGIN { done = 0 }
     { if (!done && sub(/"owner_verdict": "pass"/, "\"owner_verdict\": \"maybe\"")) done = 1
       print }' "$BUNDLE/meta/eval.json" > "$BROKEN/meta/eval.json"
break_expect meta/eval.json E_REL_EVAL_INVALID "an eval verdict outside the enum"

# The audit's second P1. The recorded CRITERION, not the recorded answer: the
# retrieval output still replays identically and the fingerprint still matches,
# so the only thing that moved is what the owner was shown they were judging.
step "break 6: an adversarial expectation edited after the owner verdict"
break_setup
awk 'BEGIN { done = 0 }
     { if (!done && sub(/"expect": "covered"/, "\"expect\": \"not-covered\"")) done = 1
       print }' "$BUNDLE/meta/eval.json" > "$BROKEN/meta/eval.json"
break_expect meta/eval.json E_REL_ADVERSARIAL_REPLAY "an adversarial expectation edited after the verdict"

# The audit's third P1, and the sharpest one: this typo used to make the check
# that would have caught it UNREACHABLE. `requierd` is not `required`, so the
# map became optional, so the sidecar's absence stopped being a defect, so
# release returned before looking at anything.
step "break 7: source_map misspelled"
break_setup
awk 'BEGIN { done = 0 }
     { if (!done && sub(/^  source_map: required$/, "  source_map: requierd")) done = 1
       print }' "$BUNDLE/meta/purpose.md" > "$BROKEN/meta/purpose.md"
break_expect meta/purpose.md E_NORMALIZATION_VALUE "source_map misspelled"

# Declared and dead. Before v0.22 this read exactly like "no raw_root declared",
# so the bundle stayed green and the advice told the owner to declare the root
# already sitting in their purpose.md.
step "break 8: raw_root declared but absent"
break_setup
awk 'BEGIN { done = 0 }
     { if (!done && sub(/^  raw_root: .*$/, "  raw_root: /nonexistent/raw/tree")) done = 1
       print }' "$BUNDLE/meta/purpose.md" > "$BROKEN/meta/purpose.md"
break_expect meta/purpose.md E_NORMALIZATION_ROOT "raw_root declared but absent"

# The audit's fourth P1, from the other side. v0.21 reported a FALSE coverage
# failure for any anchored citation; v0.22 resolves the anchor to lines and
# requires containment — so a citation genuinely outside every mapped interval
# must still be caught. Breaking it in this direction is the guard on the naive
# fix: stripping the anchor would let a row covering L1-L2 vouch for L900-L920.
step "break 9: a concept cites lines no row maps"
break_setup
awk 'BEGIN { done = 0 }
     { if (!done && sub(/^sources: \[greeks\.md\]$/, "sources: [greeks.md#L900-L920]")) done = 1
       print }' "$BUNDLE/glossary/gamma.md" > "$BROKEN/glossary/gamma.md"
break_expect glossary/gamma.md E_SOURCEMAP_COVERAGE "a concept cites lines no row maps"

# An unstated granularity is a row making no claim about pages. An UNKNOWN one
# still reads as a claim, and nothing understands it.
step "break 10: a source-map row claims an unknown granularity"
break_setup
awk 'BEGIN { done = 0 }
     { if (!done && sub(/"granularity": "whole-document"/, "\"granularity\": \"page-and-bbox\"")) done = 1
       print }' "$BUNDLE/meta/source-map.jsonl" > "$BROKEN/meta/source-map.jsonl"
break_expect meta/source-map.jsonl E_SOURCEMAP_FIELD "a source-map row claims an unknown granularity"

# --- the positive control ----------------------------------------------------
# Ten breaks all went red. That is only evidence if the bundle they were made
# from is still green: a permanently red artifact would satisfy every assertion
# above while proving nothing. Each break worked on a copy, so this re-checks
# the original and requires the same answer it gave before any of them ran.
step "positive control: the unbroken bundle is still green"
set +e
okfy release-check "$BUNDLE" > "$WORK/control.json"
CRC=$?
set -e
[ "$CRC" -eq 0 ] || fail "the unbroken bundle no longer passes (exit $CRC)"
grep -q '"ok": true' "$WORK/control.json" \
  || { cat "$WORK/control.json"; fail "the unbroken bundle no longer returns ok"; }
grep -q '"state": "verified"' "$WORK/control.json" \
  || grep -q 'raw-verified' "$WORK/control.json" \
  || fail "the source map is no longer verified on the green path"
echo "  the bundle every break was made from still returns ok: true"

printf '\nREFERENCE BUNDLE OK: %s (10 deliberate breaks, all red with their own code)\n' "$BUNDLE"
