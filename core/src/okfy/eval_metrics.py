"""Read-only metrics over a RECORDED eval run (v0.24). `okfy eval run` already
writes, per adversarial query, the declared expectation and the ordered hits
that answered it (evaluation.py). Nothing here re-runs retrieval, touches git,
or writes a file — every number is derived from meta/eval.json (and, for a
legacy row missing its own `expect`/`concept`, from the live purpose.md spec
of the same query text) plus a concept count read straight off the bundle's
.md files.

Why a control arm on every ranking number: a recall@k of 1.0 means nothing on
its own — it could be a pool of one concept. `oracle` is the metric's ceiling
on this exact row set (the declared concept always at rank 1); `random` is
what the metric would average if that concept were placed uniformly at random
in the bundle's non-meta concept pool. When the two are within 0.05 of each
other the metric cannot tell a working retriever from a coin flip on this row
set, and the report says so instead of printing a number that looks decisive.

Why abstention gets precision AND recall, plus two constant arms: the v0.22
bake-off found abstention recall identical (0.444) across five retrievers —
phrase-keyed lexicon rows, not ranking, decide it.
Recall alone is gameable by an always-abstain policy, which is exactly why
`always_abstain`/`never_abstain` are printed beside the measured arm: if the
measured recall does not beat always-abstain's, the retriever bought nothing.
"""
E_EVAL_COMPARE_SUITE = "E_EVAL_COMPARE_SUITE"

DEGENERATE_SPREAD = 0.05
NOT_RECORDED = "not recorded in this era"


# --------------------------------------------------------------------------
# small arithmetic helpers — every one returns {"value": float|None, "reason":
# str|None}; a None value NEVER stands alone without a reason.
# --------------------------------------------------------------------------

def _metric(value, reason=None) -> dict:
    return {"value": value, "reason": reason}


def _safe_div(numerator: int, denominator: int, zero_reason: str) -> dict:
    if denominator == 0:
        return _metric(None, zero_reason)
    return _metric(numerator / denominator)


def _f1(precision: dict, recall: dict) -> dict:
    p, r = precision["value"], recall["value"]
    if p is None or r is None:
        return _metric(None, "precision or recall undefined")
    if p == 0 and r == 0:
        return _metric(None, "precision and recall both zero")
    return _metric(2 * p * r / (p + r))


def _harmonic(n: int) -> float:
    return sum(1.0 / i for i in range(1, n + 1))


# --------------------------------------------------------------------------
# rows
# --------------------------------------------------------------------------

def _coverage_note_fired(notes) -> bool:
    return any("not covered" in str(n) for n in (notes or []))


def eval_rows(bundle, run: dict) -> list[dict]:
    """One row per recorded result: query, expect, declared concept, the
    concept's 1-based rank in top_hits (or None), whether a not-covered
    coverage note fired.

    A row missing its OWN `expect`/`concept` (a run recorded before v0.22
    wrote them, or a hand-edited record) falls back to the LIVE adversarial
    spec of the same query text in purpose.md — the declared expectation,
    not a re-run. When no live spec matches either, the row simply carries
    no expectation and is excluded from every denominator below, which is
    the correct treatment for an acceptance-suite row: it never claimed
    covered/not-covered in the first place."""
    from okfy.evaluation import run_suite, suite_queries
    by_query: dict[str, dict] = {}
    for spec in suite_queries(bundle, run_suite(run)):
        if isinstance(spec, dict) and spec.get("query"):
            by_query.setdefault(spec["query"], spec)

    rows = []
    for r in run.get("results") or []:
        if not isinstance(r, dict):
            continue
        query = r.get("query")
        expect, concept = r.get("expect"), r.get("concept")
        if expect is None and query in by_query:
            expect = by_query[query].get("expect")
            concept = by_query[query].get("concept")
        ids = [h["id"] for h in (r.get("top_hits") or []) if isinstance(h, dict)]
        rank = ids.index(concept) + 1 if concept and concept in ids else None
        rows.append({"query": query, "expect": expect, "concept": concept,
                     "rank": rank, "note_fired": _coverage_note_fired(r.get("notes"))})
    return rows


