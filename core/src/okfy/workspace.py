"""Workspace: federation glue over untouched member Bundles (ADR-0010).
Holds no knowledge — only the manifest, roles, crosswalks, and test queries."""
import datetime
import subprocess
from dataclasses import dataclass
from pathlib import Path

from okfy import frontmatter
from okfy.bundle import Bundle
from okfy.guard import assert_safe_bundle_path

MANIFEST = "meta/workspace.md"
ROLES = {"knowledge", "constraints"}


@dataclass
class Member:
    name: str
    path: Path
    role: str
    git_sha: str | None


def _bundle_sha(path: Path) -> str | None:
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
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
        return cls(root, meta, body, members)

    def member(self, name: str) -> Member:
        for m in self.members:
            if m.name == name:
                return m
        raise KeyError(f"unknown member: {name}")

    def bundles(self) -> dict[str, Bundle]:
        return {m.name: Bundle(m.path) for m in self.members}

    def save(self) -> None:
        self.meta["members"] = [
            {"name": m.name, "path": str(m.path), "role": m.role, "git_sha": m.git_sha}
            for m in self.members]
        (self.root / MANIFEST).write_text(
            frontmatter.serialize(self.meta, self.body), encoding="utf-8")


def init_workspace(path: Path, members: list[tuple[str, Path, str]],
                   title: str = "Workspace") -> Path:
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
    path.mkdir(parents=True, exist_ok=False)
    (path / "meta").mkdir()
    (path / "links").mkdir()
    today = datetime.date.today().isoformat()
    (path / MANIFEST).write_text(frontmatter.serialize(
        {"type": "Workspace", "title": title, "language": "en",
         "members": entries, "test_queries": []},
        f"Federation workspace created {today}. Purpose pending /okfy:workspace.\n"),
        encoding="utf-8")
    (path / "log.md").write_text(f"# Log\n\n## {today}\n\n- init: workspace\n",
                                 encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init: workspace"],
                   check=True)
    return path.resolve()


def _changed_concepts(member: Member) -> list[str]:
    """Concept ids whose files changed in the member repo since the pinned SHA."""
    if not member.git_sha:
        return []
    r = subprocess.run(
        ["git", "-C", str(member.path), "diff", "--name-only",
         member.git_sha, "HEAD", "--", "*.md"],
        capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return sorted(p[:-3] for p in r.stdout.splitlines() if p.endswith(".md"))


def workspace_status(ws: "Workspace") -> dict:
    from okfy.crosswalk import load_rows, parse_ref
    members_out = []
    changed_by_member: dict[str, set[str]] = {}
    for m in ws.members:
        head = _bundle_sha(m.path)
        changed = set(_changed_concepts(m))
        changed_by_member[m.name] = changed
        members_out.append({"name": m.name, "role": m.role,
                            "pinned": m.git_sha, "head": head,
                            "fresh": head == m.git_sha and not changed,
                            "changed_concepts": sorted(changed)})
    stale = []
    for r in load_rows(ws):
        for ref in (r.src, r.dst):
            mname, cid = parse_ref(ref)
            if cid in changed_by_member.get(mname, set()):
                stale.append(r.__dict__)
                break
    return {"members": members_out, "stale_rows": stale}
