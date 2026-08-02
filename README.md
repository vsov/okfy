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
codebase-map for changing code, api-reference for using an API,
research-synthesis for the state of knowledge on a research question,
regulatory-reference for a frozen slice of primary legal sources), and writes an
Extraction Plan you approve. `/okfy:extract`
runs blind parallel extraction, consolidates drafts into final concepts,
validates against the OKF spec with strict source checking, and packages the
Bundle (index, README, `AGENTS.md`, silent git init, structured commits, an
extraction ledger). `/okfy:eval` replays your ten test queries and records
your verdicts — the bundle stays *provisional* until you have judged every one.

The rest of the plugin maintains a bundle you already have: `/okfy:review` and
`/okfy:refine` for the change loop, `/okfy:lexicon` for the retrieval
vocabulary, `/okfy:update` and `/okfy:relink` for corpus drift, `/okfy:workspace`
for federation — plus two review passes that only ever hand you candidates:
`/okfy:challenge` probes a finished bundle for questions it answers confidently
and wrongly, and `/okfy:schism` walks the merges consolidation made and asks
*you* to rule on each one. `/okfy:challenge` has no write authority at all;
`/okfy:schism` writes one ledger row per group and only after you have given it
a verdict. [docs/USAGE-merge-audit.md](docs/USAGE-merge-audit.md) is the
operating manual for both, along with `okfy merge-audit` and the execution
attestation.

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
| `okfy segment <bundle> [--budget N] [--include ...] [--exclude ...]` | Cut the corpus into deterministic per-Worker segments; files over budget are chunked at blank-line boundaries into `{path, lines}` slices; dense files with no blank lines (minified, single-line) fall back to `{path, chars}` character windows. |
| `okfy segment-status <bundle> <segment_id> <status>` | Record a segment's extraction status in the pipeline state. |
| `okfy cluster <bundle>` | Pre-cluster draft concepts to guide consolidation. |
| `okfy validate <bundle> [--all] [--no-archetype] [--quiet] [--strict-sources] [--strict-quality] [--strict-provenance] [--strict-package] [--strict-execution] [--strict-schema]` | Validate concepts: OKF spec conformance + bundle integrity. Source paths resolve against the corpus snapshot — broken ones warn (`W_BAD_SOURCE`); `--strict-sources` escalates to errors, the bar new extractions are held to. Anchors are checked too when the corpus is locally readable: `path#L10-L20` must be a real line range, `guide.md#heading-id` a real heading (`W_BAD_ANCHOR`/`E_BAD_ANCHOR`). `--strict-quality` demands a complete `meta/purpose-fitness.md` artifact — machine-readable verdict `rows:` covering every sampled concept × purpose check, unique, with evidence. `--strict-provenance` cross-checks every job artifact, frozen prompt, and ledger job digest. `--strict-package` demands every concept reachable from `index.md` and the package fingerprint fresh. `--strict-execution` demands every job artifact attest WHO ran it (`model`, `provider`, `sampling`, `harness_version`) — absent warns by default (`W_EXEC_MISSING`) so pre-v0.10 bundles stay green, and a half-filled block is `E_EXEC_FIELD` at every level. This is attestation, not measurement: there is nowhere reliable to read these values from, so the pipeline asks the agent and records its answer verbatim — every field is what the agent reported about itself, with `unknown-to-agent` where it does not know. The core is agent-neutral and never talks to a provider, so a harness that reports the wrong model passes; what it buys is that the claim is recorded, digested, and diffable. `--strict-schema` demands every concept type be a *declared* type — `types` in `meta/extraction-plan.md` when the archetype was adapted, the archetype's `canonical_types` otherwise; without it `type: Strategyy` validated clean, contributed no required fields and demanded no sections, so a typo and a deliberate custom type were indistinguishable. Two closures apply at every level, because both are trust boundaries rather than style: `write_policy` must be exactly `proposals` or `direct` (`E_WRITE_POLICY` — the pre-commit hook compares the literal, so `proposal` or `PROPOSALS` disabled the gate without disabling the claim), and `acceptance` is a closed schema with `min_owner_pass` an integer inside `1..len(test_queries)` (`E_ACCEPTANCE_KEY`/`_TYPE`/`_VALUE`/`_RANGE`/`_SHAPE`/`_INERT` — an unrecognised key is a policy that silently does nothing, a value a gate compares literally is an enum rather than a string, and an escape hatch for a gate that was never declared is itself an error). Lexicon rows are typed too: `maps_to`/`canonical_terms` must be lists, since expansion iterates them and a scalar becomes one hard retrieval pin per character (`W_LEXICON_ROW`, and `okfy query` refuses to serve it) — and every ELEMENT must be a non-empty string, since typing only the container let `canonical_terms: [123]` reach the query and raise. Duplicate frontmatter keys are refused outright: YAML keeps the last and the generated pre-commit hook reads the first, so a duplicated `write_policy` meant two different things to two readers. |
| `okfy release-check <bundle\|workspace>` | Fail-closed release gate — the machine predicate for *accepted*, not just *consistent*. Composes the FULL strict validation (conformance + sources/anchors + quality + provenance + package, `E_REL_VALIDATE` on any error) and then demands what strict flags cannot: a non-empty extraction plan with every segment `done` (`E_REL_SEGMENTS`), provenance completeness (every done worker segment has a job artifact and a ledger row carrying its digest), eval currency (the latest run is owner-complete and its `okfy-retrieval@2` fingerprint still matches the live bundle: the digest of the deterministic live index over non-meta concepts, the NORMALISED lexicon rows, the test queries, and the bytes of `bm25.py`/`index.py`/`lexicon.py`/`query.py`. `tool_version` is deliberately absent — it was too broad, since a CLI help-text patch invalidated every recorded eval, and too narrow, since a ranking change inside one version invalidated none. Runs recorded under the previous scheme stay stale by construction), the acceptance surface itself (`E_REL_EVAL_SURFACE`: ten `test_queries`, none blank, none a duplicate after normalising case and whitespace), and the bundle's own acceptance policy (`acceptance.min_owner_pass` in `meta/purpose.md`, default 8, compared **unclamped** — it used to be `min(policy, query_count)`, so one query and one owner pass satisfied a declared bar of eight and a negative bar accepted a bundle whose only query failed; no unexcused L3 `fail`). The adversarial suite is a second mandatory layer with its own bar (`acceptance.min_adversarial_pass`, default 8) and its own codes (`E_REL_ADVERSARIAL_MISSING`/`_PROVISIONAL`/`_STALE`/`_POLICY`/`_SURFACE`): ten owner passes on the queries a bundle was built for cannot show what it answers confidently and wrongly. Malformed evidence returns a code rather than a traceback (`E_REL_ACCEPTANCE_INVALID`/`E_REL_EVAL_INVALID`): fail-closed by process death is not a contract, and this is the predicate other things call. Pre-job-chain bundles may declare `provenance: legacy` — reported, never silent. **On a workspace path this auto-detects and applies the FEDERATED predicate** (`E_REL_WS_*`): every member must itself be release-accepted, the crosswalk must still describe what an owner reviewed (no drifted or unverifiable member, no stale row), and both federated eval suites must be owner-complete and pinned to `okfy-ws-retrieval@1` — which covers each member's own retrieval fingerprint rather than its git SHA, the roster WITH roles, the workspace lexicon and the accepted crosswalk. Federation used to be accepted on a line in `log.md`. |
| `okfy merge-audit <bundle> [--ref <git-ref>] [--group <id>] [--json] [--quiet]` | Read-only shadow consolidation report: what the pre-merge drafts held and the merged concepts no longer do. Reconstructs merge groups from the ledger's `merge_map`, recovers drafts from the working tree or from git after the consolidate commit, and reports asymmetric loss in four structural kinds — `lost-source`, `enum-collapse` (drafts disagreed on an archetype enum and the merge silently kept one), `lost-link`, `lost-date`. **Not a gate**: exits 0 whether or not it finds anything, and `release-check` is untouched — findings are candidates for owner attention, not proven defects. Five explicit recovery states (`live`/`ok`/`no-merge-map`/`unreachable-ref`/`git-error`); a non-recoverable state populates `unverifiable` and prints `NOT AUDITED` — it never reads as clean. |
| `okfy dissent add\|list\|waive <bundle> …` | Append-only merge adjudication ledger (`meta/dissent.jsonl`): a claim that a merge group hides a real distinction, who held it, its source anchor, and how it resolved. A `split` stays open until an owner waiver or a real split closes it; a later `no-schism` row cannot, and `--overruled-because` is the consolidator's note on keeping the merge, which annotates without resolving. Every row pins an `adjudication_fingerprint` over the merged concept's bytes AND the sorted draft ids, so an edited concept or a newly-added draft returns the group to `stale`. `release-check` consults this **only** when `meta/purpose.md` declares `acceptance.dissent: required` (`E_REL_DISSENT_UNADJUDICATED`/`_OPEN`/`_STALE`/`_UNVERIFIABLE`, escape hatch `acceptance.allow_open_dissent`). Fill it with `/okfy:schism`, which puts every group in front of the owner. It verifies adjudication happened, never that it was rigorous. |
| `okfy index <bundle>` | Build the BM25 + link index into `.okfy-cache/`. The index carries an envelope (`schema`, `source_fingerprint` over every file that enters it including `meta/*`, `content_fingerprint`) and this verb plus `okfy package` are its ONLY writers. Every read verb verifies the envelope and falls back to a fresh in-memory build when the cache is missing, corrupt, foreign-schema stale, or `unmanifested` — the last meaning the payload does not match the digests `meta/package.json` records for it. The envelope alone only proved the cache agreed with itself: an empty payload carrying the correct digest OF THAT EMPTY LIST and the correct live source fingerprint passed every check, so queries answered nothing while the fingerprint hashed a fresh build holding every concept. The tracked manifest is the assertion from outside the cache that closes it. The cache is a speed-up, never evidence: the acceptance contract digests a freshly built index, never this file. |
| `okfy query <bundle> <text> [--type T] [--tag T] [-n N] [--include-meta] [--no-expand] [--no-stale]` | Lexical BM25 search with frontmatter filters. Lexicon query expansion is on by default (`--no-expand` opts out); stale concepts stay visible but marked (`--no-stale` drops them). |
| `okfy show <bundle> <concept_id>` | Print a concept's full content. |
| `okfy links <bundle> <concept_id>` | Show a concept's inbound and outbound links. |
| `okfy stale <bundle> <concept_id> (--reason R \| --clear)` | Owner-only: flag a concept as "do not trust as current" (or clear it). Never set automatically — staleness is a reviewed decision. |
| `okfy eval run <bundle\|workspace> [-n N] [--suite acceptance\|adversarial]` | Replay the bundle's `purpose.md` test queries (expansion → top hits) into an append-only Eval Run in `meta/eval.json`. `N >= 1` is enforced: `-n 0` used to produce ten queries with zero hits each, ten owner verdicts over nothing, and a green release. Each run records how it was invoked — `retrieval_schema`, `suite`, and `query_options` (`n`, `expand`, `include_meta`, `include_stale`) — and `release-check` requires them, because a run that cannot be replayed is not evidence. `--suite adversarial` replays `adversarial_queries` instead — ten questions the bundle is most likely to answer confidently and wrongly, each declaring its expectation up front (`expect: covered` with the concept that proves it, or `expect: not-covered`, plus a `why`). Those runs carry a deterministic `met`/`unmet` per query beside the owner verdict, so a pass over an unmet expectation is visible as the override it is. BOTH suites must be owner-judged for release. On a workspace path the queries come from `meta/workspace.md` and are replayed through the FEDERATED path (per-member expansion → RRF → role grouping → `constrains` auto-pull) into the workspace's own `meta/eval.json`; `eval verdict` and `eval status` are shared unchanged. |
| `okfy eval verdict <bundle> <run\|latest> <q-idx> <pass\|fail\|partial> (--llm \| --owner) [--note …]` | Record a Verdict on one query. `--llm` proposes (stays provisional); `--owner` disposes (the only kind release acceptance counts). |
| `okfy eval status <bundle> [run\|latest]` | Effective verdict per query and totals; a bundle stays *provisional* until every query has an owner verdict. |
| `okfy job <bundle> <segment> --prompt-file <p>` | Freeze a segment's worker contract into `meta/jobs/<segment>.json` — inputs with `lines`/`chars` spans and content hashes, corpus snapshot, archetype — and copy the exact prompt text into the bundle (`meta/prompts/<sha256>.txt`): the contract travels with the bundle, not with the plugin repo. |
| `okfy ledger add\|list <bundle> … [--job <segment>]` | Append/read the Extraction Ledger (`meta/ledger.jsonl`) — segment-level provenance of Worker and Consolidation steps. `--job` takes a segment id; the core computes the digest from the frozen artifact itself — a hand-passed digest proves nothing. |
| `okfy propose <bundle> …` | Agent channel: file a concept change into `proposals/` — never into the live map. |
| `okfy review list\|accept\|reject <bundle> …` | Owner gate: review proposals; accept validates before merging. |
| `okfy refine <bundle> <concept_id>` | Owner channel: direct edit of a live concept, validated and committed. |
| `okfy diff <bundle>` / `okfy snapshot <bundle>` | Corpus drift report (`affected` / `uncovered_new` / `stale_candidates`) / re-stamp the snapshot after updating. |
| `okfy repair-links <bundle>` | Deterministically repair dangling concept links after renames and merges. |
| `okfy workspace init\|status\|export …` / `okfy link-candidates …` | Federate several bundles: workspace lifecycle, crosswalk candidates, one-way export fusion. |
| `okfy sample <bundle> [--fraction F] [--minimum N]` | Risk-oriented deterministic sample for purpose-fitness review: changed sources, stale flags, rare types, weak coverage first, then a seeded stratified fill. Returns the selector version and seed so the sample is replayable. |
| `okfy package <bundle>` | Generate `index.md`, `README.md`, `AGENTS.md`, log, and pre-commit hook; records a fingerprint of the concept set (`meta/package.json`) so later mutations make the package provably stale, and saves a FRESH retrieval index — packaging is where contents are declared final, and leaving the cache behind is how `refine → package → eval run → accept` used to record evidence gathered from a pre-edit index. `AGENTS.md` renders its type table from the plan's `types`/`layout` when declared, so an adapted bundle no longer ships a consumption protocol omitting its own types. |
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
