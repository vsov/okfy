"""Workspace: federation glue over untouched member Bundles (ADR-0010).
Holds no knowledge — only the manifest, roles, crosswalks, and test queries."""
import datetime
import re
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
    probe = subprocess.run(
        ["git", "-C", str(member.path), "cat-file", "-e",
         f"{member.git_sha}^{{commit}}"],
        capture_output=True, text=True)
    if probe.returncode != 0:
        st = subprocess.run(["git", "-C", str(member.path), "rev-parse", "HEAD"],
                            capture_output=True, text=True)
        return ("git-error" if st.returncode != 0 else "unreachable-pin", [])
    r = subprocess.run(
        ["git", "-C", str(member.path), "diff", "--name-only",
         member.git_sha, "HEAD", "--", "*.md"],
        capture_output=True, text=True)
    if r.returncode != 0:
        return ("git-error", [])
    return ("ok", sorted(p[:-3] for p in r.stdout.splitlines()
                         if p.endswith(".md")))


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
