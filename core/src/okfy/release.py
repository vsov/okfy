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


def _check_validation(bundle: Bundle, problems: list):
    """Compose the FULL strict validation into the release predicate. The
    audit's bypass was exact: release-check checked completeness, validate
    checked consistency, and nothing required both — delete package.json and
    release-check stayed green (audit round 7, finding 1)."""
    from okfy.archetype import load_archetype
    from okfy.validate import validate_conformance, validate_integrity
    r = validate_conformance(bundle)
    plan = bundle.plan()
    arch = None
    name = (plan.meta.get("archetype") if plan else None)
    if name:
        try:
            arch = load_archetype(str(name))
        except FileNotFoundError:
            problems.append(f"E_REL_VALIDATE: unknown archetype {name!r}")
    r2 = validate_integrity(bundle, arch, strict_sources=True,
                            strict_quality=True, strict_provenance=True,
                            strict_package=True, strict_schema=True)
    errors = r.errors + r2.errors
    if errors:
        codes = sorted({f.code for f in errors})
        problems.append(
            f"E_REL_VALIDATE: {len(errors)} strict validation error(s) "
            f"({', '.join(codes[:6])}{', ...' if len(codes) > 6 else ''}) — "
            "run okfy validate with all strict flags for detail")


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
    # fail-closed on the plan itself: an empty segment list or a segment
    # parked outside `done` is missing evidence, not absent obligation —
    # flipping done→pending must not turn the gate green
    if not segs:
        problems.append("E_REL_SEGMENTS: extraction plan has no segments — "
                        "nothing proves what was extracted; declare "
                        "provenance: legacy explicitly if this bundle "
                        "predates the job chain")
        return
    not_done = [str(s.get("id")) for s in segs if s.get("status") != "done"]
    if not_done:
        problems.append(f"E_REL_SEGMENTS: segment(s) not done: "
                        f"{', '.join(not_done)} — an unfinished extraction "
                        "cannot be released")
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
    import json as _json

    from okfy.evaluation import eval_status, load_evals
    try:
        runs = load_evals(bundle).get("runs") or []
    except (_json.JSONDecodeError, AttributeError, TypeError) as e:
        # The eval record IS the acceptance evidence. Unreadable evidence is a
        # failure of the record, and it has to say so rather than crash the
        # predicate: "cannot check" must never reach the caller as an exception
        # that a wrapper might swallow into a pass.
        problems.append(f"E_REL_EVAL_INVALID: meta/eval.json cannot be read "
                        f"({type(e).__name__}: {e}) — the acceptance evidence "
                        "is unreadable, so no verdict in it can be trusted")
        return
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
    acceptance = _acceptance(bundle)
    # NOT min(min_pass, t["of"]). The clamp silently rewrote the policy to fit
    # whatever the bundle happened to offer: one test query and one owner pass
    # satisfied a declared minimum of eight, and a negative minimum accepted a
    # bundle whose only query failed. A bar that adapts to the evidence is not a
    # bar. Too few queries to meet the bar is reported below as a surface
    # problem, which is what it is.
    raw_min = acceptance.get("min_owner_pass", DEFAULT_MIN_OWNER_PASS)
    if isinstance(raw_min, bool) or not isinstance(raw_min, int):
        # _check_acceptance_readable already reported it; do not also crash on it
        return
    min_pass = raw_min
    if t["passes_owner"] < min_pass:
        problems.append(f"E_REL_EVAL_POLICY: {t['passes_owner']}/{t['of']} "
                        f"owner passes < policy minimum {min_pass}")
    notes.append(f"eval {st['run_id']}: {t['passes_owner']}/{t['of']} owner "
                 f"passes (policy min {min_pass})")


MIN_TEST_QUERIES = 10


def _acceptance(bundle: Bundle) -> dict:
    """`acceptance` as a mapping, whatever is actually in the file.

    Malformed evidence must produce a machine-readable FAIL, not a traceback.
    `acceptance: "required"` used to reach `.get` and raise AttributeError, so
    the only thing standing between a malformed bundle and a green result was the
    process dying — which is fail-closed by accident, not by contract. The shape
    itself is reported by validate (`E_ACCEPTANCE_SHAPE`) and surfaces here as
    `E_REL_VALIDATE`; this helper only keeps the gates that follow readable."""
    acc = bundle.purpose().get("acceptance")
    return acc if isinstance(acc, dict) else {}


