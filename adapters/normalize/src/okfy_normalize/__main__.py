import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="okfy-normalize",
        description="Convert raw documents to Markdown plus a source-map "
                    "sidecar that `okfy sourcemap` can verify.")
    ap.add_argument("src", type=Path, help="file or directory to convert")
    ap.add_argument("dest", type=Path,
                    help="corpus directory to write Markdown into (never the source)")
    ap.add_argument("--backend", default="passthrough",
                    help="conversion backend (passthrough needs nothing installed)")
    ap.add_argument("--option", action="append", default=[], metavar="K=V",
                    help="backend option; every option is covered by "
                         "converter_options_digest (repeatable)")
    a = ap.parse_args(argv)

    from okfy_normalize import normalize_tree
    from okfy_normalize.backends import BackendUnavailable
    options = {}
    for item in a.option:
        if "=" not in item:
            print(f"bad --option {item!r} (want K=V)", file=sys.stderr)
            return 2
        k, v = item.split("=", 1)
        options[k.strip()] = v.strip()
    try:
        out = normalize_tree(a.src, a.dest, backend=a.backend, options=options)
    except BackendUnavailable as e:
        # never a raw ImportError: the user needs an install line, not a stack
        print(str(e), file=sys.stderr)
        return 2
    except (KeyError, ValueError, FileNotFoundError) as e:
        print(str(e).strip("'"), file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