def pool_size(bundle) -> int:
    """Non-meta concept count — the same predicate `query.filter_pool` uses
    for `include_meta=False`, read straight off the bundle's .md files (no
    index, no git)."""
    return sum(1 for c in bundle.concepts() if not c.id.startswith("meta/"))


def workspace_pool_size(ws) -> tuple[int | None, str | None]:
    """Federated non-meta concept pool for a WORKSPACE root: the sum of each
    MEMBER bundle's own `pool_size`, never the workspace root's own files
    (those are crosswalks/manifest, not concepts). A `personal`-role member
    is narrowed to the concepts in scope for the workspace's `project_key`
    (ADR-0015) — the same `_in_scope` filter `federate.federated_query`
    applies before it ever ranks anything, so the control arm matches the
    pool retrieval actually draws from.

    Returns `(n, None)` on success. Returns `(None, reason)` when the pool
    cannot be computed at all (no members, or a member bundle that cannot be
    read) — the caller must null out the whole control arm with that reason,
    never fall back to a root-file count that would silently read as
    `random=1.0`/DEGENERATE."""
    from okfy.bundle import Bundle
    from okfy.federate import _in_scope
    if not ws.members:
        return None, f"workspace {ws.root} has no members — concept pool cannot be computed"
    total = 0
    for m in ws.members:
        try:
            concepts = [c for c in Bundle(m.path).concepts()
                       if not c.id.startswith("meta/")]
        except Exception as e:
            return None, (f"workspace member {m.name!r} ({m.path}) could not "
                          f"be read ({e}) — concept pool cannot be computed")
        if m.role == "personal":
            project_key = ws.project_key()
            total += sum(1 for c in concepts
                        if _in_scope(c.meta.get("applies_to"), project_key))
        else:
            total += len(concepts)
    return total, None


# --------------------------------------------------------------------------
# ranking metrics + control arms
# --------------------------------------------------------------------------

def _recorded_depth(run: dict) -> int | None:
    """The hit depth actually recorded for every row of this run:
    `query_options.n` when the run carries one (v0.22+, `eval run -n`),
    else the longest `top_hits` list actually written (a legacy run, or a
    hand-edited record). `None` only when the run has no results to read a
    depth from — recall@k is then not depth-limited.

    recall@k for a k beyond this depth cannot be measured from the record:
    rank 6..k were never even looked at, so a row's `rank is None` there
    means "not in the top-<depth> hits", not "not in the top-k". Printing
    that as a measured recall@k, or a random arm computed over the full k,
    mislabels a shallower measurement as a deeper one."""
    qo = run.get("query_options")
    if isinstance(qo, dict):
        n = qo.get("n")
        if isinstance(n, int) and n > 0:
            return n
    lens = [len(r.get("top_hits") or []) for r in (run.get("results") or [])
           if isinstance(r, dict)]
    return max(lens) if lens else None


def _random_recall(k: int, n: int | None, reason: str | None = None) -> dict:
    if not n:
        return _metric(None, reason or "empty or unknown concept pool")
    return _metric(min(k, n) / n)


def _random_mrr(n: int | None, depth: int | None = None,
               reason: str | None = None) -> dict:
    if not n:
        return _metric(None, reason or "empty or unknown concept pool")
    eff = min(n, depth) if depth else n
    return _metric(_harmonic(eff) / n)


def _control_block(value: dict, oracle: dict, random_arm: dict) -> dict:
    block = {**value, "oracle": oracle, "random": random_arm}
    if value["value"] is None or oracle["value"] is None or random_arm["value"] is None:
        block["degenerate"] = None
        block["degenerate_reason"] = ("cannot be computed: " +
                                      (value["reason"] or oracle["reason"] or random_arm["reason"]))
        return block
    spread = oracle["value"] - random_arm["value"]
    degenerate = spread < DEGENERATE_SPREAD
    block["degenerate"] = degenerate
    block["degenerate_reason"] = (
        f"oracle-random spread {spread:.3f} < {DEGENERATE_SPREAD} on this row set"
        if degenerate else
        f"oracle-random spread {spread:.3f} >= {DEGENERATE_SPREAD} on this row set")
    return block


