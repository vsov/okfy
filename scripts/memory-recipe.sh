#!/bin/sh
# Memory-only onboarding recipe, run against the INSTALLED `okfy` command.
#
#   sh scripts/memory-recipe.sh <empty-work-dir>
#
# WHAT IT PROVES. A bundle with no extracted corpus can be used as an agent's
# memory, end to end, with nothing but shipped commands:
#   1  `okfy init` makes the bundle (write policy: proposals);
#   2  the owner opens the direct lane for ONE concept type in meta/purpose.md;
#   3  that type is written at once, with its evidence stated in the concept;
#   4  every other type stays a proposal until the owner accepts it;
#   5  `okfy package` makes it consumable;
#   6  a fresh process finds both concepts again (memory survives the session);
#   7  a personal-scope concept for ANOTHER project is not surfaced through a
#      workspace while the one for THIS project is (fail-closed);
#   8  a second `okfy package` keeps owner-written text and the generated-file
#      markers, and the script records what package does NOT preserve.
#
# WHAT IT IS NOT. Every byte is synthetic and written here. It proves the
# mechanics work on the installed wheel; it says nothing about whether the
# memory a real agent files is any good. Never point it at a real bundle: it
# creates everything it touches inside <empty-work-dir> and refuses to run
# anywhere else.
#
# Output: a `STEP n:` line before each step, `MEMORY RECIPE OK` and exit 0 at
# the end, or `MEMORY RECIPE FAIL: <why>` on stderr and a non-zero exit.
# shellcheck disable=SC2016  # backticks and $ in single quotes are literal text here
set -eu

# Isolate every git call below from the caller's environment. A GIT_DIR,
# GIT_INDEX_FILE, GIT_WORK_TREE, GIT_OBJECT_DIRECTORY or GIT_COMMON_DIR left
# exported (a git hook does this) sends `git -C` and `okfy init` into ANOTHER
# repository: `-C` only changes the directory, and these variables outrank it.
# Policy source: core/src/okfy/gitenv.py -- drop every GIT_* variable except
# identity (GIT_AUTHOR_*, GIT_COMMITTER_*), config (GIT_CONFIG*), ssh
# (GIT_SSH*), GIT_TERMINAL_PROMPT, GIT_ASKPASS and GIT_EXEC_PATH. This must run
# before the first git invocation.
for v in $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do
  case "$v" in
    GIT_AUTHOR_*|GIT_COMMITTER_*|GIT_CONFIG*|GIT_SSH*|GIT_TERMINAL_PROMPT|GIT_ASKPASS|GIT_EXEC_PATH) ;;
    *) unset "$v" ;;
  esac
done

fail() { echo "MEMORY RECIPE FAIL: $*" >&2; exit 1; }
step() { printf 'STEP %s: %s\n' "$1" "$2"; }
# has FILE TEXT: FILE contains TEXT as a fixed string; says why when it does not
has() { grep -qF -- "$2" "$1" || { cat "$1" >&2; fail "$3"; }; }
lacks() { if grep -qF -- "$2" "$1"; then cat "$1" >&2; fail "$3"; fi; }

[ "$#" -eq 1 ] || fail "usage: sh scripts/memory-recipe.sh <empty-work-dir>"
command -v okfy >/dev/null 2>&1 || fail "okfy is not on PATH (install the wheel first)"
command -v git >/dev/null 2>&1 || fail "git is not on PATH"
[ -d "$1" ] || fail "$1 is not an existing directory — create an empty one (mktemp -d)"
# Capture the directory name losslessly (R23-1). Command substitution strips ALL trailing
# newlines, so `WORK="$(cd "$1" && pwd -P)"` turns a directory named "work<LF>" into the path of
# its sibling "work": the emptiness guard below then judges, and the recipe writes into, a
# different directory than the one selected. A sentinel printed inside the substitution is never
# stripped; `%?x` then drops exactly the LF that `pwd` printed plus the sentinel, so any LFs that
# are part of the name stay. A failing `cd`/`pwd` makes the assignment fail, and `|| fail` keeps
# `set -e` from ending the script without the refusal message.
WORK="$(cd -- "$1" && pwd -P && echo x)" || fail "cannot resolve $1 — pass an existing directory"
WORK="${WORK%?x}"
# Prove the listing worked before judging it, and judge emptiness by entries, not by text.
# `[ -z "$(ls -A ...)" ]` is wrong twice: a failing `ls` reads as an empty string, and command
# substitution strips trailing newlines, so a directory whose only entry is named "\n" reads as
# empty. The status line appended inside the substitution is never stripped: it carries the
# exit status of `ls` itself (the `&& ... ||` form keeps `set -e` from ending the substitution
# before it is written), and any entry leaves something in front of it.
LISTING="$(ls -A "$WORK" 2>/dev/null && echo "ls-status=0" || echo "ls-status=$?")"
case "$LISTING" in
  *ls-status=0) ;;
  *) fail "cannot list $WORK — the recipe only runs in an empty, readable directory" ;;
