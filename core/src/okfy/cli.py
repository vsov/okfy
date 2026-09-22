import argparse
import sys
from pathlib import Path

from okfy.commands import HANDLERS
from okfy.cost import DEFAULT_N as COST_N
from okfy.guard import GuardError
from okfy.memory import EVENTS
from okfy.proposals import ACTIONS, FLAG_TYPES
from okfy.segment import DEFAULT_BUDGET


def _positive_int(v: str) -> int:
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {n}")
    return n


def build_parser() -> argparse.ArgumentParser:
    """The argparse tree, split out from `main` so tests can enumerate a
    subcommand's own option strings (e.g. which `propose` flags are required)
    instead of restating them by hand — a restated list drifts."""
    ap = argparse.ArgumentParser(prog="okfy")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init");     p.add_argument("bundle", type=Path, nargs="?")
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--language", default="en")
    p.add_argument("--embed", action="store_true")
    p.add_argument("--write-policy", dest="write_policy",
                   choices=["proposals", "direct"], default=None)

    p = sub.add_parser("survey");   p.add_argument("corpus", type=Path)

    p = sub.add_parser("segment");  p.add_argument("bundle", type=Path)
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    p.add_argument("--include", nargs="*", default=None)
    p.add_argument("--exclude", nargs="*", default=None)

    p = sub.add_parser("segment-status"); p.add_argument("bundle", type=Path)
    p.add_argument("segment_id"); p.add_argument("status")
    p.add_argument("--reason", default=None,
                   help="required moving to failed/skipped, or done -> pending "
                        "(re-extraction); stored as status_reason on the segment")

    p = sub.add_parser("glean", help="queue a second pass: append pending "
                       "glean-NN segments holding the assigned corpus files no "
                       "concept cites (validate's coverage.uncited)")
    p.add_argument("bundle", type=Path)
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET)

    p = sub.add_parser("cluster");  p.add_argument("bundle", type=Path)

    p = sub.add_parser(
        "merge-audit",
        help="read-only shadow consolidation report: what the drafts held and "
             "the merged concepts no longer do (not a gate, never fails a build)")
    p.add_argument("bundle", type=Path)
    p.add_argument("--ref", default=None,
                   help="git ref holding the drafts (default: auto-detect the "
                        "commit before drafts/ was deleted); overrides live drafts")
    p.add_argument("--group", default=None,
                   help="restrict the report to one merged concept id")
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true",
                   help="summary only, no per-group detail")

    p = sub.add_parser(
        "cost",
        help="read-only context-economics report: tokens entering context to "
             "answer one question three ways (not a gate, never fails a build)")
    p.add_argument("bundle", type=Path)
    p.add_argument("--query", default=None,
                   help="one question to cost (default: purpose.md test_queries)")
    p.add_argument("-n", type=int, default=COST_N,
                   help="results per query for the retrieval strategy")
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true", help="totals only")

    p = sub.add_parser(
        "budget",
        help="advisory size report: concept sizes per type against the "
             "archetype's declared targets, and the always-resident total "
             "(never a gate, no strict flag, exits 0)")
    p.add_argument("bundle", type=Path)
    p.add_argument("--no-archetype", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true", help="no thin-concept list")

    p = sub.add_parser("validate"); p.add_argument("bundle", type=Path)
    p.add_argument("--all", action="store_true", help="include drafts and proposals")
    p.add_argument("--no-archetype", action="store_true")
    p.add_argument("--strict-sources", action="store_true",
                   help="broken sources: paths become errors (new extractions)")
    p.add_argument("--strict-quality", action="store_true",
                   help="missing/incomplete purpose-fitness artifact becomes "
                        "errors (new extractions)")
    p.add_argument("--strict-provenance", action="store_true",
                   help="job artifacts, frozen prompts, and ledger job digests "
                        "must all cross-check")
    p.add_argument("--strict-package", action="store_true",
                   help="every concept reachable from index.md; concepts "
                        "unchanged since okfy package")
    p.add_argument("--strict-execution", action="store_true",
                   help="every job artifact must attest model/provider/sampling/"
                        "harness_version (new extractions; pre-v0.10 bundles warn)")
    p.add_argument("--strict-schema", action="store_true",
                   help="every concept type must be declared — in "
                        "meta/extraction-plan.md `types` if the archetype was "
                        "adapted, else the archetype's canonical_types")
    p.add_argument("--strict-injection", action="store_true",
                   help="corpus-borne instructions (injection scan) become "
                        "errors instead of warnings — what release-check uses")
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser(
        "release-check",
        help="fail-closed release gate: provenance completeness, eval "
             "currency (retrieval fingerprint), acceptance policy")
    p.add_argument("bundle", type=Path)

    p = sub.add_parser("sourcemap", help="validate meta/source-map.jsonl: every "
                       "normalized span maps back to a raw document and its text "
                       "hash still matches (read-only; an absent sidecar is not "
                       "a defect)")
    p.add_argument("bundle", type=Path)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("index");    p.add_argument("bundle", type=Path)
    p.add_argument("--usage", action="store_true")
    p.add_argument("--journal", type=Path, default=None,
                   help="with --usage: add a second, separately labelled "
                        "usage section built from an okfy-mcp --journal file "
                        "(real sessions, not the eval set)")

    p = sub.add_parser("query");    p.add_argument("bundle", type=Path)
    p.add_argument("text"); p.add_argument("--type", dest="type_", default=None)
    p.add_argument("--tag", default=None); p.add_argument("-n", type=int, default=10)
    p.add_argument("--include-meta", action="store_true")
    p.add_argument("--no-expand", dest="expand", action="store_false",
                   help="skip lexicon query expansion")
    p.add_argument("--no-stale", dest="include_stale", action="store_false",
                   help="drop stale concepts instead of marking them")

    p = sub.add_parser("show");     p.add_argument("bundle", type=Path)
    p.add_argument("concept_id")

    p = sub.add_parser(
        "fresh",
        help="read-only per-id freshness check: compare a caller-remembered "
             "sha256 against each concept's CURRENT file bytes and stale "
             "flag (never writes; never touches git)")
    p.add_argument("bundle", type=Path)
    p.add_argument("--ids", required=True, metavar="ID=SHA[,ID=SHA...]",
                   help="comma-separated id=sha256 pairs to check")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("links");    p.add_argument("bundle", type=Path)
    p.add_argument("concept_id")

    p = sub.add_parser("sample");   p.add_argument("bundle", type=Path)
    p.add_argument("--fraction", type=float, default=0.1)
    p.add_argument("--minimum", type=int, default=20)

    p = sub.add_parser("diff");     p.add_argument("bundle", type=Path)
    # JSON stays the default: v0.23 callers parse it. --json is accepted so a
    # skill can say what it means; --text is the human summary.
    p.add_argument("--json", action="store_true")
    p.add_argument("--text", action="store_true")
    p = sub.add_parser(
        "changes",
        help="read-only window query over meta/memory.jsonl — every ledger "
             "row is judged by its OWN `at`, never by a related event's time "
             "(see the module docstring in okfy.commands.changes)")
    p.add_argument("bundle", type=Path)
    p.add_argument("--since", default=None,
                   help="inclusive lower bound: a bare date (2026-01-31, "
                        "read as 00:00:00Z) or a full RFC3339 UTC timestamp "
                        "(2026-01-31T14:30:00Z); omit for no lower bound")
    p.add_argument("--until", default=None,
                   help="EXCLUSIVE upper bound, same accepted shapes as "
                        "--since")
    p.add_argument("--target", default=None, metavar="CONCEPT_ID")
    p.add_argument("--event", action="append", choices=list(EVENTS),
                   default=None,
                   help="repeatable; filters on the ledger EVENT kind — "
                        "propose/accept/reject/superseded/dismiss (NOT the "
                        "same as --action below)")
    p.add_argument("--action", action="append", choices=sorted(ACTIONS),
                   default=None,
                   help="repeatable; filters on the underlying proposal's "
                        "ACTION — create/update/delete/supersede/flag/gap "
                        "(NOT the same as --event above: an accept EVENT can "
                        "carry a delete ACTION)")
    p.add_argument("--actor", default=None)
    # JSON stays the default, as in `okfy diff`: --json is accepted so a
    # skill can say what it means; --text is the human summary.
    p.add_argument("--json", action="store_true")
    p.add_argument("--text", action="store_true")

    p = sub.add_parser("snapshot"); p.add_argument("bundle", type=Path)
    # v0.25 audit F03: git mode now pins HEAD's committed tree, never the
    # working tree, so a DIRTY corpus is refused by default (E_CORPUS_DIRTY)
    # rather than silently advancing the baseline past uncommitted changes —
    # --force overrides, and says so in `okfy snapshot`'s own output.
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("repair-links"); p.add_argument("bundle", type=Path)
    p.add_argument("--dry-run", dest="dry_run", action="store_true")

    p = sub.add_parser(
        "reanchor",
        help="v0.25 audit F02: repair a moved anchor — the ONLY thing that "
             "clears a pin's anchor_stale flag. Rewrites the concept's own "
             "citation text from the stale anchor to its measured moved_to; "
             "does not touch concept content.")
    p.add_argument("bundle", type=Path)
    p.add_argument("--only", action="append", default=None,
                   metavar="CONCEPT_ID:SOURCE",
                   help="repeatable; restrict repair to this concept:source "
                        "pair (as named by `okfy diff`'s reanchor list) "
                        "instead of every unresolved anchor")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="report what would be repaired without writing")

    p = sub.add_parser("package");  p.add_argument("bundle", type=Path)
    p.add_argument("--demote-unretrieved", action="store_true")
    p.add_argument("--shard-index", action="store_true",
                   help="v0.24: opt-in two-level index.md — one resident line "
                        "per top-level concept directory, full listings moved "
                        "to non-resident index/<dir>.md")

    p = sub.add_parser("log");      p.add_argument("bundle", type=Path)
    p.add_argument("message")

    p = sub.add_parser("propose");  p.add_argument("bundle", type=Path)
    # v0.24: --target is optional at the argparse level — `gap` never takes
    # one, `flag` takes --target OR --query. propose() enforces the real
    # per-action requirement table (see its ValueError messages); this is
    # just "not every action can say what its target is before parsing".
    p.add_argument("--target", required=False, default=None)
    p.add_argument("--action",
                   choices=["create", "update", "delete", "supersede", "flag", "gap"],
                   default="update")
    p.add_argument("--note", default="")
    p.add_argument("--from", dest="from_file", type=Path, default=None,
                   help="full concept .md (not needed for delete, flag, gap, "
                        "or --patch-file)")
    p.add_argument("--type", dest="flag_type", default=None,
                   choices=list(FLAG_TYPES),
                   help="required with --action flag: what is wrong")
    p.add_argument("--query", default=None,
                   help="required with --action gap: the user's own wording "
                        "of the question the bundle could not answer; with "
                        "--action flag, an alternative to --target")
    p.add_argument("--patch-file", dest="patch_file", type=Path, default=None,
                   help="with --action update: JSON list of {old, new, "
                        "count=1} hunks applied to the target's current body "
                        "(mutually exclusive with --from)")
    p.add_argument("--as", dest="actor", required=True,
                   help="who is proposing, as an OKF v0.2 actor: "
                        "<producer>/<version> (claude-code/1.0) or <prefix>:<id>")
    p.add_argument("--evidence", default=None,
                   help="what the change rests on: <kind>=<ref>, kind one of "
                        "test-run, owner-decision, external-source, "
                        "agent-inference (the last needs no ref)")
    p.add_argument("--extends", default=None, metavar="CONCEPT_ID",
                   help="add to this existing concept instead of creating a new "
                        "one (turns the proposal into an update)")
    p.add_argument("--reopen", default=None, metavar="WHAT_CHANGED",
                   help="re-propose text the owner rejected, stating the new "
                        "evidence that justifies asking again")
    p.add_argument("--distinct-from", dest="distinct_from", action="append",
                   default=None, metavar="ID=REASON",
                   help="declare that a near-duplicate concept W_PROPOSAL_NEAR "
                        "found is NOT the same thing (repeatable); suppresses "
                        "the warning for that id without blocking anything")
    p.add_argument("--new-id", dest="new_id", default=None, metavar="CONCEPT_ID",
                   help="required with --action supersede: id of the new "
                        "concept that replaces --target (must not exist)")
    p.add_argument("--supersedes", dest="supersedes", default=None,
                   metavar="PROPOSAL_ID",
                   help="replace this open proposal instead of leaving both "
                        "open — only when it shares this proposal's actor "
                        "and target (E_PROPOSAL_LANE otherwise)")
    p.add_argument("--reverts", dest="reverts", default=None, metavar="GIT_SHA",
                   help="with --action update: this proposal reverts to the "
                        "named git sha, recorded as origin: revert at accept")
    p.add_argument("--batch", type=Path, default=None,
                   help="JSONL file, one proposal per line, keys mirroring "
                        "propose()'s own parameters (action, target, note, "
                        "content, evidence, extends, reopen, distinct_from, "
                        "new_id, supersedes, reverts, flag_type, query, patch) "
                        "— exclusive of every single-entry flag above; --as "
                        "applies to the whole batch. Validated all-first: any "
                        "entry refused writes nothing unless --partial")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="with --batch: validate only, never write")
    p.add_argument("--partial", action="store_true",
                   help="with --batch: file the entries that passed even if "
                        "others were refused (still exits non-zero)")

    p = sub.add_parser("review")
    rsub = p.add_subparsers(dest="rcmd", required=True)
    r = rsub.add_parser("list");    r.add_argument("bundle", type=Path)
    r = rsub.add_parser("show");    r.add_argument("bundle", type=Path)
    r.add_argument("proposal_id"); r.add_argument("--json", action="store_true")
    r = rsub.add_parser("accept");  r.add_argument("bundle", type=Path)
    r.add_argument("proposal_id")
    r.add_argument("--as", dest="as_actor", default=None,
                   help="v0.24 (c): owner-only verb — defaults to the git-config "
                        "owner role. An explicit value is honoured only when it "
                        "is a human: actor (E_OWNER_ACTION_REQUIRED otherwise)")
    r = rsub.add_parser("reject");  r.add_argument("bundle", type=Path)
    r.add_argument("proposal_id"); r.add_argument("--reason", default="")
    r.add_argument("--as", dest="as_actor", default=None,
                   help="v0.24 (c): owner-only verb — same rule as review accept")

    p = sub.add_parser("refine");   p.add_argument("bundle", type=Path)
    p.add_argument("concept_id")
    p.add_argument("--from", dest="from_file", type=Path, required=True)
    p.add_argument("-m", "--message", default="")

    p = sub.add_parser(
        "dismiss",
        help="v0.26 audit A3: owner disposition that clears an UNRESOLVED "
             "rejected change — a still-affected concept whose most recent "
             "meta/memory.jsonl event is `okfy review reject`. Records that "
             "the SOURCE CHANGE itself needs no action, distinct from "
             "rejecting the proposed text; the only other thing that clears "
             "E_UPDATE_REJECTION_UNRESOLVED is a fresh proposal on the same "
             "target actually being accepted.")
    p.add_argument("bundle", type=Path)
    p.add_argument("concept_id")
    p.add_argument("--reason", required=True,
                   help="why the source change needs no action — a "
                        "reviewed decision, same spirit as okfy stale")

    p = sub.add_parser("stale");    p.add_argument("bundle", type=Path)
    p.add_argument("concept_id", nargs="?")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--reason", help="owner's reason to distrust this concept")
    g.add_argument("--clear", action="store_true", help="remove the stale flag")
    g.add_argument("--due", action="store_true",
                   help="list concepts whose review_due date has passed — a "
                        "listing, never a stale flag")

    p = sub.add_parser("eval")
    esub = p.add_subparsers(dest="ecmd", required=True)
    e = esub.add_parser("run");     e.add_argument("bundle", type=Path)
    e.add_argument("-n", type=_positive_int, default=10,
                   help="top hits recorded per query (>= 1): a run that "
                        "retrieves nothing cannot be judged")
    e.add_argument("--suite", choices=["acceptance", "adversarial"],
                   default="acceptance",
                   help="acceptance replays purpose.md test_queries; "
                        "adversarial replays adversarial_queries and records "
                        "whether each declared expectation held")
    e = esub.add_parser("verdict"); e.add_argument("bundle", type=Path)
    e.add_argument("run", help="run_id or 'latest'")
    e.add_argument("--suite", choices=["acceptance", "adversarial"],
                   default="acceptance",
                   help="which suite 'latest' refers to")
    e.add_argument("q_index", type=int, metavar="q-idx")
    e.add_argument("verdict", choices=["pass", "fail", "partial"])
    g = e.add_mutually_exclusive_group(required=True)
    g.add_argument("--llm", dest="role", action="store_const", const="llm",
                   help="LLM-judge proposal (stays provisional)")
    g.add_argument("--owner", dest="role", action="store_const", const="owner",
                   help="owner verdict (the only kind release acceptance counts)")
    e.add_argument("--reason", default=None)
    e.add_argument("--note", dest="reason", help="alias for --reason (owner wording)")
    e = esub.add_parser("status");  e.add_argument("bundle", type=Path)
    e.add_argument("--suite", choices=["acceptance", "adversarial"],
                   default="acceptance")
    e.add_argument("run", nargs="?", default="latest")

    e = esub.add_parser(
        "metrics", help="read-only recall@k/MRR/abstention over ONE recorded "
                        "eval run, with oracle/random control arms and "
                        "always/never-abstain arms (nothing is re-run)")
    e.add_argument("bundle", type=Path)
    e.add_argument("--run", default="latest", help="run_id or 'latest'")
    e.add_argument("--suite", choices=["acceptance", "adversarial"],
                   default="adversarial")
    e.add_argument("--k", default="5,10",
                   help="comma-separated recall@k cutoffs (default 5,10)")
    e.add_argument("--json", action="store_true")

    e = esub.add_parser(
        "compare", help="read-only diff of two recorded eval runs, joined by "
                        "exact query string — no verdict, no retrieval, no git")
    e.add_argument("bundle", type=Path)
    e.add_argument("--base", required=True, help="run_id (not 'latest')")
    e.add_argument("--head", default="latest", help="run_id or 'latest'")
    e.add_argument("--suite", choices=["acceptance", "adversarial"],
                   default=None,
                   help="required to disambiguate 'latest' when not given "
                        "explicitly for both --base and --head")
    e.add_argument("--json", action="store_true")

    e = esub.add_parser(
        "qrels", help="read-only span-level ground truth: every source a "
                      "covered adversarial row's declared concept cites, "
                      "resolved via okfy.sourcemap.cited_span")
    e.add_argument("bundle", type=Path)
    e.add_argument("--unit", choices=["span"], default="span")
    e.add_argument("--json", action="store_true")

    p = sub.add_parser("job", help="freeze a segment's worker inputs into a "
                       "versioned job artifact (meta/jobs/<segment>.json)")
    p.add_argument("bundle", type=Path)
    p.add_argument("segment_id")
    p.add_argument("--prompt-file", dest="prompt_file", type=Path, required=True,
                   help="the exact worker prompt text (its SHA-256 is recorded)")
    p.add_argument("--execution-file", dest="execution_file", type=Path, default=None,
                   help="JSON with model/provider/sampling/harness_version — the "
                        "harness's attestation of WHO ran the job (the core cannot "
                        "observe it; a lying harness passes)")

    p = sub.add_parser("dissent", help="adjudication ledger for merge groups — "
                       "records that a merge was contested and how it was resolved")
    xsub = p.add_subparsers(dest="xcmd", required=True)
    x = xsub.add_parser("add");   x.add_argument("bundle", type=Path)
    x.add_argument("--run", required=True)
    x.add_argument("--group", required=True, help="the merged concept id")
    x.add_argument("--draft", action="append", required=True,
                   help="draft id that held the claim (repeatable)")
    x.add_argument("--claim", required=True)
    x.add_argument("--anchor", required=True, help="path#L10-L20 in the source")
    x.add_argument("--verdict", choices=["split", "no-schism"], required=True)
    x.add_argument("--overruled-because", dest="overruled_because", default="",
                   help="the consolidator's note on why the merge was kept — "
                        "annotates the row, never resolves it; only an owner "
                        "waiver closes a split")
    x = xsub.add_parser("list");  x.add_argument("bundle", type=Path)
    x.add_argument("--group", default=None)
    x = xsub.add_parser("waive"); x.add_argument("bundle", type=Path)
    x.add_argument("--group", required=True)
    x.add_argument("--reason", required=True)
    x.add_argument("--owner", action="store_true", required=True,
                   help="waiving is an owner act; the flag is the acknowledgement")

    p = sub.add_parser("ledger")
    dsub = p.add_subparsers(dest="dcmd", required=True)
    d = dsub.add_parser("add");     d.add_argument("bundle", type=Path)
    d.add_argument("--run", required=True)
    d.add_argument("--segment", required=True)
    d.add_argument("--inputs", required=True, help="comma-separated corpus paths")
    d.add_argument("--prompt-version", dest="prompt_version", required=True)
    # "concept ids" was wrong and this is the field E_SPAN_OUTPUT now joins
    # against: all 44 rows across the real bundles hold draft ids
    # (`drafts/segment-01/breach-escalation`), which is also what
    # `--spans-file`'s `covered` lists and what extract.md's Stage 4 asks for.
    d.add_argument("--outputs", required=True,
                   help="comma-separated draft ids written by this pass "
                        "(e.g. drafts/segment-01/breach-escalation)")
    d.add_argument("--validation", required=True)
    d.add_argument("--merge-map", dest="merge_map", default=None,
                   help="draft=final,draft2=final2 (consolidation rows)")
    d.add_argument("--job", default=None, metavar="SEGMENT_ID",
                   help="segment id of the worker-job artifact; the digest is "
                        "computed from meta/jobs/<id>.json, never hand-passed")
    d.add_argument("--spans-file", dest="spans_file", type=Path, default=None,
                   help="JSON with covered/reviewed_empty/dropped keyed by span "
                        "(path, path#L1-40, path#C1-4000) — the WORKER'S OWN "
                        "report of what it did with each assigned span, not a "
                        "measurement the core made")
    d = dsub.add_parser("list");    d.add_argument("bundle", type=Path)
    d.add_argument("--run", default=None)
    d.add_argument("--json", action="store_true",
                   help="rows plus dropped_totals (per-reason counts summed "
                        "across the listed rows' `dropped` blocks)")

    p = sub.add_parser("workspace")
    wsub = p.add_subparsers(dest="wcmd", required=True)
    w = wsub.add_parser("init");    w.add_argument("dir", type=Path)
    w.add_argument("--member", action="append", required=True,
                   help="role:name=path (role: knowledge|constraints|personal)")
    w.add_argument("--title", default="Workspace")
    w.add_argument("--project-key", dest="project_key", default=None,
                   help="owner-set slug (^[a-z0-9][a-z0-9._-]{0,63}$) that "
                        "scopes any `personal`-role member to this workspace; "
                        "required when any --member has role personal")
    w = wsub.add_parser("status");  w.add_argument("dir", type=Path)
    w = wsub.add_parser("export");  w.add_argument("dir", type=Path)
    w.add_argument("out", type=Path)
    w = wsub.add_parser("package"); w.add_argument("dir", type=Path)

    p = sub.add_parser("link-candidates"); p.add_argument("dir", type=Path)

    p = sub.add_parser(
        "transcript-lint",
        help="read-only: structural facts from a HOST session transcript — "
             "tool-call ordering/counts and concept-id-shaped strings named "
             "in assistant text (not a quality verdict; never fails a build)")
    p.add_argument("session", type=Path)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser(
        "codes",
        help="the diagnostic code registry (okfy.codes.CODES): every E_/W_ "
             "code the core and MCP adapter can raise, its meaning, and its "
             "way out — not bundle-specific")
    p.add_argument("--json", action="store_true")

    return ap


def main(argv=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    try:
        return HANDLERS[a.cmd](a)
    except GuardError as e:
        print(str(e), file=sys.stderr)
        return 2
    except (FileNotFoundError, KeyError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
