import subprocess
import sys
from fnmatch import fnmatch
from math import ceil
from pathlib import Path

from okfy import frontmatter
from okfy.bundle import Bundle

DEFAULT_BUDGET = 50_000  # ~tokens of material per Worker (ADR-0008)

DEFAULT_EXCLUDES = {
    "dirs": {"node_modules", "vendor", "dist", "build", "target",
             "__pycache__", ".venv", "venv"},
    "files": ("*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
              "*.min.js", "*.min.css"),
    "exts": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
             ".mp3", ".mp4", ".mov", ".avi", ".wav",
             ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".jar",
             ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pdf",
             ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a",
             ".pyc", ".wasm"},
}


def _is_text(path: Path) -> bool:
    try:
        chunk = path.open(encoding="utf-8").read(4096)
        return "\x00" not in chunk
    except (UnicodeDecodeError, OSError):
        return False


def _default_excluded(rel: str) -> bool:
    parts = rel.split("/")
    if any(d in DEFAULT_EXCLUDES["dirs"] for d in parts[:-1]):
        return True
    name = parts[-1]
    if any(fnmatch(name, g) for g in DEFAULT_EXCLUDES["files"]):
        return True
    return Path(name).suffix.lower() in DEFAULT_EXCLUDES["exts"]


def _walk(corpus: Path) -> list[str]:
    """Relative posix paths, files only. Git corpora get exact gitignore semantics."""
    if (corpus / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "-C", str(corpus), "ls-files", "--cached", "--others",
                 "--exclude-standard", "-z"],
                capture_output=True, text=True, check=True).stdout
            rels = {r for r in out.split("\0") if r and (corpus / r).is_file()}
            return sorted(rels)
        except subprocess.CalledProcessError:
            print(f"warning: {corpus} has .git but is not a valid git repo; "
                  "falling back to rglob", file=sys.stderr)
    rels = []
    for p in sorted(corpus.rglob("*")):
        rel = p.relative_to(corpus)
        if p.is_file() and not any(part.startswith(".") for part in rel.parts):
            rels.append(rel.as_posix())
    return rels


