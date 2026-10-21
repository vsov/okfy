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
# a step below chmods a directory to 000; make sure rm can always get in
trap 'chmod -R u+rwx "$WORK" 2>/dev/null || true; rm -rf "$WORK"' EXIT
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

step "direct-write lane: okfy write, and okfy_write falling back to a proposal"
# The lane is opt-in per concept type. Declare it for GlossaryTerm only, then:
# a write against stale bytes is refused BY CODE, the same write against the
# bytes that were read lands at once, and a Strategy sent through the same MCP
# tool is filed under proposals/ with its concept file untouched.
awk 'NR==1 { print; print "direct:"; print "  types: [GlossaryTerm]"; next }
     { print }' "$BUNDLE/meta/purpose.md" > "$WORK/purpose-direct.md"
grep -q '^  types: \[GlossaryTerm\]' "$WORK/purpose-direct.md" \
  || fail "could not declare direct.types — the assertions below would be vacuous"
mv "$WORK/purpose-direct.md" "$BUNDLE/meta/purpose.md"
# A direct write passes the same archetype schema `review accept` applies, and a
# GlossaryTerm requires `aliases` — so the new text carries one.
cat > "$WORK/gamma-new.md" <<'EOF'
---
type: GlossaryTerm
title: Gamma
description: Rate of change of delta.
aliases: [gamma risk]
sources: [gamma.md]
---

Second derivative of price with respect to the underlying.
EOF
set +e
okfy write "$BUNDLE" glossary/gamma --as claude-code/1.0 --from "$WORK/gamma-new.md" \
  --expect 0000000000000000000000000000000000000000000000000000000000000000 \
  >/dev/null 2>"$WORK/write-stale.err"
WRC=$?
set -e
[ "$WRC" -ne 0 ] || fail "okfy write landed against bytes nobody read"
grep -q 'E_WRITE_BASE_MOVED' "$WORK/write-stale.err" \
  || { cat "$WORK/write-stale.err"; fail "a stale write was not refused as E_WRITE_BASE_MOVED"; }
okfy write "$BUNDLE" glossary/gamma --as claude-code/1.0 --from "$WORK/gamma-new.md" \
  --expect "$GAMMA_SHA" > "$WORK/write.json" || fail "okfy write"
grep -q '"channel": "direct"' "$WORK/write.json" \
  || { cat "$WORK/write.json"; fail "okfy write did not report the direct channel"; }
grep -q 'with respect to the underlying' "$BUNDLE/glossary/gamma.md" \
  || fail "okfy write reported success and the concept file did not change"
grep -q '"origin": "agent-direct"' "$BUNDLE/meta/memory.jsonl" \
  || fail "a direct write left no ledger row"
python - "$BUNDLE" <<'PY' || fail "okfy_write did not route by concept type"
import sys
from pathlib import Path
from okfy_mcp.handlers import h_show, h_write
from okfy_mcp.resolve import Target
t = Target(sys.argv[1])
strategy = Path(sys.argv[1]) / "strategies" / "widget-straddle.md"
before = strategy.read_bytes()
shown = h_show(t, concept_id="strategies/widget-straddle")
r = h_write(t, "strategies/widget-straddle", "claude-code/1.0", shown["sha256"],
            patch=[{"old": "Gamma near expiry.", "new": "Gamma near expiry; vega on entry."}])
assert r.get("persisted") is True and r.get("channel") == "proposal", r
assert (Path(sys.argv[1]) / r["path"]).is_file() and strategy.read_bytes() == before, r
shown = h_show(t, concept_id="glossary/gamma")
d = h_write(t, "glossary/gamma", "claude-code/1.0", shown["sha256"],
            patch=[{"old": "Second derivative", "new": "The second derivative"}])
assert d.get("channel") == "direct", d
assert d["sha256"] == h_show(t, concept_id="glossary/gamma")["sha256"], d
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

step "sharded navigation: shard, MCP reader, sticky mode, rollback"
# v0.28: the resident index can be a short list of directories with the full
# listings in generated shards. A plain package must KEEP that mode, the MCP
# overview must be able to read a shard, and --flat-index must be the way back
# without touching a concept.
okfy package "$BUNDLE" --shard-index >/dev/null || fail "okfy package --shard-index"
[ -f "$BUNDLE/index/glossary.md" ] || fail "shard-index wrote no index/glossary.md"
okfy package "$BUNDLE" >/dev/null || fail "plain okfy package after sharding"
[ -f "$BUNDLE/index/glossary.md" ] \
  || fail "a plain package dropped the shards (index mode is not sticky)"
