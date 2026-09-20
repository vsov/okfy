"""Fail-closed release gate (external audit round 6). `okfy validate
--strict-*` proves the consistency of whatever evidence exists; it cannot
demand evidence that was never produced (a bundle with no job artifacts has
nothing to cross-check). `release_check` closes that gap with three
completeness predicates:

1. Provenance completeness — every done worker segment in the extraction plan
   must have a frozen job artifact AND a ledger row whose job_digest matches
   it. Bundles extracted before the job chain existed can declare
   `provenance: legacy` in meta/purpose.md — reported, never silent.
1b. Span outcome completeness (v0.19) — every done segment must record what
   happened to each span it was assigned, and no span may be `dropped`.
   `reviewed_empty` never blocks. The same `provenance: legacy` escape applies,
   and is likewise reported.
2. Eval currency — the latest eval run must be owner-complete AND carry a
   retrieval_fingerprint matching the bundle's current retrieval contract: the
   digest of the deterministic live index, the normalised lexicon rows, the
   test and adversarial queries, and the bytes of the retrieval modules
   (`okfy-retrieval@3`).
   A concept/lexicon/purpose edit after the run makes it stale evidence, not
   acceptance.
3. Acceptance policy — owner passes must meet the bundle's own bar
   (meta/purpose.md `acceptance.min_owner_pass`, default 8) and L3
   purpose-fitness must carry no `fail` verdicts unless the policy explicitly
   allows them (`acceptance.allow_l3_fail: true`).

`provisional: false` means "the owner finished looking", not "accepted" —
this module is the machine predicate for accepted."""
import hashlib
import json

from okfy.bundle import Bundle

DEFAULT_MIN_OWNER_PASS = 8


FINGERPRINT_SCHEMA = "okfy-retrieval@3"
# The modules that decide what a query returns. Hashing their bytes replaces
# `tool_version`, which was both too coarse and too broad: a patch touching the
# CLI help text invalidated every recorded eval, while a change to ranking inside
# an unchanged version would not have been noticed at all.
RETRIEVAL_MODULES = ("bm25.py", "index.py", "lexicon.py", "query.py")
# The lexicon fields expand() reads. Everything else in a row is documentation.
EXPANSION_FIELDS = ("term", "status", "maps_to", "canonical_terms")


