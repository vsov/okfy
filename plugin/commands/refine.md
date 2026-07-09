---
description: "Owner-directed refinement of a concept: rewrite it from its sources per instruction"
argument-hint: "<bundle-path> <concept-id> <instruction>"
---
Bundle: $1. Concept: $2. Instruction: everything after the second argument.
This is the OWNER channel (ADR-0007): you edit the live concept directly, on
the user's explicit instruction — do not use this to act on your own initiative
(that is what proposals are for).

1. `okfy show <bundle> <concept-id>` — read the current state.
2. Read its `sources:` files in the corpus if the corpus is locally available
   (check `meta/corpus.md`); ground every change in them. If the instruction
   asks for something the sources contradict, STOP and tell the user.
3. Rewrite the FULL concept file (frontmatter + body): apply the instruction,
   preserve the archetype's required fields and sections, keep aliases
   (including Russian ones) and sources accurate.
4. Write the new content to a temp file, then:
   `okfy refine <bundle> <concept-id> --from <temp-file> -m "<short reason>"`.
   The CLI validates and commits (--no-verify). Never edit the concept file
   in place with a text editor — the pre-commit hook will refuse the commit.
5. `okfy index <bundle>`; show the user a before/after summary of what changed.
