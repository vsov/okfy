# OKFy — A User Guide

*Turning a corpus into a purpose-shaped, self-teaching knowledge bundle that any agent can consume.*

---

## 1. The context problem

Every answer a language model gives is bounded by what you put in its context window. Not by how smart the model is — by what it can *see* at the moment you ask. This is the quiet ceiling on agent quality, and most teams hit it without noticing.

The instinctive fix is to give the model more: point it at the wiki, paste in the `CLAUDE.md`, dump the whole `docs/` folder, retrieve the top forty chunks. For a small project this works well enough that it feels like the answer. It is not the answer. It is a strategy that gets *worse* as the corpus grows.

Consider a real shape of the problem. You maintain a trading research repository — 593 markdown files: strategy notes, backtest write-ups, market-regime memos, risk post-mortems, half-abandoned ideas, three years of Slack exports someone converted to text. An analyst asks your agent a sharp question: *"When our mean-reversion book underperforms, what regime are we usually in, and which risk factor tends to dominate?"*

Feed the model all 593 files and two things go wrong at once. First, **attention dilution**: the handful of paragraphs that actually answer the question are drowned in tens of thousands of tokens of loosely-related prose. The model's attention is a budget, and you have spent most of it on noise. It hedges, it generalizes, it misses the one 2023 memo that names the regime precisely. Second, **staleness and contradiction**: across three years the corpus disagrees with itself. A strategy was retired, but the retirement note lives in one file and the original thesis in twelve others. The model has no way to know which is current. It averages them and gives you a confident, wrong answer.

Now feed the model a *shaped bundle* instead — thirty concepts, each one atomic and current, each tagged with its type (a strategy, a market-regime, a risk-factor), each cross-linked to the others it depends on, with retired ideas removed and contradictions resolved during the shaping. The same question retrieves four concepts totaling maybe 1,200 tokens: the mean-reversion strategy, the two regimes it fails in, the risk-factor that dominates. The model answers precisely because it can *see* precisely.

The difference is not the model. It is not even the amount of information — the shaped bundle contains strictly *less*. The difference is that someone decided what the knowledge was *for* and shaped it to that purpose. That deciding is the whole game.

## 2. Knowledge engineering, in five minutes

There is a name for that deciding: **knowledge engineering**. It is the discipline of determining what a body of knowledge is *for*, and then structuring it so that purpose is cheap to serve.

The word sounds academic — expert systems, ontologies, the 1980s. But you are already doing it, informally, every day. A `README` is knowledge engineering: you decided a newcomer needs orientation, and you shaped the repo's facts to that purpose, leaving out the parts that don't serve it. A runbook is knowledge engineering: you decided an on-call engineer at 3 a.m. needs a decision path, not a design essay, and you shaped accordingly. An Architecture Decision Record is knowledge engineering: you decided that *why* a choice was made matters as much as *what*, and you gave that a durable form. READMEs, runbooks, ADRs — engineers write these constantly without ever using the phrase.

Naming the discipline changes how you invest in it. Once you can see that a README, a runbook, and an ADR are all the *same move* — purpose first, structure second — you stop treating knowledge as exhaust from the work and start treating it as an artifact you design. You ask the questions a knowledge engineer asks: Who consumes this? What decisions must it support? What can be left out? What must never go stale? Which pieces depend on which?

OKFy is a tool for doing this move deliberately, at corpus scale, with an agent as the consumer. It does not invent knowledge engineering. It refuses to let you skip the part where you decide what the knowledge is for.

## 3. OKF in one page

