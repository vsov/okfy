"""Workspace: federation glue over untouched member Bundles (ADR-0010).
Holds no knowledge — only the manifest, roles, crosswalks, and test queries."""
import datetime
import re
from dataclasses import dataclass
from pathlib import Path

from okfy import frontmatter
from okfy.bundle import Bundle
from okfy.gitenv import run_git
from okfy.guard import assert_safe_bundle_path

MANIFEST = "meta/workspace.md"
ROLES = {"knowledge", "constraints", "personal"}

# v0.24 / ADR-0015: a `personal` member's frontmatter `applies_to` is compared
# against the workspace's OWN project_key, never against a path or a git
# remote — OKFy has no project id of its own (see the ADR). Slug shape only;
# "*" is handled separately, at the comparison site, as a wildcard.
PROJECT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _check_project_key(project_key, roles: list[tuple[str, str]]) -> None:
    """Fail closed at the MANIFEST, not at query time. A `personal` member
    with no (or malformed) project_key would otherwise just answer nothing,
    ever — which reads as "no personal memories exist" rather than "this
    workspace was never scoped", the exact silent failure ADR-0015 refuses.

    A non-string project_key (YAML parses an unquoted `2026` as an int, `1.5`
    as a float, `007` as an int) is the same silent failure one layer up:
    `Workspace.project_key()` returns the raw value, `_in_scope` only ever
    compares string `applies_to` entries with `==`, so a numeric-looking key
    passes this gate, loads, and then matches nothing — ever — for any
    non-wildcard personal concept. Reject it here, by type, before the regex
    even runs (`str(2026)` would otherwise satisfy the slug pattern)."""
    if project_key is not None and not isinstance(project_key, str):
        raise ValueError(
            f"E_WS_PROJECT_KEY: project_key must be a string, got "
            f"{type(project_key).__name__} {project_key!r} — YAML parsed an "
            "unquoted manifest value as a non-string, and a non-string key "
            "can never equal any applies_to entry, so personal memory would "
            "silently scope to nothing. Quote it in the workspace manifest, "
            f"e.g. project_key: \"{project_key}\"")
    if project_key is not None and not PROJECT_KEY_RE.match(project_key):
        raise ValueError(
            f"E_WS_PROJECT_KEY: project_key {project_key!r} must match "
            f"{PROJECT_KEY_RE.pattern} (a lowercase slug) — fix it in the "
            "workspace manifest")
    personal = [name for name, role in roles if role == "personal"]
    if personal and not project_key:
        raise ValueError(
            f"E_WS_PROJECT_KEY: workspace has a personal member "
            f"({', '.join(personal)}) but no project_key — personal memory "
            "is scoped per project and nothing in it would be visible. Add "
            "`project_key: <your-key>` to the workspace manifest")


@dataclass
class Member:
    name: str
    path: Path
    role: str
    git_sha: str | None


