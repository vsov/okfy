---
description: "Refresh stale crosswalk rows after member bundles changed"
argument-hint: "<workspace-dir>"
---
Workspace: $1.

1. `okfy workspace status <dir>`. If every member is fresh and stale_rows is
   empty: report "workspace is fresh" and STOP.
2. For each stale row: re-read BOTH endpoint concepts at their current state
   (`okfy show <member-bundle-path> <concept-id>` against the member's path
   from meta/workspace.md). Judge: still valid → keep; endpoint gone or
   meaning changed → propose drop/replacement. New concepts listed in
   `changed_concepts` may need NEW rows — run `okfy link-candidates <dir>`
   and judge additions as in /okfy:workspace step 3.
3. REVIEW CHECKPOINT: same rules — `constrains` changes need explicit user
   approval.
4. Rewrite affected `links/*.md`, re-pin member `git_sha` values in
   `meta/workspace.md`, commit:
   `git -C <dir> add . && git -C <dir> commit -m "relink: N rows refreshed"`.
5. Re-run the workspace test queries (federated smoke test) and report K/10.