def _check_acceptance_readable(bundle: Bundle, problems: list, notes: list):
    """Every acceptance value the gates below will act on must be usable.

    validate reports these too, but release-check must not depend on that: it is
    the predicate other things call, so it owes them a verdict rather than an
    exception."""
    acc = bundle.purpose().get("acceptance")
    if acc is not None and not isinstance(acc, dict):
        problems.append(
            f"E_REL_ACCEPTANCE_INVALID: meta/purpose.md `acceptance` is "
            f"{type(acc).__name__}, not a mapping — the release policy cannot "
            "be read, so nothing about it can be checked")
        return
    acc = acc or {}
    v = acc.get("min_owner_pass", DEFAULT_MIN_OWNER_PASS)
    if isinstance(v, bool) or not isinstance(v, int):
        problems.append(
            f"E_REL_ACCEPTANCE_INVALID: acceptance.min_owner_pass is "
            f"{type(v).__name__} {v!r}, not an integer — the bar is unusable")
    if acc.get("dissent") is not None and acc.get("dissent") != "required":
        problems.append(
            f"E_REL_ACCEPTANCE_INVALID: acceptance.dissent={acc['dissent']!r} "
            "is not 'required' — the only value the dissent gate recognises, so "
            "as written the contract is declared and never enforced")


def _check_acceptance_surface(bundle: Bundle, problems: list, notes: list):
    """The acceptance surface itself, before any verdict on it.

    `min_owner_pass` used to be clamped to the query count, so the whole
    declared contract could be satisfied by shrinking the evidence: a bundle
    with one test query and one owner pass met a stated bar of eight. Removing
    the clamp is only half the fix — the other half is refusing a surface too
    small or too degenerate to hold a bar. Ten is the number every archetype's
    interview asks for and every accepted bundle here carries."""
    queries = [str(q) for q in (bundle.purpose().get("test_queries") or [])]
    blank = sum(1 for q in queries if not q.strip())
    normalised = {" ".join(q.lower().split()) for q in queries if q.strip()}
    if len(queries) < MIN_TEST_QUERIES:
        problems.append(
            f"E_REL_EVAL_SURFACE: {len(queries)} test_queries in "
            f"meta/purpose.md, {MIN_TEST_QUERIES} required for release — the "
            "acceptance bar is meaningless on a surface smaller than itself")
    if blank:
        problems.append(f"E_REL_EVAL_SURFACE: {blank} blank test_queries "
                        "entries — a blank query cannot be judged and only "
                        "inflates the count")
    dupes = len([q for q in queries if q.strip()]) - len(normalised)
    if dupes > 0:
        problems.append(
            f"E_REL_EVAL_SURFACE: {dupes} duplicate test_queries entries "
            "after normalising case and whitespace — repeating a query buys "
            "owner verdicts without buying coverage")


def _check_l3(bundle: Bundle, problems: list, notes: list):
    pf = bundle.get("meta/purpose-fitness")
    if pf is None:
        problems.append("E_REL_L3_MISSING: meta/purpose-fitness.md missing")
        return
    fails = [x for x in (pf.meta.get("rows") or [])
             if isinstance(x, dict) and str(x.get("verdict")) == "fail"]
    acceptance = _acceptance(bundle)
    if fails and not acceptance.get("allow_l3_fail"):
        problems.append(
            f"E_REL_L3_FAIL: {len(fails)} purpose-fitness fail verdict(s) "
            "and no acceptance.allow_l3_fail policy in meta/purpose.md — "
            "fix the concepts or state the exception explicitly")


