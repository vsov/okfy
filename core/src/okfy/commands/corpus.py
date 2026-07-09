from pathlib import Path

from okfy.bundle import Bundle
from okfy.cluster import cluster_drafts
from okfy.repair import repair_links
from okfy.segment import (make_segments, set_segment_status, survey,
                          write_segments_to_plan)
from okfy.update import refresh_snapshot, update_plan

from .common import _print


def cmd_survey(a) -> int:
    _print(survey(a.corpus))
    return 0


def cmd_segment(a) -> int:
    b = Bundle(a.bundle)
    s = survey(Path(b.get("meta/corpus").meta["corpus"]))
    segs = make_segments(s["files"], budget=a.budget,
                         include=a.include, exclude=a.exclude,
                         corpus=Path(s["corpus"]))
    write_segments_to_plan(b, segs)
    _print(segs)
    return 0


def cmd_segment_status(a) -> int:
    b = Bundle(a.bundle)
    set_segment_status(b, a.segment_id, a.status)
    _print({"segment": a.segment_id, "status": a.status})
    return 0


def cmd_cluster(a) -> int:
    b = Bundle(a.bundle)
    _print(cluster_drafts(b))
    return 0


def cmd_diff(a) -> int:
    b = Bundle(a.bundle)
    _print(update_plan(b))
    return 0


def cmd_snapshot(a) -> int:
    b = Bundle(a.bundle)
    refresh_snapshot(b)
    _print({"snapshot": "refreshed"})
    return 0


def cmd_repair_links(a) -> int:
    b = Bundle(a.bundle)
    _print(repair_links(b, apply=not a.dry_run))
    return 0
