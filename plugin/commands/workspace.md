---
description: "Create a federation workspace: interview → init → crosswalk linking (review checkpoint) → federated smoke test → package"
argument-hint: "<workspace-dir>"
---
Target workspace dir: $1. The `okfy` CLI must be installed.

## 1. Federation interview (one question at a time, user's language)

1. Which bundles federate, and what ROLE does each play: `knowledge`
   (answers come from here) or `constraints` (limits that outrank knowledge)?
   Collect name + path + role per member; verify each path is a bundle.
2. What is the workspace FOR (one-line purpose/title)?
3. **Exactly 10 cross-bundle test queries** — questions whose answers need
   more than one member (e.g. a strategy question that must respect a
   constraint member's limits). Refuse single-member questions.
4. **Exactly 10 cross-bundle adversarial queries** — same format and same
   purpose as a bundle's (see `/okfy:new`), aimed at how FEDERATION fails
   rather than how one member fails: a question a constraint member should
   bind but whose vocabulary never names the constraint; a topic that sits in
   the gap between members and belongs to neither; a question where the
   knowledge member answers well and the binding limit does NOT surface.
   `concept` refs are member-qualified — `risk:limits/net-short-vega-cap`.

## 2. Init

- `okfy workspace init <dir> --member role:name=path ... --title "<title>"`
- Write the 10 test queries and the 10 adversarial queries into
  `meta/workspace.md` frontmatter (`test_queries:`, `adversarial_queries:`) and
  the purpose into the body. Both are required for release.
- Ensure every member has a fresh index: `okfy index <member-path>` for each.

## 3. Linking pass

1. `okfy link-candidates <dir>` → deterministic candidates (alias-exact rows
   arrive as accepted; fuzzy as proposed).
2. LLM judge — find what lexical matching CANNOT: read each member's
   glossary/index snippets and propose (a) `same-as` pairs with zero shared
   tokens, (b) `constrains` rows: for each knowledge-member Strategy/Playbook-
   like concept, which constraint-member concepts bind it? Mark all of these
   status=proposed, origin=llm.
3. REVIEW CHECKPOINT (mandatory): present ALL proposed rows to the user in a
   table (src, rel, dst, why). `constrains` rows REQUIRE explicit user
   approval row-by-row or batch; `same-as` alias-exact rows may pass silently.
   Drop rejected rows.
4. Write accepted+reviewed rows: group by member pair, one
   `links/<a>--<b>.md` per pair (the CLI's `write_rows` format: frontmatter
   `rows:` list). Re-pin member SHAs in `meta/workspace.md` (set each
   member's `git_sha` to the member repo's current HEAD).
5. Commit: `git -C <dir> add . && git -C <dir> commit -m "link: N rows (M constrains)"`.

## 4. Federated eval — an artifact, not a smoke test

This step used to say "judge PASS/FAIL honestly, record K/10". That is a
narrative: it left the strongest claim in the project resting on a line in
`log.md`, while a single bundle making the same claim had to produce a
replayable run and an owner checkpoint. Federated acceptance is now the same
kind of artifact, in `<dir>/meta/eval.json`:

1. `okfy eval run <dir>` — replays the 10 cross-bundle queries through the
   FEDERATED path (per-member expansion → RRF → role grouping → constrains
   auto-pull) and records them. `okfy eval run <dir> --suite adversarial` does
   the same for the adversarial ten, recording a deterministic `met`/`unmet`
   against each declared expectation.
2. Judge as the LLM-judge, then take the owner through both tables, exactly as
   `/okfy:eval` does for a bundle — same verbs, `--suite` selects the table:
   `okfy eval verdict <dir> latest <i> pass|fail|partial --owner --note '...'`.
   A federated PASS needs the right knowledge AND the binding constraint
   surfaced; say which of the two failed when it fails.
3. `okfy eval status <dir>` and `okfy eval status <dir> --suite adversarial`.

## 5. Package + report

- `okfy workspace package <dir>`; commit `package: federated protocol`.
- `okfy release-check <dir>` — it auto-detects the workspace and applies the
  federated predicate: every MEMBER must itself be release-accepted, the
  crosswalk must be fresh (no drifted or unverifiable member, no stale row),
  and both federated suites must be owner-complete and pinned to the live
  federated fingerprint. Exit 0 is the only thing that means accepted.
- Report to the user: members table, row counts by rel, both suites'
  owner-confirmed scores, and the release-check verdict verbatim. If it is
  red, say so — never present a workspace as accepted on the strength of the
  eval alone.