def _bundle_sha(path: Path) -> str | None:
    r = run_git(path, "rev-parse", "HEAD", capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def is_workspace(path: Path) -> bool:
    return (Path(path) / MANIFEST).is_file()


@dataclass
class Workspace:
    root: Path
    meta: dict
    body: str
    members: list[Member]

    @classmethod
    def load(cls, root: Path) -> "Workspace":
        root = Path(root).resolve()
        manifest = root / MANIFEST
        if not manifest.is_file():
            raise FileNotFoundError(f"not a workspace (no {MANIFEST}): {root}")
        meta, body = frontmatter.parse(manifest.read_text(encoding="utf-8"))
        members = [Member(m["name"], Path(m["path"]), m["role"], m.get("git_sha"))
                   for m in meta.get("members", [])]
        _check_project_key(meta.get("project_key"), [(m.name, m.role) for m in members])
        return cls(root, meta, body, members)

    def member(self, name: str) -> Member:
        for m in self.members:
            if m.name == name:
                return m
        raise KeyError(f"unknown member: {name}")

    def bundles(self) -> dict[str, Bundle]:
        return {m.name: Bundle(m.path) for m in self.members}

    def project_key(self) -> str | None:
        return self.meta.get("project_key")

    def save(self) -> None:
        self.meta["members"] = [
            {"name": m.name, "path": str(m.path), "role": m.role, "git_sha": m.git_sha}
            for m in self.members]
        (self.root / MANIFEST).write_text(
            frontmatter.serialize(self.meta, self.body), encoding="utf-8")


def init_workspace(path: Path, members: list[tuple[str, Path, str]],
                   title: str = "Workspace", project_key: str | None = None) -> Path:
    path = Path(path)
    assert_safe_bundle_path(path)
    entries = []
    for name, mpath, role in members:
        if role not in ROLES:
            raise ValueError(f"invalid role {role!r} for {name} (use: {sorted(ROLES)})")
        mpath = Path(mpath).resolve()
        if not (mpath / "meta" / "purpose.md").is_file():
            raise ValueError(f"{mpath} is not an OKFy bundle (no meta/purpose.md)")
        entries.append({"name": name, "path": str(mpath), "role": role,
                        "git_sha": _bundle_sha(mpath)})
    _check_project_key(project_key, [(e["name"], e["role"]) for e in entries])
    path.mkdir(parents=True, exist_ok=False)
    (path / "meta").mkdir()
    (path / "links").mkdir()
    today = datetime.date.today().isoformat()
    manifest: dict = {"type": "Workspace", "title": title, "language": "en",
                      "members": entries, "test_queries": []}
    if project_key:
        manifest["project_key"] = project_key
    (path / MANIFEST).write_text(frontmatter.serialize(
        manifest,
        f"Federation workspace created {today}. Purpose pending /okfy:workspace.\n"),
        encoding="utf-8")
    (path / "log.md").write_text(f"# Log\n\n## {today}\n\n- init: workspace\n",
                                 encoding="utf-8")
    run_git(path, "init", "-q", check=True)
    run_git(path, "add", "-A", check=True)
    run_git(path, "commit", "-q", "-m", "init: workspace", check=True)
    return path.resolve()


def _diff_names_no_index_write(repo: Path, sha: str, pattern: str):
    """`run_git(repo, "diff", "--name-only", sha, "--", pattern, ...)` that
    never leaves `repo/.git/index` modified — see the comment at its one
    call site (`_changed_concepts`) for why a plain call can write there.
    Falls back to the plain call when the real index can't be located (e.g.
    a bare repo) — the caller already treats a non-'ok' baseline as
    freshness-unproven either way, so a missed protection there changes
    nothing about correctness."""
    import shutil
    import tempfile
    gp = run_git(repo, "rev-parse", "--git-path", "index",
                capture_output=True, text=True)
    real_index = (Path(repo) / gp.stdout.strip()) if gp.returncode == 0 else None
    if not real_index or not real_index.is_file():
        return run_git(repo, "diff", "--name-only", sha, "--", pattern,
                       capture_output=True, text=True)
    with tempfile.TemporaryDirectory() as td:
        tmp_index = Path(td) / "index"
        shutil.copyfile(real_index, tmp_index)
        return run_git(repo, "diff", "--name-only", sha, "--", pattern,
                       capture_output=True, text=True,
                       env={"GIT_INDEX_FILE": str(tmp_index)})


def _changed_concepts(member: Member) -> tuple[str, list[str]]:
    """(baseline, changed-concept-ids) since the pinned SHA. baseline is one
    of: 'ok' (diff computed against a verified pin), 'no-pin' (member was
    registered without a SHA), 'unreachable-pin' (pin malformed or absent
    from history — rewritten, shallow, corrupted), 'git-error' (member is
    not a usable git repo). Anything but 'ok' means freshness CANNOT be
    proven — the caller must fail closed, not assume fresh (audit round 8:
    a git failure used to read as 'nothing changed')."""
    if not member.git_sha:
        return ("no-pin", [])
    if not re.fullmatch(r"[0-9a-f]{40}", str(member.git_sha)):
        return ("unreachable-pin", [])
    probe = run_git(member.path, "cat-file", "-e", f"{member.git_sha}^{{commit}}",
                    capture_output=True, text=True)
    if probe.returncode != 0:
        st = run_git(member.path, "rev-parse", "HEAD",
                    capture_output=True, text=True)
        return ("git-error" if st.returncode != 0 else "unreachable-pin", [])
    # pin vs WORKING TREE (no HEAD arg): federation reads the live member
    # path, so staged and unstaged edits count as change (audit round 8:
    # pin..HEAD missed a dirty working tree)
    #
    # A porcelain `git diff <tree> -- <pathspec>` against the working tree
    # opportunistically rewrites `.git/index` to cache fresh stat info for
    # any entry it finds racily-clean — measured on this checkout's git
    # (2.50.1), `GIT_OPTIONAL_LOCKS=0` does NOT suppress that write for this
    # form of `diff` (only for `status`'s own refresh), so `workspace status`
    # (declared read-only in commands/mutations.py's MUTATES table) would
    # otherwise write into a member bundle the caller did not name (readers
    # audit finding 39). Point GIT_INDEX_FILE at a throwaway copy instead:
    # a tree-vs-worktree diff reads the index only as a stat CACHE, never as
    # one of the two things being compared, so the output is byte-identical
    # and whatever git refreshes lands on the copy, not the real index.
    r = _diff_names_no_index_write(member.path, member.git_sha, "*.md")
    u = run_git(member.path, "ls-files", "--others", "--exclude-standard",
               "--", "*.md", capture_output=True, text=True)
    if r.returncode != 0 or u.returncode != 0:
        return ("git-error", [])
    files = set(r.stdout.splitlines()) | set(u.stdout.splitlines())
    return ("ok", sorted(p[:-3] for p in files if p.endswith(".md")))


def workspace_status(ws: "Workspace") -> dict:
    from okfy.crosswalk import load_rows, parse_ref
    members_out = []
    changed_by_member: dict[str, set[str]] = {}
    unverifiable: set[str] = set()
    for m in ws.members:
        head = _bundle_sha(m.path)
        baseline, changed_list = _changed_concepts(m)
        changed = set(changed_list)
        changed_by_member[m.name] = changed
        if baseline != "ok":
            unverifiable.add(m.name)
        members_out.append({"name": m.name, "role": m.role,
                            "pinned": m.git_sha, "head": head,
                            "baseline": baseline,
                            "fresh": (baseline == "ok" and head == m.git_sha
                                      and not changed),
                            "changed_concepts": sorted(changed)})
    stale = []
    for r in load_rows(ws):
        for ref in (r.src, r.dst):
            mname, cid = parse_ref(ref)
            # a row touching a member whose baseline cannot be verified is
            # stale by definition: there is no proof the reviewed concept
            # still exists as reviewed
            if mname in unverifiable or cid in changed_by_member.get(mname, set()):
                stale.append(r.__dict__)
                break
    return {"members": members_out, "stale_rows": stale,
            "unverifiable_members": sorted(unverifiable)}
