"""Owner-judged eval (ADR-0013): acceptance as a replayable artifact, not an
agent narrative. meta/eval.json records append-only Eval Runs — per test query
the expanded query, top hits, an LLM verdict (proposes) and an owner verdict
(disposes). Release acceptance counts owner verdicts only; LLM-only results
stay PROVISIONAL — the model that extracted the Bundle grading itself is a
closed loop of well-formatted self-deception."""
import datetime
import json

from okfy import __version__, query
from okfy.bundle import Bundle
from okfy.proposals import _commit

VERDICTS = {"pass", "fail", "partial"}
ROLES = {"llm", "owner"}
SUITES = ("acceptance", "adversarial")
EXPECTATIONS = ("covered", "not-covered")


def suite_queries(bundle: Bundle, suite: str) -> list:
    """The queries a suite replays.

    `acceptance` holds plain strings — the ten questions the bundle was built to
    answer. `adversarial` holds mappings, because an adversarial query without a
    declared expectation is not falsifiable: ten more strings judged by the same
    owner in the same sitting can be stamped exactly as easily as the first ten,
    which is the weakness the suite exists to remove. Each entry states what
    SHOULD happen (`expect`), what concept proves it (`concept`), and why the
    query is adversarial at all (`why`)."""
    p = bundle.purpose()
    if suite == "acceptance":
        return list(p.get("test_queries") or [])
    return list(p.get("adversarial_queries") or [])


def eval_path(bundle: Bundle):
    return bundle.root / "meta" / "eval.json"


