import sys

from okfy.bundle import Bundle
from okfy.evaluation import eval_run, eval_status, eval_verdict
from okfy.job import build_job, job_digest, load_job, write_job
from okfy.ledger import add_row, parse_merge_map, read_rows
from okfy.proposals import clear_stale, set_stale, overdue

from .common import _print


def cmd_eval(a) -> int:
    """`eval run` on a workspace replays its cross-bundle queries through the
    FEDERATED path; verdict and status are shared — they only need `.root`.
    `metrics`/`compare`/`qrels` are pure reads over meta/eval.json and take a
    plain Bundle — a workspace's federated eval runs are a fourth record era
    those readers already tolerate (see eval_metrics.py), not a separate path."""
    if a.ecmd in ("metrics", "compare", "qrels"):
        return _cmd_eval_metrics(a)
    from okfy.workspace import Workspace, is_workspace
    if is_workspace(a.bundle):
        b = Workspace.load(a.bundle)
    else:
        b = Bundle(a.bundle)
    if a.ecmd == "run":
        if is_workspace(a.bundle):
            from okfy.ws_release import ws_eval_run
            _print(ws_eval_run(b, n=a.n, suite=a.suite))
        else:
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


def _fmt_metric(m: dict) -> str:
    if m["value"] is None:
        return f"null ({m['reason']})"
    return f"{m['value']:.3f}"


def _cmd_eval_metrics(a) -> int:
    from okfy.eval_metrics import (eval_compare, eval_metrics, eval_qrels,
                                   workspace_pool_size)
    from okfy.workspace import Workspace, is_workspace
    # `eval metrics`'s concept pool must never be the WORKSPACE root's own
    # files (crosswalks/manifest, not concepts) — see eval_metrics.py's
    # workspace_pool_size docstring. `compare`/`qrels` don't read a pool, so
    # they're unaffected; only the metrics path below consumes the override.
    if is_workspace(a.bundle):
        ws = Workspace.load(a.bundle)
        b = Bundle(ws.root)
        n_pool_override = workspace_pool_size(ws)
    else:
        b = Bundle(a.bundle)
        n_pool_override = None
    if a.ecmd == "qrels":
        out = eval_qrels(b, unit=a.unit)
        if a.json:
            _print(out)
            return 0
        print(f"eval qrels ({out['unit']}): {len(out['rows'])} covered row(s)")
        for r in out["rows"]:
            print(f"  {r['query']!r} -> {r['concept']} "
                  f"[{len(r['spans'])} span(s), {len(r['unresolved'])} unresolved]")
        return 0
    if a.ecmd == "compare":
        out = eval_compare(b, a.base, a.head, suite=a.suite)
        if a.json:
            _print(out)
            return 0
        s = out["summary"]
        print(f"eval compare: {out['base_run_id']} -> {out['head_run_id']} "
              f"[{out['suite']}]")
        print(f"compared {s['compared']}, {s['with_differences']} differ, "
              f"only-in-base {s['only_in_base']}, only-in-head {s['only_in_head']}")
        for q in out["queries"]:
            bits = []
            if q["entered"]:
                bits.append(f"+{len(q['entered'])}")
            if q["left"]:
                bits.append(f"-{len(q['left'])}")
            if q["rank_moves"]:
                bits.append(f"{len(q['rank_moves'])} rank move(s)")
            if q["notes_gained"]:
                bits.append(f"+{len(q['notes_gained'])} note(s)")
            if q["notes_lost"]:
                bits.append(f"-{len(q['notes_lost'])} note(s)")
            if q.get("concept"):
                c = q["concept"]
                bits.append(f"{c['concept']} {c['base_rank']}->{c['head_rank']} "
                           f"{c['base_outcome']}->{c['head_outcome']}")
            print(f"  {q['query']!r}: {', '.join(bits)}")
        return 0
    # metrics
    ks = tuple(int(x) for x in a.k.split(",") if x.strip())
    out = eval_metrics(b, run_id=a.run, suite=a.suite, ks=ks,
                       n_pool_override=n_pool_override)
    if a.json:
        _print(out)
        return 0
    print(out["label"])
    rk = out["ranking"]
    print(f"n_covered_with_concept: {rk['n_covered_with_concept']}")
    for k in ks:
        m = rk["recall"][str(k)]
        print(f"  recall@{k}: {_fmt_metric(m)}  "
              f"(oracle {_fmt_metric(m['oracle'])}, random {_fmt_metric(m['random'])}"
              f"{', DEGENERATE: ' + m['degenerate_reason'] if m['degenerate'] else ''})")
    m = rk["mrr"]
    print(f"  MRR: {_fmt_metric(m)}  "
          f"(oracle {_fmt_metric(m['oracle'])}, random {_fmt_metric(m['random'])}"
          f"{', DEGENERATE: ' + m['degenerate_reason'] if m['degenerate'] else ''})")
    ab = out["abstention"]
    print(f"n_not_covered: {ab['n_not_covered']}  n_fired: {ab['n_fired']}")
    print(f"  abstention precision: {_fmt_metric(ab['precision'])}  "
          f"recall: {_fmt_metric(ab['recall'])}  f1: {_fmt_metric(ab['f1'])}")
    aa, na = ab["always_abstain"], ab["never_abstain"]
    print(f"  always-abstain: P {_fmt_metric(aa['precision'])} "
          f"R {_fmt_metric(aa['recall'])} F1 {_fmt_metric(aa['f1'])}")
    print(f"  never-abstain:  P {_fmt_metric(na['precision'])} "
          f"R {_fmt_metric(na['recall'])} F1 {_fmt_metric(na['f1'])}")
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
    import json as _json
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
        spans = None
        if getattr(a, "spans_file", None):
            # the worker's own report; read verbatim, validated by check_spans
            spans = _json.loads(a.spans_file.read_text(encoding="utf-8"))
        row = add_row(b, a.run, a.segment, inputs, a.prompt_version, outputs,
                      a.validation, merge_map=parse_merge_map(a.merge_map),
                      job_digest=jd, spans=spans)
        _print(row)
    else:
        rows = read_rows(b, run_id=a.run)
        if getattr(a, "json", False):
            # Per-reason totals (v0.25, report item 2.2): summed across every
            # listed row's `dropped` block — the writer's own vocabulary, the
            # core only counts. No new command: this is the existing
            # ledger-state-reporting command's JSON, per the phase spec.
            totals: dict[str, int] = {}
            for r in rows:
                for reason, count in (r.get("dropped") or {}).items():
                    totals[reason] = totals.get(reason, 0) + count
            _print({"rows": rows, "dropped_totals": totals})
            return 0
        for r in rows:
            mm = r.get("merge_map")
            extra = f" merge={len(mm)}" if mm else ""
            sp = r.get("spans")
            if sp:
                extra += (f" spans=c{len(sp.get('covered') or {})}"
                          f"/e{len(sp.get('reviewed_empty') or {})}"
                          f"/d{len(sp.get('dropped') or {})}")
            dr = r.get("dropped")
            if dr:
                extra += f" dropped={sum(dr.values())}"
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
    if a.due:
        _print(overdue(b))
        return 0
    if not a.concept_id:
        raise ValueError("stale needs a concept id unless --due")
    if a.clear:
        clear_stale(b, a.concept_id)
        _print({"cleared": a.concept_id})
    else:
        set_stale(b, a.concept_id, a.reason)
        _print({"stale": a.concept_id, "reason": a.reason})
    return 0


