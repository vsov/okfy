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
        _print(eval_run(b, n=a.n))
    elif a.ecmd == "verdict":
        _print(eval_verdict(b, a.run, a.q_index, a.role, a.verdict,
                            a.reason or ""))
    else:
        st = eval_status(b, a.run)
        _print(st)
        if st["provisional"]:              # ADR-0013: say it loudly
            t = st["totals"]
            print(f"PROVISIONAL: {t['owner_confirmed']}/{t['of']} "
                  f"owner-confirmed ({t['provisional']} llm-only, "
                  f"{t['pending']} pending) — release acceptance counts "
                  "owner verdicts only", file=sys.stderr)
    return 0


def cmd_job(a) -> int:
    b = Bundle(a.bundle)
    job = build_job(b, a.segment_id, prompt_file=a.prompt_file)
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


def cmd_stale(a) -> int:
    b = Bundle(a.bundle)
    if a.clear:
        clear_stale(b, a.concept_id)
        _print({"cleared": a.concept_id})
    else:
        set_stale(b, a.concept_id, a.reason)
        _print({"stale": a.concept_id, "reason": a.reason})
    return 0