def _check_dissent(bundle: Bundle, problems: list, notes: list):
    """Completeness half of the shadow consolidation audit: every multi-draft
    merge group must carry an adjudication row, and a waiver must still match the
    concept it waived.

    OPT-IN. This runs only when meta/purpose.md declares `acceptance.dissent:
    required`. A bundle accepted before v0.10 has no dissent ledger, and failing
    it for missing an artifact that did not exist at acceptance time would be
    retroactive — the same reasoning behind `provenance: legacy`. The check tests
    that adjudication HAPPENED, never that it was rigorous: a lazy adjudicator
    satisfies it. That limit is real and is stated in the notes."""
    from okfy.dissent import group_state
    from okfy.merge_audit import merge_groups, recover_drafts
    acceptance = _acceptance(bundle)
    if str(acceptance.get("dissent") or "") != "required":
        return
    state, groups = merge_groups(bundle)
    if state != "ok":
        # A bundle that declared the required-contract cannot satisfy it with an
        # absent ledger. Reporting "nothing to adjudicate" here let the strongest
        # possible evasion — never recording a merge_map at all — read as green.
        problems.append(
            "E_REL_DISSENT_UNVERIFIABLE: acceptance.dissent is required but no "
            "ledger row carries a merge_map, so the merge groups cannot be "
            "reconstructed and adjudication cannot be checked — record the "
            "merge map at consolidation or drop the required contract")
        return
    # A dissent row claims "I compared these drafts and this is what I found".
    # Once the drafts cannot be recovered, that claim is unfalsifiable: nobody
    # can re-derive it, and a future re-adjudication is impossible. Under the
    # required contract that is a failure of the evidence, not a detail — the
    # same standard merge-audit applies to itself when it refuses to report
    # "clean" for groups it could not read.
    rstate, _ = recover_drafts(bundle, sorted({d for g in groups
                                               for d in g["drafts"]}))
    if rstate not in ("live", "git"):
        problems.append(
            f"E_REL_DISSENT_UNVERIFIABLE: acceptance.dissent is required but the "
            f"pre-consolidation drafts cannot be recovered ({rstate}) — every "
            f"adjudication in meta/dissent.jsonl is unfalsifiable and cannot be "
            f"redone; restore the history that holds drafts/ or drop the "
            f"required contract")
        return

    unadjudicated, open_, stale = [], [], []
    for g in groups:
        st = group_state(bundle, g["final"], g["drafts"])
        if st == "unadjudicated":
            unadjudicated.append(g["final"])
        elif st == "open":
            open_.append(g["final"])
        elif st == "stale":
            stale.append(g["final"])
    if unadjudicated and not acceptance.get("allow_open_dissent"):
        problems.append(
            f"E_REL_DISSENT_UNADJUDICATED: {len(unadjudicated)} merge group(s) "
            f"have no dissent row (e.g. {', '.join(unadjudicated[:3])}) — run the "
            "schism pass or state acceptance.allow_open_dissent")
    if open_ and not acceptance.get("allow_open_dissent"):
        problems.append(
            f"E_REL_DISSENT_OPEN: {len(open_)} merge group(s) hold an unresolved "
            f"split (e.g. {', '.join(open_[:3])}) — `okfy dissent waive --owner` "
            "or split the concept")
    if stale:
        problems.append(
            f"E_REL_DISSENT_STALE: {len(stale)} adjudication(s) no longer match "
            f"what they ruled on (e.g. {', '.join(stale[:3])}) — the merged "
            "concept's bytes or the group's draft set moved after the ruling; "
            "re-adjudicate")
    if acceptance.get("allow_open_dissent"):
        notes.append("dissent: acceptance.allow_open_dissent is set — "
                     "unadjudicated and open groups are not blocking")
    notes.append(f"dissent: {len(groups)} merge group(s) checked for adjudication "
                 "completeness only — this does not attest that any adjudication "
                 "was adversarial")


def release_check(bundle: Bundle) -> dict:
    """The machine predicate for 'release accepted'. Fail-closed: missing
    evidence is a failure, not a skip."""
    problems: list[str] = []
    notes: list[str] = []
    _check_validation(bundle, problems)
    _check_provenance_complete(bundle, problems, notes)
    _check_acceptance_readable(bundle, problems, notes)
    _check_acceptance_surface(bundle, problems, notes)
    _check_eval(bundle, problems, notes)
    _check_l3(bundle, problems, notes)
    _check_dissent(bundle, problems, notes)
    return {"ok": not problems, "problems": problems, "notes": notes,
            "retrieval_fingerprint": retrieval_fingerprint(bundle)}
