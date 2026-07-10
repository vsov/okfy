import datetime
import stat
from pathlib import Path
from string import Template

from okfy.archetype import Archetype
from okfy.bundle import Bundle

PRECOMMIT = """#!/bin/sh
# okfy pre-commit: write-policy gate (ADR-0007) + Spec §9 conformance.
# Sanctioned mutators (okfy review accept / okfy refine) commit --no-verify.
POLICY=$(sed -n 's/^write_policy:[[:space:]]*//p' meta/purpose.md 2>/dev/null | head -1 | tr -d '\\r')
if [ "$POLICY" = "proposals" ]; then
  BLOCKED=$(git diff --cached --name-only --diff-filter=ACMRD -- '*.md' \\
    | grep -vE '^(proposals|drafts)/' \\
    | grep -vE '^(index|log|README|AGENTS|CLAUDE)\\.md$')
  if [ -n "$BLOCKED" ]; then
    echo "write_policy=proposals: direct concept edits are refused:" >&2
    echo "$BLOCKED" >&2
    echo "Agents: okfy propose. Owner: okfy refine / okfy review accept." >&2
    echo "Deliberate bypass: git commit --no-verify" >&2
    exit 1
  fi
fi
if command -v okfy >/dev/null 2>&1; then
  okfy validate . --quiet || { echo "okfy validate failed — fix or --no-verify"; exit 1; }
fi
"""


def render_index(bundle: Bundle) -> str:
    by_type: dict[str, list] = {}
    for c in bundle.concepts():
        if c.id.startswith("meta/"):
            continue
        by_type.setdefault(str(c.meta.get("type")), []).append(c)
    purpose = bundle.purpose()
    lines = [f"# {purpose.get('title', 'Knowledge Bundle')}", ""]
    for t in sorted(by_type):
        lines += [f"## {t}", ""]
        for c in sorted(by_type[t], key=lambda x: x.id):
            desc = str(c.meta.get("description", "")).strip()
            lines.append(f"- [{c.meta.get('title', c.id)}]({c.id}.md) — {desc}")
        lines.append("")
    return "\n".join(lines)


def render_readme(bundle: Bundle, archetype: Archetype) -> str:
    p = bundle.purpose()
    corpus = bundle.get("meta/corpus")
    counts: dict[str, int] = {}
    for c in bundle.concepts():
        if not c.id.startswith("meta/"):
            t = str(c.meta.get("type"))
            counts[t] = counts.get(t, 0) + 1
    rows = "\n".join(f"| {t} | {n} |" for t, n in sorted(counts.items()))
    return f"""# {p.get('title', 'Knowledge Bundle')}

An [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog) knowledge bundle,
built with OKFy. Archetype: {archetype.name} v{archetype.version}.

**Purpose:** {p.get('title', '')} — see [meta/purpose.md](meta/purpose.md).
**Corpus:** `{corpus.meta.get('corpus') if corpus else 'unknown'}`
(snapshot {corpus.meta.get('extracted_at') if corpus else '?'}).
**Language:** {p.get('language', 'en')}.

| Type | Concepts |
|---|---|
{rows}

Humans: start at [index.md](index.md). Agents: read [AGENTS.md](AGENTS.md).
"""


def render_agents_md(bundle: Bundle, archetype: Archetype) -> str:
    p = bundle.purpose()
    tmpl = Template((archetype.root / archetype.consumption_protocol).read_text(encoding="utf-8"))
    types_rows = "\n".join(f"- **{t}** — files under `{archetype.layout.get(t, './')}`"
                           for t in archetype.canonical_types)
    return tmpl.substitute(
        purpose_title=p.get("title", ""), language=p.get("language", "en"),
        write_policy=p.get("write_policy", "proposals"), types_table=types_rows)


def append_log(bundle: Bundle, message: str) -> None:
    log = bundle.root / "log.md"
    today = datetime.date.today().isoformat()
    text = log.read_text(encoding="utf-8") if log.is_file() else "# Log\n"
    marker = f"## {today}"
    if marker in text:
        text = text.replace(marker, f"{marker}\n\n- {message}", 1)
    else:
        text = text.rstrip("\n") + f"\n\n{marker}\n\n- {message}\n"
    log.write_text(text, encoding="utf-8")


def install_precommit(bundle: Bundle) -> None:
    hooks = bundle.root / ".git" / "hooks"
    if not hooks.parent.is_dir():
        return  # not a git repo (tests, exotic setups) — silently skip
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(PRECOMMIT, encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def package(bundle: Bundle, archetype: Archetype) -> None:
    import json

    from okfy.validate import package_fingerprint
    (bundle.root / "index.md").write_text(render_index(bundle), encoding="utf-8")
    (bundle.root / "README.md").write_text(render_readme(bundle, archetype), encoding="utf-8")
    (bundle.root / "AGENTS.md").write_text(render_agents_md(bundle, archetype), encoding="utf-8")
    (bundle.root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    # fingerprint of the concept set the generated docs describe — any later
    # mutation makes the package provably stale (validate --strict-package)
    (bundle.root / "meta" / "package.json").write_text(json.dumps(
        {"schema": "okfy-package@1",
         "fingerprint": package_fingerprint(bundle)}) + "\n", encoding="utf-8")
    install_precommit(bundle)
    append_log(bundle, "package: regenerated index.md, README.md, AGENTS.md")


def package_workspace(ws) -> None:
    """README + AGENTS.md + CLAUDE.md for a Workspace (self-teaching, ADR-0009)."""
    from importlib import resources
    from string import Template
    tmpl_path = Path(str(resources.files("okfy"))) / "templates" / "workspace-agents.tmpl"
    rows = "\n".join(
        f"- **{m.name}** (role: {m.role}) — `{m.path}`" for m in ws.members)
    queries = ws.meta.get("test_queries") or []
    qnote = ("\n## Acceptance queries\n\n" +
             "\n".join(f"- {q}" for q in queries) + "\n") if queries else ""
    agents = Template(tmpl_path.read_text(encoding="utf-8")).substitute(
        title=ws.meta.get("title", "Workspace"), members_table=rows,
        queries_note=qnote)
    (ws.root / "AGENTS.md").write_text(agents, encoding="utf-8")
    (ws.root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (ws.root / "README.md").write_text(
        f"# {ws.meta.get('title', 'Workspace')}\n\n"
        f"An OKFy federation workspace: no knowledge of its own, only the\n"
        f"manifest, roles, and reviewed crosswalks over these member bundles:\n\n"
        f"{rows}\n\nAgents: read [AGENTS.md](AGENTS.md). "
        f"Humans: `okfy query {ws.root} \"...\"`.\n", encoding="utf-8")
    append_log_ws = ws.root / "log.md"
    if append_log_ws.is_file():
        b = Bundle.__new__(Bundle)          # append_log needs only .root
        b.root = ws.root
        append_log(b, "package: regenerated README.md, AGENTS.md")
