import datetime
import stat
from pathlib import Path
from string import Template

from okfy.archetype import Archetype
from okfy.bundle import Bundle

PRECOMMIT = """#!/bin/sh
# okfy pre-commit: write-policy gate (ADR-0007) + Spec §9 conformance.
# Sanctioned mutators (okfy review accept / okfy refine) commit --no-verify.
POLICY_LINES=$(grep -c '^write_policy:' meta/purpose.md 2>/dev/null || echo 0)
if [ "$POLICY_LINES" != "1" ]; then
  echo "meta/purpose.md declares write_policy $POLICY_LINES times — expected 1." >&2
  echo "YAML keeps the last, this hook reads the first: two readers, two" >&2
  echo "answers. Fix the frontmatter." >&2
  exit 1
fi
POLICY=$(sed -n 's/^write_policy:[[:space:]]*//p' meta/purpose.md 2>/dev/null | head -1 | tr -d '\\r"'"'"' ')
# Fail-closed on anything that is not one of the two policies. This used to be a
# bare `= proposals` test with no else-branch, so `proposal`, `PROPOSALS` or a
# missing value left the gate inert while the bundle still read as gated.
case "$POLICY" in
  direct)
    ;;                        # the owner opted into direct edits
  proposals)
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
    ;;
  *)
    echo "write_policy in meta/purpose.md is '$POLICY', not proposals|direct —" >&2
    echo "refusing the commit rather than guessing which gate you meant." >&2
    echo "Deliberate bypass: git commit --no-verify" >&2
    exit 1
    ;;
esac
if command -v okfy >/dev/null 2>&1; then
  okfy validate . --quiet || { echo "okfy validate failed — fix or --no-verify"; exit 1; }
fi
"""


# OKF v0.2 §8: a root index may declare its version, and nothing else.
INDEX_HEAD = '---\nokf_version: "0.2"\n---\n'


DEMOTED_HEADING = "## Not reached by the acceptance suite"


def render_index(bundle: Bundle, demote=frozenset()) -> str:
    """`demote`: ids listed last, under their own heading — still linked, still
    searched by `okfy query`; only their place in the resident index moves."""
    by_type: dict[str, list] = {}
    demoted = []
    for c in bundle.concepts():
        if c.id.startswith("meta/"):
            continue
        if c.id in demote:
            demoted.append(c)
            continue
        by_type.setdefault(str(c.meta.get("type")), []).append(c)
    purpose = bundle.purpose()
    lines = [f"# {purpose.get('title', 'Knowledge Bundle')}", ""]

    def entries(cs):
        return [f"- [{c.meta.get('title', c.id)}]({c.id}.md) — "
                f"{str(c.meta.get('description', '')).strip()}"
                for c in sorted(cs, key=lambda x: x.id)] + [""]
    for t in sorted(by_type):
        lines += [f"## {t}", ""] + entries(by_type[t])
    if demoted:
        lines += [DEMOTED_HEADING, "",
                  "No recorded eval run retrieved these. They are listed last, "
                  "not removed: `okfy query` searches them as before.", ""]
        lines += entries(demoted)
    return INDEX_HEAD + "\n".join(lines)


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


# The memory lifecycle, appended to every bundle's AGENTS.md. Short on purpose:
# AGENTS.md is resident context billed on every turn, and this block measured
# 179 tokens (heuristic) — 0.8-3.4% of the large real bundles' resident core,
# but 21.7% of the smallest. The full discipline lives in the okf-consumer skill.
MEMORY_BLOCK = """
<!-- okfy:memory -->
## Memory: before and after a task

- **Before:** `okfy query` the task's own terms. For each hit you rely on, check
  `stale` (owner: do not trust as current) and `review_due` (a passed reminder:
  verify before relying — not the same as stale).
- **After:** propose only what an agent with a blank context would need — a
  confirmed root cause, a decision with its reasons, a constraint, a
  reproducible workaround. Never conversation, scratch work or guesses.
- Search first; add to an existing concept with `--extends <id>`.
- `okfy propose <bundle> --as <actor> --evidence <kind>=<ref> …` — kinds:
  test-run, owner-decision, external-source, agent-inference.
- Something is remembered only when propose returned a proposal id; it is
  accepted only after `okfy review accept`. Refusal codes name their way out.
<!-- /okfy:memory -->
"""


def render_agents_md(bundle: Bundle, archetype: Archetype) -> str:
    """The consumption protocol an agent reads instead of the CLI.

    Types and layout come from the PLAN when it declares them, because the plan
    is what the bundle was actually built to. Rendering the archetype's canonical
    list meant an adapted bundle shipped an AGENTS.md that omitted its own custom
    types and advertised archetype types it does not contain — a consumption
    contract describing a different bundle."""
    p = bundle.purpose()
    tmpl = Template((archetype.root / archetype.consumption_protocol).read_text(encoding="utf-8"))
    plan = bundle.plan()
    declared = (plan.meta.get("types") if plan else None)
    types = ([str(t) for t in declared] if isinstance(declared, (dict, list))
             else list(archetype.canonical_types))
    layout = dict(archetype.layout)
    plan_layout = (plan.meta.get("layout") if plan else None)
    if isinstance(plan_layout, dict):
        layout.update({str(k): str(v) for k, v in plan_layout.items()})
    types_rows = "\n".join(f"- **{t}** — files under `{layout.get(t, './')}`"
                           for t in types)
    return tmpl.substitute(
        purpose_title=p.get("title", ""), language=p.get("language", "en"),
        write_policy=p.get("write_policy", "proposals"),
        types_table=types_rows).rstrip("\n") + "\n" + MEMORY_BLOCK


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


def package(bundle: Bundle, archetype: Archetype, demote_unretrieved: bool = False) -> None:
    import json

    from okfy.validate import package_fingerprint
    demote = frozenset()
    if demote_unretrieved:   # opt-in: the signal is bounded by the query set
        from okfy.budget import usage_report
        demote = frozenset(usage_report(bundle)["zero_hit_ids"])
    (bundle.root / "index.md").write_text(render_index(bundle, demote), encoding="utf-8")
    (bundle.root / "README.md").write_text(render_readme(bundle, archetype), encoding="utf-8")
    (bundle.root / "AGENTS.md").write_text(render_agents_md(bundle, archetype), encoding="utf-8")
    (bundle.root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    # fingerprint of the concept set the generated docs describe — any later
    # mutation makes the package provably stale (validate --strict-package)
    # Refresh the retrieval cache here too. Packaging is the point at which the
    # bundle's contents are declared final, and leaving the cache behind meant
    # `refine → package → eval run → accept` recorded evidence gathered from a
    # pre-edit index. `okfy index` and this are the only writers.
    from okfy.index import build_index, retrieval_digest, save_index
    from okfy.memory import accepted_since
    idx = build_index(bundle)
    # The manifest is tracked, so the digests it records are the reviewable claim
    # about what build_index produces — which is what makes the gitignored cache
    # trustable at all. Written before save_index so the cache lands against a
    # manifest that already describes it.
    (bundle.root / "meta" / "package.json").write_text(json.dumps(
        {"schema": "okfy-package@1",
         "fingerprint": package_fingerprint(bundle),
         "index_content_fingerprint": idx["content_fingerprint"],
         "retrieval_digest": retrieval_digest(idx),
         "memory_accepted": accepted_since(bundle, None)}) + "\n", encoding="utf-8")
    save_index(bundle, idx)
    install_precommit(bundle)
    append_log(bundle, "package: regenerated index.md, README.md, AGENTS.md, "
                       "retrieval index")


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