def _ranking_metrics(rows: list[dict], ks: tuple[int, ...], n_pool: int | None,
                     depth: int | None = None,
                     n_pool_reason: str | None = None) -> dict:
    covered = [r for r in rows if r["expect"] == "covered" and r["concept"]]
    n = len(covered)
    out = {"n_covered_with_concept": n, "recall": {}, "mrr": None}
    empty_reason = "no rows with expect=covered and a declared concept"
    if n == 0:
        for k in ks:
            out["recall"][str(k)] = _control_block(
                _metric(None, empty_reason), _metric(None, empty_reason),
                _metric(None, empty_reason))
        out["mrr"] = _control_block(_metric(None, empty_reason),
                                    _metric(None, empty_reason),
                                    _metric(None, empty_reason))
        return out
    for k in ks:
        if depth is not None and k > depth:
            # can't be measured from this record — never print a value (or a
            # random arm) as if rank 6..k had been looked at.
            value = _metric(None, f"run recorded only top-{depth} hits")
            random_arm = _random_recall(depth, n_pool, n_pool_reason)
        else:
            measured = sum(1 for r in covered
                          if r["rank"] is not None and r["rank"] <= k) / n
            value = _metric(measured)
            random_arm = _random_recall(k, n_pool, n_pool_reason)
        out["recall"][str(k)] = _control_block(value, _metric(1.0), random_arm)
    mrr_value = sum((1.0 / r["rank"]) if r["rank"] else 0.0 for r in covered) / n
    out["mrr"] = _control_block(_metric(mrr_value), _metric(1.0),
                                _random_mrr(n_pool, depth, n_pool_reason))
    return out


# --------------------------------------------------------------------------
# abstention metrics + constant arms
# --------------------------------------------------------------------------

def _abstain_arm(n_not_covered: int, tp: int, predicted_positive: int) -> dict:
    precision = _safe_div(tp, predicted_positive, "no positive predictions")
    recall = _safe_div(tp, n_not_covered, "no not-covered rows in this suite")
    return {"precision": precision, "recall": recall, "f1": _f1(precision, recall)}


def _abstention_metrics(rows: list[dict]) -> dict:
    """Precision/recall/F1 over rows that carry a real expectation only
    (`expect` in EXPECTATIONS). An acceptance-suite row, or a legacy
    adversarial row whose query no longer matches a live spec, has NO
    ground truth — it never claimed covered/not-covered — and must not
    count toward `n_fired`, `n_not_covered`, or the always-abstain arm's
    denominator: doing so can make an empty, unjudged row set look like a
    measured 0.000 instead of an honest null."""
    from okfy.evaluation import EXPECTATIONS
    judged = [r for r in rows if r["expect"] in EXPECTATIONS]
    n_judged = len(judged)
    n_not_covered = sum(1 for r in judged if r["expect"] == "not-covered")
    n_fired = sum(1 for r in judged if r["note_fired"])
    tp = sum(1 for r in judged if r["note_fired"] and r["expect"] == "not-covered")
    if n_judged == 0:
        empty_reason = ("no rows in this run carry expect: covered/not-covered "
                        "— nothing was judged")
        precision = recall = f1 = _metric(None, empty_reason)
        always_abstain = never_abstain = {
            "precision": _metric(None, empty_reason),
            "recall": _metric(None, empty_reason),
            "f1": _metric(None, empty_reason)}
    else:
        precision = _safe_div(tp, n_fired, "no coverage notes fired in this run")
        recall = _safe_div(tp, n_not_covered, "no not-covered rows in this suite")
        f1 = _f1(precision, recall)
        always_abstain = _abstain_arm(n_not_covered, n_not_covered, n_judged)
        never_abstain = _abstain_arm(n_not_covered, 0, 0)
    return {"n": len(rows), "n_judged": n_judged,
            "n_not_covered": n_not_covered, "n_fired": n_fired,
            "precision": precision, "recall": recall, "f1": f1,
            "always_abstain": always_abstain, "never_abstain": never_abstain}