esac
[ "$LISTING" = "ls-status=0" ] || fail "$WORK is not empty — the recipe only runs in an empty directory"
# Same sentinel for the script's own location: `dirname` and `pwd` both lose trailing LFs.
D="$(dirname -- "$0" && echo x)" || fail "cannot locate this script ($0)"
D="${D%?x}"
HERE="$(cd -- "$D" && pwd -P && echo x)" || fail "cannot resolve the script directory"
HERE="${HERE%?x}"
REPO="$(cd -- "$HERE/.." && pwd -P && echo x)" || fail "cannot resolve the repository directory"
REPO="${REPO%?x}"
case "$WORK/" in
  "$REPO"/*) fail "$WORK is inside the OKFy repository — use a directory outside it" ;;
esac
if git -C "$WORK" rev-parse --git-dir >/dev/null 2>&1; then
  fail "$WORK is inside a git repository — use a directory outside any repository"
fi

# A fresh CI runner has no git identity, and `okfy init` records the bundle's
# first commit (same reason smoke.sh sets these).
export GIT_AUTHOR_NAME="okfy memory recipe" GIT_AUTHOR_EMAIL="recipe@example.invalid"
export GIT_COMMITTER_NAME="okfy memory recipe" GIT_COMMITTER_EMAIL="recipe@example.invalid"

OUT="$WORK/out"            # captured command output
DRAFTS="$WORK/drafts"      # text handed to `--from`; never inside a bundle
CORPUS="$WORK/corpus"
BUNDLE="$WORK/memory-okf"
mkdir -p "$OUT" "$DRAFTS" "$CORPUS"

# ---------------------------------------------------------------------------
step 1 "init a memory-only bundle from a tiny corpus (write policy: proposals)"
cat > "$CORPUS/api-notes.md" <<'EOF'
# List API notes

The list API pages its results. Offset paging skipped rows under concurrent inserts.
EOF
cat > "$CORPUS/test-run.md" <<'EOF'
# Test run r-17

Run r-17 paged 200 rows by cursor with concurrent inserts and lost none.
EOF
okfy init "$BUNDLE" --corpus "$CORPUS" --write-policy proposals > "$OUT/init.json" \
  || fail "okfy init"
has "$OUT/init.json" '"created"' "okfy init did not report the bundle it created"
has "$BUNDLE/meta/purpose.md" 'write_policy: proposals' "init did not set write_policy: proposals"
[ -d "$BUNDLE/.git" ] || fail "init did not make the bundle a git repository"

# ---------------------------------------------------------------------------
step 2 "the owner declares the types and opens the direct lane for Decision only"
# `init` writes no extraction plan, so the owner declares the two types this
# memory holds. codebase-map brings the Decision and Convention schemas.
cat > "$BUNDLE/meta/extraction-plan.md" <<'EOF'
---
type: ExtractionPlan
title: Memory plan
archetype: codebase-map
archetype_version: 1
types:
  Decision: one concept per decision
  Convention: one concept per standing rule
layout:
  Decision: decisions/
  Convention: conventions/
segments: []
---

Memory-only bundle: nothing is extracted; concepts arrive through writes and proposals.
EOF
awk 'NR==1 { print; print "direct:"; print "  types: [Decision]"; next } { print }' \
  "$BUNDLE/meta/purpose.md" > "$WORK/purpose.new"
mv "$WORK/purpose.new" "$BUNDLE/meta/purpose.md"
has "$BUNDLE/meta/purpose.md" '  types: [Decision]' "could not declare direct.types"
# The owner commits by hand. No hook exists yet; --no-verify is the deliberate
# owner bypass the generated pre-commit hook names, kept here so the recipe
# does not depend on whether a hook is installed.
git -C "$BUNDLE" add meta/purpose.md meta/extraction-plan.md
git -C "$BUNDLE" commit -q --no-verify -m "owner: declare types, open the direct lane for Decision" \
  || fail "owner commit"
[ -z "$(git -C "$BUNDLE" status --porcelain -- meta)" ] || fail "meta/ is not clean after the owner commit"

# ---------------------------------------------------------------------------
step 3 "an owner decision lands through the direct lane, evidence stated in the concept"
# `okfy write` has no evidence flag: the evidence is part of the concept text
# (and of --note, which goes to the bundle's log.md).
cat > "$DRAFTS/cursor-paging.md" <<'EOF'
---
type: Decision
title: Page the list API by cursor
description: The list API pages by cursor, not by offset.
tags: [api]
sources: [api-notes.md, test-run.md]
---
## Context

Offset paging skipped rows under concurrent inserts. Evidence: test run r-17
(test-run.md) paged 200 rows by cursor and lost none.

## Decision

Page the list API by opaque cursor.

## Consequences

Clients cannot jump to page N.
EOF
okfy write "$BUNDLE" decisions/cursor-paging --as human:owner --expect absent \
  --from "$DRAFTS/cursor-paging.md" --note "owner decision; evidence: test run r-17" \
  > "$OUT/write.json" || fail "okfy write (direct lane)"
has "$OUT/write.json" '"persisted": true' "okfy write did not report persisted: true"
has "$OUT/write.json" '"channel": "direct"' "okfy write did not report the direct channel"
[ -f "$BUNDLE/decisions/cursor-paging.md" ] || fail "the Decision file is not in the bundle"
has "$BUNDLE/decisions/cursor-paging.md" 'test run r-17' "the evidence is not in the concept"
has "$BUNDLE/meta/memory.jsonl" '"origin": "agent-direct"' "the direct write left no ledger row"
has "$BUNDLE/log.md" 'evidence: test run r-17' "the log line lost the note"

# ---------------------------------------------------------------------------
step 4 "a Convention is not in the lane: write refuses it, propose files it, the owner accepts"
cat > "$DRAFTS/list-cursor.md" <<'EOF'
---
type: Convention
title: Every list endpoint accepts a cursor
description: List endpoints take a cursor parameter and return a next-cursor.
tags: [api]
sources: [api-notes.md]
---
## Rule

Every list endpoint accepts `cursor` and returns `next_cursor`.

## Rationale

Follows the cursor-paging decision; evidence: test run r-17.

## Enforcement

Review checklist.
EOF
set +e
okfy write "$BUNDLE" conventions/list-cursor --as claude-code/1.0 --expect absent \
  --from "$DRAFTS/list-cursor.md" > "$OUT/write-refused.out" 2> "$OUT/write-refused.err"
RC=$?
set -e
[ "$RC" -eq 2 ] || fail "okfy write of an unlisted type exited $RC, expected 2"
has "$OUT/write-refused.err" 'E_DIRECT_LANE' "the refusal did not name E_DIRECT_LANE"
[ ! -e "$BUNDLE/conventions/list-cursor.md" ] || fail "a refused write left a concept file"
okfy propose "$BUNDLE" --target conventions/list-cursor --action create --as claude-code/1.0 \
  --from "$DRAFTS/list-cursor.md" --evidence test-run=r-17 --note "agent proposal" \
  > "$OUT/propose.json" || fail "okfy propose"
PROPOSAL="$(sed -n 's/.*"proposal": "\([^"]*\)".*/\1/p' "$OUT/propose.json" | head -1)"
[ -n "$PROPOSAL" ] || fail "okfy propose named no proposal id"
[ ! -e "$BUNDLE/conventions/list-cursor.md" ] || fail "a proposal became a concept before review"
okfy review list "$BUNDLE" > "$OUT/review-list.json" || fail "okfy review list"
has "$OUT/review-list.json" "$PROPOSAL" "the proposal is not in the review queue"
okfy review accept "$BUNDLE" "$PROPOSAL" --as human:owner > "$OUT/accept.json" \
  || fail "okfy review accept"
