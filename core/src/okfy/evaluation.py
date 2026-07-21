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


def _find_run(data: dict, run_id: str) -> dict:
    runs = data.get("runs") or []
    if run_id == "latest":
        if not runs:
            raise KeyError("no eval runs recorded — run: okfy eval run <bundle>")
        return runs[-1]
    for r in runs:
        if r.get("run_id") == run_id:
            return r
    raise KeyError(f"eval run not found: {run_id}")


def eval_run(bundle: Bundle, n: int = 10) -> dict:
    """Deterministic half of an Eval Run: purpose.md test queries → expansion
    → top hits, appended to meta/eval.json. Verdicts land later via
    eval_verdict — the LLM-judge proposes, the owner disposes."""
    queries = bundle.purpose().get("test_queries") or []
    if not queries:
        raise ValueError(
            "meta/purpose.md has no test_queries — eval replays the bundle's "
            "acceptance queries; add them to the purpose.md frontmatter first")
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    results = []
    for q in queries:
        out = query.query(bundle, str(q), n=n, expand=True)
        results.append({"query": str(q), "expanded_query": out["expanded_query"],
                        "top_hits": [_slim(h) for h in out["results"]],
                        # lexicon notes (ambiguous / not-covered) are part of
                        # the answer: an honest "this bundle does not cover X"
                        # must survive into the replayable record
                        "notes": out["notes"],
                        "llm_verdict": None, "llm_reason": None,
                        "owner_verdict": None, "owner_note": None})
    # pin the retrieval contract this run was judged against: a later
    # concept/lexicon/test-query/tool change makes the run stale evidence
    # (release_check compares this against the live bundle)
    from okfy.release import retrieval_fingerprint
    run = {"run_id": ts, "tool_version": __version__, "created": ts,
           "retrieval_fingerprint": retrieval_fingerprint(bundle),
           "results": results}
    data = load_evals(bundle)
    data["runs"].append(run)
    _save(bundle, data)
    _commit(bundle, ["meta/eval.json"], f"eval: run {ts} — {len(results)} queries")
    return run


def eval_verdict(bundle: Bundle, run_id: str, q_index: int, role: str,
                 verdict: str, reason: str = "") -> dict:
    """Record a Verdict on one result: role 'llm' proposes (llm_verdict +
    llm_reason), role 'owner' disposes (owner_verdict + owner_note)."""
    if role not in ROLES:
        raise ValueError(f"bad role {role!r} (use: {sorted(ROLES)})")
    if verdict not in VERDICTS:
        raise ValueError(f"bad verdict {verdict!r} (use: {sorted(VERDICTS)})")
    data = load_evals(bundle)
    run = _find_run(data, run_id)
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


def eval_status(bundle: Bundle, run_id: str = "latest") -> dict:
    """Effective verdict per query: owner wins; LLM-only is provisional;
    neither is pending. The top-level provisional flag stays True until every
    query carries an owner verdict — a Bundle cannot self-certify."""
    run = _find_run(load_evals(bundle), run_id)
    queries: list[dict] = []
    t = {"owner_confirmed": 0, "provisional": 0, "pending": 0,
         "of": len(run["results"]), "passes_owner": 0, "passes_provisional": 0}
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
        queries.append(q)
    return {"run_id": run["run_id"], "queries": queries, "totals": t,
            "provisional": t["owner_confirmed"] < t["of"]}
