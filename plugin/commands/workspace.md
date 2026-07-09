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

## 2. Init

- `okfy workspace init <dir> --member role:name=path ... --title "<title>"`
- Write the 10 test queries into `meta/workspace.md` frontmatter
  (`test_queries:`) and the purpose into the body.
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

## 4. Federated smoke test

For each of the 10 test queries: `okfy query <dir> "<query>"`, inspect
knowledge/constraints/pulled; `okfy show <dir> <member>:<id>` top hits; judge
PASS/FAIL honestly — PASS needs the right knowledge AND the binding
constraints surfaced. Record K/10 with gaps.

## 5. Package + report

- `okfy workspace package <dir>`; commit `package: federated protocol`.
- Report to the user: members table, row counts by rel, K/10 with per-failure
  gap notes. Acceptance target: ≥8/10.
