"""One-way Export Fusion (ADR-0010): freeze a workspace into a single
standalone bundle. No dedup, no update promise — re-export instead."""
import datetime
import re
import shutil
import subprocess
from pathlib import Path

from okfy import frontmatter
from okfy.bundle import CACHE_DIR, Bundle
from okfy.crosswalk import load_rows, parse_ref
from okfy.guard import assert_safe_bundle_path
from okfy.workspace import Workspace

ABS_LINK_RE = re.compile(r"(\]\()(/[^)#\s]+\.md)([^)]*\))")


def _prefix_links(body: str, member: str) -> str:
    return ABS_LINK_RE.sub(lambda m: f"{m.group(1)}/{member}{m.group(2)}{m.group(3)}",
                           body)


def _append_section(path: Path, heading: str, line: str) -> None:
    meta, body = frontmatter.parse(path.read_text(encoding="utf-8"))
    if heading in body:
        body = body.replace(heading, f"{heading}\n{line}", 1)
    else:
        body = body.rstrip("\n") + f"\n\n{heading}\n{line}\n"
    path.write_text(frontmatter.serialize(meta, body), encoding="utf-8")


def export_workspace(ws: Workspace, out: Path) -> Path:
    out = Path(out)
    assert_safe_bundle_path(out)
    if out.exists():
        raise FileExistsError(f"export target exists: {out}")
    out.mkdir(parents=True)

    for m in ws.members:
        dst = out / m.name
        shutil.copytree(m.path, dst,
                        ignore=shutil.ignore_patterns(".git", CACHE_DIR))
        for p in dst.rglob("*.md"):
            if p.name in {"index.md", "log.md"}:
                continue
            try:
                meta, body = frontmatter.parse(p.read_text(encoding="utf-8"))
            except frontmatter.FrontmatterError:
                continue
            new_body = _prefix_links(body, m.name)
            if new_body != body:
                p.write_text(frontmatter.serialize(meta, new_body), encoding="utf-8")

    links_out = out / "links"
    if (ws.root / "links").is_dir():
        shutil.copytree(ws.root / "links", links_out)

    def fused_path(ref: str) -> tuple[Path, str]:
        member, cid = parse_ref(ref)
        return out / member / f"{cid}.md", f"/{member}/{cid}.md"

    for r in load_rows(ws):
        if r.status != "accepted":
            continue
        if r.rel == "same-as":
            for here, there in ((r.src, r.dst), (r.dst, r.src)):
                hp, _ = fused_path(here)
                _, tref = fused_path(there)
                if hp.is_file():
                    _append_section(hp, "## See also", f"- [{there}]({tref})")
        elif r.rel == "constrains":
            kp, _ = fused_path(r.dst)
            _, cref = fused_path(r.src)
            if kp.is_file():
                _append_section(kp, "## Constraints (cross-bundle)",
                                f"- [{r.src}]({cref})")

    meta_dir = out / "meta"
    meta_dir.mkdir(exist_ok=True)
    today = datetime.date.today().isoformat()
    (meta_dir / "purpose.md").write_text(frontmatter.serialize(
        {"type": "Purpose", "title": ws.meta.get("title", "Exported fusion"),
         "language": ws.meta.get("language", "en"), "write_policy": "proposals",
         "test_queries": ws.meta.get("test_queries") or ["(from workspace)"]},
        "One-way export fusion of a federation workspace. "
        "Do not update in place — re-export from the workspace.\n"),
        encoding="utf-8")
    (meta_dir / "corpus.md").write_text(frontmatter.serialize(
        {"type": "CorpusSnapshot", "corpus": str(ws.root), "extracted_at": today,
         "exported": True,
         "members": [{"name": m.name, "path": str(m.path), "git_sha": m.git_sha}
                     for m in ws.members]},
        f"Export fusion of workspace {ws.root} on {today}.\n"), encoding="utf-8")
    (out / ".gitignore").write_text(f"{CACHE_DIR}/\n", encoding="utf-8")
    (out / "log.md").write_text(
        f"# Log\n\n## {today}\n\n- export: fused {len(ws.members)} members\n",
        encoding="utf-8")

    from okfy.package import render_index
    b = Bundle(out)
    (out / "index.md").write_text(render_index(b), encoding="utf-8")
    (out / "README.md").write_text(
        f"# {ws.meta.get('title', 'Exported fusion')} (exported fusion)\n\n"
        "Frozen one-way export of a federation workspace. `okfy diff/update/"
        "snapshot` refuse on this bundle — re-export from the workspace for a "
        "fresh copy.\n", encoding="utf-8")
    src_agents = ws.root / "AGENTS.md"
    if src_agents.is_file():
        (out / "AGENTS.md").write_text(src_agents.read_text(encoding="utf-8"),
                                       encoding="utf-8")
        (out / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(out), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(out), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(out), "commit", "-q", "-m",
                    "export: fused workspace"], check=True)
    return out.resolve()