The output format is an **Open Knowledge Format (OKF) bundle**. The spec is deliberately small, and OKFy tracks the [OKF spec](https://github.com/GoogleCloudPlatform/knowledge-catalog) (v0.1) as its only external contract.

A bundle is nothing exotic. It is:

- **A directory of markdown concepts.** One idea per file. Each file opens with YAML frontmatter carrying at minimum a `type` (`strategy`, `risk-factor`, `glossary-term`, whatever the purpose calls for) and identifying fields. The body is ordinary prose an agent — or a human — can read.
- **An `index.md` built for progressive disclosure.** The agent reads the index first, sees the shape of the whole bundle, and *then* opens only the concepts it needs. It never has to load everything to find anything.
- **A `log.md`.** An append-only record of what the bundle contains and how it was built — provenance you can audit.
- **Git-native.** The bundle *is* a git repository. Every change to knowledge is a commit. History is the audit trail; branches are proposals; diffs are review.

The spec is **permissive** (this is its §9 conformance surface): it mandates the frontmatter contract and a few structural invariants, and otherwise leaves category layout, extra fields, and body conventions to you. That permissiveness is a feature — it means a bundle shaped for trading decisions and a bundle shaped for codebase navigation can both be valid OKF while looking nothing alike. The spec is a floor, not a mold.

Because a bundle is *just markdown and git*, it has no runtime, no database, no service to stand up. It travels as a folder. Any agent that can read files can consume it.

## 4. How OKFy thinks

OKFy is not a converter. A converter takes documents in and emits documents out, one shape for all. OKFy takes a corpus **and a purpose**, and the purpose changes everything downstream. Point OKFy at the same 593 trading files twice — once to shape a decision-support bundle for analysts, once to shape an onboarding bundle for new quants — and you get two genuinely different bundles from identical inputs. Same corpus, different purpose, different knowledge.

The pipeline is a sequence of deliberate stages, each producing an artifact you can inspect:

**Purpose.** Nothing starts until you name what the bundle is for. This is the anchor for every later decision.

**Purpose Interview.** OKFy interrogates the purpose until it is operational. The interview's most important output is a set of **ten test queries** — real questions the finished bundle must answer well. These are not decoration; they are an **acceptance contract**. A bundle that cannot answer its ten queries has failed, regardless of how tidy it looks.

**Extraction Plan.** From the interview, OKFy drafts a plan: which concept types exist, how the corpus segments, what categories the bundle will have. This is a **checkpoint** — you review and correct the plan before any extraction happens, because it is far cheaper to fix the plan than the output.

**Blind parallel extraction.** The corpus is split into segments and handed to independent workers *in parallel*. Crucially, the workers are **blind to each other** — no worker sees another's output. This keeps extraction unbiased and fast: no worker anchors on a peer's framing, and the segments genuinely run at once.

**Consolidation.** Blind parallelism means duplicates and near-duplicates across segment boundaries. Consolidation pre-clusters the draft concepts deterministically, then merges and de-conflicts them into one coherent set — resolving the contradictions that made the raw corpus unusable.

**Four-layer validation.** The bundle is checked at four levels: (1) Spec §9 conformance on every concept; (2) bundle integrity — links resolve, types are declared, no orphans; (3) coverage — does the corpus's substance actually appear; (4) the acceptance contract — can the bundle answer its ten test queries. Only a bundle that clears all four is done.

**Self-teaching packaging.** Finally OKFy writes the bundle's own consumption instructions *into the bundle* (see §5).

The reason to prefer this over generic conversion is exactly the reason from §2: a generic converter has no theory of what the knowledge is for, so it keeps everything and shapes nothing, and you are back to attention dilution with extra steps. Purpose-shaping is what makes the output small enough to be useful.

## 5. What you get

The deliverable is a folder, and the folder is designed to survive on its own.

**A bundle any agent can consume with zero tooling.** Every bundle carries an `AGENTS.md` — a **consumption protocol** written for an agent that has never heard of OKFy and has no OKFy installed. It teaches the consumption discipline in plain traversal terms: read `index.md` first; expand queries through the glossary and lexicon; prefer snippets before full bodies; respect the write policy. A bundle handed to a stranger's agent still teaches that agent how to use it. (Claude Code consumers get a `CLAUDE.md` pointer to the same protocol.)

**Deterministic search when the CLI is present.** If OKFy *is* installed, the bundle gains an "accelerated mode": a self-contained BM25 index (no embeddings, no external service) driving `okfy query`, `okfy show`, `okfy links`, and `okfy sample`. `okfy query` also runs **lexicon query expansion by default** — it bridges the words you type to the concept ids the bundle actually uses, deterministically, before the search runs (§11). Same bundle, faster retrieval — but nothing about correctness depends on the CLI being there.

**Proposals-based refinement.** Consuming agents don't edit the bundle in place. They write to a `proposals/` area; changes to knowledge go through the same review gate as changes to code. The bundle's authority is protected by a write policy the core enforces.

**Git history as audit trail.** Because the bundle is a git repository, you get provenance for free: who changed which concept, when, and why, all the way back to the initial extraction. When a downstream decision turns out to rest on a concept, you can trace that concept to the corpus it came from.

## 6. OKFying a corpus, end to end

This section is the procedure. Everything in it has been run for real, twice: a ~924k-token English encyclopedia of crypto-options strategies became a 362-concept decision-support bundle, and the RayforceDB C engine (~2.7M tokens of source) became a 298-concept codebase-map. Both passed their owner's ten test queries. The steps below are what actually happened, with the commands and the checkpoints — first for a text corpus, then the deltas for a codebase.

### Track one: a text corpus

**Step 0 — what you need.** The `okfy` CLI installed (`uv tool install ./core`), the Claude Code plugin (`/okfy:new`, `/okfy:extract` and friends), a corpus directory, and roughly an hour of *your* attention spread across three checkpoints. The hour is not overhead — it is the mechanism. Every place this pipeline asks for you is a place where a model deciding alone would quietly optimize for plausibility over your actual needs.

**Step 1 — the Purpose Interview (`/okfy:new`).** The first question is never "what's in the corpus" — it is *what should the bundle be FOR*. "Support trading decisions" produces a completely different bundle than "teach a junior the terminology" from the same encyclopedia: different concept types, different granularity, different glossary depth. The interview then extracts three things from you, and each becomes a permanent artifact:

- **The purpose statement** → `meta/purpose.md`. Locked; a new purpose means a new bundle, not a renovation.
- **Ten test queries** — real questions, phrased the way you actually ask, in the language you actually use. These become the bundle's *acceptance contract*: `test_queries` in `purpose.md`, replayed by `okfy eval` forever after (§11). Spend effort here. Vague queries produce a bundle that passes vague tests.
- **Your lexicon** — a short interview about *your* vocabulary: shorthand, Russian phrasings of English terms, jargon your desk uses. It seeds `meta/lexicon.md` rows so that retrieval speaks your dialect from day one.

**Step 2 — the survey.** `okfy survey <corpus>` is a cheap reconnaissance pass: file inventory, sizes, token estimates, content samples — and, since v0.5, an honest account of what it will *not* read. Git corpora are walked with `git ls-files` (your `.gitignore` is respected); vendor directories, lockfiles, binaries and PDFs are excluded by default and **listed in the `skipped` report**. Read that report before proceeding. If something you care about is in `skipped`, fix it now — an extraction cannot know what the survey never showed it, and this is the only stage where the omission is visible in one place.

**Step 3 — schema design and the Extraction Plan (checkpoint one).** From the survey and your purpose, the agent designs the bundle's shape: concept types (a decision-support bundle for trading grew `Strategy`, `MarketRegime`, `Risk`, `GlossaryTerm`, `Playbook`), category boundaries, granularity rules, and a seed glossary. This lands in `meta/extraction-plan.md`, and **you approve it before any extraction runs**. This is the highest-leverage review you will do: a wrong concept type multiplies into hundreds of wrongly-shaped concepts, while a wrong sentence in one concept is a one-file fix later. Argue with the plan, not with the drafts.

**Step 4 — blind parallel extraction (`/okfy:extract`).** The corpus is cut into deterministic **segments** (~50k tokens each; oversized files are chunked at blank-line boundaries into `{path, lines}` slices, so no single monster file swallows a worker). Each segment goes to one **worker** that sees the plan, the templates, the seed glossary, and its own segment — *never another worker's output*. Blindness is deliberate: it buys clean parallelism and prevents error cascades, at the price of duplicates, which the next stage exists to resolve. Every worker's drafts are committed per segment (extraction is resumable), and every segment writes a **ledger row** — inputs with content hashes, prompt version, outputs — into `meta/ledger.jsonl`, so six weeks later you can still ask *which worker, reading which files, produced this concept* (§11).

**Step 5 — consolidation.** Category consolidators merge the blind drafts: duplicates collapse, cross-links resolve, the glossary is synthesized, `index.md` is generated. The ledger records a `merge_map` — which drafts became which final concept — completing the provenance chain from corpus file to final knowledge.

**Step 6 — validation.** Four layers, from cheap to expensive: OKF spec conformance, bundle integrity (links, required fields, collisions — including source paths resolved against the corpus snapshot; extraction runs `--strict-sources`, so a concept citing a nonexistent file is an *error* at birth), purpose-fitness sampling (a model reads a sample of concepts against the purpose), and finally —

**Step 7 — the eval (checkpoint two).** `okfy eval run` replays your ten queries against the finished bundle and records the evidence; the agent triages with LLM verdicts; then **you** judge each query (`okfy eval verdict ... --owner`), and the bundle stays marked *provisional* until you have. §11 explains the full mechanics; the point here is the workflow position: this is where "the extraction is done" gets decided, and it is decided by you, against the contract you wrote in step 1, on recorded evidence anyone can replay. Under eight of ten? Don't lower the bar — refine: the gaps the eval exposes become targeted `okfy refine` edits or new concepts, and you re-run the eval. The crypto bundle hit 8/10 on the first pass and needed exactly one refine cycle (two authored concepts) to reach 10/10.

**Step 8 — what you have.** A git repository, private by default, containing shaped markdown concepts, a generated `README.md` for humans and `AGENTS.md` for agents (the consumption protocol: how to query, what to check before trusting, how to propose fixes), the lexicon, the eval record, the ledger, and a pre-commit hook enforcing the write policy. Point any agent at it — raw file traversal works; the CLI makes it faster; `okfy-mcp serve` (§13) makes it a tool surface. From here, the living-bundle loops take over: proposals and review (§10), staleness and updates (§8), the next eval run.

### Track two: a codebase

The procedure is the same eight steps; four things change.

**The archetype is `codebase-map`** — five fixed concept types (Module, DataModel, Flow, Convention, Decision; §7 explains why exactly these), so step 3's schema design is mostly *placement* decisions — module boundaries, which flows deserve concepts — rather than type invention.

**Residence is a real decision.** `--embed` puts the bundle at `.okf/` inside the mapped repo, riding the same PRs as the code, under the `direct` write policy (the repo's code review *is* the gate — §7). Standalone residence keeps the map in its own repo under `proposals` policy — the right choice when the mapped repo isn't yours to commit into, or when the map's audience is wider than the repo's committers. Rayforce went standalone for exactly that reason.

**The survey leans on git.** `git ls-files` traversal means build artifacts and vendored dependencies never reach a worker — but read the `skipped` report anyway: generated-but-committed code (parsers, protobufs) is *in* git and usually deserves an explicit `exclude` glob in the plan, since mapping generated code is mapping noise.

**The update loop earns its keep.** Code moves daily, so §8's snapshot-and-diff cycle is not an occasional chore but the map's heartbeat: `okfy diff` sorts drift into `affected` / `uncovered_new` / `stale_candidates`, `/okfy:update` re-extracts exactly what moved, `okfy stale` records your verdict on what died, `okfy repair-links` mends the references. A codebase-map that is not on this loop is a snapshot decaying toward fiction; on the loop, drift is visible the day it happens.

One honest note from the rayforce run: expect the map to argue with you. The owner believed query optimization was "dispersed" through the engine; the extraction found a real ten-pass `ray_optimize` pipeline, with fusion living in the executor. The map was right, the mental model was wrong, and the correction happened at plan review — which is the checkpoint working exactly as designed.

### When not to OKFy

The pipeline costs owner attention (the interview, the plan review, ten verdicts) and model time (the extraction). That price is worth paying when the corpus is too big for a context window, consulted repeatedly, and stable enough that concepts outlive their extraction. It is *not* worth paying for a corpus that fits in one prompt (just paste it), a one-off question (just ask it), or material that churns hourly (the update loop would never rest). OKFy is infrastructure; build it where infrastructure amortizes.

## 7. Mapping a codebase

Everything so far has treated the corpus as prose — trading memos, notes, docs. But the sharpest consumer of a knowledge bundle is a coding agent, and the sharpest corpus is the code itself. OKFy ships a purpose-shaped preset for exactly this: the **codebase-map** archetype. Its purpose is fixed and narrow — let a model (or an engineer) navigate and *safely change* a codebase — and everything about its shape follows from that purpose.

A codebase-map bundle has exactly five concept types, and each answers one question a change-maker asks:

- **Module** — *"What is this part responsible for, and what does an edit here have to preserve?"* It carries a Responsibility, an Interface (real signatures, pre/postconditions, error behavior), and Dependencies (what it leans on and what leans on it).
- **DataModel** — *"What is the shape of this data, and what must always be true of it?"* Shape, Invariants, Lifecycle.
- **Flow** — *"How does this behavior actually happen, end to end?"* Trigger, Path, Failure modes — so an agent traces a flow instead of grepping call sites blindly.
- **Convention** — *"What rule binds changes across this code, and why?"* Rule, Rationale, Enforcement.
- **Decision** — *"Why is it built this way, and what did we trade off?"* Context, Decision, Consequences — the record that keeps an agent from relitigating a settled choice without new evidence.

Where does this bundle *live*? For prose corpora the bundle sits in its own repository, off to the side. For a codebase map that would be a mistake: the map drifts the instant someone merges code without touching the far-away knowledge repo. So codebase-map bundles use **embed residence** — `okfy init --embed` writes the bundle into an `.okf/` directory *inside the mapped repository's own working tree*. The knowledge then rides the same pull request as the code it describes. Change a module's interface and update its Module concept in the same commit; the reviewer sees both diffs together and cannot approve one without the other.

Embedded residence comes with a specific write policy: **`direct`**. Elsewhere OKFy is strict — consuming agents write to a `proposals/` area and never edit knowledge in place, because there is no code review standing between them and the bundle's authority. Inside an embedded map, that review gate already exists: it is the repository's own PR process. So direct writes are safe *there* precisely because the surrounding code review covers the knowledge change too. The same direct-write policy would be reckless in a standalone bundle, where nothing reviews the edit — which is why the policy is tied to residence, not left to preference.

For a coding agent, consuming a codebase-map is a discipline, not a lookup. Before **editing** any code, read the owning Module concept and every Convention that applies to the files you touch — a change that violates a documented invariant or convention is wrong even if the tests pass. Before **redesigning**, read the relevant Decision concepts; do not reopen a decided trade-off without new evidence. And observe coverage honesty: if no concept covers the area, say so rather than presenting a merely similar Module as if it answered the question.

A worked example — taken from a real map, abridged. OKFy's v0.2 acceptance run extracted [RayforceDB](https://github.com/RayforceDB/rayforce) (a columnar + graph analytics engine in C, ~160 source files) into a 298-concept codebase-map bundle that answered 10/10 of the maintainer's test queries on the first pass. Here is its `Memory` Module concept — the full version runs 117 lines; this excerpt keeps the shape:

```markdown
---
type: Module
title: Memory
description: "Rayforce's custom memory subsystem: per-thread buddy heap,
  slab cache, arena bump allocator, copy-on-write refcounting, tracked mmap
  allocator — no system malloc for ray_t."
tags: [mem, allocation, refcount]
aliases: [память, memory management, аллокатор, подсчёт ссылок, buddy allocator]
sources: [src/mem/heap.c, src/mem/heap.h, src/mem/cow.c, src/mem/arena.c,
  src/mem/sys.c, src/core/block.c, docs/docs/architecture/memory.md]
---
## Responsibility

`src/mem/` (plus `src/core/block.c`) owns every byte of `ray_t` allocation.
Rayforce **never** calls `malloc`, `calloc`, `realloc`, or `free` for `ray_t`
objects. Five cooperating mechanisms live here: a per-thread buddy heap
(every `ray_t` IS a block carved from a self-aligned mmap'd pool; the 32-byte
object header doubles as the block header), copy-on-write refcounting
(`ray_retain`/`ray_release`/`ray_cow`), an arena bump allocator for
short-lived blocks freed together, a tracked mmap allocator for
infrastructure that predates any heap, and the block-size authority
`ray_block_size`.

## Interface

    ray_t*   ray_alloc(size_t data_size);   /* main allocator; rc=1; NULL on OOM */
    void     ray_free(ray_t* v);            /* free / slab-cache / foreign-enqueue */
    void     ray_retain(ray_t* v);          /* no-op for NULL, RAY_ERROR, ARENA blocks */
    void     ray_release(ray_t* v);         /* rc-- ; ray_free(v) at 0 */
    ray_t*   ray_cow(ray_t* v);             /* rc==1: v; else alloc_copy + release */

## Dependencies

Depends only on the OS via platform VM primitives — see
[Core Runtime](/modules/core-runtime.md). Depended on by essentially every
module that constructs objects; see the
[ray_t Block Header](/data-models/ray-t-block-header.md).

## Change rules

- All object memory MUST come from `ray_alloc`; never `malloc` a `ray_t`.
  See [No System Allocator](/conventions/no-system-allocator.md).
- Arena blocks are freed only by `ray_arena_reset`/`destroy`, never `ray_free`.
- The free marker `rc == 0` is load-bearing for buddy coalescing: any code
  that caches a live block MUST keep `rc >= 1`.
- Cross-thread frees MUST route via the foreign LIFO — see
  [Refcount Discipline](/conventions/refcount-discipline.md).
- See [Custom Memory Model](/decisions/custom-memory-model.md) for the why.
```

Notice what makes this *actionable*: an agent can honor the `ray_alloc`/`ray_free`/`ray_cow` contract, knows the three objects refcount ops silently ignore, and knows that caching a block with `rc == 0` corrupts the heap — all **without opening `heap.c`**. That standalone-content test is exactly what the archetype's validation enforces. (A detail worth stealing: the `aliases:` line carries Russian equivalents, so the maintainer's Russian-language queries hit this English concept through plain lexical search.)

One more thing a real map does that a fictional one can't: it corrects its owner. Rayforce's maintainer described query optimization as "smeared across the engine" in the extraction interview; the map came back with `flows/query-optimization-pass-pipeline` — a real, ordered ten-pass rewrite (`ray_optimize`) applied to the operation DAG before execution — plus a Decision recording that expression *fusion* deliberately lives in the executor, not the optimizer. The map knew the codebase better than its author's summary of it.

The codebase-map serves whoever *changes* the engine. Its sibling, the **api-reference** archetype, serves whoever *drives* it — an agent writing calls against a C library, a query language, an HTTP surface. Five caller-side types mirror the maintainer's five: **Operation** (one callable or one family of callables — arithmetic ships as a single page whose aliases carry every member name, so a search for `sqrt` still lands), **Type** (the shapes that move through calls), **Recipe** (a task composed into a verified call sequence with one runnable example), **Contract** (the cross-cutting rules a caller must not break: who frees memory, what threads may touch what, rate limits), and **Topic** (the explanatory nodes: a language's evaluation model, the memory story as the consumer sees it). Its consumption protocol carries one rule above all the others: **never invent a signature** — every call an agent emits must be verified against an Operation concept, and "not covered by this bundle" beats a plausible guess, because a hallucinated signature is the canonical failure of API-assisted codegen. The same corpus can legitimately carry both bundles — a map for its maintainers and a reference for its users are different Purposes, and the identity rule (one Bundle per Corpus-and-Purpose pair) makes that two bundles, not one confused one.

