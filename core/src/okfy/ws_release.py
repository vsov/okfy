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

WS_FINGERPRINT_SCHEMA = "okfy-ws-retrieval@1"
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


def workspace_retrieval_fingerprint(ws: Workspace) -> str:
    """Everything that decides what a federated query returns.

    A member's own `retrieval_fingerprint` is included rather than its git SHA:
    the SHA moves for a README edit, and a federated answer does not. The roster
    carries roles because routing depends on them — re-roling a member from
    knowledge to constraints changes every answer without touching a concept.
    The accepted crosswalk rows are in because `same-as` merges results and
    `constrains` drives the auto-pull that makes federation worth having."""
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
    payload = json.dumps({
        "fingerprint_schema": WS_FINGERPRINT_SCHEMA,
        "members": members,
        "workspace_lexicon": lex,
        "crosswalk": rows,
        "test_queries": [str(q) for q in (ws.meta.get("test_queries") or [])],
        "adversarial_queries": sorted(
            json.dumps(q, sort_keys=True, ensure_ascii=False)
            if isinstance(q, dict) else str(q)
            for q in (ws.meta.get("adversarial_queries") or [])),
        "retrieval_code": retrieval_code_digest(),
        "federation_code": federation_code_digest(),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ws_suite_queries(ws: Workspace, suite: str) -> list:
    key = "test_queries" if suite == "acceptance" else "adversarial_queries"
    return list(ws.meta.get(key) or [])


def _flat_hits(out: dict, n: int) -> list:
    """One ranked list out of the role-grouped federated result, plus whatever
    the constrains auto-pull dragged in — the pull is the answer too, and an
    expectation about a constraint firing has to be able to see it."""
    hits = []
    for e in (out.get("knowledge") or []) + (out.get("constraints") or []):
        hits.append({"id": e["ref"], "member": e["member"], "role": e["role"],
                     "score": e.get("score")})
    for e in (out.get("pulled") or []):
        hits.append({"id": e["ref"], "member": e["member"], "role": e["role"],
                     "score": None, "via": "constrains"})
    return hits[:max(n * 2, n)]


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
           "query_options": {"n": n, "federated": True},
           "results": results}
    data = load_evals(ws)
    data["runs"].append(run)
    _save(ws, data)
    _commit(ws, ["meta/eval.json"],
            f"eval: workspace {suite} run {ts} — {len(results)} queries")
    return run


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
    from okfy.evaluation import eval_status, load_evals, run_suite
    tag = "EVAL" if suite == "acceptance" else "ADVERSARIAL"
    field = "test_queries" if suite == "acceptance" else "adversarial_queries"
    for msg in _surface_problems(ws_suite_queries(ws, suite), field,
                                 where="meta/workspace.md"):
        problems.append(f"E_REL_WS_{tag}_SURFACE: {msg}")
    try:
        runs = [r for r in (load_evals(ws).get("runs") or [])
                if run_suite(r) == suite]
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        problems.append(f"E_REL_WS_EVAL_INVALID: meta/eval.json cannot be read "
                        f"({type(e).__name__}: {e})")
        return
    if not runs:
        problems.append(
            f"E_REL_WS_{tag}_MISSING: no federated {suite} eval run — the "
            "workspace's own queries are the only evidence that federation "
            "answers them, and a claim in log.md is not that evidence; run "
            f"`okfy eval run <workspace> --suite {suite}`")
        return
    st = eval_status(ws, "latest", suite=suite)
    t = st["totals"]
    if st["provisional"]:
        problems.append(
            f"E_REL_WS_{tag}_PROVISIONAL: federated {suite} run {st['run_id']} "
            f"— {t['owner_confirmed']}/{t['of']} owner verdicts recorded")
    if runs[-1].get("retrieval_fingerprint") != workspace_retrieval_fingerprint(ws):
        problems.append(
            f"E_REL_WS_{tag}_STALE: the federated {suite} run was judged "
            "against a different federated contract — a member's content, the "
            "roster, the workspace lexicon or the crosswalk moved since")
    key = ("min_owner_pass" if suite == "acceptance" else "min_adversarial_pass")
    acc = ws.meta.get("acceptance")
    acc = acc if isinstance(acc, dict) else {}
    bar = acc.get(key, DEFAULT_MIN_OWNER_PASS)
    if isinstance(bar, bool) or not isinstance(bar, int):
        problems.append(f"E_REL_WS_ACCEPTANCE_INVALID: acceptance.{key} is "
                        f"{type(bar).__name__} {bar!r}, not an integer")
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