# --------------------------------------------------------------------------
# `okfy eval metrics`
# --------------------------------------------------------------------------

def eval_metrics(bundle, run_id: str = "latest", suite: str = "adversarial",
                 ks: tuple[int, ...] = (5, 10),
                 n_pool_override: tuple[int | None, str | None] | None = None
                 ) -> dict:
    """Everything `okfy eval metrics` prints, over the ONE recorded run named
    by `run_id` (or the suite's latest). Never calls `query.query`, never
    touches git — see the module docstring.

    `n_pool_override`, when given, is `(n, reason)` from `workspace_pool_size`
    — the caller's way of saying "this bundle is a workspace root; do not
    call `pool_size` on it". Omitted (the default), the pool is
    `pool_size(bundle)` as before, for an ordinary bundle.

    v0.25 audit F15's SECOND boundary: `_parse_ks` in commands/quality.py
    refuses a non-positive or non-integer `--k` at the CLI, but that check
    sits in front of the CLI only — a library caller reaches `ks` straight
    through to `_ranking_metrics` -> `_random_recall(k, n_pool, ...)`, which
    computes `min(k, n) / n`: negative for k<0, a meaningless recall@0 for
    k==0. Refused here too, in the same voice, so `eval_metrics(b,
    ks=(-5,))` is refused rather than served a nonsense number. `bool` is a
    subclass of `int` in Python; a `ks=(True,)` element is rejected here
    even though `True == 1` would otherwise clear the positivity check — a
    caller passing a boolean did not intend a rank cutoff, and letting it
    through as `k=1` would be one more nonsense value slipping in on an
    accident of Python's type hierarchy."""
    for k in ks:
        if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
            raise ValueError(
                f"ks must be a sequence of positive integers, got {k!r} in "
                f"ks={ks!r}")
    from okfy.evaluation import _find_run, load_evals, run_suite
    run = _find_run(load_evals(bundle), run_id, suite)
    rows = eval_rows(bundle, run)
    if n_pool_override is not None:
        n_pool, n_pool_reason = n_pool_override
    else:
        n_pool, n_pool_reason = pool_size(bundle), None
    return {"run_id": run["run_id"], "suite": run_suite(run),
            "label": f"computed from the recorded run {run['run_id']}; "
                     "nothing was re-run",
            "rows": rows,
            "ranking": _ranking_metrics(rows, tuple(ks), n_pool,
                                        depth=_recorded_depth(run),
                                        n_pool_reason=n_pool_reason),
            "abstention": _abstention_metrics(rows)}


# --------------------------------------------------------------------------
# `okfy eval compare`
# --------------------------------------------------------------------------

def _era_field(run: dict, key: str):
    return run[key] if key in run else NOT_RECORDED


def _run_header(run: dict) -> dict:
    return {"run_id": run.get("run_id"),
            "tool_version": _era_field(run, "tool_version"),
            "retrieval_schema": _era_field(run, "retrieval_schema"),
            "retrieval_fingerprint": _era_field(run, "retrieval_fingerprint"),
            "query_options": _era_field(run, "query_options")}


def _resolve_run(data: dict, run_id: str, suite: str | None):
    from okfy.evaluation import _find_run
    if run_id == "latest":
        return _find_run(data, "latest", suite or "acceptance")
    return _find_run(data, run_id)                 # explicit id: suite-agnostic


