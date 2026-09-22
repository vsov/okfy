---
type: Contract
title: (the rule, stated as an obligation: "Caller frees every returned ray_t")
description: "(one sentence: the cross-cutting rule a caller must honor)"
tags: [(memory | threading | limits | versioning | auth)]
aliases: []
sources: [(corpus-relative paths)]
---
## Rule

(The obligation, precisely. What the caller MUST / MUST NOT do.)

## Scope

(Concretely which operations/types this binds — link them. "Everything"
is acceptable only if literally true. Name the traced caller path or the
minimal executed example that verified the Rule above — file:line, not
just the source that stated it.)

## On violation

(What actually breaks: leak, crash, deadlock, rate-ban, corrupt data.
Name the failure mode, not "undefined behavior".)
