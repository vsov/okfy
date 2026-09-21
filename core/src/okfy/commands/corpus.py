from pathlib import Path

from okfy.bundle import Bundle
from okfy.cluster import cluster_drafts
from okfy.init import _corpus_git_sha
from okfy.repair import repair_links
from okfy.segment import (append_segments_to_plan, make_glean_segments,
                          make_segments, set_segment_status, survey,
                          write_segments_to_plan)
from okfy.proposals import list_proposals
from okfy.update import (E_BUNDLE_DIRTY, E_CORPUS_DIRTY,
                         E_UPDATE_PROPOSALS_PENDING, _corpus_dirty_paths,
                         anchored_source_share, refresh_snapshot,
                         repair_anchors, update_plan)
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


_DIRTY_PATHS_SHOWN = 10  # a readable number; the rest are counted, not named


def cmd_snapshot(a) -> int:
    """`okfy snapshot` pins the corpus's committed tree (v0.25 audit F03) —
    never the working tree — so a DIRTY corpus (uncommitted changes ahead of
    HEAD) is refused here, before `refresh_snapshot` is even called: silently
    pinning HEAD while the owner is mid-edit would look successful and still
    swallow whatever committed change HEAD represents. `--force` overrides
    and says so in the printed result; `refresh_snapshot(b, force=True)` is
    what makes that real rather than cosmetic (its own default, force=False,
    independently no-ops on a dirty corpus — this check is the user-facing
    half of that same decision, not a duplicate of it).

    v0.25 audit F04 adds two more refusals, both checked before
    `refresh_snapshot` runs, both overridden by the same `--force`:

    - A DIRTY BUNDLE (uncommitted changes in the bundle's own git working
      tree) — symmetric with the dirty-corpus check above: a snapshot pins a
      relationship between two COMMITTED states, and the bundle side can be
      uncommitted exactly the way the corpus side can. This is what actually
      closes the F04 witness: snapshotting between an uncommitted concept
      edit and the commit meant to land it must not silently advance the
      baseline past a change that might still fail to commit.
    - Proposals filed for THIS update still PENDING owner review — a
      snapshot declares the corpus baseline current, and an update whose
      proposals have not been accepted or rejected yet is not complete
      (phase-5.md). `okfy review list` names what is open."""
    b = Bundle(a.bundle)

    pending = list_proposals(b)
    if pending and not a.force:
        shown = ", ".join(p["id"] for p in pending[:_DIRTY_PATHS_SHOWN])
        more = (f" (+{len(pending) - _DIRTY_PATHS_SHOWN} more)"
               if len(pending) > _DIRTY_PATHS_SHOWN else "")
        raise ValueError(
            f"{E_UPDATE_PROPOSALS_PENDING}: {len(pending)} proposal(s) are "
            f"still pending owner review: {shown}{more}. Pending owner "
            "proposals are not a completed update — snapshotting now would "
            "declare the corpus baseline current while what to do about "
            "these concepts has not actually been decided, and the next "
            "okfy diff would report nothing pending even though they still "
            "are. Run `okfy review accept` / `okfy review reject` on each "
            "one (see `okfy review list`) and run okfy snapshot again, or "
            "pass --force to snapshot anyway and treat them as out of "
            "scope for this baseline")

    # Scoped to bundles that installed the write-policy hook (`okfy package`)
    # — that hook is the only mechanism that can refuse the commit and
    # strand an advanced snapshot; see refresh_snapshot's own docstring for
    # why an un-hooked bundle's uncommitted concepts are not this risk.
    bundle_dirty: list[str] = []
    if (b.root / ".git" / "hooks" / "pre-commit").is_file() \
            and _corpus_git_sha(b.root) is not None:
        bundle_dirty = _corpus_dirty_paths(b.root)

    if bundle_dirty and not a.force:
        shown = ", ".join(bundle_dirty[:_DIRTY_PATHS_SHOWN])
        more = (f" (+{len(bundle_dirty) - _DIRTY_PATHS_SHOWN} more)"
               if len(bundle_dirty) > _DIRTY_PATHS_SHOWN else "")
        raise ValueError(
            f"{E_BUNDLE_DIRTY}: the bundle itself has {len(bundle_dirty)} "
            f"uncommitted change(s): {shown}{more}. A snapshot pins a "
            "relationship between two COMMITTED states — the corpus's, and "
            "the bundle's own — so snapshotting now would record a "
            "baseline that a refused or abandoned commit could leave the "
            "bundle's own history never actually reaching (v0.25 audit "
            "F04). Commit or discard the bundle's local changes and run "
            "okfy snapshot again, or pass --force to snapshot anyway and "
            "pin the corpus baseline against this uncommitted bundle state")

    cm = b.get("meta/corpus")
    corpus_raw = Path(str(cm.meta.get("corpus") or "")) if cm else None
    corpus = corpus_raw if corpus_raw and corpus_raw.is_dir() else None

    dirty: list[str] = []
    if corpus is not None and _corpus_git_sha(corpus) is not None:
        dirty = _corpus_dirty_paths(corpus)

    if dirty and not a.force:
        shown = ", ".join(dirty[:_DIRTY_PATHS_SHOWN])
        more = (f" (+{len(dirty) - _DIRTY_PATHS_SHOWN} more)"
               if len(dirty) > _DIRTY_PATHS_SHOWN else "")
        raise ValueError(
            f"{E_CORPUS_DIRTY}: the corpus at {corpus} has {len(dirty)} "
            f"uncommitted change(s) — git mode pins the corpus's last "
            f"COMMIT, so snapshotting now would record a baseline that "
            f"disagrees with what is actually on disk: {shown}{more}. "
            "Commit or discard the changes and run okfy snapshot again, "
            "or pass --force to snapshot the last commit anyway and ignore "
            "the uncommitted changes")

    outcome = refresh_snapshot(b, force=a.force)
    result = {"snapshot": "refreshed" if outcome["refreshed"] else "skipped",
             **outcome}
    if (dirty or bundle_dirty or pending) and a.force:
        result["forced"] = True
        notes = []
        if pending:
            notes.append(f"{len(pending)} proposal(s) pending")
        if bundle_dirty:
            notes.append(f"bundle had {len(bundle_dirty)} uncommitted change(s)")
        if dirty:
            notes.append(f"corpus had {len(dirty)} uncommitted change(s)")
        result["note"] = ("snapshotted anyway, per --force: " + "; ".join(notes))
    _print(result)
    return 0 if outcome["refreshed"] else 1


def cmd_repair_links(a) -> int:
    b = Bundle(a.bundle)
    _print(repair_links(b, apply=not a.dry_run))
    return 0


def cmd_reanchor(a) -> int:
    """`okfy reanchor` (v0.25 audit F02): the ONLY thing that clears a pin
    `refresh_snapshot` flagged `anchor_stale` — see `repair_anchors`'s
    docstring for why rewriting the concept's own citation text is what
    makes the debt actually go away, rather than a snapshot-side reset.
    `--only` restricts repair to one `concept:source` pair (repeatable);
    omitted, every current `reanchor` entry is repaired. `--dry-run` reports
    what would be repaired without writing anything."""
    b = Bundle(a.bundle)
    only = None
    if a.only:
        only = set()
        for item in a.only:
            concept, _, source = item.partition(":")
            only.add((concept, source))
    _print(repair_anchors(b, only=only, apply=not a.dry_run))
    return 0
