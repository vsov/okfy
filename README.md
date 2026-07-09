# OKFy

Tooling that eases context assembly for models. When a full knowledge base is
too large for any context window — or actively harmful, diluting the model's
attention with task-irrelevant material — OKFy extracts knowledge from corpora
(codebases, docs, research collections) into purpose-shaped **OKF Bundles** and
gives models precise access to just the slice a task needs. An OKF Bundle is a
self-contained directory of Markdown concepts with YAML frontmatter, shaped by
a declared **Purpose** and consumable by any agent — even one without OKFy
installed: raw file traversal works, the CLI makes it fast, MCP makes it a tool
surface.

The pipeline is split by responsibility. Everything deterministic — validation,
indexing, BM25 retrieval, lexicon-driven query expansion, segmentation, eval
records, write gates, packaging — lives in an agent-neutral Python core
(`core/`, PyYAML is its only dependency, zero LLM calls). The LLM-driven work —
the Purpose Interview, schema design, blind parallel extraction, consolidation,
and the judgment inside a lexicon or an eval verdict — is an agent workflow
driven by Claude Code plugin commands that call that core CLI. The core is the
machine; the agents supply judgment on top of it. Retrieval is deliberately
**vector-free**: BM25 plus a reviewed, machine-readable lexicon closes the
cross-language gap deterministically, so the same query returns the same
answer, every run, auditable.

**Extraction quality is not self-certified.** OKFy makes no claim that a bundle
is good because the model that built it said so. Acceptance is a replayable
artifact: `okfy eval run` replays the bundle's own test queries
deterministically, an LLM-judge may *propose* verdicts, but only an **owner**
verdict counts toward release — a bundle grading its own output is a closed
loop this design refuses to trust. The verdicts, the query-expansion contract,
and every "this concept is no longer current" flag are recorded inside the
bundle, not in a chat transcript.

## Install