def cmd_transcript_lint(a) -> int:
    """Read-only structural report over a HOST session transcript. Always
    exits 0 when the file was readable at all — a malformed or truncated
    transcript comes back `partial`, never a crash. See transcript_lint.py's
    module docstring for exactly what is and is not checked."""
    from okfy.transcript_lint import lint_transcript
    b = Bundle(a.bundle)
    out = lint_transcript(b, a.session)
    if a.json:
        _print(out)
        return 0

    print(f"transcript-lint: {out['session']}")
    print(f"bundle: {out['bundle']}")
    if out["partial"]:
        print(f"PARTIAL: {out['skipped_lines']} line(s) could not be parsed, "
              f"{out['skipped_blocks']} block(s) skipped (unexpected shape)")
    tc = out["tool_calls"]
    print("tool calls: " + " · ".join(f"{k} {tc[k]}" for k in
                                      ("SEARCH", "SHOW", "PROPOSE", "MUTATION")))

    fs, fm = out["first_search"], out["first_mutation"]
    fs_label = fs["index"] if fs else "none"
    fm_label = f"{fm['index']} ({fm['tool']})" if fm else "none"
    print(f"first search: {fs_label}   first mutation: {fm_label}")
    print(f"searched before first mutation: {out['searched_before_first_mutation']}")

    if out["proposes"]:
        print("\nproposes:")
        for p in out["proposes"]:
            print(f"  #{p['index']}  searched_before={p['searched_before']}  "
                  f"target_shown={p['target_shown']}")

    if out["cited_ids"]:
        print("\ncited ids:")
        for c in out["cited_ids"]:
            print(f"  {c['id']}  [{c['status']}]")

    print(f"\n{out['label']}")
    return 0
