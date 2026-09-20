import datetime
import hashlib
import json
import os
from pathlib import Path

from okfy.frontmatter import serialize
from okfy.gitenv import run_git
from okfy.guard import assert_safe_bundle_path


def _git(bundle: Path, *args) -> None:
    """Run git in the bundle, surfacing git's own words on failure.

    `check=True, capture_output=True` swallowed stderr and raised a bare
    CalledProcessError, so the commonest first-run failure — a machine with no
    configured git identity, which is every fresh CI runner and plenty of fresh
    laptops — reached the user as a traceback ending in `exit status 128` and no
    hint of what to do. Found by the CI matrix: the same command passed on macOS
    runners and failed on Ubuntu ones for exactly this reason."""
    r = run_git(bundle, *args, capture_output=True, text=True)
    if r.returncode == 0:
        return
    err = (r.stderr or r.stdout or "").strip()
    hint = ""
    if "Please tell me who you are" in err or "unable to auto-detect" in err \
            or "empty ident name" in err:
        hint = ("\n\ngit has no configured identity on this machine, so it "
                "cannot record the bundle's first commit. Set one:\n"
                '  git config --global user.name "Your Name"\n'
                '  git config --global user.email "you@example.com"\n'
                "or export GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL / "
                "GIT_COMMITTER_NAME / GIT_COMMITTER_EMAIL for this run.")
    raise RuntimeError(f"git {' '.join(args)} failed in {bundle}: {err}{hint}")


def _corpus_git_sha(corpus: Path) -> str | None:
    r = run_git(corpus, "rev-parse", "HEAD", capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _manifest(corpus: Path, errors: list[str] | None = None,
              skipped_symlinks: list[str] | None = None) -> dict[str, str]:
    """path -> sha256, for every non-hidden file under `corpus`.

    Walked with `os.walk(..., onerror=...)` rather than `Path.rglob` so an
    unreadable directory or file can be REPORTED instead of silently dropped
    from the listing: `rglob` swallows `OSError` from `scandir` the same way
    `os.walk`'s default (no `onerror`) does, so passing `errors` is a pure
    addition — callers that omit it (init, refresh_snapshot) see the exact
    same manifest as before. `errors` collects the relative path of every
    directory or file `os.walk`/`read_bytes` could not read; `update.corpus_diff`
    is the caller that treats a non-empty list as E_DIFF_PARTIAL_LISTING.

    A name from `os.walk`'s `filenames` that is NOT `Path.is_file()` — a
    dangling symlink, a FIFO, a socket, a device — is skipped, exactly as the
    v0.23 `rglob()` + `is_file()` walk skipped it (a broken link is not a
    listing failure, it is an absent file). `read_bytes()` on one of these
    would either raise on a broken link or, for a FIFO, hang forever; neither
    belongs in `errors`. `skipped_symlinks` collects the relative path of
    every skipped entry that IS a symlink (broken or otherwise non-file), for
    `update.corpus_diff` to report as `skipped_dangling_symlinks` — a note,
    never a refusal."""
    out: dict[str, str] = {}

    def onerror(exc: OSError) -> None:
        if errors is not None:
            errors.append(getattr(exc, "filename", None) or str(exc))

    for dirpath, dirnames, filenames in os.walk(corpus, onerror=onerror):
        # Prune hidden dirs from descent — matches the old rglob filter,
        # which excluded any path with a "."-prefixed component.
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = Path(dirpath) / name
            rel = p.relative_to(corpus).as_posix()
            if not p.is_file():
                if skipped_symlinks is not None and p.is_symlink():
                    skipped_symlinks.append(rel)
                continue
            try:
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError:
                if errors is not None:
                    errors.append(rel)
    return out


def init_bundle(path: Path | None, corpus: Path, language: str = "en",
                write_policy: str | None = None, embed: bool = False) -> Path:
    corpus = Path(corpus).resolve()
    if embed:
        if _corpus_git_sha(corpus) is None:
            raise ValueError("--embed requires the corpus to be a git repository")
        path = Path(path) if path is not None else corpus / ".okf"
        if not path.resolve().is_relative_to(corpus):
            raise ValueError("--embed bundle path must live inside the corpus")
    elif path is None:
        raise ValueError("bundle path required (or use --embed)")
    else:
        path = Path(path)
    if write_policy is None:
        write_policy = "direct" if embed else "proposals"
    assert_safe_bundle_path(path)
    path.mkdir(parents=True, exist_ok=False)
    meta = path / "meta"
    meta.mkdir()
    today = datetime.date.today().isoformat()

    (path / ".gitignore").write_text(".okfy-cache/\n", encoding="utf-8")
    (meta / "purpose.md").write_text(serialize(
        {"type": "Purpose", "title": "(to be written by Purpose Interview)",
         "language": language, "write_policy": write_policy, "test_queries": [],
         # The dissent gate reads this declaration and returns early when it is
         # absent, so omitting it left the gate off for every bundle created
         # after v0.10 — default-off by omission rather than by decision. New
         # bundles opt in at birth; bundles accepted before the ledger existed
         # stay exempt BY CONSTRUCTION, because they simply do not carry the key
         # and nothing here rewrites them.
         "acceptance": {"dissent": "required"}},
        "Purpose statement pending — /okfy:new fills this in.\n"), encoding="utf-8")
    (meta / "corpus-manifest.json").write_text(
        json.dumps(_manifest(corpus), indent=0, sort_keys=True), encoding="utf-8")
    (meta / "corpus.md").write_text(serialize(
        {"type": "CorpusSnapshot", "corpus": str(corpus), "extracted_at": today,
         "git_sha": _corpus_git_sha(corpus), "manifest": "corpus-manifest.json",
         "embed": embed},
        f"Snapshot of {corpus} taken {today}.\n"), encoding="utf-8")
    (path / "log.md").write_text(f"# Log\n\n## {today}\n\n- init: bundle skeleton\n",
                                 encoding="utf-8")

    if not embed:
        _git(path, "init", "-q")
        _git(path, "add", "-A")
        _git(path, "commit", "-q", "-m", "init: bundle skeleton")
    return path.resolve()