## 8. Keeping bundles fresh

An embedded map is only worth trusting if it stays honest, and code moves faster than anyone updates knowledge by hand. So the map *will* drift — the question is whether the drift is visible. OKFy's answer is a snapshot-and-diff loop.

At extraction time OKFy records a **snapshot**: for every concept, a fingerprint of the corpus sources it was built from. When you want to know whether the map has fallen behind, run `okfy diff <bundle>`. It compares the current state of those sources against the snapshot and sorts every concept into three buckets, emitted as JSON keys: **`affected`** (its sources changed — the concept may now be wrong), **`uncovered_new`** (source files appeared that no concept covers), and **`stale_candidates`** (every source a concept was built from is gone). The diff is deterministic core logic, not a model call: same inputs, same verdict, every time. The third key's name is deliberate: these are *candidates* for the persisted `stale: true` trust flag, never the flag itself. The whole report is transient diagnosis, recomputed from scratch on every run — a diff describes drift; it never writes it down. Promoting a candidate into an actual `stale` flag is a reviewed owner decision (`okfy stale`), and §11 explains why the report and the flag are deliberately kept apart.

`okfy diff` only *reports* drift; the `/okfy:update` command *acts* on it. It runs the diff, then walks the affected and new concepts, re-extracting just those from the changed sources and reconsolidating them into the bundle — an incremental re-extraction that touches only what moved, instead of rebuilding the whole map. Concepts the diff called clean are left exactly as they are.

