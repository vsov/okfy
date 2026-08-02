import sys

from okfy.bundle import Bundle
from okfy.evaluation import eval_run, eval_status, eval_verdict
from okfy.job import build_job, job_digest, load_job, write_job
from okfy.ledger import add_row, parse_merge_map, read_rows
from okfy.proposals import clear_stale, set_stale

from .common import _print


def cmd_eval(a) -> int:
    b = Bundle(a.bundle)
    if a.ecmd == "run":
        _print(eval_run(b, n=a.n, suite=a.suite))
    elif a.ecmd == "verdict":
        _print(eval_verdict(b, a.run, a.q_index, a.role, a.verdict,
                            a.reason or "", suite=a.suite))
    else:
        st = eval_status(b, a.run, suite=a.suite)
        _print(st)
        if st["provisional"]:              # ADR-0013: say it loudly
            t = st["totals"]
            print(f"PROVISIONAL: {t['owner_confirmed']}/{t['of']} "
                  f"owner-confirmed ({t['provisional']} llm-only, "
                  f"{t['pending']} pending) — release acceptance counts "
                  "owner verdicts only", file=sys.stderr)
    return 0


def cmd_job(a) -> int:
    import json as _json
    b = Bundle(a.bundle)
    execution = None
    if getattr(a, "execution_file", None):
        execution = _json.loads(a.execution_file.read_text(encoding="utf-8"))
    job = build_job(b, a.segment_id, prompt_file=a.prompt_file,
                    execution=execution)
    write_job(b, job)
    _print(job)
    return 0


def cmd_ledger(a) -> int:
    b = Bundle(a.bundle)
    if a.dcmd == "add":
        inputs = [s for s in (x.strip() for x in a.inputs.split(",")) if s]
        outputs = [s for s in (x.strip() for x in a.outputs.split(",")) if s]
        jd = None
        if a.job:
            # --job takes a SEGMENT ID: the core loads the frozen artifact and
            # computes the digest itself — a hand-passed digest proves nothing
            job = load_job(b, a.job)
            if job.get("segment") != a.segment:
                raise ValueError(f"job artifact is for segment "
                                 f"{job.get('segment')!r}, row is for {a.segment!r}")
            jd = job_digest(job)
        row = add_row(b, a.run, a.segment, inputs, a.prompt_version, outputs,
                      a.validation, merge_map=parse_merge_map(a.merge_map),
                      job_digest=jd)
        _print(row)
    else:
        for r in read_rows(b, run_id=a.run):
            mm = r.get("merge_map")
            extra = f" merge={len(mm)}" if mm else ""
            print(f"{r['run_id']} {r['segment']} [{r['validation']}] "
                  f"{r['prompt_version']} in={len(r['inputs'])} "
                  f"out={len(r['outputs'])} @{r['commit'][:7]}{extra}")
    return 0


def cmd_merge_audit(a) -> int:
    """Read-only shadow consolidation report. Always exits 0 when the audit ran —
    findings are candidates for owner attention, not gate failures. Operational
    problems (missing bundle, unreadable path) surface as the CLI's usual exit 2."""
    from okfy.merge_audit import KINDS, audit_merge
    b = Bundle(a.bundle)
    out = audit_merge(b, ref=a.ref, only_group=a.group)
    if a.json:
        _print(out)
        return 0

    where = out["state"]
    if out["recovery"] == "git":
        where += f" (ref {(out['ref'] or '')[:7]})"
    print(f"merge-audit: {b.root}")
    print(f"state: {where}   groups: {out['groups_total']}   "
          f"with findings: {out['groups_with_findings']}")

    if not a.quiet:
        by_group: dict[str, list[dict]] = {}
        for f in out["findings"]:
            by_group.setdefault(f["group"], []).append(f)
        for group, findings in sorted(by_group.items()):
            drafts = sorted({d for f in findings for d in f["drafts"]})
            print(f"\n{group}   [{len(drafts)} draft(s) held lost values]")
            for f in findings:
                print(f"  {f['kind']:<14} {', '.join(f['drafts'])}  {f['value']}")

    print("\nsummary: " + " · ".join(f"{k} {out['by_kind'][k]}" for k in KINDS))
    if out["unverifiable"]:
        # loud on purpose: "could not check" must never look like "nothing wrong"
        print(f"unverifiable: {len(out['unverifiable'])} group(s) NOT AUDITED")
        for u in out["unverifiable"]:
            print(f"  {u['group']}: {u['reason']}")
    else:
        print("unverifiable: 0")
    for n in out["notes"]:
        print(f"note: {n}")
    return 0


def cmd_dissent(a) -> int:
    from okfy.dissent import add_row as dissent_add
    from okfy.dissent import group_state, read_rows, waive
    b = Bundle(a.bundle)
    if a.xcmd == "add":
        _print(dissent_add(b, a.run, a.group, a.draft, a.claim, a.anchor,
                           a.verdict, a.overruled_because))
    elif a.xcmd == "waive":
        _print(waive(b, a.group, a.reason))
    else:
        for r in read_rows(b, group=a.group):
            # An owner waiver carries `waiver`; it is NOT just another no-schism
            # row, and printing it as one would erase the distinction the whole
            # layer rests on — the party that recorded a merge cannot close the
            # objection to it.
            mark = "waived" if r.get("waiver") else r["verdict"]
            print(f"{r['group']} [{mark}] drafts={len(r['drafts'])} "
                  f"{r['claim'][:60]}")
        if a.group:
            print(f"state: {group_state(b, a.group)}")
    return 0


def cmd_stale(a) -> int:
    b = Bundle(a.bundle)
    if a.clear:
        clear_stale(b, a.concept_id)
        _print({"cleared": a.concept_id})
    else:
        set_stale(b, a.concept_id, a.reason)
        _print({"stale": a.concept_id, "reason": a.reason})
    return 0
