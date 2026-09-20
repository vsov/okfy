#!/usr/bin/env bash
# End-to-end smoke test against INSTALLED `okfy` and `okfy-mcp` commands.
#
# This is the check the published repository can actually run: its export ships
# src, not tests, so CI there cannot execute the unit suite. What it can prove is
# the part unit tests never touch — that the wheels build, install clean on a
# given OS and Python, and that the console scripts work end to end afterwards.
# Packaging breaks (a module missing from the wheel, an archetype data file not
# included, a console-script entry point typo) are invisible to a suite run from
# the source tree and fatal to a user running `uv tool install`.
#
# Builds its bundle in a temp directory: never touches a real bundle, and the
# corpus is synthetic, per the project's standing rule for fixtures.
set -euo pipefail

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
CORPUS="$WORK/corpus"
BUNDLE="$WORK/widget-okf"
mkdir -p "$CORPUS"

# A fresh CI runner has no git identity, and `okfy init` records the bundle's
# first commit. A real user has one configured; supply it here rather than
# skipping the verb, and note that the CLI now explains this failure instead of
# raising a bare exit-128 traceback (which is how CI found it).
export GIT_AUTHOR_NAME="okfy smoke" GIT_AUTHOR_EMAIL="smoke@example.invalid"
export GIT_COMMITTER_NAME="okfy smoke" GIT_COMMITTER_EMAIL="smoke@example.invalid"

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
step() { printf '\n=== %s\n' "$1"; }

cat > "$CORPUS/straddle.md" <<'EOF'
# Widget straddle

Sell an at-the-money call and put on a widget when implied volatility spikes.
Exit at fifty percent of maximum profit. The dominant risk is gamma.
EOF
cat > "$CORPUS/gamma.md" <<'EOF'
# Gamma

Gamma is the rate of change of delta with respect to the underlying price.
EOF

step "version and help"
okfy --help >/dev/null || fail "okfy --help"
python -c "import okfy, sys; sys.stdout.write(okfy.__version__)" >/dev/null \
  || fail "core not importable"

step "init + survey + segment"
okfy init "$BUNDLE" --corpus "$CORPUS" --language en >/dev/null \
  || fail "okfy init"
okfy survey "$CORPUS" > "$WORK/survey.json" || fail "okfy survey"
python - "$WORK/survey.json" <<'PY' || fail "survey produced no files"
import json, sys
d = json.load(open(sys.argv[1]))
assert d.get("files"), d
PY
# `okfy init` writes the skeleton; the extraction plan is the interview's job,
# so the smoke test supplies a minimal one rather than skipping the verb
cat > "$BUNDLE/meta/extraction-plan.md" <<'EOF'
---
type: ExtractionPlan
title: Smoke plan
archetype: decision-support
archetype_version: 1
types:
  Strategy: one concept per setup
  GlossaryTerm: one concept per term
layout:
  Strategy: strategies/
  GlossaryTerm: glossary/
segments: []
---

Synthetic plan for the smoke test.
EOF
okfy segment "$BUNDLE" --budget 50000 >/dev/null || fail "okfy segment"
grep -q "^- id: segment-01" "$BUNDLE/meta/extraction-plan.md" \
  || grep -q "segment-01" "$BUNDLE/meta/extraction-plan.md" \
  || fail "segment wrote no segments"

step "archetype data files survived packaging"
python - <<'PY' || fail "archetype templates missing from the installed package"
from okfy.archetype import archetypes_root, load_archetype
for name in ("decision-support", "codebase-map", "api-reference",
             "research-synthesis", "regulatory-reference"):
    a = load_archetype(name)
    assert a.canonical_types, name
    tmpl = a.root / a.consumption_protocol
    assert tmpl.is_file(), f"{name}: {tmpl} not installed"
print("archetypes ok:", archetypes_root().name)
PY

step "concepts + index + query"
mkdir -p "$BUNDLE/strategies" "$BUNDLE/glossary"
cat > "$BUNDLE/strategies/widget-straddle.md" <<'EOF'
---
type: Strategy
title: Widget Straddle
description: Sell a straddle on widget IV spikes.
tags: [volatility]
sources: [straddle.md]
---

## Setup

Sell the at-the-money call and put.

## Risk

Gamma near expiry.

## Exit

Fifty percent of maximum profit. See [Gamma](../glossary/gamma.md).
EOF
cat > "$BUNDLE/glossary/gamma.md" <<'EOF'
---
type: GlossaryTerm
title: Gamma
description: Rate of change of delta.
sources: [gamma.md]
---

The second derivative of price.
EOF
okfy index "$BUNDLE" >/dev/null || fail "okfy index"
okfy query "$BUNDLE" "when do I sell a widget straddle" > "$WORK/q.json" \
  || fail "okfy query"