Re-extraction can leave a concept pointing at a link that no longer resolves — a renamed or merged concept id. `okfy repair-links` fixes these dangling references deterministically: for each broken link it finds the best-matching surviving concept id (via stdlib string matching, no model, no embeddings) and rewrites the reference, reporting anything it could not confidently repair for a human to resolve.

The snapshot is refreshed **last**, and the ordering is deliberate. The snapshot is the map's record of "what the code looked like when I was last known-good." If you refreshed it *before* re-extracting, you would erase the very evidence of what changed — the diff would come back empty and the drift would be invisible. So the snapshot is only re-stamped after the concepts have actually been brought back into agreement with the code. Update the knowledge first; declare it current second.

Underneath all of this sits one honesty rule, and it is the same one the consumption protocol states: **the code is the truth; the map only flags drift.** When a concept and the code it describes disagree, the code wins — no exceptions. The map's job is not to be authoritative over the code but to be honest about its own staleness: to say clearly "these concepts may be behind, here is the drift" rather than to present a stale answer with false confidence. A map that admits what it doesn't know is worth more to an agent than one that quietly lies.

## 9. Federating bundles

There is a temptation, once you have the machinery, to build one enormous bundle that knows everything. Resist it. A mega-bundle fails the same way a wiki fails: it stops being shaped. Purposes blur, the acceptance queries become a grab-bag, and the map grows monotonically until no single reader — human or agent — can hold it. The discipline that makes a bundle useful is that it answers *one* stated purpose well. Several purpose-shaped bundles, each sharp, beat one bundle that is vaguely about a domain.

