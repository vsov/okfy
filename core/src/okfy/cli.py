import argparse
import sys
from pathlib import Path

from okfy.commands import HANDLERS
from okfy.guard import GuardError
from okfy.segment import DEFAULT_BUDGET


def main(argv=None) -> int:
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

    p = sub.add_parser("cluster");  p.add_argument("bundle", type=Path)

    p = sub.add_parser("validate"); p.add_argument("bundle", type=Path)
    p.add_argument("--all", action="store_true", help="include drafts and proposals")
    p.add_argument("--no-archetype", action="store_true")
    p.add_argument("--strict-sources", action="store_true",
                   help="broken sources: paths become errors (new extractions)")
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser("index");    p.add_argument("bundle", type=Path)

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

    p = sub.add_parser("links");    p.add_argument("bundle", type=Path)
    p.add_argument("concept_id")

    p = sub.add_parser("sample");   p.add_argument("bundle", type=Path)
    p.add_argument("--fraction", type=float, default=0.1)
    p.add_argument("--minimum", type=int, default=20)

    p = sub.add_parser("diff");     p.add_argument("bundle", type=Path)
    p = sub.add_parser("snapshot"); p.add_argument("bundle", type=Path)

    p = sub.add_parser("repair-links"); p.add_argument("bundle", type=Path)
    p.add_argument("--dry-run", dest="dry_run", action="store_true")

    p = sub.add_parser("package");  p.add_argument("bundle", type=Path)

    p = sub.add_parser("log");      p.add_argument("bundle", type=Path)
    p.add_argument("message")

    p = sub.add_parser("propose");  p.add_argument("bundle", type=Path)
    p.add_argument("--target", required=True)
    p.add_argument("--action", choices=["create", "update", "delete"],
                   default="update")
    p.add_argument("--note", default="")
    p.add_argument("--from", dest="from_file", type=Path, default=None,
                   help="full concept .md (not needed for delete)")

    p = sub.add_parser("review")
    rsub = p.add_subparsers(dest="rcmd", required=True)
    r = rsub.add_parser("list");    r.add_argument("bundle", type=Path)
    r = rsub.add_parser("accept");  r.add_argument("bundle", type=Path)
    r.add_argument("proposal_id")
    r = rsub.add_parser("reject");  r.add_argument("bundle", type=Path)
    r.add_argument("proposal_id"); r.add_argument("--reason", default="")

    p = sub.add_parser("refine");   p.add_argument("bundle", type=Path)
    p.add_argument("concept_id")
    p.add_argument("--from", dest="from_file", type=Path, required=True)
    p.add_argument("-m", "--message", default="")

    p = sub.add_parser("stale");    p.add_argument("bundle", type=Path)
    p.add_argument("concept_id")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--reason", help="owner's reason to distrust this concept")
    g.add_argument("--clear", action="store_true", help="remove the stale flag")

    p = sub.add_parser("eval")
    esub = p.add_subparsers(dest="ecmd", required=True)
    e = esub.add_parser("run");     e.add_argument("bundle", type=Path)
    e.add_argument("-n", type=int, default=10)
    e = esub.add_parser("verdict"); e.add_argument("bundle", type=Path)
    e.add_argument("run", help="run_id or 'latest'")
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
    e.add_argument("run", nargs="?", default="latest")

    p = sub.add_parser("ledger")
    dsub = p.add_subparsers(dest="dcmd", required=True)
    d = dsub.add_parser("add");     d.add_argument("bundle", type=Path)
    d.add_argument("--run", required=True)
    d.add_argument("--segment", required=True)
    d.add_argument("--inputs", required=True, help="comma-separated corpus paths")
    d.add_argument("--prompt-version", dest="prompt_version", required=True)
    d.add_argument("--outputs", required=True, help="comma-separated concept ids")
    d.add_argument("--validation", required=True)
    d.add_argument("--merge-map", dest="merge_map", default=None,
                   help="draft=final,draft2=final2 (consolidation rows)")
    d = dsub.add_parser("list");    d.add_argument("bundle", type=Path)
    d.add_argument("--run", default=None)

    p = sub.add_parser("workspace")
    wsub = p.add_subparsers(dest="wcmd", required=True)
    w = wsub.add_parser("init");    w.add_argument("dir", type=Path)
    w.add_argument("--member", action="append", required=True,
                   help="role:name=path (role: knowledge|constraints)")
    w.add_argument("--title", default="Workspace")
    w = wsub.add_parser("status");  w.add_argument("dir", type=Path)
    w = wsub.add_parser("export");  w.add_argument("dir", type=Path)
    w.add_argument("out", type=Path)
    w = wsub.add_parser("package"); w.add_argument("dir", type=Path)

    p = sub.add_parser("link-candidates"); p.add_argument("dir", type=Path)

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