python - "$WORK/q.json" <<'PY' || fail "query returned no hits"
import json, sys
# `okfy query` prints the results LIST on stdout; expansion and notes go to stderr
hits = json.load(open(sys.argv[1]))
ids = [h["id"] for h in hits]
assert "strategies/widget-straddle" in ids, ids
PY
okfy show "$BUNDLE" strategies/widget-straddle >/dev/null || fail "okfy show"
okfy links "$BUNDLE" strategies/widget-straddle >/dev/null || fail "okfy links"

step "codes + fresh"
okfy codes >/dev/null || fail "okfy codes"
okfy show "$BUNDLE" glossary/gamma >/dev/null || fail "okfy show (re-check)"
GAMMA_SHA=$(python - "$BUNDLE" <<'PY'
import hashlib, sys
from pathlib import Path
p = Path(sys.argv[1]) / "glossary" / "gamma.md"
print(hashlib.sha256(p.read_bytes()).hexdigest())
PY
) || fail "could not compute gamma.md's sha256"
okfy fresh "$BUNDLE" --ids "glossary/gamma=$GAMMA_SHA" --json > "$WORK/fresh.json" \
  || fail "okfy fresh"
grep -q '"unchanged"' "$WORK/fresh.json" \
  || { cat "$WORK/fresh.json"; fail "okfy fresh did not report the concept unchanged"; }

step "propose --action flag / gap + review list"
okfy propose "$BUNDLE" --target glossary/gamma --action flag --type out-of-date \
  --as claude-code/1.0 --note "smoke: flagged for review" > "$WORK/flag.json" \
  || fail "okfy propose --action flag"
okfy propose "$BUNDLE" --action gap --query "smoke: what is theta" \
  --as claude-code/1.0 --note "smoke: unanswered question" > "$WORK/gap.json" \
  || fail "okfy propose --action gap"
okfy review list "$BUNDLE" > "$WORK/review-list.json" || fail "okfy review list"
python - "$WORK/review-list.json" <<'PY' || fail "review list did not show both proposals"
import json, sys
d = json.load(open(sys.argv[1]))
proposals = d if isinstance(d, list) else d.get("proposals", d)
actions = [p.get("action") for p in proposals]
assert "flag" in actions and "gap" in actions, actions
PY

step "validate + package + release-check reports (not passes)"
okfy validate "$BUNDLE" --quiet || true      # a two-concept stub is not release-ready
okfy package "$BUNDLE" >/dev/null || fail "okfy package"
[ -f "$BUNDLE/AGENTS.md" ] || fail "package wrote no AGENTS.md"
[ -f "$BUNDLE/index.md" ] || fail "package wrote no index.md"
# release-check MUST exit non-zero here and MUST still be valid JSON: the gate is
# fail-closed, and a crash instead of a verdict is the defect audit round 10 found
set +e
okfy release-check "$BUNDLE" > "$WORK/rel.json" 2>"$WORK/rel.err"
RC=$?
set -e
[ "$RC" -eq 1 ] || fail "release-check on a stub exited $RC, expected 1"
python - "$WORK/rel.json" <<'PY' || fail "release-check did not emit a verdict"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["ok"] is False and d["problems"], d
PY

step "eval refuses a degenerate invocation"
set +e
okfy eval run "$BUNDLE" -n 0 >/dev/null 2>&1
RC=$?
set -e
[ "$RC" -ne 0 ] || fail "eval run -n 0 was accepted"

step "normalize adapter end to end"
# The third published package. It was in the export from the day it existed and
# in no CI job, so nothing here ever proved its wheel installs or its console
# script runs. Convert a raw tree, then hand the sidecar to `okfy sourcemap` —
# the two halves are deliberately in different packages (core validates with the
# standard library; the adapter may depend on whatever a converter needs), and
# this is the only place they meet.
RAW="$WORK/raw"
NCORPUS="$WORK/ncorpus"
mkdir -p "$RAW"
printf '# Handbook\n\nSection one of the widget handbook.\n' > "$RAW/handbook.md"
printf 'Loose operator notes, not markdown.\n' > "$RAW/notes.txt"
okfy-normalize "$RAW" "$NCORPUS" > "$WORK/norm.json" || fail "okfy-normalize"
grep -q '"converted": 2' "$WORK/norm.json" || fail "normalize converted the wrong count"
grep -q '"skipped": \[\]' "$WORK/norm.json" || fail "normalize skipped a file it handles"
[ -f "$NCORPUS/source-map.jsonl" ] || fail "normalize wrote no sidecar"

# A bundle over the NORMALIZED corpus, carrying the sidecar where core reads it.
NBUNDLE="$WORK/normalized-okf"
okfy init "$NBUNDLE" --corpus "$NCORPUS" --language en >/dev/null \
  || fail "okfy init (normalized corpus)"
cp "$NCORPUS/source-map.jsonl" "$NBUNDLE/meta/source-map.jsonl"
okfy sourcemap --json "$NBUNDLE" > "$WORK/sourcemap.json" || fail "okfy sourcemap"
grep -q '"ok": true' "$WORK/sourcemap.json" \
  || { cat "$WORK/sourcemap.json"; fail "sourcemap rejected the adapter's own output"; }