But real questions cross purposes. "Which options strategy fits this thesis, and does it stay inside our risk limits?" is not a crypto-options question and not a risk-limits question — it is both. The answer lives in a knowledge bundle; the ceiling on the answer lives somewhere else entirely. Federation is how you ask that question without merging the two bundles into one and losing the shape of both.

The artifact that does this is a **workspace**. A workspace is not a bundle — it holds no concepts of its own. It names a set of *member* bundles, assigns each a **role**, records a **crosswalk** between their vocabularies, and carries its own ten test queries. The roles are the point: a member is either `knowledge` (answers come from here) or `constraints` (limits that outrank knowledge). When a query touches both, the constraints win. Strictest wins — a risk-limits member that says "no naked short options over 2% of book" outranks any knowledge member's enthusiasm for the trade, every time.

The hard part is that two independently-shaped bundles do not share a vocabulary. A crypto-options bundle calls something a *short strangle*; a risk-limits bundle written by a different desk calls the same exposure *непокрытая продажа опционов* — an uncovered option sale, zero shared tokens with the English phrase. No amount of query-time vector similarity reliably bridges that gap, and even where it might, it does so invisibly, un-auditably, differently on every run. Federation closes the gap **once, at link time, in a reviewed crosswalk.** `okfy link-candidates` proposes matches deterministically from aliases and fuzzy overlap; an LLM judge proposes the ones lexical matching cannot see — the `same-as` pairs with no shared tokens and the `constrains` rows that bind a strategy to the limits it must obey. Then a human reviews them. `constrains` rows in particular require explicit approval, because a wrong one silently changes what the federation will let you do. The accepted rows are written into `links/*.md` and committed — a frozen, inspectable, version-controlled bridge, not a runtime guess.

Once linked, `query` over the workspace auto-detects the federation, pulls from every relevant member, and surfaces the binding constraints alongside the knowledge. When you are done, `okfy workspace export` fuses the members into a single frozen hand-off marked `exported: true` — at which point the update verbs refuse to touch it. An export is a snapshot for delivery, not a living bundle; if the members move, you re-federate and re-export rather than editing the frozen fusion in place.

Cheatsheet: `okfy workspace init|status|export` manage the workspace lifecycle; `okfy link-candidates` proposes crosswalk rows; `okfy query <workspace>` auto-detects the federation and answers across members.

## 10. The refinement loop

A bundle is not written once and frozen. The moment agents start consuming it, they will find it wrong — a concept that drifted, a gap the extraction missed, an alias nobody thought of. That discovery is the single most valuable signal a knowledge base can produce, and it is also the most dangerous: the agent that noticed is mid-task, has write access to the same repo, and is one tool-call away from silently editing knowledge that other people's decisions depend on. "Please don't edit the finals" in a prompt is not a control. Trust is not a control.

So OKFy routes every write through one of three channels, and lets the bundle owner pick which is open. When a consuming agent finds a problem, it does not touch the finals — it drops a full concept file into `proposals/` with `okfy propose`, carrying a small envelope that says what it wants (`create`/`update`/`delete`), which concept it targets, and why. Nothing in the live map moves. When *you*, the owner, want to change a concept directly, you use `okfy refine`: it edits the file, validates it, and commits — a fast path, but a deliberate, owner-driven one. And for embedded bundles that live inside a code repo under the `direct` policy, writes are just writes, reviewed by the same pull-request process that reviews the code around them. Three doors, one of them always the agent's, none of them a back door.

What makes this enforcement rather than etiquette is that the core cannot intercept arbitrary file writes — so it moves the gate to the one place every change must pass: the git commit. A `proposals`-policy bundle carries a pre-commit hook that refuses any commit staging a final concept. Hand-edit a concept, `git commit`, and you get:

```
write_policy=proposals: direct concept edits are refused:
concepts/short-strangle.md
Agents: okfy propose. Owner: okfy refine / okfy review accept.
Deliberate bypass: git commit --no-verify
```