python - "$BUNDLE" <<'PY' || fail "okfy_overview could not read a generated shard"
import sys
from okfy_mcp.handlers import h_overview
from okfy_mcp.resolve import Target
r = h_overview(Target(sys.argv[1]), shard="glossary")
assert "Gamma" in r["index"], r
assert r.get("sha256") and r.get("truncated") is not True, r
PY
# v0.29: freshness compares the recorded fingerprint, a re-render AND the files
# on disk. A hand edit of the resident index.md must read stale through the MCP
# overview (the bytes a reader is served) and as a validate finding; packaging
# again is the way back.
cp "$BUNDLE/index.md" "$WORK/index.md.good"
printf '\nA hand-written line nobody rendered.\n' >> "$BUNDLE/index.md"
python - "$BUNDLE" <<'PY' || fail "an edited index.md was not reported stale by okfy_overview"
import sys
from okfy_mcp.handlers import h_overview
from okfy_mcp.resolve import Target
r = h_overview(Target(sys.argv[1]))
assert r.get("stale") is True and r.get("way_out"), r
PY
# to a file, not `| grep -q`: under pipefail grep exiting early SIGPIPEs validate
okfy validate "$BUNDLE" > "$WORK/validate-stale.txt" 2>&1 || true
grep -q "STALE_NAVIGATION" "$WORK/validate-stale.txt" \
  || fail "okfy validate did not report the edited index.md as stale navigation"
