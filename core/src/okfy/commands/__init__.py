"""Verb handlers for the okfy CLI.

Each handler takes the parsed argparse namespace and returns an exit code.
The argparse construction and dispatch live in okfy.cli; this package holds
the per-verb logic, grouped by domain.
"""
from .bundle import (cmd_index, cmd_init, cmd_log, cmd_package,
                     cmd_release_check, cmd_validate)
from .corpus import (cmd_cluster, cmd_diff, cmd_repair_links, cmd_segment,
                     cmd_segment_status, cmd_snapshot, cmd_survey)
from .quality import cmd_eval, cmd_job, cmd_ledger, cmd_stale
from .retrieval import cmd_links, cmd_query, cmd_sample, cmd_show
from .review import cmd_propose, cmd_refine, cmd_review
from .workspace import cmd_link_candidates, cmd_workspace

HANDLERS = {
    "init": cmd_init,
    "survey": cmd_survey,
    "segment": cmd_segment,
    "segment-status": cmd_segment_status,
    "cluster": cmd_cluster,
    "validate": cmd_validate,
    "release-check": cmd_release_check,
    "index": cmd_index,
    "query": cmd_query,
    "show": cmd_show,
    "links": cmd_links,
    "sample": cmd_sample,
    "diff": cmd_diff,
    "snapshot": cmd_snapshot,
    "repair-links": cmd_repair_links,
    "package": cmd_package,
    "log": cmd_log,
    "propose": cmd_propose,
    "review": cmd_review,
    "refine": cmd_refine,
    "stale": cmd_stale,
    "eval": cmd_eval,
    "job": cmd_job,
    "ledger": cmd_ledger,
    "workspace": cmd_workspace,
    "link-candidates": cmd_link_candidates,
}
