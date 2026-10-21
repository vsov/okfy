import datetime
import errno
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from okfy.bundle import is_bundle_dir
from okfy.frontmatter import serialize
from okfy.gitenv import run_git
from okfy.guard import assert_safe_bundle_path
from okfy.ids import new_id
from okfy.segment import CorpusListingError, git_listing


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
    # Bytes, decoded only after the exit status (R23-2): text=True decodes with
    # the locale and replaces bad bytes, so a non-ASCII stderr/stdout could be
    # read as a value. A SHA is ASCII hex; anything else is not one.
    r = run_git(corpus, "rev-parse", "HEAD", capture_output=True)
    if r.returncode != 0:
        return None
    try:
        sha = r.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    return sha if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha) else None


def _manifest(corpus: Path, errors: list[str] | None = None,
              skipped_symlinks: list[str] | None = None) -> dict[str, str]:
    """path -> sha256, for every non-hidden file under `corpus`.

    A corpus whose root holds `.git` is listed by `segment.git_listing` (`git
    ls-files --cached --others --exclude-standard`), the listing the plan
    reads, so a gitignored file (a private corpus, a build directory) never
    reaches the committed manifest; hidden components and embedded bundles are
    dropped as the walk below drops them. Any other corpus — no git at its
    root, or a subdirectory of a repo — takes the os.walk below unchanged, as
    the plan takes rglob.

    A selected corpus whose git listing FAILED or is PARTIAL (R21-3, R21-4) is
    never walked: a walk would add the gitignored files git was asked to leave
    out. Without `errors` it raises `CorpusListingError` (E_CORPUS_LISTING);
    with `errors` it appends a descriptive entry and returns what git did list
    (nothing, when it failed), so `update.corpus_diff` can refuse with
    E_DIFF_PARTIAL_LISTING instead of reading unseen files as removed.

    Walked with `os.walk(..., onerror=...)` rather than `Path.rglob` so an
    unreadable directory or file can be REPORTED instead of silently dropped
    from the listing: `rglob` swallows `OSError` from `scandir` the same way
    `os.walk`'s default (no `onerror`) does. `errors` collects the path of
    every directory or file `os.walk`/`read_bytes` could not read (listed git
    files included); `update.corpus_diff` is the caller that treats a
    non-empty list as E_DIFF_PARTIAL_LISTING.

    A read failure is a refusal, not an omission (R22-1): read failures are
    collected always, and a caller that passed no `errors` list (init,
    refresh_snapshot, with or without `force`) gets `CorpusListingError` with
    state "unreadable" before it writes anything, because a successful listing
    does not prove the listed bytes were readable and a baseline that silently
    lacks a file it just listed is not a capture.

    A listed or walked name is classified by one `os.stat` (follows symlinks)
    (R23-3). ENOENT/ENOTDIR means absent (a dangling symlink is not a listing
    failure) and is skipped; any other stat error (EACCES, ELOOP) is
    collected as unreadable, never silently skipped. A stat that succeeds but
    is not a regular file — a directory, FIFO, socket, device — is skipped and
    never read (`read_bytes()` on a FIFO would hang forever); neither belongs
    in `errors`. `skipped_symlinks` collects the relative path of
    every skipped entry that IS a symlink (broken or otherwise non-file), for
    `update.corpus_diff` to report as `skipped_dangling_symlinks` — a note,
    never a refusal."""
    out: dict[str, str] = {}
    # Read failures are collected ALWAYS (R22-1): a caller's `errors` list gets
    # them and nothing is raised; a caller without one is refused below, because
    # a manifest that silently lacks a file it just listed is not a capture.
    problems: list[str] = errors if errors is not None else []

    def onerror(exc: OSError) -> None:
        problems.append(getattr(exc, "filename", None) or str(exc))

    def _is_regular(p: Path, rel: str) -> bool:
        """True only for a regular file to read; absent / non-file names are
        skipped (symlinks noted), an unobservable one goes to `problems`."""
        try:
            st = os.stat(p)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                if skipped_symlinks is not None and os.path.islink(p):
                    skipped_symlinks.append(rel)
            else:
                problems.append(rel)
            return False
        if not stat.S_ISREG(st.st_mode):
            if skipped_symlinks is not None and os.path.islink(p):
                skipped_symlinks.append(rel)
            return False
        return True

    def done() -> dict[str, str]:
        if errors is None and problems:
            shown = ", ".join(str(x) for x in problems[:5])
            more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
            raise CorpusListingError(corpus, "unreadable", f"{shown}{more}")
        return out

    try:
        listed = git_listing(corpus)
    except CorpusListingError as exc:
        if errors is None:
            raise
        errors.append(f"git listing {exc.state}: {exc.detail or 'no detail from git'}")
        listed = exc.listed or []        # failed: nothing, and never a walk
    if listed is not None:
        for rel in listed:
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            p = corpus / rel
            if not _is_regular(p, rel):
                continue
            try:
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError:
                problems.append(rel)
        return done()

    for dirpath, dirnames, filenames in os.walk(corpus, onerror=onerror):
        # Prune hidden dirs from descent — matches the old rglob filter,
        # which excluded any path with a "."-prefixed component — and any
        # bundle embedded in the corpus, wherever it sits: `.okf/` was only
        # ever skipped for its dot, so one at `kb/` was listed as corpus.
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")
                             and not is_bundle_dir(Path(dirpath) / d))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = Path(dirpath) / name
            rel = p.relative_to(corpus).as_posix()
            if not _is_regular(p, rel):
                continue
            try:
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError:
                problems.append(rel)
    return done()


def manifest_digest(manifest: dict[str, str]) -> str:
    """One sha256 over a `_manifest` — every path read and the sha256 of the
    bytes read from it. This is what a snapshot can prove it acted on; the
    corpus git SHA cannot, because the walk reads the working tree (dirty
    files included) and an embedded bundle shares the corpus HEAD."""
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


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
    # Listed before the bundle exists: an embedded one is recognised by the
    # meta/corpus.md written below, so a later walk would read it as corpus.
    manifest = _manifest(corpus)
    path.mkdir(parents=True, exist_ok=False)
    meta = path / "meta"
    meta.mkdir()
    today = datetime.date.today().isoformat()

    (path / ".gitignore").write_text(".okfy-cache/\n", encoding="utf-8")
    (meta / "purpose.md").write_text(serialize(
        {"type": "Purpose", "title": "(to be written by Purpose Interview)",
         "bundle_uid": new_id(),
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
        json.dumps(manifest, indent=0, sort_keys=True), encoding="utf-8")
    (meta / "corpus.md").write_text(serialize(
        {"type": "CorpusSnapshot", "corpus": str(corpus), "extracted_at": today,
         "git_sha": _corpus_git_sha(corpus), "manifest": "corpus-manifest.json",
         "manifest_digest": manifest_digest(manifest), "embed": embed},
        f"Snapshot of {corpus} taken {today}.\n"), encoding="utf-8")
    (path / "log.md").write_text(f"# Log\n\n## {today}\n\n- init: bundle skeleton\n",
                                 encoding="utf-8")

    if not embed:
        _git(path, "init", "-q")
        _git(path, "add", "-A")
        _git(path, "commit", "-q", "-m", "init: bundle skeleton")
    return path.resolve()
