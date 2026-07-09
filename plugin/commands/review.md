---
description: "Review pending proposals: show diffs, judge, accept/reject with the user"
argument-hint: "<bundle-path>"
---
Bundle: $1. Proposals are agent-suggested changes waiting for a human gate
(ADR-0007). You judge quality; the USER decides. Never accept without an
explicit user decision.

1. `okfy review list <bundle>`. Empty → report "no pending proposals", STOP.
2. For each proposal: `okfy show <bundle> <proposal-id>`; if it targets an
   existing concept, `okfy show <bundle> <target>` and present a concise diff
   (what changes, what is added/removed). Flag invalid ones (the list marks
   them) with their issues.
3. Add YOUR judgment per proposal: is the change grounded (check `sources:`
   against the corpus if locally available), does it preserve required
   sections, does it improve the concept? One line each.
4. Present the batch to the user: table of id / action / target / note / your
   verdict. Ask accept/reject per proposal (batch answers fine).
5. Execute decisions: `okfy review accept <bundle> <id>` /
   `okfy review reject <bundle> <id> --reason "<user's reason>"`.
   accept validates against the archetype and refuses broken content (exit 2)
   — report refusals back to the user rather than forcing.
6. Finish: `okfy index <bundle>` and if anything was accepted,
   `okfy package <bundle>` (regenerate index/docs), then report counts.
   All mutations were committed by the CLI itself (--no-verify) — do NOT
   git-commit concept files by hand.