has "$OUT/accept.json" '"target": "conventions/list-cursor"' "accept did not report its target"
[ -f "$BUNDLE/conventions/list-cursor.md" ] || fail "accept did not write the Convention"

# ---------------------------------------------------------------------------
step 5 "package the bundle (index, AGENTS.md, retrieval cache)"
okfy package "$BUNDLE" > "$OUT/package.json" || fail "okfy package"
has "$OUT/package.json" '"packaged"' "okfy package did not report success"
for f in AGENTS.md index.md README.md protocols/memory.md; do
  [ -f "$BUNDLE/$f" ] || fail "package wrote no $f"
done
has "$BUNDLE/index.md" 'cursor-paging' "index.md does not list the Decision"
has "$BUNDLE/index.md" 'list-cursor' "index.md does not list the Convention"
has "$BUNDLE/AGENTS.md" '<!-- okfy:direct -->' "AGENTS.md does not state the direct lane"
has "$BUNDLE/AGENTS.md" '`Decision`' "AGENTS.md does not name the direct type"

# ---------------------------------------------------------------------------
step 6 "a FRESH process finds both concepts"
# env -i: nothing of this script's shell state (variables, cwd tricks) reaches it.
env -i PATH="$PATH" HOME="${HOME:-/}" sh -c '
  set -eu
  okfy query "$1" "cursor paging" > "$2/fresh-query.json"
  okfy show "$1" decisions/cursor-paging > "$2/fresh-show-decision.md"
  okfy show "$1" conventions/list-cursor > "$2/fresh-show-convention.md"
