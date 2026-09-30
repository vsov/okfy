"""Release predicate for a Workspace.

Federation is the most valuable applied feature here and, until this module, the
least verifiable: `~/bundles/trading-desk` carried ten cross-bundle test queries
in its manifest and a `log.md` line claiming "run 2 10/10 owner-confirmed", and
nothing else. No eval artifact, no retrieval fingerprint, no predicate — the
strongest claim in the project rested on prose, while a single bundle making the
same claim had to produce a replayable run and an owner checkpoint. An external
audit put it exactly right: federation's acceptance was less verifiable than a
member's.

The shape mirrors the bundle gate rather than inventing a second one:

1. Every MEMBER must itself be release-accepted. A workspace assembled from
   bundles nobody accepted cannot be accepted — this composes the existing
   predicate instead of re-deriving a weaker version of it.
2. The crosswalk must be fresh. `workspace_status` already knows which rows are
   stale and which members cannot be verified at all; both block, and an
   unverifiable member blocks harder, because "cannot check" is not "clean".
3. Both eval suites must exist, be owner-complete, and be pinned to the live
   federated retrieval contract.
"""
import hashlib
import json

from okfy.release import (DEFAULT_MIN_OWNER_PASS, MIN_TEST_QUERIES,
                          _surface_problems, retrieval_code_digest,
                          retrieval_fingerprint, release_check)
from okfy.workspace import Workspace, workspace_status

WS_FINGERPRINT_SCHEMA = "okfy-ws-retrieval@2"
# The modules that decide what a FEDERATED query returns, on top of the
# per-bundle retrieval code already covered by retrieval_code_digest().
FEDERATION_MODULES = ("crosswalk.py", "federate.py", "workspace.py")


