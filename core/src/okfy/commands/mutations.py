"""v0.24 (d): one table naming, for every leaf of the CLI's argparse tree,
whether it writes anything (to the bundle, the filesystem, or git) or is
read-only.

Keyed by the FULL subcommand path exactly as `okfy.cli.build_parser`'s
subparser tree spells it (`"review accept"`, `"eval metrics"`, `"propose"`,
`"diff"`, ...) — `tests/test_mutations_v024.py` walks the real parser tree
and fails on a missing or extra key, so this table cannot silently drift from
the commands that actually exist.

`True` means the command's INTENDED effect is a write (the bundle's files,
`.okfy-cache`, or a new directory it creates) — even a command that refuses
mid-way and writes nothing on THAT call is still `True` here, the same way
`propose` is `True` although a refused proposal writes nothing. `False` means
the command never writes by design; the same test file proves this
mechanically for every `False` leaf it can cheaply exercise (a synthetic git
bundle, snapshotted before/after — see `SKIP` there for the few it cannot).

This is documentation + a test fixture, not a runtime gate: nothing reads
this table to decide whether to allow a call. See GUIDE.md's "Mutation
table" section for the one-paragraph summary."""

MUTATES: dict[str, bool] = {
    "init": True,
    "survey": False,
    "segment": True,
    "segment-status": True,
    "glean": True,
    "cluster": False,
    "merge-audit": False,
    "cost": False,
    "budget": False,
    "validate": False,
    "release-check": False,
    "sourcemap": False,
    "index": True,            # writes .okfy-cache/index.json (and meta/package.json if present); --usage alone is read-only, but the leaf covers both
    "query": False,
    "show": False,
    "fresh": False,
    "links": False,
    "sample": False,
    "diff": False,
    "changes": False,         # v0.25: read-only window query over meta/memory.jsonl
    "snapshot": True,         # writes meta/corpus-manifest.json, meta/source-pins.json, meta/corpus.md
    "repair-links": True,     # default apply=True; --dry-run does not write, but the leaf's intent is a write
    "reanchor": True,         # v0.25 F02: default apply=True; --dry-run does not write, but the leaf's intent is a write (rewrites a concept's own sources: citation)
    "package": True,
    "log": True,
    "propose": True,          # a refused proposal (or --dry-run/all-refused --batch) writes nothing, but the verb's intent is a write
    "review list": False,
    "review show": False,
    "review accept": True,
    "review reject": True,
    "refine": True,
    "stale": True,            # default sets/clears the stale flag; `--due` alone is a read-only listing, but the leaf covers both
    "eval run": True,
    "eval verdict": True,
    "eval status": False,
    "eval metrics": False,
    "eval compare": False,
    "eval qrels": False,
    "job": True,
    "dissent add": True,
    "dissent list": False,
    "dissent waive": True,
    "ledger add": True,
    "ledger list": False,
    "workspace init": True,
    "workspace status": False,       # workspace.py's _changed_concepts diff call
                                      # passes GIT_OPTIONAL_LOCKS=0 so it never
                                      # rewrites a member's .git/index (readers
                                      # audit finding 39) — this stays literally
                                      # False, not a documented exception
    "workspace export": True,
    "workspace package": True,
    "link-candidates": False,
    "transcript-lint": False,
    "codes": False,
}