def _query_diff(base_r: dict, head_r: dict, adversarial: bool) -> dict | None:
    """v0.25 audit F08: this used to pick ONE run's declared concept —
    `base_r.get("concept") or head_r.get("concept")` — and use it to rank
    BOTH runs, so a target that changed from `a` to `b` between runs, with
    identical hits in both, produced identical ranks and reported no
    difference at all: a comparison the tool never actually made, reported
    as agreement.

    The release replay (`evaluation.replay_run_bounded`) already has the
    discipline this needed: compare each named field EXPLICITLY and report
    `recorded`/`replayed` (there) — `base`/`head` (here) — side by side,
    never collapse two runs' values into one with `or`. Reused below:
    `base_concept`/`head_concept` and `base_expect`/`head_expect` are each
    run's OWN declared criterion, `base_rank`/`head_rank` is each one's OWN
    target's rank in that run's OWN hits — never one target's rank looked
    up in the other run's hits. When the criterion itself changed
    (`concept` or `expect` differs), `criteria_changed` is set and the block
    says explicitly that base_rank/head_rank are not a like-for-like ranking
    comparison — they answer two different questions, not one question
    twice."""
    base_ids = [h["id"] for h in (base_r.get("top_hits") or []) if isinstance(h, dict)]
    head_ids = [h["id"] for h in (head_r.get("top_hits") or []) if isinstance(h, dict)]
    entered = [i for i in head_ids if i not in base_ids]
    left = [i for i in base_ids if i not in head_ids]
    rank_moves = []
    for i in head_ids:
        if i in base_ids:
            old, new = base_ids.index(i) + 1, head_ids.index(i) + 1
            if old != new:
                rank_moves.append({"id": i, "from": old, "to": new})
    base_notes, head_notes = set(base_r.get("notes") or []), set(head_r.get("notes") or [])
    notes_gained = sorted(head_notes - base_notes)
    notes_lost = sorted(base_notes - head_notes)
    concept_block = None
    if adversarial:
        base_concept, head_concept = base_r.get("concept"), head_r.get("concept")
        base_expect, head_expect = base_r.get("expect"), head_r.get("expect")
        if base_concept or head_concept:
            b_rank = (base_ids.index(base_concept) + 1
                     if base_concept and base_concept in base_ids else None)
            h_rank = (head_ids.index(head_concept) + 1
                     if head_concept and head_concept in head_ids else None)
            b_out, h_out = base_r.get("outcome"), head_r.get("outcome")
            criteria_changed = (base_concept != head_concept
                               or base_expect != head_expect)
            if criteria_changed or b_rank != h_rank or b_out != h_out:
                concept_block = {
                    "base_concept": base_concept, "head_concept": head_concept,
                    "base_expect": base_expect, "head_expect": head_expect,
                    "base_rank": b_rank, "head_rank": h_rank,
                    "base_outcome": b_out, "head_outcome": h_out,
                    "criteria_changed": criteria_changed,
                }
                if criteria_changed:
                    concept_block["note"] = (
                        "the declared target changed between runs "
                        f"(base: expect={base_expect!r} concept={base_concept!r}; "
                        f"head: expect={head_expect!r} concept={head_concept!r}) "
                        "— base_rank/head_rank are each run's OWN target's own "
                        "rank, not one target's rank in both runs; this is not "
                        "a like-for-like ranking comparison")
    if not (entered or left or rank_moves or notes_gained or notes_lost or concept_block):
        return None
    out = {"query": base_r.get("query"), "entered": entered, "left": left,
           "rank_moves": rank_moves, "notes_gained": notes_gained,
           "notes_lost": notes_lost}
    if concept_block:
        out["concept"] = concept_block
    return out


