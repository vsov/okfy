"""One-way Export Fusion (ADR-0010): freeze a workspace into a single
standalone bundle. No dedup, no update promise — re-export instead."""
import datetime
import re
import shutil
from pathlib import Path

from okfy import frontmatter
from okfy.bundle import CACHE_DIR, Bundle
from okfy.crosswalk import load_rows, parse_ref
from okfy.federate import personal_scope_ids, ref_in_scope
from okfy.gitenv import run_git
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
    """Fuse every member into one standalone bundle. A `personal` member is
    the one member whose content is NOT fully owned by this workspace's
    project — ADR-0015 fails closed at query time (`federated_query` narrows
    the search pool to `applies_to`-matching concepts), and an export that
    freezes the whole bundle regardless would silently contradict that: the
    fused bundle has no workspace left to scope it afterward (finding 42).

    So export applies the SAME predicate (`personal_scope_ids`,
    `ref_in_scope` — the one federate.py builds per query) at export time:
    only in-scope personal concepts are copied, and any crosswalk row
    touching an excluded one is dropped too, from both the injected
    see-also/constraints sections AND the exported links/*.md row data.
    Kept/excluded counts land in meta/corpus.md and log.md — nothing is
    dropped silently."""
    out = Path(out)
    assert_safe_bundle_path(out)
    if out.exists():
        raise FileExistsError(f"export target exists: {out}")
    out.mkdir(parents=True)

    scope_ids = personal_scope_ids(ws, ws.project_key())
    personal_scope_report: dict[str, dict] = {}

    for m in ws.members:
        dst = out / m.name
        dst.mkdir(parents=True)
        # Allowlist, not subtraction (audit F01): copy exactly the member's
        # ordinary concept files — what Bundle(m.path).concepts() yields,
        # which already excludes proposals/, drafts/, index/, protocols/,
        # index.md, log.md and the generated root docs (README.md, AGENTS.md,
        # CLAUDE.md) — plus the member's own meta/ tree. Nothing else is ever
        # copied, so a pending proposal or an out-of-scope draft is never a
        # candidate for the export and cannot ride along the way a post-hoc
        # subtraction over a wholesale copytree let it (a nested member path
        # defeats bundle.py's root-only reserved-dir/file checks, so that
        # subtraction could never see what it needed to remove).
        in_scope = scope_ids.get(m.name, set())
        excluded: list[str] = []
        total = 0
        copied: list[Path] = []
        for c in Bundle(m.path).concepts():
            if m.role == "personal" and not c.id.startswith("meta/"):
                # structural meta is never scoped (ADR-0015 — applies_to
                # narrows CONTENT, not the bundle's own purpose/plan/snapshot)
                total += 1
                if c.id not in in_scope:
                    excluded.append(c.id)
                    continue
            dst_file = dst / f"{c.id}.md"
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(c.path, dst_file)
            copied.append(dst_file)
        if m.role == "personal":
            personal_scope_report[m.name] = {
                "project_key": ws.project_key(), "kept": total - len(excluded),
                "total": total, "excluded": sorted(excluded)}
        for p in copied:
            try:
                meta, body = frontmatter.parse(p.read_text(encoding="utf-8"))
            except frontmatter.FrontmatterError:
                continue
            new_body = _prefix_links(body, m.name)
            if new_body != body:
                p.write_text(frontmatter.serialize(meta, new_body), encoding="utf-8")

    links_out = out / "links"
    n_rows_dropped = 0
    if (ws.root / "links").is_dir():
        shutil.copytree(ws.root / "links", links_out)
        for p in sorted(links_out.glob("*.md")):
            meta, body = frontmatter.parse(p.read_text(encoding="utf-8"))
            rows = meta.get("rows", [])
            kept_rows = [d for d in rows if ref_in_scope(scope_ids, d["src"])
                        and ref_in_scope(scope_ids, d["dst"])]
            if len(kept_rows) != len(rows):
                n_rows_dropped += len(rows) - len(kept_rows)
                meta = dict(meta)
                meta["rows"] = kept_rows
                p.write_text(frontmatter.serialize(meta, body), encoding="utf-8")

    def fused_path(ref: str) -> tuple[Path, str]:
        member, cid = parse_ref(ref)
        return out / member / f"{cid}.md", f"/{member}/{cid}.md"

    kept_rows_for_sections = [r for r in load_rows(ws)
                              if ref_in_scope(scope_ids, r.src)
                              and ref_in_scope(scope_ids, r.dst)]
    for r in kept_rows_for_sections:
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
    corpus_meta = {"type": "CorpusSnapshot", "corpus": str(ws.root),
                   "extracted_at": today, "exported": True,
                   "members": [{"name": m.name, "path": str(m.path),
                               "git_sha": m.git_sha} for m in ws.members]}
    if personal_scope_report:
        # additive key (ADR-0015 / finding 42): how many personal concepts
        # per member were in scope vs excluded from this export, and their
        # ids — never a silent drop.
        corpus_meta["personal_scope"] = personal_scope_report
    (meta_dir / "corpus.md").write_text(frontmatter.serialize(
        corpus_meta, f"Export fusion of workspace {ws.root} on {today}.\n"),
        encoding="utf-8")
    (out / ".gitignore").write_text(f"{CACHE_DIR}/\n", encoding="utf-8")
    log_line = f"- export: fused {len(ws.members)} members"
    if personal_scope_report:
        parts = ", ".join(
            f"{name}: {r['kept']}/{r['total']} personal concepts in scope"
            for name, r in sorted(personal_scope_report.items()))
        log_line += f" ({parts}"
        if n_rows_dropped:
            log_line += f"; {n_rows_dropped} crosswalk row(s) excluded"
        log_line += ")"
    (out / "log.md").write_text(f"# Log\n\n## {today}\n\n{log_line}\n",
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
    run_git(out, "init", "-q", check=True)
    run_git(out, "add", "-A", check=True)
    run_git(out, "commit", "-q", "-m", "export: fused workspace", check=True)
    return out.resolve()
