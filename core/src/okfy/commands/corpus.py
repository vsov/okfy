from pathlib import Path

from okfy.bundle import Bundle
from okfy.cluster import cluster_drafts
from okfy.repair import repair_links
from okfy.segment import (append_segments_to_plan, make_glean_segments,
                          make_segments, set_segment_status, survey,
                          write_segments_to_plan)
from okfy.update import anchored_source_share, refresh_snapshot, update_plan
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
    result = set_segment_status(b, a.segment_id, a.status, reason=a.reason)
    _print({"segment": result["segment_id"], "status": result["status"],
           "legacy_unbacked": result["legacy_unbacked"]})
    return 0


def cmd_cluster(a) -> int:
    b = Bundle(a.bundle)
    _print(cluster_drafts(b))
    return 0


def cmd_diff(a) -> int:
    """JSON by default, as in v0.23 (callers parse it); `--text` is the human
    summary. Both carry `protected`: a concept the owner verified or accepted is
    re-filed through `okfy propose`, never overwritten by a rewrite."""
    b = Bundle(a.bundle)
    plan = update_plan(b)
    if not getattr(a, "text", False):
        _print(plan)
        return 0

    diff = plan["diff"]
    print(f"diff: mode={diff['mode']} changed={len(diff['changed'])} "
          f"added={len(diff['added'])} removed={len(diff['removed'])}")
    if diff.get("skipped_dangling_symlinks"):
        print(f"skipped dangling symlinks: {diff['skipped_dangling_symlinks']}")

    share = anchored_source_share(b)
    if share is not None:
        print(f"anchored source share (meta/source-pins.json): {share:.1%}")

    if plan["affected"]:
        print(f"\naffected ({len(plan['affected'])}):")
        for cid in sorted(plan["affected"]):
            marker = " [protected]" if plan["protected"].get(cid) else ""
            outcomes = [v["outcome"] if isinstance(v, dict) else v
                       for v in plan["spans"].get(cid, {}).values()]
            span_note = ""
            if outcomes:
                if all(o in ("span_intact", "span_moved") for o in outcomes):
                    span_note = "  (measured: bytes unchanged)"
                else:
                    span_note = f"  (spans: {', '.join(sorted(set(outcomes)))})"
            print(f"  {cid}{marker}{span_note}  <- "
                 f"{', '.join(plan['affected'][cid])}")
    else:
        print("\naffected: none")

    if plan["reextract"]:
        print(f"\nreextract ({len(plan['reextract'])}): "
             + ", ".join(plan["reextract"]))

    if plan.get("reanchor"):
        print(f"\nreanchor ({len(plan['reanchor'])}): " + ", ".join(
            f"{r['concept']}:{r['source']}->{r['moved_to']}"
            for r in plan["reanchor"]))

    if plan["uncovered_new"]:
        print(f"\nuncovered_new ({len(plan['uncovered_new'])}): "
             + ", ".join(plan["uncovered_new"]))
    else:
        print("\nuncovered_new: none")

    if plan["stale_candidates"]:
        print(f"\nstale_candidates ({len(plan['stale_candidates'])}): "
             + ", ".join(plan["stale_candidates"]))
    else:
        print("\nstale_candidates: none")
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