def survey(corpus: Path, sample_chars: int = 400, max_samples: int = 25) -> dict:
    corpus = Path(corpus).resolve()
    rels = _walk(corpus)
    excluded = [r for r in rels if _default_excluded(r)]
    survivors = [r for r in rels if not _default_excluded(r)]
    binary = [r for r in survivors if not _is_text(corpus / r)]
    files = []
    for r in survivors:
        if r in binary:
            continue
        size = (corpus / r).stat().st_size
        files.append({"path": r, "bytes": size,
                      "tokens_est": max(1, size // 4), "ext": Path(r).suffix.lower()})
    by_ext: dict[str, int] = {}
    for f in files:
        by_ext[f["ext"]] = by_ext.get(f["ext"], 0) + 1
    samples = []
    step = max(1, len(files) // max_samples)
    for f in files[::step][:max_samples]:
        head = (corpus / f["path"]).read_text(encoding="utf-8")[:sample_chars]
        samples.append({"path": f["path"], "head": head})
    return {"corpus": str(corpus), "file_count": len(files),
            "tokens_est": sum(f["tokens_est"] for f in files),
            "by_ext": by_ext, "files": files, "samples": samples,
            "skipped": {"excluded": excluded, "binary": binary},
            "oversized": [f["path"] for f in files if f["tokens_est"] > DEFAULT_BUDGET]}


def _char_chunks(rel: str, start: int, end: int, budget: int) -> list[dict]:
    """Fixed-width character windows over text[start:end), 1-based inclusive spans."""
    window = budget * 4
    out = []
    pos = start
    while pos < end:
        e = min(pos + window, end)
        out.append({"path": rel, "chars": f"{pos + 1}-{e}",
                    "tokens_est": max(1, (e - pos) // 4)})
        pos = e
    return out


def _chunk_file(path: Path, rel: str, budget: int) -> list[dict]:
    """Split one oversized file at blank lines nearest to even token-budget cuts.

    Chunks that still exceed the budget (no blank lines in range — minified or
    single-line files) fall back to fixed character windows.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    toks = [len(ln) // 4 for ln in lines]
    prefix = [0]
    for t in toks:
        prefix.append(prefix[-1] + t)
    total = prefix[-1]
    n = max(2, ceil(total / budget))
    blanks = [i for i, ln in enumerate(lines) if not ln.strip()]
    cuts, lo = [], 0  # cut after line index c -> next chunk starts at c+1
    for k in range(1, n):
        ideal = total * k / n
        cands = [i for i in blanks if i > lo] or list(range(lo + 1, len(lines) - 1))
        if not cands:
            break
        c = min(cands, key=lambda i: abs(prefix[i + 1] - ideal))
        cuts.append(c)
        lo = c
    bounds = [0] + [c + 1 for c in cuts] + [len(lines)]
    starts = [0]  # absolute char offset where each line begins
    for ln in lines:
        starts.append(starts[-1] + len(ln) + 1)
    chunks = []
    for s, e in zip(bounds, bounds[1:]):
        if s >= e:
            continue
        tokens = max(1, prefix[e] - prefix[s])
        if tokens > budget:
            end = len(text) if e == len(lines) else starts[e]
            chunks += _char_chunks(rel, starts[s], end, budget)
        else:
            chunks.append({"path": rel, "lines": f"{s + 1}-{e}", "tokens_est": tokens})
    return chunks


def make_segments(files: list[dict], budget: int = DEFAULT_BUDGET,
                  include: list[str] | None = None,
                  exclude: list[str] | None = None,
                  corpus: Path | None = None) -> list[dict]:
    def keep(f):
        if include and not any(fnmatch(f["path"], g) for g in include):
            return False
        if exclude and any(fnmatch(f["path"], g) for g in exclude):
            return False
        return True

    items: list[tuple[str | dict, int]] = []  # (entry, tokens_est)
    for f in (f for f in files if keep(f)):
        src = Path(corpus) / f["path"] if corpus else None
        if f["tokens_est"] > budget and src and src.is_file():
            items += [(c, c["tokens_est"]) for c in _chunk_file(src, f["path"], budget)]
        else:
            items.append((f["path"], f["tokens_est"]))

    segments: list[dict] = []
    cur: list[str | dict] = []
    cur_tokens = 0

    def flush():
        nonlocal cur, cur_tokens
        if cur:
            segments.append({"id": f"segment-{len(segments) + 1:02d}",
                             "files": cur, "tokens_est": cur_tokens, "status": "pending"})
            cur, cur_tokens = [], 0

    for entry, tokens in items:
        if cur and cur_tokens + tokens > budget:
            flush()
        cur.append(entry)
        cur_tokens += tokens
    flush()
    return segments


def _entry_tokens(entry, corpus: Path | None) -> int:
    if isinstance(entry, dict):
        return int(entry.get("tokens_est") or 1)
    p = (Path(corpus) / str(entry)) if corpus else None
    return max(1, p.stat().st_size // 4) if p and p.is_file() else 1


def make_glean_segments(plan_segments: list[dict], uncited: list[str],
                        corpus: Path | None = None,
                        budget: int = DEFAULT_BUDGET) -> list[dict]:
    """Pending segments holding exactly the entries of `done` segments whose file
    produced no concept — `okfy validate`'s `coverage.uncited`.

    A glean pass is not a new provenance mechanism: it is another segment. That
    keeps the whole Stage 4 machine unchanged — job artifact, worker, ledger row,
    `segment-status done` — and `release-check` (fail-closed on job+ledger per
    done segment) needs no exception for it.

    Entries are copied VERBATIM, spans included, so the gleaner is handed the
    same window the first worker saw and not the whole file — the segment budget
    exists precisely because these files do not fit.

    File granularity, matching what coverage measures: a file split across
    several spans counts as cited when ANY span yielded a concept, so its silent
    spans are never gleaned.
    # ponytail: file granularity. The stated upgrade path — "once concepts carry
    # span anchors" — is dead: concepts may cite `path#L10-20`, but `_cited_paths`
    # strips anchors deliberately, so coverage will never report a span. The live
    # route is the v0.19 ledger, whose `covered` map IS keyed by span. Not built:
    # it would glean spans a worker declared `reviewed_empty`, which is the third
    # pass the protocol forbids, and `dropped` already blocks release.
    """
    # A file already queued in a glean segment that has not run yet is not
    # gleaned again: it is still uncited only because nobody has looked, and a
    # second call before the workers run would otherwise duplicate the whole
    # round into segments that each demand their own job artifact and ledger row.
    # A `done` glean segment is not excluded — a file still uncited after being
    # looked at twice is a finding, and refusing to re-queue it is the protocol's
    # call to make, not the core's.
    queued = {e["path"] if isinstance(e, dict) else str(e)
              for s in plan_segments
              if str(s.get("id", "")).startswith("glean-") and s.get("status") != "done"
              for e in (s.get("files") or [])}
    want = set(uncited) - queued
    entries = [e for s in plan_segments if s.get("status") == "done"
               for e in (s.get("files") or [])
               if (e["path"] if isinstance(e, dict) else str(e)) in want]
    if not entries:
        return []
    # Continue the numbering: a second round must not reuse `glean-01`, whose
    # job artifact and ledger row already exist under that id.
    start = sum(1 for s in plan_segments
                if str(s.get("id", "")).startswith("glean-"))
    out: list[dict] = []
    cur: list = []
    cur_tokens = 0

    def flush():
        nonlocal cur, cur_tokens
        if cur:
            out.append({"id": f"glean-{start + len(out) + 1:02d}", "files": cur,
                        "tokens_est": cur_tokens, "status": "pending"})
            cur, cur_tokens = [], 0

    for e in entries:
        t = _entry_tokens(e, corpus)
        if cur and cur_tokens + t > budget:
            flush()
        cur.append(e)
        cur_tokens += t
    flush()
    return out


def append_segments_to_plan(bundle: Bundle, segments: list[dict]) -> None:
    plan = bundle.plan()
    if plan is None:
        raise FileNotFoundError("meta/extraction-plan.md missing — run /okfy:new first")
    plan.meta["segments"] = (plan.meta.get("segments") or []) + segments
    plan.path.write_text(frontmatter.serialize(plan.meta, plan.body), encoding="utf-8")


def write_segments_to_plan(bundle: Bundle, segments: list[dict]) -> None:
    plan = bundle.plan()
    if plan is None:
        raise FileNotFoundError("meta/extraction-plan.md missing — run /okfy:new first")
    plan.meta["segments"] = segments
    plan.path.write_text(frontmatter.serialize(plan.meta, plan.body), encoding="utf-8")


def set_segment_status(bundle: Bundle, segment_id: str, status: str) -> None:
    plan = bundle.plan()
    segs = plan.meta.get("segments", [])
    for s in segs:
        if s["id"] == segment_id:
            s["status"] = status
            break
    else:
        raise KeyError(f"unknown segment: {segment_id}")
    plan.meta["segments"] = segs
    plan.path.write_text(frontmatter.serialize(plan.meta, plan.body), encoding="utf-8")