' sh "$BUNDLE" "$OUT" || fail "the fresh process failed"
has "$OUT/fresh-query.json" '"id": "decisions/cursor-paging"' "query did not find the Decision"
has "$OUT/fresh-query.json" '"id": "conventions/list-cursor"' "query did not find the Convention"
has "$OUT/fresh-show-decision.md" 'type: Decision' "show did not return the Decision"
has "$OUT/fresh-show-decision.md" 'test run r-17' "show lost the Decision's evidence"
has "$OUT/fresh-show-convention.md" 'type: Convention' "show did not return the Convention"

# ---------------------------------------------------------------------------
step 7 "personal memory is scoped to its project, fail-closed (negative control)"
PCORPUS="$WORK/personal-corpus"
PERSONAL="$WORK/personal-okf"
WS="$WORK/workspace"
mkdir -p "$PCORPUS"
printf '# Preferences\n\nOne owner'"'"'s working preferences.\n' > "$PCORPUS/prefs.md"
okfy init "$PERSONAL" --corpus "$PCORPUS" > "$OUT/init-personal.json" || fail "okfy init (personal)"
mkdir -p "$PERSONAL/notes"
# The same needle in both concepts: only `applies_to` can tell them apart.
cat > "$PERSONAL/notes/here.md" <<'EOF'
---
type: Preference
title: Prefer terse commit messages here
description: Terse commits for this project.
applies_to: [this-project]
---
Prefer terse commit messages. Needle: zephyrquartz.
EOF
cat > "$PERSONAL/notes/elsewhere.md" <<'EOF'
---
type: Preference
title: Prefer verbose commit messages elsewhere
description: Verbose commits for another project.
applies_to: [other-project]
---
Prefer verbose commit messages. Needle: zephyrquartz.
EOF
okfy index "$PERSONAL" > "$OUT/index-personal.json" || fail "okfy index (personal)"
okfy workspace init "$WS" --member knowledge:main="$BUNDLE" --member personal:me="$PERSONAL" \
  --project-key this-project > "$OUT/workspace-init.json" || fail "okfy workspace init"