def load_evals(bundle: Bundle) -> dict:
    p = eval_path(bundle)
    if not p.is_file():
        return {"runs": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _save(bundle: Bundle, data: dict) -> None:
    p = eval_path(bundle)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")


def _slim(h: dict) -> dict:
    out = {"id": h["id"], "score": h["score"]}
    if h.get("via"):
        out["via"] = h["via"]
    if h.get("stale"):
        out["stale"] = True
    return out


def run_suite(run: dict) -> str:
    """A run's suite. Runs recorded before suites existed are acceptance runs —
    they replayed `test_queries`, which is what acceptance means."""
    return str(run.get("suite") or "acceptance")


def latest_run(data: dict, suite: str = "acceptance") -> dict | None:
    """The most recent run of ONE suite, or None if that suite never ran.

    One definition, three callers. Each used to take the last row of the run log
    directly, which is the same expression meaning three different things
    depending on what the list had been filtered by first: the bundle acceptance
    gate read an UNFILTERED log, so it took whichever suite ran last — and since
    the pipeline appends the adversarial run after the acceptance run, that gate
    compared the wrong run's fingerprint, query_options and retrieval_schema. A
    stale acceptance run released clean. The other two call sites pre-filtered
    and were correct; they route through here so the next reader cannot tell the
    correct ones from the broken one by luck.

    None rather than a raise: every caller already reports "this suite never
    ran" in its own words before it needs a run."""
    try:
        return _find_run(data, "latest", suite)
    except KeyError:
        return None


def _find_run(data: dict, run_id: str, suite: str = "acceptance") -> dict:
    runs = data.get("runs") or []
    if run_id == "latest":
        # `latest` is per suite: with two suites appended to one append-only log,
        # "the last run" would silently mean whichever suite ran most recently.
        in_suite = [r for r in runs if run_suite(r) == suite]
        if not in_suite:
            raise KeyError(f"no {suite} eval runs recorded — run: "
                           f"okfy eval run <bundle> --suite {suite}")
        return in_suite[-1]
    for r in runs:
        if r.get("run_id") == run_id:
            return r
    raise KeyError(f"eval run not found: {run_id}")


MIN_TOP_HITS = 1


# --------------------------------------------------------------------------
# The eval-record schema, in ONE place.
#
# MEASURED before it was written (2026-09-06, all nine bundles under ~/bundles:
# 21 runs). Three eras exist on disk and all three are legitimate:
#
#   13 runs  {created, results, run_id, tool_version}
#    5 runs  + retrieval_fingerprint
#    3 runs  + query_options, retrieval_schema, suite
#
# A FOURTH era exists that this sweep could not see: a federated workspace run
# writes `expanded_query` as a mapping of member name to expansion, because each
# member expands against its own lexicon. No workspace lives under ~/bundles, so
# the measurement missed it and the first version of this schema reddened three
# workspace tests. It is handled by widening the type, not by excusing a caller.
#
# and three result shapes, 12 of which carry no `notes` key at all. So the
# REQUIRED sets below are the INTERSECTION, not what today's producer emits. A
# schema written from the current writer would have reported every one of those
# 13 legacy runs as malformed — a new failure mode, not a closed bypass. An
# absent key is absent, not wrong; that is the whole exemption mechanism, and
# it is why nothing here holds a list of which bundles are excused.
#
# What IS closed is the value space: a key outside the known set, a field of the
# wrong type, a verdict outside VERDICTS. Those cannot be produced by any era of
# this tool, so reporting them costs no real bundle anything.
RUN_REQUIRED = ("run_id", "tool_version", "created", "results")
RUN_KNOWN = set(RUN_REQUIRED) | {"suite", "retrieval_schema",
                                 "retrieval_fingerprint", "query_options"}
RESULT_REQUIRED = ("query", "expanded_query", "top_hits",
                   "llm_verdict", "llm_reason", "owner_verdict", "owner_note")
RESULT_KNOWN = set(RESULT_REQUIRED) | {"notes", "expect", "concept", "why",
                                       "outcome", "outcome_detail"}
OUTCOMES = ("met", "unmet")
MAX_REPORTED = 8


def _result_problems(i: int, r) -> list[str]:
    """One recorded result against the schema. Checks are ordered and never
    merged: a field that is absent and a field that holds the wrong type are
    different findings, and reporting the second for the first sends a reader
    looking for a value that is not there."""
    at = f"results[{i}]"
    if not isinstance(r, dict):
        return [f"{at} is {type(r).__name__} {r!r}, not a mapping — a result "
                f"that is not a record carries no query, no hits and no "
                f"verdict, so nothing about it can be replayed or judged"]
    out = []
    missing = [k for k in RESULT_REQUIRED if k not in r]
    if missing:
        out.append(f"{at} is missing required field(s): {', '.join(missing)}")
    unknown = sorted(set(r) - RESULT_KNOWN)
    if unknown:
        out.append(f"{at} has unknown field(s): {', '.join(unknown)} "
                   f"(known: {', '.join(sorted(RESULT_KNOWN))})")
    if out:
        return out
    if not isinstance(r["query"], str):
        out.append(f"{at}.query is {type(r['query']).__name__}, not a string")
    # `expanded_query` is a string for a single bundle and a MAPPING of member
    # name to expansion for a federated workspace run, because each member
    # expands against its own lexicon (`federate.federated_query`, written
    # through by `ws_release`). This case was missed by the sweep that produced
    # the sets above — no workspace exists under ~/bundles, so the measurement
    # could not see a fourth legitimate era that the code writes every day.
    # Widening here rather than exempting workspaces by name: an exemption list
    # is the thing this release is removing everywhere else.
    eq = r["expanded_query"]
    if isinstance(eq, dict):
        if any(not isinstance(k, str) or not isinstance(v, str)
               for k, v in eq.items()):
            out.append(f"{at}.expanded_query is a mapping but not of member "
                       f"name to expanded text")
    elif not isinstance(eq, str):
        out.append(f"{at}.expanded_query is {type(eq).__name__}, not a string "
                   f"or a per-member mapping")
    if not isinstance(r["top_hits"], list):
        out.append(f"{at}.top_hits is {type(r['top_hits']).__name__}, not a list")
    elif any(not isinstance(h, dict) for h in r["top_hits"]):
        out.append(f"{at}.top_hits contains a non-mapping hit — a hit without "
                   f"an id and a score cannot be compared against a replay")
    if "notes" in r and (not isinstance(r["notes"], list)
                         or any(not isinstance(n, str) for n in r["notes"])):
        out.append(f"{at}.notes is not a list of strings — the coverage notes "
                   f"are part of the answer that was judged")
    for k in ("llm_verdict", "owner_verdict"):
        if r[k] is not None and r[k] not in VERDICTS:
            out.append(f"{at}.{k} is {r[k]!r}, not one of {sorted(VERDICTS)}")
    for k in ("llm_reason", "owner_note"):
        if r[k] is not None and not isinstance(r[k], str):
            out.append(f"{at}.{k} is {type(r[k]).__name__}, not a string or null")
    if "expect" in r and r["expect"] not in EXPECTATIONS:
        out.append(f"{at}.expect is {r['expect']!r}, not one of "
                   f"{list(EXPECTATIONS)}")
    if "outcome" in r and r["outcome"] not in OUTCOMES:
        out.append(f"{at}.outcome is {r['outcome']!r}, not one of "
                   f"{list(OUTCOMES)}")
    if r.get("concept") is not None and not isinstance(r["concept"], str):
        out.append(f"{at}.concept is {type(r['concept']).__name__}, not a "
                   f"concept id or null")
    if "why" in r and not isinstance(r["why"], str):
        out.append(f"{at}.why is {type(r['why']).__name__}, not a string")
    return out


def eval_record_problems(run) -> list[str]:
    """Everything wrong with ONE recorded eval run, as human sentences.

    Called BEFORE `eval_status()` and before the replay, because both walk the
    nested structure assuming every result is a mapping. Replacing one result
    with a string used to raise `AttributeError: 'str' object has no attribute
    'get'` out of `release_check` — so the README's promise of a machine-readable
    `E_REL_EVAL_INVALID` held for the file's outer JSON form and not for
    anything inside it.

    Widening an `except` clause would have hidden the next malformed variant
    instead, which is why this is a schema and not a catch."""
    if not isinstance(run, dict):
        return [f"the run is {type(run).__name__} {run!r}, not a mapping"]
    missing = [k for k in RUN_REQUIRED if k not in run]
    if missing:
        return [f"the run is missing required field(s): {', '.join(missing)}"]
    unknown = sorted(set(run) - RUN_KNOWN)
    if unknown:
        return [f"the run has unknown field(s): {', '.join(unknown)} "
                f"(known: {', '.join(sorted(RUN_KNOWN))})"]
    if not isinstance(run["results"], list):
        return [f"results is {type(run['results']).__name__}, not a list"]
    out: list[str] = []
    for i, r in enumerate(run["results"]):
        out.extend(_result_problems(i, r))
        if len(out) > MAX_REPORTED:
            return out[:MAX_REPORTED] + [
                f"... and more; {len(run['results']) - i - 1} further result(s) "
                f"were not inspected after the first {MAX_REPORTED} problems"]
    return out


def canonical_query_options(n: int) -> dict:
    """How `eval_run` invokes retrieval, in ONE place.

    Only `n` ever varied; the other three have been constants since the field
    existed. Writing them as a literal in `eval_run` and validating `n >= 1` in
    the release gate meant the gate could not tell an invocation this function
    produces from one nobody could have run — and the audit walked straight
    through the gap: `{"n": 5, "expand": false, "include_meta": true,
    "include_stale": false}` was accepted as evidence of a run that never
    happened. The recorder and the checker now share this definition, so a
    record whose options are not exactly this one is a record `eval_run` did not
    write."""
    return {"n": int(n), "expand": True, "include_meta": False,
            "include_stale": True}


def replay_run(bundle: Bundle, run: dict) -> list[dict]:
    """Re-derive a recorded run and return the field-by-field differences.

    COMPARED FIELDS, all of them, so this docstring cannot claim more than the
    code checks — the exact failure v0.22 is closing elsewhere:

        query, expanded_query, top_hits, notes

    and for an adversarial run, additionally:

        expect, concept, why, outcome, outcome_detail

    The first four are what retrieval returned; the next three are the criterion
    the owner judged against, read from the LIVE purpose spec; the last two are
    the deterministic verdict on whether the declared expectation held.

    Comparison is EXACT — no rounding, no excluded field. That is licensed by
    measurement, not by hope: `query.query` is bit-identical across calls and
    across processes, float scores included. If that ever stops holding, name
    the unstable field here rather than loosening the comparison quietly, which
    would make this gate claim more than it checks."""
    return replay_run_bounded(bundle, run)[0]


def replay_run_bounded(bundle: Bundle, run: dict,
                       budget_s: float | None = None
                       ) -> tuple[list[dict], int, int]:
    """`replay_run`, with a time bound that is actually enforced.

    Returns `(differences, compared, total)`. When `budget_s` is given, the
    clock is consulted BEFORE each query, so the bound stops work rather than
    describing it after the fact — the previous version measured elapsed time
    only after the whole loop had run, and its success path returned before even
    that, so the budget could not stop a single query. A gate whose docstring
    describes a behaviour it does not have is the defect class this release is
    about, so the bound is either real or the word goes.

    `compared < total` means the bound stopped early, and the caller MUST report
    that as a problem: replaying nine of twenty queries and returning "no
    differences" is a gate quietly checking less than it claims."""
    import time
    suite = run_suite(run)
    diffs: list[dict] = []
    deadline = None if budget_s is None else time.monotonic() + budget_s
    compared = 0
    opts = run.get("query_options")
    if not isinstance(opts, dict):
        return ([{"field": "query_options", "recorded": opts,
                  "expected": "a mapping of n/expand/include_meta/include_stale"}],
                0, 0)
    n = opts.get("n")
    if isinstance(n, bool) or not isinstance(n, int) or n < MIN_TOP_HITS:
        return ([{"field": "query_options.n", "recorded": n,
                  "expected": f"an integer >= {MIN_TOP_HITS}"}], 0, 0)
    want = canonical_query_options(n)
    if opts != want:
        return ([{"field": "query_options", "recorded": opts,
                  "expected": want}], 0, 0)

    specs = suite_queries(bundle, suite)
    results = run.get("results") or []
    if len(results) != len(specs):
        return ([{"field": "results", "recorded": f"{len(results)} queries",
                  "expected": f"{len(specs)} in meta/purpose.md"}], 0,
                len(results))

    for i, (spec_raw, rec) in enumerate(zip(specs, results)):
        # BEFORE the query, not after the loop. This is the whole fix.
        if deadline is not None and time.monotonic() >= deadline:
            break
        compared += 1
        spec = spec_raw if isinstance(spec_raw, dict) else {"query": str(spec_raw)}
        text = str(spec.get("query") or "")
        out = query.query(bundle, text, n=n, expand=want["expand"])
        got = {"query": text,
               "expanded_query": out["expanded_query"],
               "top_hits": [_slim(h) for h in out["results"]],
               "notes": out["notes"]}
        if suite == "adversarial":
            # THE CRITERION, not only the answer. `eval_run` writes `expect`,
            # `concept` and `why` into the record, and until v0.22 the replay
            # compared none of them — so all three could be edited after owner
            # review and the gate stayed green, while `eval_status` showed the
            # human the substituted criterion. The recomputed `outcome` did not
            # catch it either: it is derived from the LIVE spec, so a tampered
            # `expect` and an honest outcome agree.
            #
            # These are copied verbatim out of the spec, so exact equality is
            # the right predicate — there is nothing here to round.
            #
            # `adversarial_outcome` goes LAST so a future outcome key can never
            # be shadowed by a spec key of the same name.
            got.update({"expect": spec.get("expect"),
                        "concept": spec.get("concept"),
                        "why": spec.get("why")})
            got.update(adversarial_outcome(spec, out))
        for field, value in got.items():
            if rec.get(field) != value:
                diffs.append({"index": i, "field": field,
                              "recorded": rec.get(field), "replayed": value})
    return diffs, compared, len(results)


ADVERSARIAL_KEYS = {"query", "expect", "concept", "why"}


def adversarial_row_problems(row, valid_target=None) -> list[str]:
    """The adversarial-query schema, in ONE place.

    Bundles name a bare concept id, workspaces a `member:concept-id` ref, so the
    caller supplies `valid_target`. Everything else — the closed key set, the
    value enum, `concept` required exactly when `expect` is `covered` — is the
    same predicate for both, because splitting it is how the lexicon container
    check and its element check came apart."""
    if not isinstance(row, dict):
        return [f"must be a mapping with query/expect/why, got "
                f"{type(row).__name__} {row!r} — a bare string carries no "
                "expectation, so nothing about it can be falsified"]
    out = [f"unknown key {k!r} (known: {sorted(ADVERSARIAL_KEYS)})"
           for k in sorted(set(row) - ADVERSARIAL_KEYS)]
    for f in ("query", "why"):
        if not isinstance(row.get(f), str) or not row.get(f, "").strip():
            out.append(f"{f} must be a non-empty string" +
                       ("" if f == "query" else
                        " — state why this query is adversarial, or the suite "
                        "records an answer to a question nobody framed"))
    expect = row.get("expect")
    if expect not in EXPECTATIONS:
        out.append(f"expect must be one of {list(EXPECTATIONS)}, got {expect!r}")
        return out
    concept = row.get("concept")
    if expect == "covered":
        if not isinstance(concept, str) or not concept.strip():
            out.append("concept is required when expect is 'covered' — the "
                       "expectation is that THIS concept comes back")
        elif valid_target is not None and not valid_target(concept):
            out.append(f"concept is not reachable in this bundle/workspace: "
                       f"{concept} — an expectation nothing can satisfy is not "
                       "a test, it is a guaranteed failure")
    elif concept is not None:
        out.append("concept is set but expect is 'not-covered', so it is never "
                   "read — a field nothing honours reads as a claim")
    return out


def adversarial_outcome(spec: dict, out: dict) -> dict:
    """The DETERMINISTIC half of an adversarial result: did the declared
    expectation hold?

    This is evidence, not a verdict. The owner still disposes — they may accept
    an `unmet` outcome because the expectation itself was wrong, and that is a
    reasoned judgement rather than a rubber stamp. What the core supplies is the
    thing that was missing: a criterion stated before the answer was seen."""
    ids = [h["id"] for h in out["results"]]
    if spec.get("expect") == "covered":
        want = str(spec.get("concept") or "")
        met = want in ids
        detail = (f"{want} at rank {ids.index(want) + 1}" if met
                  else f"{want} absent from the top {len(ids)}")
    else:
        met = any("not covered" in str(nte) for nte in out["notes"])
        detail = ("coverage note emitted" if met else
                  "no coverage note; top hit " + (ids[0] if ids else "(none)"))
    return {"outcome": "met" if met else "unmet", "outcome_detail": detail}


def eval_run(bundle: Bundle, n: int = 10, suite: str = "acceptance") -> dict:
    """Deterministic half of an Eval Run: purpose.md test queries → expansion
    → top hits, appended to meta/eval.json. Verdicts land later via
    eval_verdict — the LLM-judge proposes, the owner disposes.

    The invocation is recorded, not just its output. `n` used to be accepted and
    forgotten, so `okfy eval run -n 0` produced ten queries with zero hits each,
    ten owner passes over nothing, and a green release — the same
    shrink-the-evidence move that `E_REL_EVAL_SURFACE` closed on the query count.
    A run that cannot say how it was invoked is not replayable, and evidence that
    is not replayable is not evidence."""
    if not isinstance(n, int) or isinstance(n, bool) or n < MIN_TOP_HITS:
        raise ValueError(
            f"eval run needs n >= {MIN_TOP_HITS} top hits per query, got {n!r} — "
            "a run that retrieves nothing cannot be judged, and owner verdicts "
            "over empty results are not acceptance evidence")
    if suite not in SUITES:
        raise ValueError(f"unknown suite {suite!r} (use: {list(SUITES)})")
    queries = suite_queries(bundle, suite)
    if not queries:
        field = "test_queries" if suite == "acceptance" else "adversarial_queries"
        raise ValueError(
            f"meta/purpose.md has no {field} — the {suite} suite replays them; "
            "add them to the purpose.md frontmatter first")
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _OPTS = canonical_query_options(n)
    results = []
    for q in queries:
        spec = q if isinstance(q, dict) else {"query": str(q)}
        text = str(spec.get("query") or "")
        out = query.query(bundle, text, n=n, expand=_OPTS["expand"])
        r = {"query": text, "expanded_query": out["expanded_query"],
             "top_hits": [_slim(h) for h in out["results"]],
             # lexicon notes (ambiguous / not-covered) are part of
             # the answer: an honest "this bundle does not cover X"
             # must survive into the replayable record
             "notes": out["notes"],
             "llm_verdict": None, "llm_reason": None,
             "owner_verdict": None, "owner_note": None}
        if suite == "adversarial":
            r.update({"expect": spec.get("expect"),
                      "concept": spec.get("concept"),
                      "why": spec.get("why")})
            r.update(adversarial_outcome(spec, out))
        results.append(r)
    # pin the retrieval contract this run was judged against: a later
    # concept/lexicon/test-query/tool change makes the run stale evidence
    # (release_check compares this against the live bundle)
    from okfy.release import FINGERPRINT_SCHEMA, retrieval_fingerprint
    run = {"run_id": ts, "tool_version": __version__, "created": ts,
           "retrieval_schema": FINGERPRINT_SCHEMA,
           "retrieval_fingerprint": retrieval_fingerprint(bundle),
           # how the queries were asked, not only what came back. `suite` names
           # what this run is evidence FOR: `acceptance` replays the questions
           # the bundle was built to answer, `adversarial` the ones it is most
           # likely to answer confidently and wrongly. One format, one ledger.
           "suite": suite,
           "query_options": canonical_query_options(n),
           "results": results}
    data = load_evals(bundle)
    data["runs"].append(run)
    _save(bundle, data)
    _commit(bundle, ["meta/eval.json"],
            f"eval: {suite} run {ts} — {len(results)} queries")
    return run


def eval_verdict(bundle: Bundle, run_id: str, q_index: int, role: str,
                 verdict: str, reason: str = "",
                 suite: str = "acceptance") -> dict:
    """Record a Verdict on one result: role 'llm' proposes (llm_verdict +
    llm_reason), role 'owner' disposes (owner_verdict + owner_note)."""
    if role not in ROLES:
        raise ValueError(f"bad role {role!r} (use: {sorted(ROLES)})")
    if verdict not in VERDICTS:
        raise ValueError(f"bad verdict {verdict!r} (use: {sorted(VERDICTS)})")
    data = load_evals(bundle)
    run = _find_run(data, run_id, suite)
    if not 0 <= q_index < len(run["results"]):
        raise KeyError(f"run {run['run_id']} has no query {q_index} "
                       f"(valid: 0..{len(run['results']) - 1})")
    res = run["results"][q_index]
    if role == "llm":
        res["llm_verdict"], res["llm_reason"] = verdict, reason
    else:
        res["owner_verdict"], res["owner_note"] = verdict, reason
    _save(bundle, data)
    _commit(bundle, ["meta/eval.json"], f"eval: {role} verdict q{q_index} {verdict}")
    return res


def eval_status(bundle: Bundle, run_id: str = "latest",
                suite: str = "acceptance") -> dict:
    """Effective verdict per query: owner wins; LLM-only is provisional;
    neither is pending. The top-level provisional flag stays True until every
    query carries an owner verdict.

    What that guarantees, precisely: the tool will not mark a run
    owner-confirmed by itself. `owner` is a ROLE in a JSON file, written by
    whoever operates this machine — not an authenticated identity, and nothing
    here verifies who they were. For a locally operated bundle that is the
    guarantee that matters; for a bundle handed to a third party as evidence it
    is weaker than a signature, and the guides say so rather than leaving
    "cannot self-certify" to be read as more than it is."""
    run = _find_run(load_evals(bundle), run_id, suite)
    # `okfy eval status` is a path a user reaches directly, so the malformed
    # case has to arrive as a sentence naming the record and the field. It used
    # to arrive as "'str' object has no attribute 'get'".
    bad = eval_record_problems(run)
    if bad:
        raise ValueError(
            f"eval run {run.get('run_id') if isinstance(run, dict) else run!r} "
            f"in the {suite} suite is not a valid record: {'; '.join(bad)}")
    queries: list[dict] = []
    t = {"owner_confirmed": 0, "provisional": 0, "pending": 0,
         "of": len(run["results"]), "passes_owner": 0, "passes_provisional": 0,
         "outcomes_met": 0, "outcomes_unmet": 0}
    for i, r in enumerate(run["results"]):
        if r.get("owner_verdict"):
            q = {"i": i, "query": r["query"], "verdict": r["owner_verdict"],
                 "source": "owner"}
            t["owner_confirmed"] += 1
            t["passes_owner"] += r["owner_verdict"] == "pass"
        elif r.get("llm_verdict"):
            q = {"i": i, "query": r["query"], "verdict": r["llm_verdict"],
                 "source": "llm", "provisional": True}
            t["provisional"] += 1
            t["passes_provisional"] += r["llm_verdict"] == "pass"
        else:
            q = {"i": i, "query": r["query"], "verdict": "pending"}
            t["pending"] += 1
        if r.get("outcome"):
            # the declared expectation and whether it held, carried alongside the
            # verdict so an owner pass over an `unmet` outcome is visible as the
            # override it is
            q["expect"] = r.get("expect")
            q["outcome"] = r["outcome"]
            q["outcome_detail"] = r.get("outcome_detail")
            t["outcomes_met" if r["outcome"] == "met" else "outcomes_unmet"] += 1
        queries.append(q)
    return {"run_id": run["run_id"], "suite": run_suite(run),
            "queries": queries, "totals": t,
            "provisional": t["owner_confirmed"] < t["of"]}
