"""Fail-closed release gate (external audit round 6). `okfy validate
--strict-*` proves the consistency of whatever evidence exists; it cannot
demand evidence that was never produced (a bundle with no job artifacts has
nothing to cross-check). `release_check` closes that gap with three
completeness predicates:

1. Provenance completeness — every done worker segment in the extraction plan
   must have a frozen job artifact AND a ledger row whose job_digest matches
   it. Bundles extracted before the job chain existed can declare
   `provenance: legacy` in meta/purpose.md — reported, never silent.
2. Eval currency — the latest eval run must be owner-complete AND carry a
   retrieval_fingerprint matching the bundle's current retrieval contract
   (concept set, test queries, lexicon, tool version). A concept/lexicon/
   purpose edit after the run makes it stale evidence, not acceptance.
3. Acceptance policy — owner passes must meet the bundle's own bar
   (meta/purpose.md `acceptance.min_owner_pass`, default 8) and L3
   purpose-fitness must carry no `fail` verdicts unless the policy explicitly
   allows them (`acceptance.allow_l3_fail: true`).

`provisional: false` means "the owner finished looking", not "accepted" —
this module is the machine predicate for accepted."""
import hashlib
import json

from okfy import __version__
from okfy.bundle import Bundle

DEFAULT_MIN_OWNER_PASS = 8


def retrieval_fingerprint(bundle: Bundle) -> str:
    """Fingerprint of everything that shapes retrieval answers and the test
    contract: non-meta concept set (package fingerprint), purpose test
    queries, raw lexicon file, tool version. Any change → old eval is stale."""
    from okfy.validate import package_fingerprint
    lex = bundle.root / "meta" / "lexicon.md"
    lex_sha = (hashlib.sha256(lex.read_bytes()).hexdigest()
               if lex.is_file() else "")
    payload = json.dumps({
        "concepts": package_fingerprint(bundle),
        "test_queries": [str(q) for q in
                         (bundle.purpose().get("test_queries") or [])],
        "lexicon_sha256": lex_sha,
        "tool_version": __version__,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check_provenance_complete(bundle: Bundle, problems: list, notes: list):
    from okfy.job import job_digest
    from okfy.ledger import read_rows
    if str(bundle.purpose().get("provenance", "")).strip() == "legacy":
        notes.append("provenance: legacy declared in meta/purpose.md — "
                     "worker-job completeness not enforced")
        return
    plan = bundle.plan()
    segs = [s for s in (plan.meta.get("segments") if plan else []) or []
            if isinstance(s, dict)]
    done = [str(s["id"]) for s in segs if s.get("status") == "done"]
    rows = read_rows(bundle)
    by_seg = {}
    for row in rows:
        by_seg.setdefault(str(row.get("segment")), []).append(row)
    for seg in done:
        jf = bundle.root / "meta" / "jobs" / f"{seg}.json"
        if not jf.is_file():
            problems.append(f"E_REL_JOB_MISSING: done segment {seg} has no "
                            f"job artifact meta/jobs/{seg}.json")
            continue
        try:
            art = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append(f"E_REL_JOB_MISSING: job artifact for {seg} unreadable")
            continue
        ledgered = [row for row in by_seg.get(seg, [])
                    if row.get("job_digest") == job_digest(art)]
        if not ledgered:
            problems.append(f"E_REL_LEDGER_JOB: no ledger row for segment "
                            f"{seg} carries the job artifact's digest")


def _check_eval(bundle: Bundle, problems: list, notes: list):
    from okfy.evaluation import eval_status, load_evals
    runs = load_evals(bundle).get("runs") or []
    if not runs:
        problems.append("E_REL_EVAL_MISSING: no eval runs recorded")
        return
    st = eval_status(bundle, "latest")
    t = st["totals"]
    if st["provisional"]:
        problems.append(f"E_REL_EVAL_PROVISIONAL: latest run "
                        f"{st['run_id']} — {t['owner_confirmed']}/{t['of']} "
                        f"owner verdicts recorded")
    recorded = runs[-1].get("retrieval_fingerprint")
    current = retrieval_fingerprint(bundle)
    if recorded != current:
        problems.append(
            "E_REL_EVAL_STALE: latest eval run's retrieval_fingerprint "
            f"{'is missing' if not recorded else 'does not match'} — the "
            "concept set, lexicon, test queries or tool changed after the "
            "run; re-run the eval and repeat the owner checkpoint")
    acceptance = bundle.purpose().get("acceptance") or {}
    min_pass = int(acceptance.get("min_owner_pass", DEFAULT_MIN_OWNER_PASS))
    if t["passes_owner"] < min(min_pass, t["of"]):
        problems.append(f"E_REL_EVAL_POLICY: {t['passes_owner']}/{t['of']} "
                        f"owner passes < policy minimum {min_pass}")
    notes.append(f"eval {st['run_id']}: {t['passes_owner']}/{t['of']} owner "
                 f"passes (policy min {min_pass})")


def _check_l3(bundle: Bundle, problems: list, notes: list):
    pf = bundle.get("meta/purpose-fitness")
    if pf is None:
        problems.append("E_REL_L3_MISSING: meta/purpose-fitness.md missing")
        return
    fails = [x for x in (pf.meta.get("rows") or [])
             if isinstance(x, dict) and str(x.get("verdict")) == "fail"]
    acceptance = bundle.purpose().get("acceptance") or {}
    if fails and not acceptance.get("allow_l3_fail"):
        problems.append(
            f"E_REL_L3_FAIL: {len(fails)} purpose-fitness fail verdict(s) "
            "and no acceptance.allow_l3_fail policy in meta/purpose.md — "
            "fix the concepts or state the exception explicitly")


def release_check(bundle: Bundle) -> dict:
    """The machine predicate for 'release accepted'. Fail-closed: missing
    evidence is a failure, not a skip."""
    problems: list[str] = []
    notes: list[str] = []
    _check_provenance_complete(bundle, problems, notes)
    _check_eval(bundle, problems, notes)
    _check_l3(bundle, problems, notes)
    return {"ok": not problems, "problems": problems, "notes": notes,
            "retrieval_fingerprint": retrieval_fingerprint(bundle)}