okfy query "$WS" zephyrquartz > "$OUT/ws-needle.json" || fail "okfy query (workspace)"
has "$OUT/ws-needle.json" '"ref": "me:notes/here"' \
  "the control concept (applies_to this-project) was not returned"
lacks "$OUT/ws-needle.json" 'me:notes/elsewhere' \
  "a concept scoped to other-project was returned through the workspace"
has "$OUT/ws-needle.json" 'personal scope this-project — 1 of 2 concepts in scope' \
  "the workspace did not report the scope it applied"
# Not vacuous: queried directly, the personal bundle is unfiltered, so the
# concept the workspace withheld exists and matches the same needle.
okfy query "$PERSONAL" zephyrquartz > "$OUT/direct-needle.json" || fail "okfy query (personal)"
has "$OUT/direct-needle.json" '"id": "notes/elsewhere"' \
  "the other-project concept does not match the needle, so the negative control proves nothing"
okfy query "$WS" "cursor paging" > "$OUT/ws-memory.json" || fail "okfy query (workspace, memory)"
has "$OUT/ws-memory.json" '"ref": "main:decisions/cursor-paging"' \
  "the workspace did not surface the memory bundle's Decision"

# ---------------------------------------------------------------------------
step 8 "a second package keeps owner text and the generated markers; AGENTS.md itself is regenerated"
# AGENTS.md is generated WHOLE: package rewrites it from the template and the
# bundle's declarations, so a line added to it is not preserved. The markers in
# it (okfy:memory, okfy:direct) delimit generated blocks inside that generated
# file; they are not a place for owner text. The owner's place for a standing
# instruction is the body of meta/purpose.md, which AGENTS.md tells every agent
# to read first and which package never writes.
printf 'LOCAL EDIT TO A GENERATED FILE\n' >> "$BUNDLE/AGENTS.md"
printf '\nOwner note: list endpoints in this project are cursor-paged.\n' >> "$BUNDLE/meta/purpose.md"
git -C "$BUNDLE" add meta/purpose.md
git -C "$BUNDLE" commit -q --no-verify -m "owner: standing note in purpose" || fail "owner commit (purpose note)"
okfy package "$BUNDLE" > "$OUT/package2.json" || fail "okfy package (second run)"
has "$BUNDLE/meta/purpose.md" 'Owner note: list endpoints in this project are cursor-paged.' \
  "package lost the owner's line in meta/purpose.md"
has "$BUNDLE/AGENTS.md" 'Read `meta/purpose.md` first' "AGENTS.md no longer sends the agent to purpose.md"
has "$BUNDLE/AGENTS.md" '<!-- okfy:memory -->' "the okfy:memory marker is gone"
has "$BUNDLE/AGENTS.md" '<!-- /okfy:memory -->' "the closing okfy:memory marker is gone"
has "$BUNDLE/AGENTS.md" '<!-- okfy:direct -->' "the okfy:direct marker is gone"
has "$BUNDLE/AGENTS.md" '<!-- /okfy:direct -->' "the closing okfy:direct marker is gone"
lacks "$BUNDLE/AGENTS.md" 'LOCAL EDIT TO A GENERATED FILE' \
  "AGENTS.md kept a hand edit: it is no longer wholly regenerated, update this recipe"
[ "$(head -n 1 "$BUNDLE/protocols/memory.md")" = \
  '<!-- okfy:generated — do not edit; rewritten by okfy package -->' ] \
  || fail "protocols/memory.md lost its generated-file marker"
# the memory survived the second package too
okfy query "$BUNDLE" "cursor paging" > "$OUT/query-after.json" || fail "okfy query (after)"
has "$OUT/query-after.json" '"id": "decisions/cursor-paging"' "the Decision is gone after package"
has "$OUT/query-after.json" '"id": "conventions/list-cursor"' "the Convention is gone after package"

echo "MEMORY RECIPE OK"