def eval_compare(bundle, base_id: str, head_id: str = "latest",
                 suite: str | None = None) -> dict:
    """Pure diff of two RECORDED runs, joined by exact query string. No
    verdict, no retrieval, no git — see the module docstring."""
    from okfy.evaluation import load_evals, run_suite
    data = load_evals(bundle)
    base = _resolve_run(data, base_id, suite)
    head = _resolve_run(data, head_id, suite)
    bs, hs = run_suite(base), run_suite(head)
    if bs != hs:
        raise ValueError(
            f"{E_EVAL_COMPARE_SUITE}: base run {base['run_id']} is suite "
            f"{bs!r}, head run {head['run_id']} is suite {hs!r} — pick two "
            "runs of the same suite (`okfy eval metrics --run` lists each "
            "run's suite), or pass --suite when a record holds both")

    base_results = {r.get("query"): r for r in base.get("results") or []
                    if isinstance(r, dict)}
    head_results = {r.get("query"): r for r in head.get("results") or []
                    if isinstance(r, dict)}
    common = [q for q in base_results if q in head_results]
    only_base = sorted(q for q in base_results if q not in head_results)
    only_head = sorted(q for q in head_results if q not in base_results)

    queries = []
    for q in common:
        d = _query_diff(base_results[q], head_results[q], bs == "adversarial")
        if d is not None:
            queries.append(d)

    return {"base_run_id": base["run_id"], "head_run_id": head["run_id"],
            "suite": bs, "base": _run_header(base), "head": _run_header(head),
            "queries": queries, "only_in_base": only_base, "only_in_head": only_head,
            "summary": {"compared": len(common), "with_differences": len(queries),
                       "only_in_base": len(only_base), "only_in_head": len(only_head)}}


# --------------------------------------------------------------------------
# `okfy eval qrels --unit span`
# --------------------------------------------------------------------------

UNITS = ("span",)


def eval_qrels(bundle, unit: str = "span") -> dict:
    """Span-level ground truth for the raw-corpus arm: every source a
    covered-with-concept adversarial row's declared concept cites, resolved
    to (file, start, end) via the SAME anchor grammar `okfy sourcemap`
    validates against.

    v0.25 audit F11: PARSED is not VERIFIED, and this reader used to report
    only two states. `cited_span`'s line-anchor branch now checks existence
    and bounds whenever corpus bytes are available (see its docstring), so
    with a readable corpus a bad citation — a missing file, `L0`, a reversed
    range, a beyond-EOF range — correctly comes back unresolved rather than
    resolved-on-grammar-alone. But a citation this reader never even HAD
    corpus bytes to check is a third thing, not either of the first two: it
    PARSED (the shape is a legal line range) and is simply UNVERIFIABLE, and
    conflating it with either `spans` (claiming it was checked and passed)
    or `unresolved` (claiming it names something wrong) would both overstate
    what this reader established. Three lists, not two:

    - `spans`: VERIFIED — corpus was readable and the span checked out.
    - `unresolved`: this reader could not place the citation at all, or the
      corpus was readable and the citation was checked and found bad.
    - `unverifiable`: the citation parsed to a well-formed candidate span,
      but the bundle's corpus was not locally readable, so nothing about it
      was actually checked. Never silently promoted to `spans` — an
      unreachable corpus is not evidence the span is right, only that this
      reader could not tell."""
    if unit not in UNITS:
        raise ValueError(f"unsupported --unit {unit!r} (use: {list(UNITS)})")
    from okfy.evaluation import suite_queries
    from okfy.sourcemap import _corpus, cited_span
    corpus = _corpus(bundle)
    rows = []
    # Per-run cache, local to this one call — same shape and reasoning as
    # `validate._check_quotes`'s: several adversarial rows routinely cite the
    # same corpus file, and `cited_span`'s own F11 bounds check must not
    # re-read a file an earlier row in this loop already read.
    lines_cache: dict[str, list[str] | None] = {}
    for spec in suite_queries(bundle, "adversarial"):
        if not isinstance(spec, dict) or spec.get("expect") != "covered":
            continue
        concept_id = spec.get("concept")
        if not concept_id:
            continue
        c = bundle.get(concept_id)
        spans, unresolved, unverifiable = [], [], []
        for s in (c.meta.get("sources") or []) if c else []:
            s = str(s)
            path, start, end = cited_span(s, corpus, lines_cache=lines_cache)
            if start is None:
                unresolved.append(s)
            elif corpus is None:
                # PARSED, never checked against a byte — see the docstring.
                unverifiable.append(s)
            else:
                spans.append({"file": path, "start": start, "end": end})
        rows.append({"query": spec.get("query"), "concept": concept_id,
                     "concept_found": c is not None,
                     "spans": spans, "unresolved": unresolved,
                     "unverifiable": unverifiable})
    return {"unit": unit, "rows": rows}