Refused, not discouraged. The sanctioned verbs commit with `--no-verify` precisely because they *are* the authority the hook defers to; the owner keeps the escape hatch, but has to name it.

The gate that turns proposals into knowledge is `/okfy:review`. A human reads each proposed concept and decides — this is judgment, and judgment stays with the person. The CLI's job is narrower and non-negotiable: on accept it validates the concept against the spec, merges it into the finals, and records a structured `review: accept` commit; a proposal that fails validation cannot be accepted. Human decides, CLI validates.

One more bridge needs tending. The vocabulary of the people asking questions drifts away from the vocabulary the bundle was built with — new slang, new instruments, new shorthand. `/okfy:lexicon` is the interview that keeps `meta/lexicon.md` current: it mines your recent language, proposes new aliases, and writes the reviewed additions back, so retrieval keeps matching what you actually say.

Cheatsheet: `okfy propose` (agents file changes) → `/okfy:review` (owner accepts/rejects, CLI validates) is the loop; `okfy refine` is the owner's direct edit; `/okfy:lexicon` keeps the vocabulary bridge fresh.

## 11. Verifying bundle quality

Everything up to here has taken a bundle's quality on faith — the extraction was careful, the consolidation resolved the contradictions, the ten test queries pass. But *who says they pass?* For most of OKFy's life the answer was: the agent that built the bundle said so, in prose, in a log line. That is exactly the wrong witness. A model grading the output it just produced is a closed loop of well-formatted self-deception — it has every incentive to declare victory and no independent standard to fail against. So v0.5 replaces the narrative with three artifacts that live *inside the bundle* and can be replayed by anyone — an owner-judged **eval**, the **lexicon** as a machine-readable retrieval contract, and a reviewed notion of **staleness** — backed by three supporting checks on the extraction itself: verified **sources**, an extraction **ledger**, and a survey that reports what it skipped. None of them lets the machine certify itself.

### Owner-judged eval

The acceptance contract from §4 — the bundle's own test queries — becomes a recorded, replayable run. `okfy eval run <bundle>` reads the test queries from `meta/purpose.md`, and for each one does the deterministic half: expands the query (below), runs BM25, and records the expanded query actually searched plus the top hits. It writes them, append-only, to `meta/eval.json` and commits. No verdict yet — just reproducible evidence. Anyone with the bundle can re-run this and get the same hits.

Then the judging, in two roles that never collapse into one:

- **The LLM-judge proposes.** `okfy eval verdict <bundle> latest <q-idx> pass|fail|partial --llm --note "…"` records a machine verdict with its reasoning. This is useful triage — but it stays **provisional**. An LLM verdict alone never counts toward release.
- **The owner disposes.** `okfy eval verdict <bundle> latest <q-idx> pass|fail|partial --owner --note "…"` records the human's verdict. This is the only kind release acceptance counts.

`okfy eval status` collapses the run to an effective verdict per query — owner wins over LLM, LLM-only is flagged provisional, neither is pending — and reports a top-level `provisional` flag that stays **true until every query carries an owner verdict**. A bundle cannot self-certify: the flag only clears when a human has signed off on the whole run. That friction is the point — it is the price of the claim "this bundle answers its purpose."

### The lexicon as a retrieval contract

Query expansion used to be folklore: every consuming agent re-invented "the user said *непокрытая продажа*, they probably mean the short-strangle concept" in its own head, differently each time, un-auditably. v0.5 moves the deterministic part into the core and pins it to a contract. `meta/lexicon.md` still reads as a human table, but its YAML frontmatter **rows** are now the source of truth, and `okfy query` consumes them by default (`--no-expand` opts out). Each row carries a `status`, and exactly three are allowed — more would be taxonomy creep:

- **`accepted`** — a confident mapping. The row *pins* its `maps_to` concepts into the results (marked `via: lexicon`, ahead of the BM25 hits) and adds its `canonical_terms` to the lexical search. This is how a Russian query reaches an English concept with zero shared tokens.
- **`ambiguous`** — the term maps to several concepts and the lexicon won't guess. It fires **no** pins; instead it emits an explicit *note* listing the candidates, so the agent (or human) disambiguates with eyes open rather than the tool silently picking one.
- **`not-covered`** — the term is real but this bundle has nothing for it. It emits a *note* saying so. This is the honesty move: an absence stated out loud beats a plausible-looking wrong hit. A bundle that admits "I don't cover funding-rate arbitrage" is worth more than one that quietly returns its nearest neighbour.

Pre-rows lexicons (prose only, no `rows:` key) stay valid — expansion is simply a no-op — so old bundles don't break. Migrating a prose lexicon to rows is a judgment task for `/okfy:lexicon` and the owner, not an automatic converter. And the deterministic step doesn't retire agent judgment: a consuming agent may still rewrite a query on top of expansion using glossary knowledge and task context. The core supplies the reproducible floor; the agent adds reasoning above it.

### Staleness is a reviewed decision

`stale: true` on a concept means one specific thing — *do not trust this as current* — and it is set only by a human. `okfy stale <bundle> <concept-id> --reason "…"` flags it (and `--clear` removes it); accepting a Proposal can also set it. Nothing automatic ever flips it. This is the line §8 drew: a corpus diff reports a concept as **`affected`** (its sources moved) or lists it under **`stale_candidates`** (its sources are gone), but a candidate is transient diagnosis — a description of drift, recomputed every run and gone the next time the corpus settles, distinct from this persisted flag. Persisting "this is no longer trustworthy" is a decision with consequences, and decisions belong to owners, not to a file-hash comparison. Note too that a stale concept is *not* a deprecated one: it may still be the best answer available, which is why retrieval keeps stale hits **visible but marked** rather than hiding or demoting them (`--no-stale` drops them entirely if you insist). No silent score magic; the mark travels with the hit and the agent decides.

### Sources that must exist