# The TEXT half. v0.21 stopped calling this `verified`: the raw document is not
# in the bundle, so its hash was never recomputed, and reporting it as verified
# is what an audit walked a raw hash of all zeros through. `raw-unverified` is
# the honest name for a row whose span text matched and whose origin was not
# checked.
grep -q '"state": "raw-unverified"' "$WORK/sourcemap.json" \
  || { cat "$WORK/sourcemap.json"; fail "sourcemap did not report the raw half as unchecked"; }
grep -q '"text_verified": 2' "$WORK/sourcemap.json" \
  || fail "sourcemap could not recompute the span hashes — the join is unverified"
grep -q '"raw_verified": 0' "$WORK/sourcemap.json" \
  || fail "sourcemap claimed a raw verification it could not have performed"

# ...and now the WHOLE chain, because here the raw tree still exists. Declaring
# `normalization.raw_root` makes core hash the raw bytes for real, and only then
# is a row `verified` — both halves recomputed. This is the one place outside the
# adapter's own tests where the full provenance chain can be closed, and before
# v0.21 there was no way to close it anywhere.
# The declaration goes in the frontmatter, right after its opening `---`.
awk -v raw="$RAW" 'NR==1 { print; print "normalization:"; print "  raw_root: " raw; next }
                   { print }' \
  "$NBUNDLE/meta/purpose.md" > "$WORK/purpose-declared.md"
grep -q '^  raw_root: ' "$WORK/purpose-declared.md" \
  || fail "could not declare normalization.raw_root — the assertions below would be vacuous"
mv "$WORK/purpose-declared.md" "$NBUNDLE/meta/purpose.md"
okfy sourcemap --json "$NBUNDLE" > "$WORK/sourcemap-full.json" || fail "okfy sourcemap (raw_root)"
grep -q '"state": "verified"' "$WORK/sourcemap-full.json" \
  || { cat "$WORK/sourcemap-full.json"; fail "declaring raw_root did not close the chain"; }
grep -q '"raw_verified": 2' "$WORK/sourcemap-full.json" \
  || fail "raw_root was declared and the raw bytes were still not hashed"

# The negative for the raw half: change the raw document after conversion and
# the mapping must be refused. Without this the check above proves only that
# something was computed, not that it compares.
printf 'totally different raw bytes\n' > "$RAW/handbook.md"
set +e
okfy sourcemap --json "$NBUNDLE" > "$WORK/sourcemap-rawdrift.json"
RDRC=$?
set -e
[ "$RDRC" -ne 0 ] || fail "sourcemap accepted a raw document that changed after conversion"
grep -q 'E_SOURCEMAP_RAW_DRIFT' "$WORK/sourcemap-rawdrift.json" \
  || { cat "$WORK/sourcemap-rawdrift.json"; fail "raw drift was not reported as raw drift"; }
# Put it back, so the text-drift assertion below is about the NORMALIZED file.
printf '# Handbook\n\nSection one of the widget handbook.\n' > "$RAW/handbook.md"

# ...and the negative: a sidecar the corpus contradicts must NOT pass. A verifier
# that has only ever seen valid input is not known to verify anything.
# Rewrite INSIDE the mapped span. Appending past its last line is deliberately
# not drift — the row pins the span's text, not the file's — and using an append
# here would have made this assertion fail for the right reason and the wrong one.
printf '# Handbook\n\nSection one, silently rewritten after conversion.\n' \
  > "$NCORPUS/handbook.md"
set +e
okfy sourcemap --json "$NBUNDLE" > "$WORK/sourcemap-drift.json"
SMRC=$?
set -e
[ "$SMRC" -ne 0 ] || fail "sourcemap accepted a corpus that moved after conversion"
grep -q 'E_SOURCEMAP_TEXT_DRIFT' "$WORK/sourcemap-drift.json" \
  || { cat "$WORK/sourcemap-drift.json"; fail "drift was not reported as drift"; }

step "reference bundle: the release contract, end to end"
# The whole point of shipping this script: the unit suite is not in the export,
# so this is the only evidence public CI can produce that the release contract is
# satisfiable at all. It builds a synthetic bundle, requires ok=true, and breaks
# it on purpose to require the red.
bash "$(dirname "$0")/reference-bundle.sh" "$WORK/reference" > "$WORK/reference.log" 2>&1 \
  || { tail -40 "$WORK/reference.log"; fail "reference bundle did not go green"; }
grep -q 'REFERENCE BUNDLE OK' "$WORK/reference.log" \
  || fail "reference-bundle.sh exited 0 without reporting OK"

step "mcp adapter"
okfy-mcp --help >/dev/null 2>&1 || python -c "import okfy_mcp" \
  || fail "okfy-mcp not installed"
okfy-mcp config "$BUNDLE" --client claude-code > "$WORK/mcp.json" \
  || fail "okfy-mcp config"
python - "$WORK/mcp.json" <<'PY' || fail "mcp snippet malformed"
import json, sys
d = json.load(open(sys.argv[1]))
assert list(d["mcpServers"]), d
PY

printf '\nSMOKE OK\n'
