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