**Core CLI** (Python 3.11+, [uv](https://docs.astral.sh/uv/)):

```bash
git clone https://github.com/vsov/okfy && cd okfy
uv tool install ./core        # installs the `okfy` command
```

**Claude Code plugin** (adds `/okfy:new`, `/okfy:extract`, `/okfy:eval` and friends):

```
/plugin marketplace add vsov/okfy
/plugin install okfy@okfy
```

The plugin drives the interviews and extraction; it shells out to the `okfy`
CLI for every deterministic step, so the core must be installed first.

## Quickstart

```
/okfy:new    <corpus-path> <bundle-path>   # Purpose Interview → Survey → Extraction Plan
#   ↳ review and approve the plan  (the single mandatory checkpoint)
/okfy:extract <bundle-path>                # blind parallel Workers → Consolidate → Validate → package
/okfy:eval    <bundle-path>                # replay your test queries; you judge the verdicts
```

`/okfy:new` interviews you about what the Bundle is *for*, surveys the corpus,
designs a schema from a Purpose Archetype (decision-support for prose corpora,
codebase-map for changing code, api-reference for using an API), and writes an
Extraction Plan you approve. `/okfy:extract`
runs blind parallel extraction, consolidates drafts into final concepts,
validates against the OKF spec with strict source checking, and packages the
Bundle (index, README, `AGENTS.md`, silent git init, structured commits, an
extraction ledger). `/okfy:eval` replays your ten test queries and records
your verdicts — the bundle stays *provisional* until you have judged every one.

The [User Guide](docs/guide/GUIDE.md) walks both tracks end to end — §6 is the
step-by-step procedure for OKFying a text corpus and a codebase, §11 explains
how bundle quality is verified and why the owner, not the model, signs off.

## CLI reference

The core CLI is agent-neutral and usable directly. Run `okfy <command> --help`
for full options.

| Command | What it does |
| --- | --- |
| `okfy init <bundle> --corpus <path> [--language en]` | Create an empty Bundle skeleton and init its git repo. |
| `okfy survey <corpus>` | Cheap reconnaissance pass over a corpus → JSON corpus map (files, sizes, samples). Git corpora walk `git ls-files` (`.gitignore` respected); vendor/build/lockfile/binary noise is excluded by default — and everything skipped is reported, never silently dropped. |
| `okfy segment <bundle> [--budget N] [--include ...] [--exclude ...]` | Cut the corpus into deterministic per-Worker segments; files over budget are chunked at blank-line boundaries into `{path, lines}` slices. |
| `okfy segment-status <bundle> <segment_id> <status>` | Record a segment's extraction status in the pipeline state. |
| `okfy cluster <bundle>` | Pre-cluster draft concepts to guide consolidation. |
| `okfy validate <bundle> [--all] [--no-archetype] [--quiet] [--strict-sources]` | Validate concepts: OKF spec conformance + bundle integrity. Source paths resolve against the corpus snapshot — broken ones warn (`W_BAD_SOURCE`); `--strict-sources` escalates to errors, the bar new extractions are held to. |
| `okfy index <bundle>` | Build the BM25 + link index into `.okfy-cache/`. |
| `okfy query <bundle> <text> [--type T] [--tag T] [-n N] [--include-meta] [--no-expand] [--no-stale]` | Lexical BM25 search with frontmatter filters. Lexicon query expansion is on by default (`--no-expand` opts out); stale concepts stay visible but marked (`--no-stale` drops them). |
| `okfy show <bundle> <concept_id>` | Print a concept's full content. |
| `okfy links <bundle> <concept_id>` | Show a concept's inbound and outbound links. |
| `okfy stale <bundle> <concept_id> (--reason R \| --clear)` | Owner-only: flag a concept as "do not trust as current" (or clear it). Never set automatically — staleness is a reviewed decision. |
| `okfy eval run <bundle> [-n N]` | Replay the bundle's `purpose.md` test queries (expansion → top hits) into an append-only Eval Run in `meta/eval.json`. |
| `okfy eval verdict <bundle> <run\|latest> <q-idx> <pass\|fail\|partial> (--llm \| --owner) [--note …]` | Record a Verdict on one query. `--llm` proposes (stays provisional); `--owner` disposes (the only kind release acceptance counts). |
| `okfy eval status <bundle> [run\|latest]` | Effective verdict per query and totals; a bundle stays *provisional* until every query has an owner verdict. |
| `okfy ledger add\|list <bundle> …` | Append/read the Extraction Ledger (`meta/ledger.jsonl`) — segment-level provenance of Worker and Consolidation steps. |
| `okfy propose <bundle> …` | Agent channel: file a concept change into `proposals/` — never into the live map. |
| `okfy review list\|accept\|reject <bundle> …` | Owner gate: review proposals; accept validates before merging. |
| `okfy refine <bundle> <concept_id>` | Owner channel: direct edit of a live concept, validated and committed. |
| `okfy diff <bundle>` / `okfy snapshot <bundle>` | Corpus drift report (`affected` / `uncovered_new` / `stale_candidates`) / re-stamp the snapshot after updating. |
| `okfy repair-links <bundle>` | Deterministically repair dangling concept links after renames and merges. |
| `okfy workspace init\|status\|export …` / `okfy link-candidates …` | Federate several bundles: workspace lifecycle, crosswalk candidates, one-way export fusion. |
| `okfy sample <bundle> [--fraction F] [--minimum N]` | Sample a deterministic subset of concepts (e.g. for review). |
| `okfy package <bundle>` | Generate `index.md`, `README.md`, `AGENTS.md`, log, and pre-commit hook. |
| `okfy log <bundle> <message>` | Append a structured entry to the Bundle's `log.md`. |

## MCP

Serve a Bundle (or a federation Workspace) to any MCP client — Claude Code,
Claude Desktop, Cursor — over stdio. The adapter is an isolated package: the
MCP SDK lives there, the core stays PyYAML-only.

```bash
uv tool install ./adapters/mcp                              # installs `okfy-mcp`
okfy-mcp config ~/bundles/<b> --client claude-code          # prints a config snippet
```

Paste the printed block into your project's `.mcp.json`, restart the client, and
five tools appear: `okfy_query` / `okfy_show` / `okfy_links` / `okfy_overview`
(read) and `okfy_propose` (writes only to `proposals/`, review gate intact).
`okfy_query` runs the same lexicon expansion as the CLI (`expand`, `include_stale`
default true); `okfy_show` takes a `section` heading and a `max_chars` cap, and
`okfy_overview` a `max_items` / `max_chars` cap — every capped response carries a
`truncated` marker so an agent bounds its own context. OKFy prints the snippet but
never edits your client config. Point `config` at a Workspace path to federate
several bundles. Details: [adapters/mcp/README.md](adapters/mcp/README.md).

## Bundles are private by default

Bundles produced by OKFy are non-public artifacts and **cannot be created inside
this repo** — the core refuses to place a Bundle under the OKFy source tree,
enforced by the CLI, not by convention. A Bundle lives in its own git repo (or,
with `--embed`, at `.okf/` inside a git corpus). Sharing a Bundle is always an
explicit user act: its repo, its access control.

## Documentation

- [User Guide](docs/guide/GUIDE.md) — knowledge engineering for working
  engineers: the OKF format primer, how OKFy thinks (Purpose → Plan → Bundle),
  the end-to-end walkthrough for text corpora and codebases, keeping bundles
  fresh, federation, the refinement loop, verifying quality, and MCP.
- [MCP adapter](adapters/mcp/README.md) — serving bundles to MCP clients.

## License

[Apache-2.0](LICENSE)