Every extracted concept carries `sources:` — the corpus files it was built from. For most of OKFy's life that field was decorative: validation checked it was *present*, never that the paths were *real*. A concept could cite `src/engine/optimizer.c` for years after the file was deleted, and nothing would notice. v0.5 makes the citation checkable: `okfy validate` resolves every source path against the corpus snapshot (or the live tree, for embedded bundles) and reports a **`W_BAD_SOURCE`** warning for each path that no longer exists, plus a coverage summary — *N concepts with sources, M all-valid, K with broken paths* — so you can watch provenance health as a number, not a feeling.

Deliberately, the default is a *warning*, not an error. The check arrived after the reference bundles were accepted; failing them retroactively for a rule they never knew would punish honesty. New extractions are held to the higher bar: `okfy validate --strict-sources` escalates every broken path to an error, and the extraction workflow runs it that way, so bundles born under v0.5 are born with verified citations. And a broken source never flips the `stale` flag on its own — it is one more *signal* for the owner's review, because "the file moved" and "the knowledge is wrong" are different claims, and only a human can tell which one happened.

### The extraction paper trail

Extraction is LLM work — workers read segments, drafts get consolidated, judgment happens in prompts. That is by design (the core stays deterministic; the model does the reading), but it left a hole: when a concept turned out wrong six weeks later, there was no way to ask *which worker, reading which files, under which prompt, produced this?* The **extraction ledger** closes that hole without pretending the LLM steps are reproducible. Every pipeline transition appends one row to `meta/ledger.jsonl`:

```
$ okfy ledger add ./bundle --run 2026-07-08T12-00 --segment segment-03 \
    --inputs src/vec/fuse.c,src/vec/pipe.c --prompt-version extract-worker@1 \
    --outputs drafts/segment-03/operator-fusion.md --validation ok
$ okfy ledger list ./bundle --run 2026-07-08T12-00
```

A row records what went in (paths *and* content hashes, resolved from the corpus manifest), what came out, which prompt version did the work, and the commit that landed it. Consolidation rows additionally carry a **merge map** — `draft → final` — so you can trace any final concept back through the merge to the worker drafts and from there to the exact source files and their hashes at extraction time. The ledger is deliberately *shallow*: one row per artifact transition, not per claim or per sentence. Segment-level provenance answers the questions that actually come up ("what fed this concept?", "which prompt version was this batch?"); claim-level provenance would cost an order of magnitude more machinery, and it can be added later *if real failures ever show segment-level is not enough* — not before.

### Extraction that survives messy corpora

The first corpora OKFy ate were clean — curated markdown, a tidy C codebase. Real corpora are not: they carry `node_modules`, build artifacts, lockfiles, binaries, and the occasional 800-kilotoken file that would swallow a worker's entire budget. v0.5 hardens the survey/segment stage against all of that, and the theme is the same as everywhere else in this chapter: *no silent drops.*

Three changes. First, if the corpus is a git repository, the survey walks `git ls-files` instead of the raw filesystem — whatever the project's own `.gitignore` excludes, the survey excludes, with git's exact semantics and zero re-implemented matching. Second, for everything else there is a default exclude list (vendor and build directories, lockfiles, minified assets, media, archives, binaries — PDFs included, honestly reported as unsupported rather than mangled). Third, a file too large for one worker's token budget no longer lands whole: it is **chunked** at blank-line boundaries into `{path, lines}` slices, each within budget, and the worker is told to read only its lines. And everything the survey skipped or split is *reported* — a `skipped` section lists every excluded and binary path, and oversized files are flagged before segmentation — because a survey that quietly ate a directory would tell you the corpus was covered when it wasn't. The report is the contract: what the extraction saw, and what it deliberately did not.

### A worked example

You extracted a crypto-options decision-support bundle. Its purpose declares ten test queries, one of them a Russian phrasing of an English concept. You run the eval; `eval run` prints the recorded run as JSON on stdout (top hits use the slim `id`/`score`/`via` shape, and are trimmed to the first query here) and commits it:

```
$ okfy eval run ./options-bundle
{
  "run_id": "2026-07-08T09:14:02.511+00:00",
  "tool_version": "0.5.0",
  "created": "2026-07-08T09:14:02.511+00:00",
  "results": [
    {
      "query": "непокрытая продажа опционов под лимит риска",
      "expanded_query": "непокрытая продажа опционов под лимит риска short strangle naked option",
      "top_hits": [
        {"id": "strategies/short-strangle", "score": null, "via": "lexicon"},
        {"id": "risk/naked-option-limit", "score": 0.71},
        {"id": "market-regimes/high-iv", "score": 0.42}
      ],
      "llm_verdict": null, "llm_reason": null,
      "owner_verdict": null, "owner_note": null
    }
  ]
}
```

Nine more results follow — and there are no verdicts yet, just reproducible evidence. Inspect any one query directly and `okfy query` prints its ranked hits as a JSON array on **stdout**, with the expansion and lexicon notes on **stderr** (shown interleaved above the array, as the terminal renders them):

```
$ okfy query ./options-bundle "непокрытая продажа опционов под лимит риска"
expanded: непокрытая продажа опционов под лимит риска short strangle naked option
note: term "гамма-скальпинг" not covered by this bundle
[
  {"id": "strategies/short-strangle", "type": "strategy", "title": "Short strangle",
   "description": "Sell OTM call and put; profit while realized vol stays low.",
   "score": null, "via": "lexicon"},
  {"id": "risk/naked-option-limit", "type": "constraint", "title": "Naked option limit",
   "description": "Uncovered short options capped at 2% of book.", "score": 0.71},
  {"id": "market-regimes/high-iv", "type": "regime", "title": "High-IV regime",
   "description": "Elevated implied vol favours premium selling.", "score": 0.42}
]
```

The `accepted` lexicon row bridged the Russian phrase to `short-strangle` and *pinned* it — it surfaces first with `"via": "lexicon"` and a `null` score, ahead of the BM25 hits, and its canonical terms (`short strangle naked option`) were appended to the `expanded:` line. An `accepted` row emits no note; the pin itself is the evidence. The `not-covered` note, by contrast, tells you plainly that gamma-scalping is outside this bundle's scope — no phantom hit. Now the judging. The LLM-judge triages the run and the owner reviews query 3, where the top hit looked plausible but pointed at a retired strategy:

```
$ okfy eval verdict ./options-bundle latest 3 partial --llm --note "top hit relevant but omits the vega cap"
$ okfy eval verdict ./options-bundle latest 3 fail  --owner --note "retired strategy, must be flagged stale"
$ okfy stale ./options-bundle strategies/iron-condor-2023 --reason "retired 2024-Q3, superseded by dynamic-condor"
```

Then `eval status` collapses the run to an effective verdict per query and prints a JSON dict on **stdout** (the `queries` array holds all ten; intermediate entries are elided below) plus one loud line on **stderr**:

```
$ okfy eval status ./options-bundle
{
  "run_id": "2026-07-08T09:14:02.511+00:00",
  "queries": [
    {"i": 0, "query": "непокрытая продажа опционов под лимит риска", "verdict": "pass", "source": "owner"},
    {"i": 3, "query": "...", "verdict": "fail", "source": "owner"},
    {"i": 9, "query": "...", "verdict": "pass", "source": "llm", "provisional": true}
  ],
  "totals": {"owner_confirmed": 9, "provisional": 1, "pending": 0, "of": 10,
             "passes_owner": 8, "passes_provisional": 1},
  "provisional": true
}
PROVISIONAL: 9/10 owner-confirmed (1 llm-only, 0 pending) — release acceptance counts owner verdicts only
```

The owner overrode the LLM's optimistic `partial` with a `fail`, flagged the offending concept stale (so every future retrieval marks it), and the top-level `"provisional": true` stays set — with the stderr line saying it loudly — while even one query (here q9) rests on an LLM verdict alone. That flag is the whole design: the bundle stays *provisional* until a human has judged all ten. When the owner records an owner verdict on q9, `provisional` flips to `false` and the run is a signed, replayable acceptance record — evidence a third party can re-run, not a story the machine told about itself.

Cheatsheet: `okfy eval run` records the deterministic evidence; `okfy eval verdict --llm` proposes and `--owner` disposes; `okfy eval status` shows what's owner-confirmed vs provisional; `okfy stale` is the owner's reviewed distrust flag; `/okfy:lexicon` maintains the expansion rows; `okfy validate --strict-sources` holds new extractions to verified citations; `okfy ledger add|list` keeps the extraction paper trail.

## 12. A different way to organize your work

Step back from the mechanics and the argument is simple.

Most teams treat knowledge as *exhaust* — a byproduct that accumulates in wikis and doc folders and chat logs, growing monotonically, never shaped, consulted by grep and hope. That model was survivable when the consumer was a human who could skim, disambiguate, and ignore the stale bits. It is not survivable when the consumer is an agent whose entire competence is bounded by what fits, cleanly, in its context.

OKFy proposes the alternative: **knowledge as a first-class, versioned, purpose-shaped, agent-consumable artifact that lives next to your code.** Not a wiki off to the side, but a bundle in a git repo, shaped to a stated purpose, validated against an acceptance contract, carrying its own instructions for use.

What changes in a team's habits when you adopt this? You start *naming purposes* before you write knowledge, the way you name a function before you write it. You review knowledge in diffs, the way you review code. You let stale concepts be *retired* by a commit instead of lingering forever. You measure a knowledge base not by how much it contains but by whether it answers its ten queries. And you stop pasting the whole wiki into the prompt — because you finally have something smaller, sharper, and shaped that does the job better.

The context window is the ceiling. Knowledge engineering is how you raise it. OKFy is how you do knowledge engineering on purpose.

## 13. Serving a bundle over MCP

Everything so far assumed the agent runs the `okfy` CLI itself. Many do not — they speak the Model Context Protocol (MCP), a small standard for handing an agent a set of *tools* it can call. So OKFy ships an MCP adapter: run one command and a bundle becomes a live tool surface that any MCP client — Claude Code, Claude Desktop, Cursor — can query and enrich, without knowing anything about OKFy or having it installed.

The adapter is a **separate package**, on purpose. The MCP SDK carries real dependencies, and the core's whole discipline is that it stays PyYAML-only and portable — just markdown and git, no runtime. So the SDK weight lives entirely in `adapters/mcp/`; the core never learns MCP exists. An adapter is exactly the place a dependency is allowed to sit, precisely so the thing everyone imports does not have to.

It exposes five tools. Four are read: `okfy_query` (BM25 search → ranked snippets, running the same lexicon query expansion as the CLI — `expand` and `include_stale` default true, so an agent gets the bridged terms and sees stale hits marked without any extra work), `okfy_show` (one full concept by id — with a `section` heading to pull just one block and a `max_chars` cap for the rest), `okfy_links` (a concept's inbound and outbound links), and `okfy_overview` (the index — the first thing an agent should read, so it discloses progressively instead of bulk-reading the map, with `max_items` / `max_chars` caps). Every capped response carries a `truncated` flag, so a remote agent can bound its own context honestly instead of blowing its window on one call. The fifth is a *write*, but a deliberately narrow one: `okfy_propose` drops a full concept into `proposals/` and nowhere else. The v0.4a write-gate makes that a guarantee, not a request — a tool call physically cannot touch a final concept — so a remote agent that spots a gap can file a fix over the wire, and a human still reviews every one through `okfy review`. The enrichment loop closes across a transport with the gate intact. There is deliberately no `okfy_validate` tool: validation is a maintainer's job, not a consumer's.

One server serves exactly one bundle — the path is a launch argument, and that one server is one access boundary, matching private-by-default. To expose several bundles at once, point it at a **workspace** path instead: the adapter auto-detects the federation and the same tools answer across members, constraints pulled in, with no MCP-specific machinery. Transport is stdio only for now — local-first, no networked truth-daemon — and SSE is a later flag the SDK gives nearly for free.

Setup is a paste, never an edit. OKFy will *print* you a correct config snippet — `okfy-mcp config <path> --client claude-code` — but it never writes to your client's config file, because those formats drift and would break silently. You paste the block into `.mcp.json`, restart the client, and the five `okfy_*` tools appear.

Cheatsheet: `uv tool install ./adapters/mcp` installs `okfy-mcp`; `okfy-mcp serve <path>` runs the stdio server for a bundle or workspace; `okfy-mcp config <path> --client claude-code` prints the snippet you paste into your client.