okfy package "$BUNDLE" >/dev/null || fail "okfy package after a hand edit"
python - "$BUNDLE" <<'PY' || fail "okfy package did not make the navigation fresh again"
import sys
from okfy_mcp.handlers import h_overview
from okfy_mcp.resolve import Target
r = h_overview(Target(sys.argv[1]))
assert not r.get("stale"), r
PY
CONCEPTS_BEFORE=$(cat "$BUNDLE"/glossary/*.md "$BUNDLE"/strategies/*.md | shasum -a 256)
okfy package "$BUNDLE" --flat-index >/dev/null || fail "okfy package --flat-index"
[ ! -e "$BUNDLE/index/glossary.md" ] || fail "--flat-index left a shard behind"
[ "$CONCEPTS_BEFORE" = "$(cat "$BUNDLE"/glossary/*.md "$BUNDLE"/strategies/*.md | shasum -a 256)" ] \
  || fail "--flat-index changed a concept file"

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

step "mcp HTTP auth boundary: write group and retired signing keys (offline)"
# In-process ASGI app over this bundle, real RS256 tokens from a synthetic
# issuer; only discovery and the JWKS fetch are served locally (no network).
# A reader outside the write group must not write however often `okfy:write`
# repeats; a key the issuer stopped publishing must not verify. Each refusal
# has its control: the owner writes, the current key verifies.
python - "$BUNDLE" <<'PY' || fail "mcp HTTP auth boundary"
import io, json, sys, time, types, urllib.request
from pathlib import Path
import jwt, jwt.jwk_set_cache, jwt.jwks_client
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient
from okfy_mcp.http import OidcVerifier
from okfy_mcp.server import http_app

bundle = Path(sys.argv[1])
ISS, AUD = "https://issuer.invalid/okfy", "okfy-smoke"
keys = {k: rsa.generate_private_key(public_exponent=65537, key_size=2048) for k in ("k1", "k2")}
published = {"kids": ["k1"]}

def jwks():
    return {"keys": [{**jwt.algorithms.RSAAlgorithm.to_jwk(keys[k].public_key(), as_dict=True),
                      "kid": k, "use": "sig", "alg": "RS256"} for k in published["kids"]]}

OidcVerifier._discover = staticmethod(lambda iss: (ISS, ISS + "/jwks"))
# PyJWT fetches through urlopen (<= 2.13) or build_opener().open (newer): serve
# both. Its cache clocks read a fake monotonic time we advance by hand, so
# the newer client's 30 s re-read cooldown is passed without sleeping.
serve = lambda req, **kw: io.BytesIO(json.dumps(jwks()).encode())
urllib.request.urlopen = serve
urllib.request.build_opener = lambda *h: types.SimpleNamespace(open=serve)
clock = [1000.0]
for mod in (jwt.jwks_client, jwt.jwk_set_cache):
    mod.time = types.SimpleNamespace(monotonic=lambda: clock[0])

def mint(scope, groups, kid="k1", sub="guest"):
    return jwt.encode({"iss": ISS, "aud": AUD, "sub": sub, "exp": int(time.time()) + 300,
                       "scope": scope, "groups": groups}, keys[kid], algorithm="RS256",
                      headers={"kid": kid})

def filed():
    return sorted(p.name for p in (bundle / "proposals").glob("*.md"))

app = http_app(bundle, oidc=OidcVerifier([ISS], [AUD], write_group="owners"),
               public_url="http://127.0.0.1:8765/mcp")

def gap(client, token, query):
    r = client.post("/mcp", headers={"Authorization": "Bearer " + token,
                                     "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "okfy_propose", "arguments": {
                              "target_id": "", "action": "gap", "note": "smoke auth",
                              "query": query}}})
    return r.status_code, (r.json() if r.status_code == 200 else None)

with TestClient(app, base_url="http://127.0.0.1:8765") as client:
    before = filed()
    status, body = gap(client, mint("okfy:read okfy:write okfy:write", []), "smoke: guest")
    assert status == 200 and "E_MCP_FORBIDDEN" in json.dumps(body), (status, body)
    assert filed() == before, "a guest with a repeated okfy:write filed a proposal"
    status, body = gap(client, mint("okfy:read okfy:write", ["owners"], sub="owner"),
                       "smoke: owner")
    assert status == 200 and '\\"persisted\\": true' in json.dumps(body), (status, body)
    assert len(filed()) == len(before) + 1, "the owner's proposal was not filed"

    published["kids"] = ["k2"]                          # the issuer retires k1
    clock[0] += 31                                      # past any re-read cooldown
    assert gap(client, mint("okfy:read", [], kid="k2"), "smoke: k2")[0] == 200
    assert gap(client, mint("okfy:read", [], kid="k1"), "smoke: k1")[0] == 401, \
        "a token signed by the retired k1 was accepted"
PY

# ---------------------------------------------------------------------------
# Installed-artifact negatives (round 23). Public CI runs this script and never
# the unit suite, so a defect the suite pins must be pinned here too, against the
# INSTALLED commands, or it can come back unseen. Each step prints
# `SMOKE NEG <id>: ...` before it runs and `SMOKE NEG <id>: PASS` after. A failure
# that starts `NEG <id> setup:` is a broken fixture; one that starts `NEG <id>:`
# is the owning assertion, i.e. the bug is back. Everything lives under $WORK.
# ---------------------------------------------------------------------------

step "NEG row-local (R23-4): a stale proposed twin must not hide an accepted endpoint diagnostic"
echo "SMOKE NEG row-local: accepted dangling relation + stale proposed twin, pre-edit via the workspace"
RL="$WORK/neg-rowlocal"
RL_CORPUS="$RL/source"
mkdir -p "$RL_CORPUS/engine" || fail "NEG row-local setup: mkdir"
# Two member bundles (policy: constraints, kb: knowledge) as the unit fixture builds
# them: minimal meta + concepts, each a git repo with one commit to pin.
python - "$RL" "$RL_CORPUS" <<'PY' || fail "NEG row-local setup: could not write the member bundles"
import sys
from pathlib import Path
from okfy import frontmatter
rl, corpus = Path(sys.argv[1]), sys.argv[2]
def put(path, meta, body="Rule evidence.\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontmatter.serialize(meta, body), encoding="utf-8")
for name, types, governing in (("policy", ["Convention", "Decision"], ["Convention"]),
                               ("kb", ["Module"], [])):
    b = rl / name
    put(b / "meta/corpus.md", {"type": "CorpusSnapshot", "corpus": corpus})
    put(b / "meta/purpose.md", {"type": "Purpose", "preedit": {"governing_types": governing}})
    put(b / "meta/extraction-plan.md", {"type": "ExtractionPlan", "types": types})
for cid in ("alpha", "beta"):
    put(rl / f"policy/conventions/{cid}.md",
        {"type": "Convention", "title": cid, "path_scope": ["engine/"]})
put(rl / "policy/decisions/exception.md", {"type": "Decision", "path_scope": []})
put(rl / "kb/modules/engine.md", {"type": "Module", "path_scope": ["engine/"]})
PY
for m in policy kb; do
  { git -C "$RL/$m" init -q && git -C "$RL/$m" add . \
      && git -C "$RL/$m" commit -qm "Fixture"; } >/dev/null 2>&1 \
    || fail "NEG row-local setup: could not commit the $m member"
done
okfy workspace init "$RL/workspace" --member "constraints:policy=$RL/policy" \
  --member "knowledge:kb=$RL/kb" >/dev/null 2>"$WORK/rl-ws.err" \
  || { cat "$WORK/rl-ws.err"; fail "NEG row-local setup: okfy workspace init (pins the members)"; }
cat > "$WORK/rl-rows.py" <<'PY'
import sys
from pathlib import Path
from okfy import frontmatter
ws, which = Path(sys.argv[1]), sys.argv[2]
A, G = "policy:conventions/alpha", "policy:conventions/never-existed"
D = "policy:decisions/exception"
base = {"src": A, "rel": "conflicts", "dst": G, "origin": "owner", "scope": ["engine/"]}
rows = {
    "accepted": {**base, "status": "accepted", "reason": "Fresh accepted declaration"},
    "proposed": {**base, "status": "proposed", "reason": "Stale proposed declaration",
                 "resolution": {"decision": D, "scope": ["engine/"]}},
}
(ws / "links").mkdir(exist_ok=True)
(ws / "links" / f"{which}.md").write_text(
    frontmatter.serialize({"type": "Crosswalk", "rows": [rows[which]]}, "Smoke row.\n"),
    encoding="utf-8")
PY
rl_preedit() {   # $1 = output file; the lookup itself must succeed, whatever it answers
  okfy preedit "$RL/workspace" engine/run.py --root "$RL_CORPUS" > "$1" 2>"$WORK/rl-pre.err" \
    || { cat "$WORK/rl-pre.err"; fail "NEG row-local setup: okfy preedit exited non-zero"; }
}
python "$WORK/rl-rows.py" "$RL/workspace" accepted \
  || fail "NEG row-local setup: could not write links/accepted.md"
# The decision concept moves by a REAL commit after the pin: the pinned workspace
# entry still names the old SHA, so a row citing it as its resolution is stale.
printf '\nChanged after owner review.\n' >> "$RL/policy/decisions/exception.md"
{ git -C "$RL/policy" add decisions/exception.md \
    && git -C "$RL/policy" commit -qm "Move the decision"; } >/dev/null 2>&1 \
  || fail "NEG row-local setup: could not commit the moved decision"
rl_preedit "$WORK/rl-alone.json"
RL_ALONE_MSG="NEG row-local setup: the accepted dangling relation alone is not flagged (complete=true), so the twin assertion would be vacuous"
python - "$WORK/rl-alone.json" <<'PY' || fail "$RL_ALONE_MSG"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["complete"] is False and "invalid_relation" in d["incomplete_reasons"], d
PY
python "$WORK/rl-rows.py" "$RL/workspace" proposed \
  || fail "NEG row-local setup: could not write links/proposed.md"
rl_preedit "$WORK/rl-twin.json"
RL_TWIN_MSG="NEG row-local: a stale proposed twin hid the accepted endpoint diagnostic (complete=true, or no invalid_relation / endpoint_missing:dst)"
python - "$WORK/rl-twin.json" <<'PY' || fail "$RL_TWIN_MSG"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["complete"] is False, (d["complete"], d["incomplete_reasons"], d["excluded_relations"])
assert "invalid_relation" in d["incomplete_reasons"], d["incomplete_reasons"]
assert any(e["reason"] == "endpoint_missing:dst" for e in d["excluded_relations"]), \
    d["excluded_relations"]
PY
echo "SMOKE NEG row-local: PASS"

step "NEG capture (R23-2): a corpus whose .git names a gitdir with a non-UTF-8 byte"
echo "SMOKE NEG capture: okfy validate on the gitfile 'gitdir: missing-<0xff>' must report W_CORPUS_SHA"
CC="$WORK/neg-capture/corpus"
CB="$WORK/neg-capture/bundle"
mkdir -p "$CC" || fail "NEG capture setup: mkdir"
printf '# Keep\n\nOne tracked note.\n' > "$CC/keep.md"
{ git -C "$CC" init -q && git -C "$CC" add . && git -C "$CC" commit -qm "initial"; } \
  >/dev/null 2>&1 || fail "NEG capture setup: could not commit the corpus"
okfy init "$CB" --corpus "$CC" --language en >/dev/null 2>"$WORK/cap-init.err" \
  || { cat "$WORK/cap-init.err"; fail "NEG capture setup: okfy init on the git corpus"; }
grep -q 'git_sha' "$CB/meta/corpus.md" \
  || fail "NEG capture setup: the snapshot recorded no git_sha, so validate has nothing to verify"
mv "$CC/.git" "$WORK/neg-capture/saved-git" \
  || fail "NEG capture setup: could not move the real .git aside"
printf 'gitdir: missing-\377\n' > "$CC/.git" \
  || fail "NEG capture setup: could not write the gitfile"
set +e
okfy validate "$CB" > "$WORK/cap-validate.out" 2>"$WORK/cap-validate.err"
CAPRC=$?
set -e
if grep -q 'Traceback' "$WORK/cap-validate.err"; then
  tail -8 "$WORK/cap-validate.err"
  fail "NEG capture: okfy validate raised a traceback on git's non-UTF-8 stderr"
fi
CAP_MSG="NEG capture: okfy validate did not print a JSON report naming W_CORPUS_SHA for a gitfile with a non-UTF-8 byte (rc $CAPRC; the decode error became a bare refusal)"
python - "$WORK/cap-validate.out" <<'PY' || fail "$CAP_MSG"
import json, sys
out = open(sys.argv[1], "rb").read()
assert out.strip(), "empty stdout"
codes = [f["code"] for f in json.loads(out)["findings"]]
assert "W_CORPUS_SHA" in codes, codes
PY
echo "SMOKE NEG capture: PASS"

step "NEG stat (R23-3): a tracked file in a directory that cannot be searched"
echo "SMOKE NEG stat: okfy init must refuse with E_CORPUS_LISTING, no traceback, and create nothing"
SC="$WORK/neg-stat/corpus"
SB="$WORK/neg-stat/new-bundle"
mkdir -p "$SC/private-dir" || fail "NEG stat setup: mkdir"
printf '# Keep\n' > "$SC/keep.md"
printf 'evidence\n' > "$SC/private-dir/evidence.md"
printf 'private-dir/\n' > "$SC/.gitignore"
# tracked first, ignored after: git still lists the file (exit 0, empty stderr)
{ git -C "$SC" init -q && git -C "$SC" add .gitignore keep.md \
    && git -C "$SC" add -f private-dir/evidence.md && git -C "$SC" commit -qm "initial"; } \
  >/dev/null 2>&1 || fail "NEG stat setup: could not commit the corpus"
STAT_SKIP=""
if [ "$(id -u)" -eq 0 ]; then
  STAT_SKIP="effective uid is 0: mode bits do not stop stat"
else
  chmod 000 "$SC/private-dir" || fail "NEG stat setup: chmod 000"
  if [ -r "$SC/private-dir/evidence.md" ] || ls "$SC/private-dir" >/dev/null 2>&1; then
    STAT_SKIP="this filesystem does not enforce mode bits"
  fi
fi
if [ -n "$STAT_SKIP" ]; then
  chmod 755 "$SC/private-dir" 2>/dev/null || true
  echo "SMOKE NEG stat: SKIP ($STAT_SKIP)"
else
  set +e
  okfy init "$SB" --corpus "$SC" --language en > "$WORK/stat-init.out" 2>"$WORK/stat-init.err"
  STATRC=$?
  set -e
  chmod 755 "$SC/private-dir" || fail "NEG stat setup: could not restore the directory mode"
  if grep -q 'Traceback' "$WORK/stat-init.err"; then
    tail -8 "$WORK/stat-init.err"
    fail "NEG stat: okfy init raised a traceback on an unstattable corpus entry"
  fi
  cat "$WORK/stat-init.out" "$WORK/stat-init.err" > "$WORK/stat-init.all"
  grep -q 'E_CORPUS_LISTING' "$WORK/stat-init.all" \
    || { cat "$WORK/stat-init.all"; fail "NEG stat: okfy init did not refuse with E_CORPUS_LISTING (rc $STATRC)"; }
  [ ! -e "$SB" ] || fail "NEG stat: okfy init refused but still created the bundle"
  echo "SMOKE NEG stat: PASS"
fi

printf '\nSMOKE OK\n'