def retrieval_code_digest() -> str:
    from pathlib import Path

    import okfy
    root = Path(okfy.__file__).parent
    lines = [f"{n}:{hashlib.sha256((root / n).read_bytes()).hexdigest()}"
             for n in sorted(RETRIEVAL_MODULES)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def retrieval_fingerprint(bundle: Bundle) -> str:
    """Fingerprint of everything that decides what a query returns.

    Four inputs, and the reason for each:

    - the digest of the DETERMINISTIC live index (`build_index`, never the
      gitignored cache), so the evidence is pinned to the retrievable content
      rather than to a derived file that may be missing or out of date — and
      narrowed to non-meta concepts, which is what an eval run actually queries;
    - the NORMALISED lexicon rows, not the file's bytes: reflowing prose or
      reordering keys no longer invalidates a recorded eval, while a semantic
      change to expansion still does;
    - the purpose test queries AND the adversarial queries with their declared
      expectations, which together are the contract being judged;
    - the bytes of the retrieval modules themselves.

    `okfy-retrieval@3` is in the payload, so runs recorded under an old scheme
    stay stale by construction — they were judged against a different definition
    of "the same bundle", and silently accepting them would be the fail-open this
    replaces."""
    from okfy.index import build_index, retrieval_digest
    from okfy.lexicon import load_rows
    idx = build_index(bundle)
    try:
        rows = load_rows(bundle)
        # Only the fields expansion actually reads. `language`, `note` and any
        # unknown key are documentation, and letting them into the contract
        # invalidated a recorded eval for an edit that cannot change an answer.
        lexicon = [{k: r.get(k) for k in EXPANSION_FIELDS if k in r}
                   for r in rows]
    except (ValueError, AttributeError, TypeError) as e:
        # A malformed lexicon still has to yield a stable fingerprint: validate
        # reports the malformation, and this must not raise inside a predicate.
        lexicon = [{"__unreadable__": f"{type(e).__name__}: {e}"}]
    payload = json.dumps({
        "fingerprint_schema": FINGERPRINT_SCHEMA,
        "index": retrieval_digest(idx),
        "lexicon_rows": lexicon,
        "test_queries": [str(q) for q in
                         (bundle.purpose().get("test_queries") or [])],
        # the adversarial queries and their declared expectations are part of the
        # judged contract too: change what you expect, and the recorded verdicts
        # were given about a different question
        "adversarial_queries": sorted(
            json.dumps(q, sort_keys=True, ensure_ascii=False) if isinstance(q, dict)
            else str(q)
            for q in (bundle.purpose().get("adversarial_queries") or [])),
        "retrieval_code": retrieval_code_digest(),
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
    # ALL SIX strict flags. `strict_execution` was the one omitted, so a
    # release accepted job artifacts with no executor identity while the
    # documented workflow prescribed `--strict-execution` for every new
    # extraction — the contract disagreeing with itself, and release was the
    # half that was wrong. Bundles that predate the field declare
    # `provenance: legacy` and `_check_execution` returns on exactly that.
    r2 = validate_integrity(bundle, arch, strict_sources=True,
                            strict_quality=True, strict_provenance=True,
                            strict_package=True, strict_schema=True,
                            strict_execution=True)
    errors = r.errors + r2.errors
    if errors:
        codes = sorted({f.code for f in errors})
        problems.append(
            f"E_REL_VALIDATE: {len(errors)} strict validation error(s) "
            f"({', '.join(codes[:6])}{', ...' if len(codes) > 6 else ''}) — "
            "run okfy validate with all strict flags for detail")


def _check_source_map_release(bundle: Bundle, problems: list, notes: list):
    """The source map at release strictness. Same three outcomes as validate,
    one strictness harder — and the middle one is the reason this function is
    not a one-liner.

    ABSENT is not a defect and never blocks. None of the real bundles carries a
    sidecar; they are built from authored text and have no raw document to map
    back to. Blocking on absence would invent a regression the audit never asked
    for, so absence is silent here rather than a waived note.

    UNVERIFIABLE blocks. A row whose text hash could not be recomputed is not a
    row that matched, and releasing it as provenance would be the exact failure
    `okfy cost` and the injection gate already refuse — an unscanned file is not
    a clean one, and an unchecked mapping is not a verified one.

    FAILED blocks, obviously, and names the first few rows so the owner does not
    have to re-run the standalone command to learn what broke."""
    from okfy.sourcemap import check_source_map, normalization_problems
    # The BLOCK, before the sidecar and before the early return. These are
    # findings about meta/purpose.md, not about the sidecar's contents, so they
    # carry their own codes instead of arriving folded inside E_REL_SOURCEMAP.
    for pr in normalization_problems(bundle):
        problems.append(f"{pr['code']}: {pr['message']}")
    out = check_source_map(bundle)
    if out["state"] == "absent":
        return
    if out["problems"]:
        first = "; ".join(
            (f"line {p['line']} {p['code']}" if "line" in p else p["code"])
            for p in out["problems"][:3])
        problems.append(
            f"E_REL_SOURCEMAP: meta/source-map.jsonl has "
            f"{len(out['problems'])} problem(s) ({first}) — the sidecar is the "
            "only thing connecting a cited normalized span back to the raw "
            "document, and a broken one cannot be that")
    if out["unverifiable"]:
        problems.append(
            f"E_REL_SOURCEMAP_UNVERIFIABLE: {out['unverifiable']} of "
            f"{out['rows']} source-map row(s) could not be checked against the "
            "corpus — an unchecked mapping is not a verified one; make the "
            "corpus readable and re-run")
    if not out["problems"] and not out["unverifiable"]:
        notes.append(
            f"source map: {out['text_verified']}/{out['rows']} row(s) text-"
            f"verified, {out['raw_verified']} raw-verified; page and bbox are "
            "carried, not verified"
            + ("" if out.get("raw_root_readable") else
               " — declare normalization.raw_root to check the raw bytes"))


def _check_injection(bundle: Bundle, problems: list, notes: list):
    """Corpus-borne instructions block a release unless the owner declared
    otherwise — and the declaration never makes the count disappear.

    `acceptance.allow_injection: true` is the hatch, and it is deliberately
    NARROWER than it looks: it excuses the PHRASE rules, which are heuristics an
    owner can reasonably overrule after reading them, and it does NOT excuse
    `invisible-unicode`. A zero-width character or a bidi override inside an
    extracted concept has no benign reading — it is not a heuristic that fired,
    it is a fact about the bytes — so no acceptance key turns it off. That
    asymmetry is the whole reason the scan reports `kind` separately.

    A hatch that hid the count would repeat the `allow_open_dissent` mistake:
    the note names how many findings were waived and what kinds they were."""
    from okfy.injection import scan_bundle
    out = scan_bundle(bundle)
    if out["skipped"]:
        problems.append(
            f"E_REL_INJECTION: {len(out['skipped'])} file(s) could not be "
            f"scanned for injection ({out['skipped'][0]['reason']}) — an "
            "unscanned file is not a clean one")
    findings = out["findings"]
    if not findings:
        return
    phrase_n, unicode_n = out["by_kind"]["phrase"], out["by_kind"]["unicode"]
    rules = ", ".join(f"{k} {v}" for k, v in sorted(out["by_rule"].items()) if v)
    p = bundle.get("meta/purpose")
    acc = (p.meta.get("acceptance") if p else None) or {}
    if acc.get("allow_injection") is True:
        notes.append(
            f"acceptance.allow_injection: {phrase_n} phrase finding(s) waived "
            f"by the owner ({rules}) — run okfy validate --strict-injection "
            "to read them")
        if unicode_n:
            problems.append(
                f"E_REL_INJECTION: {unicode_n} invisible-unicode finding(s) — "
                "allow_injection excuses the phrase heuristics, never an "
                "invisible codepoint in an extracted concept")
        return
    problems.append(
        f"E_REL_INJECTION: {len(findings)} injection finding(s) "
        f"({phrase_n} phrase, {unicode_n} unicode; {rules}) — run okfy "
        "validate --strict-injection for the lines, then fix them or declare "
        "acceptance.allow_injection")


def _check_provenance_complete(bundle: Bundle, problems: list, notes: list):
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
    for seg in done:
        state = segment_provenance_state(bundle, seg)
        if state == "no-job":
            problems.append(f"E_REL_JOB_MISSING: done segment {seg} has no "
                            f"job artifact meta/jobs/{seg}.json")
        elif state == "job-unreadable":
            problems.append(f"E_REL_JOB_MISSING: job artifact for {seg} unreadable")
        elif state == "no-ledger-row":
            problems.append(f"E_REL_LEDGER_JOB: no ledger row for segment "
                            f"{seg} carries the job artifact's digest")


def segment_provenance_state(bundle: Bundle, segment_id: str) -> str:
    """Whether `segment_id` has the provenance a `done` status claims: a job
    artifact (`meta/jobs/<segment_id>.json`) AND a ledger row whose
    `job_digest` matches it. One of `ok`, `no-job`, `job-unreadable`,
    `no-ledger-row` — read-only, never writes, never raises.

    Shared by `_check_provenance_complete` above (which turns a bad state
    into `E_REL_JOB_MISSING`/`E_REL_LEDGER_JOB` across every `done` segment
    at release time) and `okfy.segment.set_segment_status`'s
    `E_SEGMENT_DONE_UNBACKED` (checked for ONE segment, before it is marked
    done — same predicate, checked earlier)."""
    from okfy.job import job_digest
    from okfy.ledger import read_rows
    jf = bundle.root / "meta" / "jobs" / f"{segment_id}.json"
    if not jf.is_file():
        return "no-job"
    try:
        art = json.loads(jf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "job-unreadable"
    ledgered = [row for row in read_rows(bundle)
               if str(row.get("segment")) == segment_id
               and row.get("job_digest") == job_digest(art)]
    return "ok" if ledgered else "no-ledger-row"


def _check_span_outcomes(bundle: Bundle, problems: list, notes: list):
    """Completeness of the span outcome report, the fail-closed half of what
    `_check_span_coverage` warns about. `validate` cannot demand evidence that
    was never produced; this can.

    Only `dropped` gates. `reviewed_empty` never blocks at any strictness:
    completeness does not mean every paragraph owes a concept, and a worker that
    is punished for saying "nothing here" will stop saying it. `dropped` is
    unambiguous — assigned material that was never looked at — so it is the one
    outcome a release cannot carry."""
    from okfy.ledger import latest_span_outcomes
    if str(bundle.purpose().get("provenance", "")).strip() == "legacy":
        notes.append("provenance: legacy declared in meta/purpose.md — span "
                     "outcome completeness not enforced; nothing records what "
                     "happened to this bundle's assigned spans")
        return
    plan = bundle.plan()
    done = [str(s.get("id")) for s in (plan.meta.get("segments") if plan else []) or []
            if isinstance(s, dict) and s.get("status") == "done"]
    if not done:
        return  # _check_provenance_complete already failed on the plan itself
    latest = latest_span_outcomes(bundle)
    missing = [s for s in done if s not in latest]
    if missing:
        problems.append(
            f"E_REL_SPAN_COVERAGE_MISSING: {len(missing)} done segment(s) carry "
            f"no span outcome report ({', '.join(missing)}) — a green ledger row "
            "proves a worker wrote something, not that it looked at everything "
            "it was given; record outcomes with `okfy ledger add --spans-file`, "
            "or declare provenance: legacy if this bundle predates them")
    for seg in done:
        dropped = (latest.get(seg) or {}).get("dropped") or {}
        if dropped:
            problems.append(
                f"E_REL_SPAN_DROPPED: segment {seg} records {len(dropped)} "
                f"dropped span(s) ({', '.join(sorted(dropped)[:3])}) — assigned "
                "material nobody looked at cannot be released as covered")
    total_empty = sum(len((v.get("reviewed_empty") or {})) for v in latest.values())
    if total_empty:
        notes.append(f"spans: {total_empty} reviewed-empty span(s) reported by "
                     "workers and not blocking — the report is the worker's own, "
                     "not a measurement")


# MEASURED, not guessed. On 2026-09-06 the worst real bundle here
# (sec-cftc-sfp-okf, 309 concepts) replayed a ten-query suite in 3.75 s —
# 0.375 s per query — and `_replay` is called once per suite, so the bound is
# per suite. 30 s is EIGHT TIMES that worst case, which leaves room for a bundle
# roughly eight times the size of the largest one here before the bound can fire
# on honest work.
#
# The old value was 10.0 and nothing measured it: at 3.75 s per suite it left
# 1.3x headroom, tight enough that a normal bundle could have tripped a bound
# that — as it happens — could not fire at all, because the clock was consulted
# only after the loop finished.
REPLAY_BUDGET_MEASURED_S = 3.75
REPLAY_BUDGET_MULTIPLE = 8
REPLAY_BUDGET_S = REPLAY_BUDGET_MEASURED_S * REPLAY_BUDGET_MULTIPLE


def _replay(bundle: Bundle, run: dict, suite: str, code: str,
            problems: list, notes: list) -> None:
    """Re-derive the recorded run and report what no longer matches.

    Only ever called when the fingerprint ALREADY matches. A bundle whose
    retrieval contract has moved is reported stale by the check above, and
    replaying it would produce a second finding about the same fact — with the
    misleading implication that someone edited the record, when in truth the
    index moved underneath it.

    The budget exists because this is real retrieval work at release time:
    0.375 s per query on the largest bundle here (measured 2026-09-06), so a
    ten-query suite spends about 3.75 s and `REPLAY_BUDGET_S` is eight times
    that.

    The clock is consulted BEFORE each query, so the bound stops work. Until
    v0.22 it was read only after the whole loop had finished — and the success
    path returned before even that — so this docstring described a behaviour the
    function did not have. Over budget it now stops and reports
    E_REL_REPLAY_INCOMPLETE naming how many queries went uncompared: a gate that
    quietly checks less than it claims is the exact failure this release is
    about, so partial coverage is a problem rather than a note."""
    import time

    from okfy.evaluation import replay_run_bounded
    if run.get("retrieval_schema") != FINGERPRINT_SCHEMA:
        # Exempt BY CONSTRUCTION, not by a list. A run recorded before
        # `query_options` existed cannot be replayed against a definition it
        # predates, and `_check_eval` already tolerates it on exactly this
        # condition. Using a different condition here would invent a finding
        # that the bundle has no way to answer — the run is what it is, and no
        # edit to the bundle could make an old record carry a new field.
        notes.append(f"{suite} replay: skipped — run predates "
                     f"{FINGERPRINT_SCHEMA} and records no invocation to "
                     "replay")
        return
    t0 = time.monotonic()
    try:
        diffs, compared, total = replay_run_bounded(bundle, run,
                                                    REPLAY_BUDGET_S)
    except (OSError, ValueError, KeyError, TypeError) as e:
        problems.append(
            f"{code}: the recorded {suite} run could not be replayed "
            f"({type(e).__name__}: {e}) — evidence that cannot be re-derived "
            "is not replayable evidence")
        return
    elapsed = time.monotonic() - t0
    if compared < total:
        # Reported BEFORE the differences, and as a problem. Whatever the
        # replayed prefix found, the rest of the run is unexamined, and "no
        # differences in the nine queries I got to" is not the claim this gate
        # is here to make.
        problems.append(
            f"E_REL_REPLAY_INCOMPLETE: the {suite} replay stopped after "
            f"{compared}/{total} queries and {elapsed:.1f}s, leaving "
            f"{total - compared} unexamined — the {REPLAY_BUDGET_S:.0f}s budget "
            "bounds release-time retrieval work, and a partial replay cannot "
            "stand as evidence for the whole run. Re-run "
            f"`okfy eval run <bundle> --suite {suite}` so the record is fresh, "
            "or shorten the suite")
    if not diffs and compared == total:
        notes.append(f"{suite} replay: {len(run.get('results') or [])} "
                     f"queries re-derived and identical ({elapsed:.1f}s)")
        return
    shown = diffs[:3]
    where = ", ".join(
        f"query {d['index']} field {d['field']}" if "index" in d
        else f"field {d['field']}" for d in shown)
    problems.append(
        f"{code}: the recorded {suite} run does not match what this bundle "
        f"produces now — {len(diffs)} difference(s), first: {where}. The "
        "fingerprint still matches, so the environment did not move: the record "
        f"did. Re-run `okfy eval run <bundle> --suite {suite}` and repeat the "
        "owner checkpoint.")
    if elapsed > REPLAY_BUDGET_S:
        notes.append(f"{suite} replay took {elapsed:.1f}s, over the "
                     f"{REPLAY_BUDGET_S:.0f}s budget")


def _record_invalid(run: dict, suite: str, problems: list) -> bool:
    """Report a malformed eval record and say whether to stop.

    One call site each in `_check_eval` and `_check_adversarial`, both BEFORE
    `eval_status` and the replay. Those two walk the record assuming every
    result is a mapping; a string in that position used to escape
    `release_check` as `AttributeError: 'str' object has no attribute 'get'`,
    which is the opposite of the machine-readable finding the README promises."""
    from okfy.evaluation import eval_record_problems
    bad = eval_record_problems(run)
    if not bad:
        return False
    problems.append(
        f"E_REL_EVAL_INVALID: the latest {suite} run "
        f"{run.get('run_id') if isinstance(run, dict) else run!r} is not a "
        f"valid eval record — {'; '.join(bad)}. Nothing in a malformed record "
        "can be judged or replayed; repair meta/eval.json or re-run "
        f"`okfy eval run <bundle> --suite {suite}` and judge it again")
    return True


def _check_eval(bundle: Bundle, problems: list, notes: list):
    import json as _json

    from okfy.evaluation import eval_status, latest_run, load_evals
    try:
        data = load_evals(bundle)
    except (_json.JSONDecodeError, AttributeError, TypeError) as e:
        # The eval record IS the acceptance evidence. Unreadable evidence is a
        # failure of the record, and it has to say so rather than crash the
        # predicate: "cannot check" must never reach the caller as an exception
        # that a wrapper might swallow into a pass.
        problems.append(f"E_REL_EVAL_INVALID: meta/eval.json cannot be read "
                        f"({type(e).__name__}: {e}) — the acceptance evidence "
                        "is unreadable, so no verdict in it can be trusted")
        return
    # The ACCEPTANCE suite's latest run, not the log's last row. An adversarial
    # run is appended after it and was masking it here.
    latest = latest_run(data, "acceptance")
    if latest is None:
        problems.append("E_REL_EVAL_MISSING: no eval runs recorded")
        return
    # BEFORE eval_status and before the replay. Both walk the nested structure
    # assuming every result is a mapping, so a malformed record reached them as
    # an AttributeError out of release_check rather than as a finding.
    if _record_invalid(latest, "acceptance", problems):
        return
    st = eval_status(bundle, "latest")
    t = st["totals"]
    if st["provisional"]:
        problems.append(f"E_REL_EVAL_PROVISIONAL: latest run "
                        f"{st['run_id']} — {t['owner_confirmed']}/{t['of']} "
                        f"owner verdicts recorded")
    # An eval run has to say how it was invoked. Without it the record cannot be
    # replayed, and `-n 0` produced ten queries with zero hits each, ten owner
    # passes over nothing, and a green release.
    opts = latest.get("query_options")
    if latest.get("retrieval_schema") == FINGERPRINT_SCHEMA:
        if not isinstance(opts, dict):
            problems.append(
                "E_REL_EVAL_INVALID: the latest eval run records no "
                "query_options — how it was invoked is part of the evidence, "
                "and a run that cannot be replayed cannot be acceptance")
        elif not isinstance(opts.get("n"), int) or isinstance(opts.get("n"), bool) \
                or opts["n"] < 1:
            problems.append(
                f"E_REL_EVAL_INVALID: the latest eval run recorded "
                f"query_options.n={opts.get('n')!r} — a run that retrieves "
                "nothing cannot have been judged")
        else:
            notes.append(f"eval invocation: {latest.get('suite', 'acceptance')} "
                         f"suite, {opts['n']} top hits per query")
    recorded = latest.get("retrieval_fingerprint")
    current = retrieval_fingerprint(bundle)
    if recorded != current:
        problems.append(
            "E_REL_EVAL_STALE: latest eval run's retrieval_fingerprint "
            f"{'is missing' if not recorded else 'does not match'} — the "
            "live index, lexicon rows, test queries or retrieval code changed "
            "after the run — or the run predates "
            f"{FINGERPRINT_SCHEMA}; re-run the eval and repeat the owner "
            "checkpoint")
    else:
        _replay(bundle, latest, "acceptance", "E_REL_EVAL_REPLAY",
                problems, notes)
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


def _surface_problems(queries: list, field: str,
                      where: str = "meta/purpose.md") -> list:
    """Shared shape check for an acceptance surface: enough of them, none blank,
    none a duplicate. Both suites are judged the same way — a bar is meaningless
    on a surface smaller than itself whichever set of questions it covers."""
    out = []
    texts = [str(q.get("query") if isinstance(q, dict) else q) for q in queries]
    blank = sum(1 for q in texts if not q.strip())
    normalised = {" ".join(q.lower().split()) for q in texts if q.strip()}
    if len(texts) < MIN_TEST_QUERIES:
        out.append(f"{len(texts)} {field} in {where}, "
                   f"{MIN_TEST_QUERIES} required for release — the acceptance "
                   "bar is meaningless on a surface smaller than itself")
    if blank:
        out.append(f"{blank} blank {field} entries — a blank query cannot be "
                   "judged and only inflates the count")
    dupes = len([q for q in texts if q.strip()]) - len(normalised)
    if dupes > 0:
        out.append(f"{dupes} duplicate {field} entries after normalising case "
                   "and whitespace — repeating a query buys owner verdicts "
                   "without buying coverage")
    return out


def _check_acceptance_surface(bundle: Bundle, problems: list, notes: list):
    """The acceptance surface itself, before any verdict on it.

    `min_owner_pass` used to be clamped to the query count, so the whole
    declared contract could be satisfied by shrinking the evidence: a bundle
    with one test query and one owner pass met a stated bar of eight. Removing
    the clamp is only half the fix — the other half is refusing a surface too
    small or too degenerate to hold a bar. Ten is the number every archetype's
    interview asks for and every accepted bundle here carries."""
    for msg in _surface_problems(bundle.purpose().get("test_queries") or [],
                                 "test_queries"):
        problems.append(f"E_REL_EVAL_SURFACE: {msg}")
    for msg in _surface_problems(bundle.purpose().get("adversarial_queries") or [],
                                 "adversarial_queries"):
        problems.append(f"E_REL_ADVERSARIAL_SURFACE: {msg}")


def _check_adversarial(bundle: Bundle, problems: list, notes: list):
    """The second acceptance layer, and the reason it is mandatory.

    Ten owner passes on the acceptance suite prove that ten phrasings chosen
    alongside the bundle work. They cannot show what the bundle answers
    confidently and wrongly, and measurements on real bundles here are blunt
    about it: a plain-English rephrasing of an accepted query returned zero
    relevant concepts in the top ten, and an out-of-scope product got the
    highest-scoring hit of an entire run with no coverage signal. Those defects
    were recorded as characterisation tests that PASS, so nothing in the repo
    ever failed while they held — honest, and completely without pressure.

    This suite supplies the pressure, inside the eval format rather than beside
    it: same runs, same verdict machinery, same fingerprint, `suite:
    adversarial`. What it adds is that each query declares its expectation up
    front, so the run carries a deterministic `met`/`unmet` outcome and an owner
    pass over an `unmet` outcome is visible as the override it is."""
    from okfy.evaluation import eval_status, latest_run, load_evals
    try:
        latest = latest_run(load_evals(bundle), "adversarial")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return                       # _check_eval already reported the file
    if latest is None:
        problems.append(
            "E_REL_ADVERSARIAL_MISSING: no adversarial eval run — ten owner "
            "passes on the queries the bundle was built for cannot show what it "
            "answers confidently and wrongly; run `okfy eval run <bundle> "
            "--suite adversarial` and judge it")
        return
    if _record_invalid(latest, "adversarial", problems):
        return
    st = eval_status(bundle, "latest", suite="adversarial")
    t = st["totals"]
    if st["provisional"]:
        problems.append(
            f"E_REL_ADVERSARIAL_PROVISIONAL: adversarial run {st['run_id']} — "
            f"{t['owner_confirmed']}/{t['of']} owner verdicts recorded")
    if latest.get("retrieval_fingerprint") != retrieval_fingerprint(bundle):
        problems.append(
            "E_REL_ADVERSARIAL_STALE: the adversarial run was judged against a "
            "different retrieval contract than the live bundle — re-run it "
            "alongside the acceptance suite")
    else:
        _replay(bundle, latest, "adversarial", "E_REL_ADVERSARIAL_REPLAY",
                problems, notes)
    acceptance = _acceptance(bundle)
    raw_min = acceptance.get("min_adversarial_pass", DEFAULT_MIN_OWNER_PASS)
    if isinstance(raw_min, bool) or not isinstance(raw_min, int):
        problems.append(
            f"E_REL_ACCEPTANCE_INVALID: acceptance.min_adversarial_pass is "
            f"{type(raw_min).__name__} {raw_min!r}, not an integer")
        return
    if t["passes_owner"] < raw_min:
        problems.append(
            f"E_REL_ADVERSARIAL_POLICY: {t['passes_owner']}/{t['of']} owner "
            f"passes on the adversarial suite < policy minimum {raw_min}")
    # An owner pass over an unmet expectation is legitimate — the expectation may
    # simply have been wrong — but it is an override, and an override that leaves
    # no trace is indistinguishable from not looking.
    if t.get("outcomes_unmet"):
        notes.append(
            f"adversarial: {t['outcomes_unmet']}/{t['of']} declared expectations "
            "were NOT met; every owner pass among them is an explicit override "
            "of a stated criterion")
    notes.append(f"adversarial {st['run_id']}: {t['passes_owner']}/{t['of']} "
                 f"owner passes (policy min {raw_min}), "
                 f"{t.get('outcomes_met', 0)} expectation(s) met")


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


def _check_memory(bundle: Bundle, problems: list, notes: list):
    """The working-memory profile, stated. `okfy review accept` puts one change
    into the bundle and does not re-run the eval, re-pin L3 or repackage — so a
    bundle with accepted memory is expected to fail the gates above until the
    owner runs the release loop again. This adds no problem of its own (those
    gates already fire); it says how much memory moved the bundle, so the
    reddening has a visible cause instead of looking like a regression.

    `meta/package.json` records `memory_accepted` when packaged; a package from
    before the ledger has no count, so every accept counts."""
    import json

    from okfy import memory
    total = sum(1 for row in memory.events(bundle)[0] if row["event"] == "accept")
    recorded = None
    try:
        recorded = json.loads((bundle.root / "meta" / "package.json")
                              .read_text(encoding="utf-8")).get("memory_accepted")
    except (OSError, ValueError, AttributeError):
        pass
    if isinstance(recorded, int) and not isinstance(recorded, bool):
        n, tail = max(total - recorded, 0), ""
    else:
        n = total
        tail = " (the package predates the memory ledger)" if total else ""
    notes.append(f"memory: {n} accepted proposal(s) since last package{tail} — "
                 "accept does not re-run the eval, re-pin L3 or repackage")


def release_check(bundle: Bundle) -> dict:
    """The machine predicate for 'release accepted'. Fail-closed: missing
    evidence is a failure, not a skip."""
    problems: list[str] = []
    notes: list[str] = []
    _check_validation(bundle, problems)
    _check_injection(bundle, problems, notes)
    _check_source_map_release(bundle, problems, notes)
    _check_provenance_complete(bundle, problems, notes)
    _check_span_outcomes(bundle, problems, notes)
    _check_acceptance_readable(bundle, problems, notes)
    _check_acceptance_surface(bundle, problems, notes)
    _check_eval(bundle, problems, notes)
    _check_l3(bundle, problems, notes)
    _check_adversarial(bundle, problems, notes)
    _check_dissent(bundle, problems, notes)
    _check_memory(bundle, problems, notes)
    return {"ok": not problems, "problems": problems, "notes": notes,
            "retrieval_fingerprint": retrieval_fingerprint(bundle)}
