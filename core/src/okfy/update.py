"""Deterministic incremental re-extraction support (ADR-0005): snapshot diff,
affected-concept reverse lookup, snapshot refresh. LLM re-extraction itself is
orchestrated by the /okfy:update plugin command."""
import datetime
import json
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from okfy import frontmatter
from okfy.bundle import Bundle
from okfy.init import _corpus_git_sha, _manifest


def _snapshot(bundle: Bundle) -> dict:
    c = bundle.get("meta/corpus")
    if c is None:
        raise FileNotFoundError("meta/corpus.md missing — not an OKFy bundle?")
    if c.meta.get("exported"):
        raise ValueError("exported fusion — diff/update/snapshot are not "
                         "supported; re-export from the workspace instead")
    return c.meta


def _embedded_prefix(bundle: Bundle, corpus: Path) -> str | None:
    """For an --embed bundle living inside the corpus, the corpus-relative path
    prefix of the bundle's own files. These must never count as corpus changes
    (the bundle rides the corpus git repo, so git diff would otherwise report
    .okf/** as added). None for standalone bundles outside the corpus."""
    root = bundle.root.resolve()
    corpus = corpus.resolve()
    if root != corpus and root.is_relative_to(corpus):
        return root.relative_to(corpus).as_posix() + "/"
    return None


def corpus_diff(bundle: Bundle) -> dict:
    snap = _snapshot(bundle)
    corpus = Path(snap["corpus"])
    prefix = _embedded_prefix(bundle, corpus)

    def keep(p: str) -> bool:
        return prefix is None or not p.startswith(prefix)

    old_sha = snap.get("git_sha")
    new_sha = _corpus_git_sha(corpus)
    if old_sha and new_sha:
        out = subprocess.run(
            ["git", "-C", str(corpus), "diff", "--name-status", "-M", old_sha, "HEAD"],
            capture_output=True, text=True, check=True).stdout
        changed, added, removed = [], [], []
        for line in out.splitlines():
            parts = line.split("\t")
            status = parts[0]
            if status.startswith("R"):          # rename: treat as remove+add
                removed.append(parts[1])
                added.append(parts[2])
            elif status == "A":
                added.append(parts[1])
            elif status == "D":
                removed.append(parts[1])
            else:                               # M, T, ...
                changed.append(parts[1])
        return {"mode": "git", "old": old_sha, "new": new_sha,
                "changed": sorted(p for p in changed if keep(p)),
                "added": sorted(p for p in added if keep(p)),
                "removed": sorted(p for p in removed if keep(p))}
    manifest_file = bundle.root / "meta" / "corpus-manifest.json"
    old = (json.loads(manifest_file.read_text(encoding="utf-8"))
           if manifest_file.is_file() else {})
    new = _manifest(corpus)
    return {"mode": "manifest", "old": None, "new": None,
            "changed": sorted(p for p in old if p in new and old[p] != new[p] and keep(p)),
            "added": sorted(p for p in new if p not in old and keep(p)),
            "removed": sorted(p for p in old if p not in new and keep(p))}


def _source_path(src: str) -> str:
    return str(src).split("#", 1)[0]


def affected_concepts(bundle: Bundle, files: list[str]) -> dict[str, list[str]]:
    """Reverse lookup: which concepts cite any of these corpus files in sources:."""
    fset = set(files)
    out: dict[str, list[str]] = {}
    for c in bundle.concepts():
        if c.id.startswith("meta/"):
            continue
        hits = sorted({_source_path(s) for s in (c.meta.get("sources") or [])
                       if _source_path(s) in fset})
        if hits:
            out[c.id] = hits
    return out


def update_plan(bundle: Bundle) -> dict:
    diff = corpus_diff(bundle)
    affected = affected_concepts(bundle, diff["changed"] + diff["removed"])

    plan = bundle.plan()
    seg = (plan.meta.get("segmentation") or {}) if plan else {}
    include = seg.get("include") or []
    exclude = seg.get("exclude") or []

    def in_scope(p: str) -> bool:
        if include and not any(fnmatch(p, g) for g in include):
            return False
        return not any(fnmatch(p, g) for g in exclude or [])

    covered: set[str] = set()
    stale_candidates: list[str] = []
    removed_set = set(diff["removed"])
    for c in bundle.concepts():
        if c.id.startswith("meta/"):
            continue
        srcs = {_source_path(s) for s in (c.meta.get("sources") or [])}
        covered |= srcs
        if srcs and srcs <= removed_set:
            stale_candidates.append(c.id)

    # Report suggestion only — never the persisted `stale` flag (ADR-0013:
    # staleness is a reviewed owner decision via `okfy stale`, not automatic).
    return {
        "diff": diff,
        "affected": affected,
        "uncovered_new": sorted(p for p in diff["added"]
                                if in_scope(p) and p not in covered),
        "stale_candidates": sorted(stale_candidates),
    }


def refresh_snapshot(bundle: Bundle) -> None:
    c = bundle.get("meta/corpus")
    if c.meta.get("exported"):
        raise ValueError("exported fusion — diff/update/snapshot are not "
                         "supported; re-export from the workspace instead")
    corpus = Path(c.meta["corpus"])
    c.meta["git_sha"] = _corpus_git_sha(corpus)
    c.meta["extracted_at"] = datetime.date.today().isoformat()
    (bundle.root / "meta" / "corpus-manifest.json").write_text(
        json.dumps(_manifest(corpus), indent=0, sort_keys=True), encoding="utf-8")
    c.path.write_text(frontmatter.serialize(c.meta, c.body), encoding="utf-8")