def federation_code_digest() -> str:
    from pathlib import Path

    import okfy
    root = Path(okfy.__file__).parent
    lines = [f"{n}:{hashlib.sha256((root / n).read_bytes()).hexdigest()}"
             for n in sorted(FEDERATION_MODULES)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _personal_scope(ws: Workspace) -> dict:
    """For every `personal` member, `concept id -> applies_to`, read the way
    `federate.personal_scope_ids` reads it (straight from the concept files —
    the index does not carry `applies_to`). Only `personal` narrows a query, so
    only `personal` members are listed; a knowledge/constraints member's own
    `applies_to` is inert to federation and stays out of the fingerprint. A list
    of strings is sorted (its order decides nothing); anything else — the
    malformed values that fail closed — is carried as written."""
    from okfy.bundle import Bundle
    out: dict = {}
    for m in sorted(ws.members, key=lambda x: x.name):
        if m.role != "personal":
            continue
        try:
            scope = {}
            for c in Bundle(m.path).concepts():
                a = c.meta.get("applies_to")
                if isinstance(a, list) and all(isinstance(k, str) for k in a):
                    a = sorted(a)
                scope[c.id] = a
            out[m.name] = dict(sorted(scope.items()))
        except Exception as e:                      # unreadable personal member
            out[m.name] = f"__unreadable__:{type(e).__name__}"
    return out


def _workspace_fingerprint_payload(ws: Workspace) -> dict:
    """The named inputs of `workspace_retrieval_fingerprint`, before hashing.
    One dict, so the guide can be pinned to its keys: a new input cannot be
    added here without `docs/guide/GUIDE.md` naming it."""
    from okfy.bundle import Bundle
    from okfy.crosswalk import load_rows as load_crosswalk
    from okfy.lexicon import load_rows as load_lexicon
    from okfy.release import EXPANSION_FIELDS
    members = []
    for m in sorted(ws.members, key=lambda x: x.name):
        try:
            fp = retrieval_fingerprint(Bundle(m.path))
        except Exception as e:                      # unreadable member
            fp = f"__unreadable__:{type(e).__name__}"
        members.append({"name": m.name, "role": m.role, "retrieval": fp})
    try:
        lex = [{k: r.get(k) for k in EXPANSION_FIELDS if k in r}
               for r in load_lexicon(Bundle(ws.root))]
    except (ValueError, AttributeError, TypeError) as e:
        lex = [{"__unreadable__": f"{type(e).__name__}: {e}"}]
    rows = sorted(f"{r.rel}|{r.src}|{r.dst}|{r.status}"
                  for r in load_crosswalk(ws))
    return {
        "fingerprint_schema": WS_FINGERPRINT_SCHEMA,
        "members": members,
        "project_key": ws.meta.get("project_key"),
        "personal_scope": _personal_scope(ws),
        "workspace_lexicon": lex,
        "crosswalk": rows,
        "test_queries": [str(q) for q in (ws.meta.get("test_queries") or [])],
        "adversarial_queries": sorted(
            json.dumps(q, sort_keys=True, ensure_ascii=False)
            if isinstance(q, dict) else str(q)
            for q in (ws.meta.get("adversarial_queries") or [])),
        "retrieval_code": retrieval_code_digest(),
        "federation_code": federation_code_digest(),
    }


def workspace_retrieval_fingerprint(ws: Workspace) -> str:
    """Everything that decides what a federated query returns.

    A member's own `retrieval_fingerprint` is included rather than its git SHA:
    the SHA moves for a README edit, and a federated answer does not. The roster
    carries roles because routing depends on them — re-roling a member from
    knowledge to constraints changes every answer without touching a concept.
    The accepted crosswalk rows are in because `same-as` merges results and
    `constrains` drives the auto-pull that makes federation worth having.

    `okfy-ws-retrieval@2` adds the two inputs that SCOPE personal memory:
    the workspace `project_key`, and each `personal` member's per-concept
    `applies_to` (`federate` reads it from the concept files, the member's own
    fingerprint does not carry it). Editing either changes what
    `federated_query` returns; under `@1` the fingerprint stayed put, so a run
    recorded before the edit looked current after it. A run recorded under
    `@1` is a previous era: the schema tag differs, so it reads as stale and its
    stored bytes are left alone."""
    payload = json.dumps(_workspace_fingerprint_payload(ws), sort_keys=True,
                         ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ws_suite_queries(ws: Workspace, suite: str) -> list:
    key = "test_queries" if suite == "acceptance" else "adversarial_queries"
    return list(ws.meta.get(key) or [])


def _flat_hits(out: dict, n: int) -> list:
    """One ranked list out of the role-grouped federated result, plus whatever
    the constrains auto-pull dragged in — the pull is the answer too, and an
    expectation about a constraint firing has to be able to see it.

    Truncation rule (what a persisted eval record keeps): each role group —
    `knowledge`, `constraints`, `personal` — is kept in that order, whole, up to
    `n` entries each (`federated_query` already cuts each to `n`; the cap here
    keeps the record honest if a caller hands over more). EVERY `pulled` hit is
    kept after them: a pull is the answer to a `constrains` expectation, so it is
    never sliced off. There is no blanket cut across groups. Until
    `okfy-ws-retrieval@2` the flat list was cut to the first `2n`, which for a
    10/10/10/1 result stored 20 knowledge+constraint hits and dropped every
    personal hit and the pulled constraint — and `adversarial_outcome` graded
    that truncated list. It now grades exactly what is kept.

    `personal` is the third, additive group `federated_query` emits only when
    the workspace has a `personal` member (already scoped to in-scope hits by
    `_personal_scope_filter`); `out.get("personal")` is `None` otherwise, so a
    workspace without one flattens exactly as before. The projection changed
    together with the schema bump to `okfy-ws-retrieval@2`; runs recorded under
    `@1` are a previous era and read as stale rather than being rewritten."""
    hits = []
    for group in ("knowledge", "constraints", "personal"):
        for e in (out.get(group) or [])[:n]:
            hits.append({"id": e["ref"], "member": e["member"],
                         "role": e["role"], "score": e.get("score")})
    for e in (out.get("pulled") or []):
        hits.append({"id": e["ref"], "member": e["member"], "role": e["role"],
                     "score": None, "via": "constrains"})
    return hits


def ws_query_options(n: int) -> dict:
    """How `ws_eval_run` invokes federated retrieval, in ONE place.

    The recorder writes it and the release check replays against it, so a
    record whose `query_options` are not exactly this one is a record
    `ws_eval_run` did not write (`n = 0`, an extra key, `federated: false`).
    The bundle-side twin is `evaluation.canonical_query_options`."""
    return {"n": int(n), "federated": True}


def ws_eval_run(ws: Workspace, n: int = 10, suite: str = "acceptance") -> dict:
    """A federated Eval Run, in the bundle eval's format and verdict machinery.

    Stored in the WORKSPACE's own meta/eval.json: same schema, same verbs, a
    different artifact for a different thing. `eval_verdict` and `eval_status`
    need only `.root` and work on it unchanged."""
    import datetime

    from okfy import __version__
    from okfy.evaluation import (MIN_TOP_HITS, SUITES, _save, adversarial_outcome,
                                 load_evals)
    from okfy.federate import federated_query
    from okfy.proposals import _commit
    if not isinstance(n, int) or isinstance(n, bool) or n < MIN_TOP_HITS:
        raise ValueError(f"eval run needs n >= {MIN_TOP_HITS}, got {n!r}")
    if suite not in SUITES:
        raise ValueError(f"unknown suite {suite!r} (use: {list(SUITES)})")
    queries = ws_suite_queries(ws, suite)
    if not queries:
        key = "test_queries" if suite == "acceptance" else "adversarial_queries"
        raise ValueError(f"meta/workspace.md has no {key} — the {suite} suite "
                         "replays them; add them to the manifest first")
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    results = []
    for q in queries:
        spec = q if isinstance(q, dict) else {"query": str(q)}
        text = str(spec.get("query") or "")
        out = federated_query(ws, text, n=n)
        hits = _flat_hits(out, n)
        r = {"query": text,
             "expanded_query": out.get("expanded_query"),
             "top_hits": hits,
             "notes": out.get("notes") or [],
             "llm_verdict": None, "llm_reason": None,
             "owner_verdict": None, "owner_note": None}
        if suite == "adversarial":
            r.update({"expect": spec.get("expect"),
                      "concept": spec.get("concept"), "why": spec.get("why")})
            r.update(adversarial_outcome(
                spec, {"results": hits, "notes": r["notes"]}))
        results.append(r)
    run = {"run_id": ts, "tool_version": __version__, "created": ts,
           "retrieval_schema": WS_FINGERPRINT_SCHEMA,
           "retrieval_fingerprint": workspace_retrieval_fingerprint(ws),
           "suite": suite,
           "query_options": ws_query_options(n),
           "results": results}
    data = load_evals(ws)
    data["runs"].append(run)
    _save(ws, data)
    _commit(ws, ["meta/eval.json"],
            f"eval: workspace {suite} run {ts} — {len(results)} queries")
    return run


# MEASURED, not guessed. On 2026-09-30 a two-member workspace built from clones
# of the two largest real bundles here (sec-cftc-sfp-okf, 309 concepts, and
# crypto-options-okf, 368) answered a federated query in about 3.1 s: 12
# acceptance queries recorded in 37.0 s and replayed in 36.4 s; 10 adversarial
# queries recorded in 31.2 s and replayed in 30.2 s. `federated_query` rebuilds
# every member's index on every call (`load_index` was 7.3 of 7.6 profiled
# seconds, nearly all of it YAML parsing), so the cost is per QUERY and grows
# with the members' size, not with the workspace's. `_ws_replay` is called once
# per suite, so the bound is per suite: 37.0 s is the worst suite measured, and
# 296 s is EIGHT TIMES that, room for members roughly eight times as large
# before the bound can fire on honest work. Determinism was measured first
# (record in one process, replay in others under different PYTHONHASHSEED,
# personal + constraints + knowledge members, two pulled constraints and 15
# personal hits): 0 field differences, scores included, so the comparison
# stays exact. The bundle-side twin is `release.REPLAY_BUDGET_S` (30 s).
WS_REPLAY_BUDGET_MEASURED_S = 37.0
WS_REPLAY_BUDGET_MULTIPLE = 8
WS_REPLAY_BUDGET_S = WS_REPLAY_BUDGET_MEASURED_S * WS_REPLAY_BUDGET_MULTIPLE


def ws_replay_run_bounded(ws: Workspace, run: dict, suite: str,
                          budget_s: float | None = None
                          ) -> tuple[list[dict], int, int]:
    """Re-derive a recorded FEDERATED run and return `(differences, compared,
    total)`, the way `evaluation.replay_run_bounded` does for a bundle.

    Compared, exactly (no rounding, no excluded field): `query_options`, the
    count of results against the workspace's own suite, and per result `query`,
    `expanded_query` (a per-member mapping), `top_hits` (id, member, role,
    score, `via`), `notes`; for the adversarial suite also `expect`, `concept`,
    `why`, `outcome`, `outcome_detail`. The owner/llm verdict fields are
    judgements, not derivations, and are never compared.

    The same `federated_query` and `_flat_hits` the recorder uses, so the
    stored personal group and every pulled hit are re-derived whole. The clock
    is consulted BEFORE each query; `compared < total` means the bound stopped
    the replay and the caller must report that as a problem. A structural
    refusal (options, result count) returns `compared == total == 0`: nothing
    was left unexamined, the record simply is not a run of this suite."""
    import time

    from okfy.evaluation import MIN_TOP_HITS, adversarial_outcome
    from okfy.federate import federated_query
    diffs: list[dict] = []
    deadline = None if budget_s is None else time.monotonic() + budget_s
    opts = run.get("query_options")
    if not isinstance(opts, dict):
        return ([{"field": "query_options", "recorded": opts,
                  "expected": "a mapping of n/federated"}], 0, 0)
    n = opts.get("n")
    if isinstance(n, bool) or not isinstance(n, int) or n < MIN_TOP_HITS:
        return ([{"field": "query_options.n", "recorded": n,
                  "expected": f"an integer >= {MIN_TOP_HITS}"}], 0, 0)
    want = ws_query_options(n)
    if opts != want:
        return ([{"field": "query_options", "recorded": opts,
                  "expected": want}], 0, 0)
    specs = ws_suite_queries(ws, suite)
    results = run.get("results") or []
    if len(results) != len(specs):
        return ([{"field": "results", "recorded": f"{len(results)} queries",
                  "expected": f"{len(specs)} in meta/workspace.md"}], 0, 0)
    compared = 0
    for i, (spec_raw, rec) in enumerate(zip(specs, results)):
        if deadline is not None and time.monotonic() >= deadline:
            break
        compared += 1
        spec = spec_raw if isinstance(spec_raw, dict) else {"query": str(spec_raw)}
        text = str(spec.get("query") or "")
        out = federated_query(ws, text, n=n)
        hits = _flat_hits(out, n)
        got = {"query": text,
               "expanded_query": out.get("expanded_query"),
               "top_hits": hits,
               "notes": out.get("notes") or []}
        if suite == "adversarial":
            got.update({"expect": spec.get("expect"),
                        "concept": spec.get("concept"),
                        "why": spec.get("why")})
            got.update(adversarial_outcome(
                spec, {"results": hits, "notes": got["notes"]}))
        for field, value in got.items():
            if rec.get(field) != value:
                diffs.append({"index": i, "field": field,
                              "recorded": rec.get(field), "replayed": value})
    return diffs, compared, len(results)


def _ws_replay(ws: Workspace, run: dict, suite: str, tag: str,
               problems: list, notes: list) -> None:
    """Report what no longer matches. Called only when `retrieval_schema` and
    `retrieval_fingerprint` already match: a previous era or a moved contract
    keeps its stale finding and is never replayed (a second finding about the
    same fact, implying the record was edited when the environment moved)."""
    import time
    code = f"E_REL_WS_{tag}_REPLAY"
    t0 = time.monotonic()
    try:
        diffs, compared, total = ws_replay_run_bounded(
            ws, run, suite, WS_REPLAY_BUDGET_S)
    except (OSError, ValueError, KeyError, TypeError) as e:
        problems.append(
            f"{code}: the recorded federated {suite} run could not be "
            f"replayed ({type(e).__name__}: {e}) — evidence that cannot be "
            "re-derived is not replayable evidence")
        return
    elapsed = time.monotonic() - t0
    if compared < total:
        problems.append(
            f"E_REL_WS_REPLAY_INCOMPLETE: the federated {suite} replay stopped "
            f"after {compared}/{total} queries and {elapsed:.1f}s, leaving "
            f"{total - compared} unexamined — the {WS_REPLAY_BUDGET_S:.0f}s "
            "budget bounds release-time retrieval work, and a partial replay "
            "cannot stand as evidence for the whole run. Re-run "
            f"`okfy eval run <workspace> --suite {suite}` so the record is "
            "fresh, or shorten the suite")
    if not diffs and compared == total:
        notes.append(f"workspace {suite} replay: "
                     f"{len(run.get('results') or [])} queries re-derived and "
                     f"identical ({elapsed:.1f}s)")
        return
    if not diffs:
        return
    where = ", ".join(
        f"query {d['index']} field {d['field']}" if "index" in d
        else f"field {d['field']}" for d in diffs[:3])
    problems.append(
        f"{code}: the recorded federated {suite} run does not match what this "
        f"workspace produces now — {len(diffs)} difference(s), first: {where}. "
        "The fingerprint still matches, so the environment did not move: the "
        f"record did. Re-run `okfy eval run <workspace> --suite {suite}` and "
        "repeat the owner checkpoint.")
    if elapsed > WS_REPLAY_BUDGET_S:
        notes.append(f"workspace {suite} replay took {elapsed:.1f}s, over the "
                     f"{WS_REPLAY_BUDGET_S:.0f}s budget")


def _ws_record_invalid(run, suite: str, problems: list) -> bool:
    """A malformed latest run, reported as a finding rather than a traceback.
    Called BEFORE `eval_status` and the replay, which both walk the record
    assuming every result is a mapping (`release._record_invalid` twin)."""
    from okfy.evaluation import eval_record_problems
    bad = eval_record_problems(run)
    if not bad:
        return False
    problems.append(
        f"E_REL_WS_EVAL_INVALID: the latest federated {suite} run "
        f"{run.get('run_id') if isinstance(run, dict) else run!r} is not a "
        f"valid eval record — {'; '.join(bad)}. Nothing in a malformed record "
        "can be judged or replayed; repair meta/eval.json or re-run "
        f"`okfy eval run <workspace> --suite {suite}` and judge it again")
    return True


def _check_members(ws: Workspace, problems: list, notes: list):
    from okfy.bundle import Bundle
    for m in sorted(ws.members, key=lambda x: x.name):
        try:
            out = release_check(Bundle(m.path))
        except Exception as e:
            problems.append(
                f"E_REL_WS_MEMBER_UNREADABLE: member {m.name} at {m.path} "
                f"cannot be checked ({type(e).__name__}: {e})")
            continue
        if not out["ok"]:
            codes = sorted({p.split(":")[0] for p in out["problems"]})
            problems.append(
                f"E_REL_WS_MEMBER_UNACCEPTED: member {m.name} ({m.role}) is not "
                f"release-accepted ({', '.join(codes)}) — a workspace cannot be "
                "accepted on top of bundles that are not")


def _check_crosswalk(ws: Workspace, problems: list, notes: list):
    st = workspace_status(ws)
    if st["unverifiable_members"]:
        problems.append(
            f"E_REL_WS_MEMBER_UNVERIFIABLE: "
            f"{', '.join(st['unverifiable_members'])} — no pin, an unreachable "
            "pin or broken git, so no crosswalk row touching them can be shown "
            "to still describe what was reviewed")
    drifted = [m["name"] for m in st["members"]
               if not m["fresh"] and m["name"] not in st["unverifiable_members"]]
    if drifted:
        problems.append(
            f"E_REL_WS_MEMBER_DRIFT: {', '.join(sorted(drifted))} moved since "
            "the pin recorded in meta/workspace.md — re-review the affected "
            "crosswalk rows and re-pin (okfy workspace status)")
    if st["stale_rows"]:
        problems.append(
            f"E_REL_WS_CROSSWALK_STALE: {len(st['stale_rows'])} crosswalk "
            "row(s) cite a concept that changed or cannot be verified — an "
            "owner approved a link between two things, and one of them moved")
    notes.append(f"workspace: {len(ws.members)} member(s), "
                 f"{len(st['stale_rows'])} stale crosswalk row(s)")


def _check_ws_eval(ws: Workspace, suite: str, problems: list, notes: list):
    from okfy.evaluation import eval_status, latest_run, load_evals
    tag = "EVAL" if suite == "acceptance" else "ADVERSARIAL"
    field = "test_queries" if suite == "acceptance" else "adversarial_queries"
    for msg in _surface_problems(ws_suite_queries(ws, suite), field,
                                 where="meta/workspace.md"):
        problems.append(f"E_REL_WS_{tag}_SURFACE: {msg}")
    try:
        latest = latest_run(load_evals(ws), suite)
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        problems.append(f"E_REL_WS_EVAL_INVALID: meta/eval.json cannot be read "
                        f"({type(e).__name__}: {e})")
        return
    if latest is None:
        problems.append(
            f"E_REL_WS_{tag}_MISSING: no federated {suite} eval run — the "
            "workspace's own queries are the only evidence that federation "
            "answers them, and a claim in log.md is not that evidence; run "
            f"`okfy eval run <workspace> --suite {suite}`")
        return
    # BEFORE eval_status and the replay: both walk the nested record assuming
    # every result is a mapping.
    if _ws_record_invalid(latest, suite, problems):
        return
    st = eval_status(ws, "latest", suite=suite)
    t = st["totals"]
    if st["provisional"]:
        problems.append(
            f"E_REL_WS_{tag}_PROVISIONAL: federated {suite} run {st['run_id']} "
            f"— {t['owner_confirmed']}/{t['of']} owner verdicts recorded")
    if latest.get("retrieval_schema") != WS_FINGERPRINT_SCHEMA:
        # A previous era: the run was fingerprinted under a payload that did not
        # cover every input (`@1` omitted project_key and personal applies_to),
        # so its fingerprint cannot vouch for today's contract even if it were
        # to compare equal. Reported, never rewritten.
        problems.append(
            f"E_REL_WS_{tag}_STALE: the federated {suite} run was recorded "
            f"under {latest.get('retrieval_schema')!r}, not "
            f"{WS_FINGERPRINT_SCHEMA!r} — a previous fingerprint era that "
            "did not cover the personal scope inputs; re-run "
            f"`okfy eval run <workspace> --suite {suite}`")
    elif latest.get("retrieval_fingerprint") != workspace_retrieval_fingerprint(ws):
        problems.append(
            f"E_REL_WS_{tag}_STALE: the federated {suite} run was judged "
            "against a different federated contract — a member's content, the "
            "roster, project_key, a personal concept's applies_to, the workspace "
            "lexicon or the crosswalk moved since")
    else:
        _ws_replay(ws, latest, suite, tag, problems, notes)
    key = ("min_owner_pass" if suite == "acceptance" else "min_adversarial_pass")
    acc = ws.meta.get("acceptance")
    acc = acc if isinstance(acc, dict) else {}
    bar = acc.get(key, DEFAULT_MIN_OWNER_PASS)
    if isinstance(bar, bool) or not isinstance(bar, int):
        problems.append(f"E_REL_WS_ACCEPTANCE_INVALID: acceptance.{key} is "
                        f"{type(bar).__name__} {bar!r}, not an integer")
        return
    n_queries = len(ws_suite_queries(ws, suite))
    if key in acc and not 1 <= bar <= max(n_queries, 1):
        # the bundle side refuses this at validate (E_ACCEPTANCE_RANGE) for a
        # bar the owner SET: a bar below 1 accepts a run whose every verdict
        # is `fail`, one above the query count can never be met. The default
        # is not range-checked (as on the bundle side): it is a policy minimum
        # that a short suite simply fails.
        problems.append(f"E_REL_WS_ACCEPTANCE_INVALID: acceptance.{key}={bar} "
                        f"is outside 1..{n_queries} (the number of the "
                        f"workspace's {suite} queries)")
        return
    if t["passes_owner"] < bar:
        problems.append(
            f"E_REL_WS_{tag}_POLICY: {t['passes_owner']}/{t['of']} owner passes "
            f"on the federated {suite} suite < policy minimum {bar}")
    if t.get("outcomes_unmet"):
        notes.append(f"workspace adversarial: {t['outcomes_unmet']}/{t['of']} "
                     "declared expectations were NOT met; every owner pass "
                     "among them is an explicit override")
    notes.append(f"workspace {suite} {st['run_id']}: {t['passes_owner']}/"
                 f"{t['of']} owner passes (policy min {bar})")


def workspace_release_check(ws: Workspace) -> dict:
    """The machine predicate for 'this workspace is accepted'. Fail-closed, and
    strictly stronger than the sum of its members: it additionally demands that
    the links BETWEEN them still describe what an owner reviewed, and that the
    cross-bundle questions were replayed and judged as a federated whole."""
    problems: list[str] = []
    notes: list[str] = []
    _check_members(ws, problems, notes)
    _check_crosswalk(ws, problems, notes)
    for suite in ("acceptance", "adversarial"):
        _check_ws_eval(ws, suite, problems, notes)
    return {"ok": not problems, "problems": problems, "notes": notes,
            "workspace_retrieval_fingerprint": workspace_retrieval_fingerprint(ws),
            "min_queries": MIN_TEST_QUERIES}
