from pathlib import Path

from okfy.bundle import Bundle
from okfy.cluster import cluster_drafts
from okfy.repair import repair_links
from okfy.segment import (append_segments_to_plan, make_glean_segments,
                          make_segments, set_segment_status, survey,
                          write_segments_to_plan)
from okfy.update import refresh_snapshot, update_plan
from okfy.validate import validate_integrity

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


def cmd_glean(a) -> int:
    """Queue a second pass over the corpus files the first pass left silent.

    The uncited list comes from `validate_integrity` rather than being recomputed
    here, so `okfy glean` and `okfy validate` can never disagree about what was
    missed.
    """
    b = Bundle(a.bundle)
    cov = validate_integrity(b).coverage
    if cov is None:
        _print({"segments": [], "files": 0,
                "note": "no coverage — the plan has no done segments"})
        return 0
    snap = b.get("meta/corpus")
    corpus = Path(str(snap.meta.get("corpus"))) if snap else None
    segs = make_glean_segments(b.plan().meta.get("segments") or [],
                               cov["uncited"], corpus=corpus, budget=a.budget)
    if segs:
        append_segments_to_plan(b, segs)
    _print({"segments": segs, "files": sum(len(s["files"]) for s in segs),
            "uncited_files": cov["uncited_files"], "files_pct": cov["files_pct"],
            "bytes_pct": cov["bytes_pct"], "bytes_state": cov["bytes_state"]})
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
