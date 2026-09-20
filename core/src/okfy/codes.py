"""Diagnostic code registry: one row per E_/W_ code raised or emitted across
the core and the MCP adapter.

A PINNED INDEX, not a message source: `summary` and `way_out` are hand-derived
from the actual raise/emit site in source, so this module never invents
behaviour the code does not have. It is read by `okfy codes` and cross-checked
against source and docs by `tests/test_doc_pins.py` — do not refactor a raise
site to read from here; add or edit a row here to describe what the site
already does.

`kind` is "error" or "warning" and always matches the E_/W_ prefix. Many rows
come in E_/W_ pairs: the same finding at two strictness levels (a `--strict-*`
flag turns the warning into the error), documented on each row rather than
folded into one, because a doc or a test may cite either half by name.

Where the raise/emit site states no remedy — the finding is inherently
unfixable, informational, or a judgment call left to the owner — `way_out` is
exactly the string "owner decision — no mechanical way out".
"""

CODES: dict[str, dict] = {
    "E_FRONTMATTER": {
        "kind": "error",
        "summary": "a concept file's frontmatter failed to parse",
        "way_out": "fix the YAML frontmatter block per the parse error in the message",
    },
    "E_PROPOSAL_TRUNCATED": {
        "kind": "error",
        "summary": "MCP okfy_propose `content`'s frontmatter block opens with "
                   "`---` but never closes (or ends mid-line inside it) — the "
                   "text was cut off before the write finished, not malformed",
        "way_out": "resend the whole text; if your output limit is the cause, "
                  "send the body in a patch or split the change",
    },
    "E_TYPE": {
        "kind": "error",
        "summary": "frontmatter `type` is missing or empty",
        "way_out": "add a non-empty `type:` field to the concept's frontmatter",
    },
    "E_INDEX_FRONTMATTER": {
        "kind": "error",
        "summary": 'index.md frontmatter declares more than `okf_version: "0.2"`',
        "way_out": "run `okfy package` to regenerate index.md's frontmatter",
    },
    "E_LOG_DATE": {
        "kind": "error",
        "summary": "a log.md `## ` heading is not an ISO 8601 date",
        "way_out": "fix the heading to an ISO 8601 date (YYYY-MM-DD)",
    },
    "E_META_MISSING": {
        "kind": "error",
        "summary": "a required meta concept (purpose, extraction-plan, or corpus) is missing",
        "way_out": "create the missing meta/<name>.md concept",
    },
    "E_META_FIELD": {
        "kind": "error",
        "summary": "a required field on a meta concept is missing or empty",
        "way_out": "fill in the named field",
    },
    "E_WRITE_POLICY": {
        "kind": "error",
        "summary": 'meta/purpose.md `write_policy` is not exactly "proposals" or "direct"',
        "way_out": "set write_policy to exactly one of the two literals — the pre-commit hook matches the literal, so a near-miss disables the gate silently",
    },
    "E_ACCEPTANCE_SHAPE": {
        "kind": "error",
        "summary": "meta/purpose.md `acceptance` is not a mapping",
        "way_out": "make `acceptance:` a mapping of known keys",
    },
    "E_ACCEPTANCE_KEY": {
        "kind": "error",
        "summary": "an `acceptance` key is not one of the recognised keys",
        "way_out": "use only min_owner_pass, allow_l3_fail, allow_open_dissent, min_adversarial_pass, allow_injection, or dissent",
    },
    "E_ACCEPTANCE_VALUE": {
        "kind": "error",
        "summary": "an `acceptance` key's value is not one of its permitted literal values",
        "way_out": "set it to exactly one of the permitted values (e.g. dissent: required)",
    },
    "E_ACCEPTANCE_TYPE": {
        "kind": "error",
        "summary": "an `acceptance` value has the wrong type",
        "way_out": "fix the value's type (integer, boolean, or string as declared)",
    },
    "E_ACCEPTANCE_RANGE": {
        "kind": "error",
        "summary": "acceptance.min_owner_pass or min_adversarial_pass is outside 1..(query count)",
        "way_out": "set the bar within 1 and the number of test_queries/adversarial_queries",
    },
    "E_ACCEPTANCE_INERT": {
        "kind": "error",
        "summary": 'allow_open_dissent is set but dissent is not "required"',
        "way_out": "set acceptance.dissent: required, or drop allow_open_dissent",
    },
    "E_ADVERSARIAL_SHAPE": {
        "kind": "error",
        "summary": "adversarial_queries (or one of its rows) is not the shape the schema requires",
        "way_out": "make adversarial_queries a list of mappings",
    },
    "E_ADVERSARIAL_TARGET": {
        "kind": "error",
        "summary": "an adversarial query's expected concept is not reachable in the bundle",
        "way_out": "point the row's `concept`/expectation at an id that exists in the bundle",
    },
    "E_ADVERSARIAL_FIELD": {
        "kind": "error",
        "summary": "an adversarial_queries row fails another field-level check",
        "way_out": "fix the row per the reported message (see evaluation.adversarial_row_problems)",
    },
    "E_UNKNOWN_TYPE": {
        "kind": "error",
        "summary": "a concept's type is not in meta/extraction-plan.md's declared `types`",
        "way_out": "fix the concept's `type:`, or add the type to extraction-plan.md `types`",
    },
    "E_UNDECLARED_TYPE": {
        "kind": "error",
        "summary": "(--strict-schema) a concept's type is neither declared in extraction-plan.md nor in the archetype's canonical_types",
        "way_out": "declare the adapted type set as `types:` in meta/extraction-plan.md",
    },
    "W_UNDECLARED_TYPE": {
        "kind": "warning",
        "summary": "a concept's type is neither declared in extraction-plan.md nor in the archetype's canonical_types",
        "way_out": "declare the adapted type set as `types:` in meta/extraction-plan.md; becomes an error under --strict-schema",
    },
    "E_CORPUS_SHA": {
        "kind": "error",
        "summary": "(--strict-sources) meta/corpus.md's git_sha is malformed or not a real commit of the local corpus repo",
        "way_out": "re-pin meta/corpus.md's git_sha to a real commit of the corpus repo",
    },
    "W_CORPUS_SHA": {
        "kind": "warning",
        "summary": "meta/corpus.md's git_sha is malformed or not a real commit of the local corpus repo",
        "way_out": "re-pin meta/corpus.md's git_sha; becomes an error under --strict-sources",
    },
    "E_EXEC_MISSING": {
        "kind": "error",
        "summary": "(--strict-execution) a job artifact records no execution identity",
        "way_out": "add an `execution` block (see job.EXECUTION_FIELDS) to the job artifact, or declare provenance: legacy",
    },
    "W_EXEC_MISSING": {
        "kind": "warning",
        "summary": "a job artifact records no execution identity",
        "way_out": "add an `execution` block to the job artifact; becomes an error under --strict-execution",
    },
    "E_EXEC_FIELD": {
        "kind": "error",
        "summary": "a job artifact's `execution` block is not a mapping, is missing a required field, or carries an unknown field",
        "way_out": "fix the execution block to carry exactly the fields in job.EXECUTION_FIELDS",
    },
    "W_EXEC_LEGACY": {
        "kind": "warning",
        "summary": "provenance: legacy is declared, so executor identity is not enforced on this bundle's job artifacts",
        "way_out": "owner decision — no mechanical way out",
    },
    "E_STALE_FIELDS": {
        "kind": "error",
        "summary": "stale: true is set without a valid stale_reason and ISO stale_since",
        "way_out": "set both stale_reason and a valid ISO (YYYY-MM-DD) stale_since",
    },
    "E_SUPERSEDE_DANGLING": {
        "kind": "error",
        "summary": "supersedes/superseded_by names a concept that does not exist, or the two concepts' links are not reciprocal",
        "way_out": "fix the link (correct the id, or add the missing reciprocal supersedes/superseded_by field) or remove it via `okfy refine`",
    },
    "E_DELETE_EXPECTED": {
        "kind": "error",
        "summary": "accepting this delete would remove a concept a purpose.md test_queries/adversarial_queries expectation still names",
        "way_out": "edit the expectation in meta/purpose.md first — a suite that expects a deleted concept can never pass — then accept again",
    },
    "E_PROPOSAL_LANE": {
        "kind": "error",
        "summary": "--supersedes names an open proposal filed by a different actor, or against a different target, than the new one; or, inside one `--batch` file, two entries name the same --supersedes",
        "way_out": "file a separate proposal, or ask the owner to reject the old one; inside a batch, only one entry may supersede a given proposal — file the second entry separately",
    },
    "E_PROPOSAL_GAP_CAP": {
        "kind": "error",
        "summary": "this actor already has GAP_CAP (10) open `--action gap` proposals — `--supersedes` of one's own open gap is exempt from the count, so withdrawing one and filing its replacement never trips the cap",
        "way_out": "the owner clears the queue with `okfy review list` / accept / reject, or withdraw one with --supersedes",
    },
    "E_BATCH_ENTRY": {
        "kind": "error",
        "summary": "an `okfy propose --batch` JSONL entry could not be parsed into a proposal — malformed JSON, not an object, an unknown key, or an unparseable `content`",
        "way_out": "fix that entry per the message (it names the line number); every other entry in the batch is still checked and reported independently",
    },
    "E_BATCH_MATERIALIZE": {
        "kind": "error",
        "summary": "an `okfy propose --batch` write failed after validation "
                   "passed; the batch's own new proposals were rolled back",
        "way_out": "file the entries one at a time to find the one that "
                  "fails, and report it — validation should have caught it",
    },
    "E_GAP_ROW_EXISTS": {
        "kind": "error",
        "summary": "accepting a gap proposal as not-covered would duplicate a lexicon row that already names this exact term",
        "way_out": "reject the proposal — the row is already there",
    },
    "E_PATCH_SHAPE": {
        "kind": "error",
        "summary": "a --patch-file (or MCP patch) is not a non-empty list of {old, new, count} objects with no unknown keys and a non-empty old",
        "way_out": "fix the patch JSON to a list of {old, new, count} hunks",
    },
    "E_PATCH_COUNT": {
        "kind": "error",
        "summary": "a patch hunk's declared count does not match how many times `old` actually occurs in the target's current body",
        "way_out": "re-read the concept with `okfy show`, fix the hunk or set count to the real occurrence count",
    },
    "E_PROPOSAL_SOURCE": {
        "kind": "error",
        "summary": "a proposed `sources:` entry does not resolve to a file of this bundle's corpus (checked at propose time when a checker is available)",
        "way_out": "`okfy show <id>` to copy a real anchor from a concept you are extending, or file with --evidence external-source=<url> and no corpus source",
    },
    "E_REVIEW_DUE": {
        "kind": "error",
        "summary": "review_due is not an ISO date (YYYY-MM-DD)",
        "way_out": "correct it with `okfy refine`",
    },
    "W_REVIEW_DUE": {
        "kind": "warning",
        "summary": "a concept's review_due date has passed",
        "way_out": "review the concept and update it (or its review_due) with `okfy refine`; `okfy stale <bundle> --due` lists every overdue concept",
    },
    "E_APPLIES_TO": {
        "kind": "error",
        "summary": "a concept's `applies_to` is not a non-empty list of project-key strings (or `*`), or an entry is well-formed but does not match the project-key slug grammar workspace.PROJECT_KEY_RE enforces (lowercase, no leading/trailing whitespace, no uppercase) and so could never match any real project_key",
        "way_out": "fix applies_to to a list of project keys or `*`, e.g. [\"my-project\", \"*\"] — lowercase and trimmed",
    },
    "E_WS_PROJECT_KEY": {
        "kind": "error",
        "summary": "a workspace has a `personal`-role member but no (or a malformed) project_key, so nothing in that member would ever be visible through federation; also raised when project_key is present but not a string (e.g. an unquoted YAML int/float), since a non-string key can never equal any `applies_to` entry",
        "way_out": "add (or quote) `project_key: \"<your-key>\"` (a lowercase slug) in the workspace manifest",
    },
    "W_VERIFIED_SUPERSEDED": {
        "kind": "warning",
        "summary": "a concept's text changed after its last verification",
        "way_out": "re-verify with `okfy propose` and `okfy review accept`",
    },
    "E_BAD_SOURCE": {
        "kind": "error",
        "summary": "(--strict-sources) a concept's source path is not in the corpus",
        "way_out": "fix the source path, or remove it",
    },
    "W_BAD_SOURCE": {
        "kind": "warning",
        "summary": "a concept's source path is not in the corpus",
        "way_out": "fix the source path, or remove it; becomes an error under --strict-sources",
    },
    "E_BAD_ANCHOR": {
        "kind": "error",
        "summary": "(--strict-sources) a source's line range or heading anchor does not exist",
        "way_out": "fix the anchor to a real line range or heading id in the source",
    },
    "W_BAD_ANCHOR": {
        "kind": "warning",
        "summary": "a source's line range or heading anchor does not exist",
        "way_out": "fix the anchor; becomes an error under --strict-sources",
    },
    "W_ANCHOR_UNCHECKED": {
        "kind": "warning",
        "summary": "a source anchor could not be checked (unreadable file, or a non-line fragment on a non-markdown source)",
        "way_out": "owner decision — no mechanical way out",
    },
    "E_SOURCE_MANIFEST": {
        "kind": "error",
        "summary": "(--strict-sources) meta/corpus-manifest.json is unreadable",
        "way_out": "fix or regenerate meta/corpus-manifest.json",
    },
    "W_SOURCE_MANIFEST": {
        "kind": "warning",
        "summary": "meta/corpus-manifest.json is unreadable",
        "way_out": "fix or regenerate meta/corpus-manifest.json; becomes an error under --strict-sources",
    },
    "W_NO_SOURCES": {
        "kind": "warning",
        "summary": "an extracted concept has no `sources`",
        "way_out": "add `sources:` citing the corpus, or confirm the type legitimately has none",
    },
    "W_CORPUS_COVERAGE": {
        "kind": "warning",
        "summary": "assigned corpus files are cited by no concept",
        "way_out": "extract concepts from the uncited files, or accept that they legitimately yield nothing — see coverage.uncited",
    },
    "W_SOURCE_OUTSIDE_SCOPE": {
        "kind": "warning",
        "summary": "a cited source is in the corpus but was assigned to no segment",
        "way_out": "owner decision — no mechanical way out",
    },
    "W_SPAN_COVERAGE_CONTRADICTION": {
        "kind": "warning",
        "summary": "a span the ledger declares `covered` cites a file coverage reports as uncited by any concept",
        "way_out": "owner decision — no mechanical way out",
    },
    "W_SPAN_COVERAGE_MISSING": {
        "kind": "warning",
        "summary": "a done segment has no span outcome report",
        "way_out": "record outcomes with `okfy ledger add --spans-file`, or declare provenance: legacy",
    },
    "E_SPAN_DOUBLE": {
        "kind": "error",
        "summary": "a span is declared in two outcome classes at once",
        "way_out": "fix the ledger's spans block so each span is in exactly one class (covered, reviewed_empty, or dropped)",
    },
    "E_SPAN_UNACCOUNTED": {
        "kind": "error",
        "summary": "an assigned span has no recorded outcome",
        "way_out": "add the missing span to the ledger's spans report",
    },
    "E_SPAN_UNKNOWN": {
        "kind": "error",
        "summary": "a span in the outcome report is not in the job artifact",
        "way_out": "remove the extraneous span key, or fix the job artifact's inputs",
    },
    "E_SPAN_OUTPUT": {
        "kind": "error",
        "summary": "a `covered` span names a draft id the ledger row does not list in outputs",
        "way_out": "add the draft id to the ledger row's outputs, or move the span to reviewed_empty",
    },
    "W_LEXICON_ROW": {
        "kind": "warning",
        "summary": "a lexicon row fails a shape check",
        "way_out": "fix the row per the reported message (see lexicon.row_problems)",
    },
    "W_LEXICON_STATUS": {
        "kind": "warning",
        "summary": "meta/lexicon.md `rows` is not a list, or a row has a bad `status`",
        "way_out": "make rows a list of well-formed lexicon rows with a valid status",
    },
    "W_LEXICON_TARGET": {
        "kind": "warning",
        "summary": "a lexicon row's maps_to names an unknown concept",
        "way_out": "fix maps_to to a real concept id",
    },
    "E_ID_COLLISION": {
        "kind": "error",
        "summary": "two concept ids collide case-insensitively",
        "way_out": "rename one of the colliding concepts",
    },
    "W_DANGLING_LINK": {
        "kind": "warning",
        "summary": "a concept body links to a missing concept",
        "way_out": "fix or remove the link",
    },
    "E_ORPHAN": {
        "kind": "error",
        "summary": "(--strict-package) a concept is unreachable from index.md or any concept",
        "way_out": "link the concept from index.md or another concept, then run `okfy package`",
    },
    "W_ORPHAN": {
        "kind": "warning",
        "summary": "a concept is unreachable from index.md or any concept",
        "way_out": "link the concept from index.md or another concept; becomes an error under --strict-package",
    },
    "W_INDEX_DRIFT": {
        "kind": "warning",
        "summary": "an index.md line no longer carries its concept's description",
        "way_out": "run `okfy package` to regenerate index.md",
    },
    "E_INDEX_SHARD": {
        "kind": "error",
        "summary": "v0.24 --shard-index: a concept is not listed in exactly one "
                   "of the resident index or its index/<dir>.md shards, a shard "
                   "lists a concept that does not exist, or a shard file is not "
                   "referenced from the resident index — the shard set no "
                   "longer matches the concept set; does NOT fire for ordinary "
                   "package staleness (a create/delete since the last `okfy "
                   "package`, reported instead as W_/E_STALE_PACKAGE plus the "
                   "usual orphan/dangling-link warnings) — only corruption "
                   "staleness cannot explain, e.g. a concept listed twice",
        "way_out": "re-run `okfy package --shard-index`",
    },
    "E_RESERVED_DIR": {
        "kind": "error",
        "summary": "a `.md` file under a reserved directory (index/, "
                   "protocols/) has a `type:` key in its frontmatter — it "
                   "looks like a concept, but `okfy package` owns those "
                   "directories and concept discovery skips them "
                   "unconditionally, so it is invisible to validation and "
                   "retrieval; also raised by `okfy propose --action create` "
                   "(or `--new-id`/`--extends`) when the write target's "
                   "first path segment is a reserved directory; and by "
                   "`okfy package` itself, before it writes anything, when "
                   "index/ or protocols/ holds a file it did not generate "
                   "(no `<!-- okfy:generated -->` marker) — packaging "
                   "refuses rather than delete or overwrite it",
        "way_out": "move the file to another directory (e.g. <dir>-notes/), then re-run `okfy package` if that is what was refused",
    },
    "E_QUALITY_MISSING": {
        "kind": "error",
        "summary": "(--strict-quality) meta/purpose-fitness.md is missing",
        "way_out": "create meta/purpose-fitness.md, the L3 review artifact",
    },
    "W_QUALITY_MISSING": {
        "kind": "warning",
        "summary": "meta/purpose-fitness.md is missing",
        "way_out": "create meta/purpose-fitness.md; becomes an error under --strict-quality",
    },
    "E_QUALITY_FIELD": {
        "kind": "error",
        "summary": "(--strict-quality) a required purpose-fitness field is missing/empty",
        "way_out": "fill in the missing field (date, prompt_version, selector_version, seed, sampled, or rows)",
    },
    "W_QUALITY_FIELD": {
        "kind": "warning",
        "summary": "a required purpose-fitness field is missing/empty",
        "way_out": "fill in the missing field; becomes an error under --strict-quality",
    },
    "E_QUALITY_UNKNOWN_ID": {
        "kind": "error",
        "summary": "(--strict-quality) a purpose-fitness `sampled` id is not in the bundle",
        "way_out": "fix `sampled` to only list ids that exist in the bundle",
    },
    "W_QUALITY_UNKNOWN_ID": {
        "kind": "warning",
        "summary": "a purpose-fitness `sampled` id is not in the bundle",
        "way_out": "fix `sampled` to only list ids that exist in the bundle; becomes an error under --strict-quality",
    },
    "E_QUALITY_ROW": {
        "kind": "error",
        "summary": "(--strict-quality) no verdict row exists for a sampled concept x check pair",
        "way_out": "add a verdict row (concept_id/check_id/verdict/evidence) for that pair",
    },
    "W_QUALITY_ROW": {
        "kind": "warning",
        "summary": "no verdict row exists for a sampled concept x check pair",
        "way_out": "add the missing verdict row; becomes an error under --strict-quality",
    },
    "E_QUALITY_DUP": {
        "kind": "error",
        "summary": "(--strict-quality) duplicate verdict rows exist for one concept x check pair",
        "way_out": "remove the duplicate row so only one verdict per pair remains",
    },
    "W_QUALITY_DUP": {
        "kind": "warning",
        "summary": "duplicate verdict rows exist for one concept x check pair",
        "way_out": "remove the duplicate row; becomes an error under --strict-quality",
    },
    "E_QUALITY_VERDICT": {
        "kind": "error",
        "summary": "(--strict-quality) a purpose-fitness row's verdict is not pass/fail/n-a",
        "way_out": "set verdict to one of pass, fail, or n/a",
    },
    "W_QUALITY_VERDICT": {
        "kind": "warning",
        "summary": "a purpose-fitness row's verdict is not pass/fail/n-a",
        "way_out": "set verdict to one of pass, fail, or n/a; becomes an error under --strict-quality",
    },
    "E_QUALITY_EVIDENCE": {
        "kind": "error",
        "summary": "(--strict-quality) a purpose-fitness row's evidence is empty",
        "way_out": "fill in the evidence field",
    },
    "W_QUALITY_EVIDENCE": {
        "kind": "warning",
        "summary": "a purpose-fitness row's evidence is empty",
        "way_out": "fill in the evidence field; becomes an error under --strict-quality",
    },
    "E_QUALITY_STALE": {
        "kind": "error",
        "summary": "(--strict-quality) the recorded L3 seed or selector_version no longer matches the live corpus or sampler",
        "way_out": "re-run `okfy sample` and redo the L3 review over what it picks",
    },
    "W_QUALITY_STALE": {
        "kind": "warning",
        "summary": "the recorded L3 seed or selector_version no longer matches the live corpus or sampler",
        "way_out": "re-run `okfy sample` and redo the L3 review; becomes an error under --strict-quality",
    },
    "E_QUALITY_SAMPLE": {
        "kind": "error",
        "summary": "(--strict-quality) the recorded L3 sample misses concepts the deterministic selector would now pick",
        "way_out": "re-run `okfy sample` and review the newly selected concepts too",
    },
    "W_QUALITY_SAMPLE": {
        "kind": "warning",
        "summary": "the recorded L3 sample misses concepts the deterministic selector would now pick",
        "way_out": "re-run `okfy sample` and review the newly selected concepts too; becomes an error under --strict-quality",
    },
    "E_QUALITY_UNPINNED": {
        "kind": "error",
        "summary": "(--strict-quality) the L3 review records no sampled_fingerprint or checks_digest",
        "way_out": "re-run `okfy sample` and record the pins it reports, or declare provenance: legacy",
    },
    "W_QUALITY_UNPINNED": {
        "kind": "warning",
        "summary": "the L3 review records no sampled_fingerprint or checks_digest",
        "way_out": "re-run `okfy sample` and record the pins it reports, or declare provenance: legacy; becomes an error under --strict-quality",
    },
    "E_QUALITY_DRIFT": {
        "kind": "error",
        "summary": "(--strict-quality) a recorded L3 pin no longer matches what it recomputes to",
        "way_out": "re-run `okfy sample` and redo the L3 pass over what it selects",
    },
    "W_QUALITY_DRIFT": {
        "kind": "warning",
        "summary": "a recorded L3 pin no longer matches what it recomputes to",
        "way_out": "re-run `okfy sample` and redo the L3 pass; becomes an error under --strict-quality",
    },
    "E_PROV_JOB_UNREADABLE": {
        "kind": "error",
        "summary": "(--strict-provenance) a job artifact is unreadable",
        "way_out": "repair or regenerate the job artifact JSON",
    },
    "W_PROV_JOB_UNREADABLE": {
        "kind": "warning",
        "summary": "a job artifact is unreadable",
        "way_out": "repair or regenerate the job artifact JSON; becomes an error under --strict-provenance",
    },
    "E_PROV_JOB_SEGMENT": {
        "kind": "error",
        "summary": "(--strict-provenance) a job artifact's `segment` field does not match its filename",
        "way_out": "fix the artifact's segment field to match its filename",
    },
    "W_PROV_JOB_SEGMENT": {
        "kind": "warning",
        "summary": "a job artifact's `segment` field does not match its filename",
        "way_out": "fix the artifact's segment field; becomes an error under --strict-provenance",
    },
    "E_PROV_JOB_DIGEST": {
        "kind": "error",
        "summary": "(--strict-provenance) a job artifact's stored digest does not recompute",
        "way_out": "regenerate the job artifact with `okfy job` rather than hand-editing it",
    },
    "W_PROV_JOB_DIGEST": {
        "kind": "warning",
        "summary": "a job artifact's stored digest does not recompute",
        "way_out": "regenerate the job artifact with `okfy job`; becomes an error under --strict-provenance",
    },
    "E_PROV_PROMPT": {
        "kind": "error",
        "summary": "(--strict-provenance) a job artifact's frozen prompt copy is missing or does not match its prompt_sha256",
        "way_out": "restore the exact frozen prompt file, or regenerate the job artifact with `okfy job`",
    },
    "W_PROV_PROMPT": {
        "kind": "warning",
        "summary": "a job artifact's frozen prompt copy is missing or does not match its prompt_sha256",
        "way_out": "restore the frozen prompt file, or regenerate the job artifact; becomes an error under --strict-provenance",
    },
    "E_PROV_LEDGER_JOB": {
        "kind": "error",
        "summary": "(--strict-provenance) a ledger row claims a job but no artifact exists for its segment",
        "way_out": "create the missing job artifact, or fix the ledger row's segment",
    },
    "W_PROV_LEDGER_JOB": {
        "kind": "warning",
        "summary": "a ledger row claims a job but no artifact exists for its segment",
        "way_out": "create the missing job artifact, or fix the ledger row's segment; becomes an error under --strict-provenance",
    },
    "E_PROV_LEDGER_DIGEST": {
        "kind": "error",
        "summary": "(--strict-provenance) a ledger row's job_digest does not match the frozen artifact",
        "way_out": "re-record the ledger row with `okfy ledger add --job` so the digest is computed, not hand-passed",
    },
    "W_PROV_LEDGER_DIGEST": {
        "kind": "warning",
        "summary": "a ledger row's job_digest does not match the frozen artifact",
        "way_out": "re-record the ledger row with `okfy ledger add --job`; becomes an error under --strict-provenance",
    },
    "E_PROV_LEDGER_INPUTS": {
        "kind": "error",
        "summary": "(--strict-provenance) a ledger row's inputs are not all in the job artifact",
        "way_out": "fix the ledger row's --inputs to match the job artifact's inputs",
    },
    "W_PROV_LEDGER_INPUTS": {
        "kind": "warning",
        "summary": "a ledger row's inputs are not all in the job artifact",
        "way_out": "fix the ledger row's --inputs to match the job artifact's inputs; becomes an error under --strict-provenance",
    },
    "E_PACKAGE_MISSING": {
        "kind": "error",
        "summary": "(--strict-package) no meta/package.json fingerprint exists",
        "way_out": "run `okfy package`",
    },
    "W_PACKAGE_MISSING": {
        "kind": "warning",
        "summary": "no meta/package.json fingerprint exists",
        "way_out": "run `okfy package`; becomes an error under --strict-package",
    },
    "E_STALE_PACKAGE": {
        "kind": "error",
        "summary": "(--strict-package) concepts changed since the last `okfy package`",
        "way_out": "run `okfy package` again before acceptance",
    },
    "W_STALE_PACKAGE": {
        "kind": "warning",
        "summary": "concepts changed since the last `okfy package`",
        "way_out": "run `okfy package` again; becomes an error under --strict-package",
    },
    "E_INJECTION": {
        "kind": "error",
        "summary": "(--strict-injection) corpus-borne text matched an injection scan rule, or a file could not be scanned",
        "way_out": "review the excerpt; remove or rewrite the offending text, or declare acceptance.allow_injection for phrase findings",
    },
    "W_INJECTION": {
        "kind": "warning",
        "summary": "corpus-borne text matched an injection scan rule, or a file could not be scanned",
        "way_out": "review the excerpt; becomes an error under --strict-injection",
    },
    "W_BUDGET_RESIDENT": {
        "kind": "warning",
        "summary": "the always-resident files (AGENTS.md + index.md) exceed the archetype's advisory token budget",
        "way_out": "shrink index.md, or accept the size — advisory only, and `release_check` never composes it",
    },
    "W_SOURCEMAP": {
        "kind": "warning",
        "summary": "a meta/source-map.jsonl row failed a check (see the wrapped code)",
        "way_out": "fix the source-map row per its own code and message",
    },
    "W_SOURCEMAP_UNVERIFIABLE": {
        "kind": "warning",
        "summary": "some source-map rows could not be checked against the corpus",
        "way_out": "make the corpus tree locally readable and re-run",
    },
    "E_REQUIRED_FIELD": {
        "kind": "error",
        "summary": "an archetype-required field is missing or empty",
        "way_out": "fill in the required field",
    },
    "E_REQUIRED_SECTION": {
        "kind": "error",
        "summary": "an archetype-required `## Section` heading is missing",
        "way_out": "add the missing section heading with real content",
    },
    "E_LINK_RULE": {
        "kind": "error",
        "summary": "a concept does not link enough concepts of a required kind, per the archetype's link_rules",
        "way_out": "add the required links (see the archetype's link_rules) to the concept",
    },
    "E_FIELD_ENUM": {
        "kind": "error",
        "summary": "a field's value is not in the archetype's declared enum",
        "way_out": "set the field to one of the archetype's allowed enum values",
    },
    "E_EMPTY_SECTION": {
        "kind": "error",
        "summary": "a required section heading is present but its body is empty",
        "way_out": "fill in the section body, or remove the empty heading",
    },
    "E_PROPOSAL_EVIDENCE": {
        "kind": "error",
        "summary": "proposed --evidence is malformed, or the proposal meta carries `verified`",
        "way_out": "pass `--evidence <kind>=<ref>` with kind one of test-run, owner-decision, external-source, or agent-inference",
    },
    "E_PROPOSAL_BASE_MOVED": {
        "kind": "error",
        "summary": "the target concept changed after the proposal was written against it",
        "way_out": "re-read the concept, file a fresh proposal against the new bytes, and reject the stale one",
    },
    "E_PROPOSAL_INJECTION": {
        "kind": "error",
        "summary": "proposed text matched an injection scan rule",
        "way_out": "have the owner write the text directly with `okfy refine` if it is legitimate; agent-written text matching the scan is refused, not filed",
    },
    "E_PROPOSAL_SECRET": {
        "kind": "error",
        "summary": "proposed text contains a secret-shaped value",
        "way_out": "remove the value and refer to where it lives (env var name, vault path) instead, then propose again",
    },
    "E_PROPOSAL_REJECTED": {
        "kind": "error",
        "summary": "this exact or reworded text was already rejected or deleted by the owner",
        "way_out": 'reopen it: `okfy propose ... --reopen "<new evidence>"` stating what changed',
    },
    "E_PROPOSAL_DUPLICATE": {
        "kind": "error",
        "summary": "a create/supersede's title already names an existing concept, as its title or an alias; an open flag or gap already covers the same target/query; or two entries inside one `--batch` file collide the same way (same title, same gap query, or same flag key)",
        "way_out": "add to the existing concept with `--extends <id>`, give the new one a title that says how it differs, or review the existing open flag/gap instead of filing another — inside a batch, file the colliding entries one at a time",
    },
    "W_PROPOSAL_NEAR": {
        "kind": "warning",
        "summary": "a create is close to one or more existing concepts",
        "way_out": '`--extends <id>` if it is the same thing, or `--distinct-from <id>="reason"` if it is not',
    },
    "E_PROPOSAL_OBSERVED_FORGED": {
        "kind": "error",
        "summary": "proposal content carries a top-level `observed`/`read_set` key, or `proposal.observed` — a claimed read/search history that is not the adapter's own",
        "way_out": "remove the key — it is written by the adapter process, not by the author",
    },
    "E_JOURNAL_INSIDE_BUNDLE": {
        "kind": "error",
        "summary": "okfy-mcp --journal (or OKFY_MCP_JOURNAL) resolves to a path inside the served bundle or workspace root",
        "way_out": "put it outside, e.g. ~/.okfy/journal/<name>.jsonl — a journal inside the bundle would be committed and billed",
    },
    "W_PROPOSAL_UNBASED": {
        "kind": "warning",
        "summary": "a proposal was filed before base_sha256 existed and accepted without a lost-update check",
        "way_out": "re-file the proposal with `okfy propose` so it carries a base_sha256 for future lost-update checks",
    },
    "E_PROPOSAL_ACTOR": {
        "kind": "error",
        "summary": "the --as actor string is not a valid OKF v0.2 actor",
        "way_out": "pass `--as <producer>/<version>` (e.g. claude-code/1.0) or `<prefix>:<id>` (e.g. human:alice)",
    },
    "E_OWNER_ACTION_REQUIRED": {
        "kind": "error",
        "summary": "`review accept`/`review reject` was passed `--as <actor>` naming a non-human: actor — an agent declaring itself the owner",
        "way_out": "the message names the exact command to hand to the owner (okfy review accept/reject <bundle> <id>); omit --as to use the local git-config owner role, or pass --as human:<name>",
    },
    "W_MEMORY_LINE": {
        "kind": "warning",
        "summary": "a meta/memory.jsonl line is unreadable or malformed",
        "way_out": "fix or remove the reported line; `okfy propose` refuses until it is fixed",
    },
    "E_MEMORY_LINE": {
        "kind": "error",
        "summary": "a meta/memory.jsonl line is not JSON, not an object, missing a required key, or names an unknown event",
        "way_out": "fix or remove the reported line; `okfy propose`'s rejected-content gate refuses to run while any line is unreadable",
    },
    "E_DIFF_PARTIAL_LISTING": {
        "kind": "error",
        "summary": "manifest-mode `okfy diff`/`okfy snapshot` could not fully list the corpus (an unreadable path, or an empty new listing against a non-empty old one)",
        "way_out": "fix the permissions on the reported path, or point meta/corpus.md at the right directory, then run okfy diff again — a partial listing would report every unlisted file as removed",
    },
    "E_SOURCEMAP_JSON": {
        "kind": "error",
        "summary": "a meta/source-map.jsonl line is not valid JSON",
        "way_out": "fix the malformed JSON line",
    },
    "E_SOURCEMAP_FIELD": {
        "kind": "error",
        "summary": "a source-map row is missing a required field, or carries an unknown one",
        "way_out": "fix the row's fields to match the documented schema",
    },
    "E_SOURCEMAP_DIGEST": {
        "kind": "error",
        "summary": "a source-map row's hash field is not the documented shape (lowercase hex of the right length)",
        "way_out": "fix the field to a valid hex digest of the documented length",
    },
    "E_SOURCEMAP_LINES": {
        "kind": "error",
        "summary": "a source-map row's normalized_lines is not a valid, ascending, in-range line anchor",
        "way_out": "fix normalized_lines to a real ascending range (e.g. L12-L40) within the file",
    },
    "E_SOURCEMAP_NO_FILE": {
        "kind": "error",
        "summary": "a source-map row's normalized_path or raw_path is not a readable file where declared",
        "way_out": "fix the path to point at a real file inside the corpus (or the declared raw_root)",
    },
    "E_SOURCEMAP_TEXT_DRIFT": {
        "kind": "error",
        "summary": "a source-map row's text_sha256 no longer matches the cited lines",
        "way_out": "re-run the converter and regenerate the sidecar row for that span",
    },
    "E_SOURCEMAP_RAW_DRIFT": {
        "kind": "error",
        "summary": "a source-map row's raw_sha256 no longer matches the raw document's bytes",
        "way_out": "re-run the converter and regenerate the sidecar row for that span",
    },
    "E_SOURCEMAP_DUPLICATE": {
        "kind": "error",
        "summary": "the same normalized span is mapped by two source-map rows",
        "way_out": "remove the duplicate row",
    },
    "E_SOURCEMAP_EMPTY": {
        "kind": "error",
        "summary": "meta/source-map.jsonl exists and contains no rows",
        "way_out": "delete the file if this corpus was never normalized, or regenerate it",
    },
    "E_SOURCEMAP_MISSING": {
        "kind": "error",
        "summary": "normalization.source_map: required is declared and meta/source-map.jsonl is absent",
        "way_out": "generate meta/source-map.jsonl, or drop the `normalization.source_map: required` declaration",
    },
    "E_SOURCEMAP_COVERAGE": {
        "kind": "error",
        "summary": "a cited span or source is not covered by the (required) source map",
        "way_out": "add a sidecar row covering the cited span, or fix the citation's anchor",
    },
    "E_NORMALIZATION_KEY": {
        "kind": "error",
        "summary": "meta/purpose.md `normalization` is not a mapping, or has an unknown key",
        "way_out": "fix the normalization block to only use known keys (source_map, raw_root)",
    },
    "E_NORMALIZATION_VALUE": {
        "kind": "error",
        "summary": 'normalization.source_map is set to something other than "required"',
        "way_out": 'set normalization.source_map to exactly "required", or omit the key',
    },
    "E_NORMALIZATION_ROOT": {
        "kind": "error",
        "summary": "normalization.raw_root is not a readable directory",
        "way_out": "point raw_root at a real readable directory, or remove the key",
    },
    "E_SPAN_BAD_REPORT": {
        "kind": "error",
        "summary": "a worker's span outcome report is not a well-formed object (wrong shape, unknown class, or a blank key)",
        "way_out": "fix the spans report to map covered/reviewed_empty/dropped keys to the right value shape",
    },
    "E_SPAN_BLANK_REASON": {
        "kind": "error",
        "summary": "a reviewed_empty or dropped span has no reason",
        "way_out": "give the span a non-empty reason",
    },
    "E_SPAN_EMPTY_DRAFTS": {
        "kind": "error",
        "summary": "a `covered` span names no draft id",
        "way_out": "name at least one draft id, or move the span to reviewed_empty",
    },
    "E_SPAN_NO_JOB": {
        "kind": "error",
        "summary": "span outcomes were given but no job artifact exists for the segment",
        "way_out": "run `okfy job` first to create the job artifact",
    },
    "E_REL_VALIDATE": {
        "kind": "error",
        "summary": "strict validation found errors, or the plan's declared archetype is unknown",
        "way_out": "run `okfy validate` with all strict flags and fix the listed errors (or fix the plan's declared archetype name)",
    },
    "E_REL_SOURCEMAP": {
        "kind": "error",
        "summary": "the source map has problems at release strictness",
        "way_out": "fix the source-map rows named in the message (`okfy sourcemap` for detail)",
    },
    "E_REL_SOURCEMAP_UNVERIFIABLE": {
        "kind": "error",
        "summary": "source-map rows could not be checked against the corpus at release time",
        "way_out": "make the corpus readable and re-run",
    },
    "E_REL_INJECTION": {
        "kind": "error",
        "summary": "injection findings remain at release time, or a file could not be scanned",
        "way_out": "run `okfy validate --strict-injection` for detail, fix the findings, or declare acceptance.allow_injection (it never excuses invisible-unicode)",
    },
    "E_REL_SEGMENTS": {
        "kind": "error",
        "summary": "the extraction plan has no segments, or some are not done",
        "way_out": "finish extraction on every segment, or declare provenance: legacy if this bundle predates the job chain",
    },
    "E_SEGMENT_STATUS": {
        "kind": "error",
        "summary": "`okfy segment-status` was given a status outside the closed vocabulary (pending, running, done, failed, skipped)",
        "way_out": "use one of pending, running, done, failed, skipped",
    },
    "E_SEGMENT_TRANSITION": {
        "kind": "error",
        "summary": "the requested segment-status move (including a self-transition) is not one of the legal edges out of the segment's current status",
        "way_out": "the message names the legal next state(s) from the current status — move through one of those, or fix the status by hand in meta/extraction-plan.md if it holds an unrecognised value",
    },
    "E_SEGMENT_REASON_REQUIRED": {
        "kind": "error",
        "summary": "moving a segment to failed or skipped, or a done segment back to pending, was attempted without --reason",
        "way_out": "pass --reason \"...\" explaining why the segment stopped, or why a finished one is being re-extracted",
    },
    "E_SEGMENT_DONE_UNBACKED": {
        "kind": "error",
        "summary": "a segment's job artifact exists but is unreadable, or no ledger row carries its digest, so marking the segment done would claim provenance that does not exist",
        "way_out": "run `okfy ledger add --job <segment-id> ...` (job, worker, ledger add, THEN segment-status done) before marking the segment done; a segment with NO job artifact at all is let through as legacy, unbacked",
    },
    "E_REL_JOB_MISSING": {
        "kind": "error",
        "summary": "a done segment has no readable job artifact",
        "way_out": "create the job artifact with `okfy job`",
    },
    "E_REL_LEDGER_JOB": {
        "kind": "error",
        "summary": "no ledger row carries a done segment's job artifact digest",
        "way_out": "record the ledger row with `okfy ledger add --job <segment>`",
    },
    "E_REL_SPAN_COVERAGE_MISSING": {
        "kind": "error",
        "summary": "a done segment carries no span outcome report",
        "way_out": "record outcomes with `okfy ledger add --spans-file`, or declare provenance: legacy",
    },
    "E_REL_SPAN_DROPPED": {
        "kind": "error",
        "summary": "a segment records one or more dropped spans",
        "way_out": "extract the dropped material, or account for why it was dropped — a release cannot carry assigned material nobody looked at",
    },
    "E_REL_REPLAY_INCOMPLETE": {
        "kind": "error",
        "summary": "the eval replay stopped before the time budget let it finish comparing every query",
        "way_out": "re-run `okfy eval run <bundle> --suite <suite>` so the record is fresh, or shorten the suite",
    },
    "E_REL_EVAL_INVALID": {
        "kind": "error",
        "summary": "the latest eval record is malformed, unreadable, or missing usable query_options",
        "way_out": "repair meta/eval.json, or re-run `okfy eval run` and judge it again",
    },
    "E_REL_EVAL_MISSING": {
        "kind": "error",
        "summary": "no acceptance eval runs are recorded",
        "way_out": "run `okfy eval run <bundle>`",
    },
    "E_REL_EVAL_PROVISIONAL": {
        "kind": "error",
        "summary": "the latest acceptance eval run is not owner-complete",
        "way_out": "record an owner verdict (`okfy eval verdict --owner`) for every query",
    },
    "E_REL_EVAL_STALE": {
        "kind": "error",
        "summary": "the latest acceptance run's retrieval_fingerprint no longer matches the live bundle",
        "way_out": "re-run the eval and repeat the owner checkpoint",
    },
    "E_REL_EVAL_REPLAY": {
        "kind": "error",
        "summary": "the replayed acceptance run does not match its recorded results",
        "way_out": "re-run `okfy eval run <bundle> --suite acceptance` and repeat the owner checkpoint",
    },
    "E_REL_EVAL_POLICY": {
        "kind": "error",
        "summary": "owner passes on the acceptance suite are below the policy minimum",
        "way_out": "improve retrieval or concepts until the bar is met, or have the owner lower acceptance.min_owner_pass",
    },
    "E_REL_ACCEPTANCE_INVALID": {
        "kind": "error",
        "summary": "meta/purpose.md's acceptance block (or one of its fields) is unreadable or malformed",
        "way_out": 'fix the acceptance block: a mapping, integer min_owner_pass/min_adversarial_pass, and dissent exactly "required" if set',
    },
    "E_REL_EVAL_SURFACE": {
        "kind": "error",
        "summary": "test_queries is too small, has blanks, or has duplicates",
        "way_out": "add distinct, non-blank test_queries until at least release.MIN_TEST_QUERIES (10) remain",
    },
    "E_REL_ADVERSARIAL_SURFACE": {
        "kind": "error",
        "summary": "adversarial_queries is too small, has blanks, or has duplicates",
        "way_out": "add distinct, non-blank adversarial_queries until at least release.MIN_TEST_QUERIES (10) remain",
    },
    "E_REL_ADVERSARIAL_MISSING": {
        "kind": "error",
        "summary": "no adversarial eval run is recorded",
        "way_out": "run `okfy eval run <bundle> --suite adversarial` and judge it",
    },
    "E_REL_ADVERSARIAL_PROVISIONAL": {
        "kind": "error",
        "summary": "the latest adversarial eval run is not owner-complete",
        "way_out": "record an owner verdict for every adversarial query",
    },
    "E_REL_ADVERSARIAL_STALE": {
        "kind": "error",
        "summary": "the adversarial run was judged against a different retrieval contract than the live bundle",
        "way_out": "re-run it alongside the acceptance suite",
    },
    "E_REL_ADVERSARIAL_REPLAY": {
        "kind": "error",
        "summary": "the replayed adversarial run does not match its recorded results",
        "way_out": "re-run `okfy eval run <bundle> --suite adversarial` and repeat the owner checkpoint",
    },
    "E_REL_ADVERSARIAL_POLICY": {
        "kind": "error",
        "summary": "owner passes on the adversarial suite are below the policy minimum",
        "way_out": "improve retrieval or concepts, or have the owner lower acceptance.min_adversarial_pass",
    },
    "E_REL_L3_MISSING": {
        "kind": "error",
        "summary": "meta/purpose-fitness.md is missing at release time",
        "way_out": "run the L3 purpose-fitness review and write the artifact",
    },
    "E_REL_L3_FAIL": {
        "kind": "error",
        "summary": "purpose-fitness records fail verdicts and acceptance.allow_l3_fail is not set",
        "way_out": "fix the failing concepts, or declare acceptance.allow_l3_fail: true",
    },
    "E_REL_DISSENT_UNVERIFIABLE": {
        "kind": "error",
        "summary": "acceptance.dissent: required is declared but the merge groups or their pre-consolidation drafts cannot be reconstructed/recovered",
        "way_out": "record the merge_map at consolidation and keep the history holding drafts/ readable, or drop the required contract",
    },
    "E_REL_DISSENT_UNADJUDICATED": {
        "kind": "error",
        "summary": "a merge group has no dissent row",
        "way_out": "run the schism pass to adjudicate it, or state acceptance.allow_open_dissent",
    },
    "E_REL_DISSENT_OPEN": {
        "kind": "error",
        "summary": "a merge group holds an unresolved split",
        "way_out": "`okfy dissent waive --owner`, or split the concept",
    },
    "E_REL_DISSENT_STALE": {
        "kind": "error",
        "summary": "an adjudication no longer matches what it ruled on (the concept or draft set moved)",
        "way_out": "re-adjudicate with `okfy dissent add`",
    },
    "E_REL_WS_MEMBER_UNREADABLE": {
        "kind": "error",
        "summary": "a workspace member bundle could not be release-checked",
        "way_out": "fix whatever makes the member bundle unreadable (path, git, etc.) and retry",
    },
    "E_REL_WS_MEMBER_UNACCEPTED": {
        "kind": "error",
        "summary": "a workspace member is not itself release-accepted",
        "way_out": "get every member bundle release-accepted first (see the codes it names)",
    },
    "E_REL_WS_MEMBER_UNVERIFIABLE": {
        "kind": "error",
        "summary": "a member's crosswalk pin cannot be verified (no pin, an unreachable pin, or broken git)",
        "way_out": "fix the member's pin or git so `okfy workspace status` can verify it",
    },
    "E_REL_WS_MEMBER_DRIFT": {
        "kind": "error",
        "summary": "a member moved since the crosswalk pin recorded it",
        "way_out": "re-review the affected crosswalk rows and re-pin (`okfy workspace status`)",
    },
    "E_REL_WS_CROSSWALK_STALE": {
        "kind": "error",
        "summary": "a crosswalk row cites a concept that changed or cannot be verified",
        "way_out": "re-review that crosswalk row and re-accept it",
    },
    "E_REL_WS_EVAL_INVALID": {
        "kind": "error",
        "summary": "the workspace's meta/eval.json cannot be read",
        "way_out": "repair meta/eval.json",
    },
    "E_REL_WS_EVAL_SURFACE": {
        "kind": "error",
        "summary": "the workspace's test_queries surface is too small or degenerate",
        "way_out": "fix meta/workspace.md's test_queries (enough, non-blank, no duplicates)",
    },
    "E_REL_WS_ADVERSARIAL_SURFACE": {
        "kind": "error",
        "summary": "the workspace's adversarial_queries surface is too small or degenerate",
        "way_out": "fix meta/workspace.md's adversarial_queries (enough, non-blank, no duplicates)",
    },
    "E_REL_WS_EVAL_MISSING": {
        "kind": "error",
        "summary": "no federated acceptance eval run is recorded",
        "way_out": "run `okfy eval run <workspace> --suite acceptance`",
    },
    "E_REL_WS_ADVERSARIAL_MISSING": {
        "kind": "error",
        "summary": "no federated adversarial eval run is recorded",
        "way_out": "run `okfy eval run <workspace> --suite adversarial`",
    },
    "E_REL_WS_EVAL_PROVISIONAL": {
        "kind": "error",
        "summary": "the latest federated acceptance run is not owner-complete",
        "way_out": "record an owner verdict for every query",
    },
    "E_REL_WS_ADVERSARIAL_PROVISIONAL": {
        "kind": "error",
        "summary": "the latest federated adversarial run is not owner-complete",
        "way_out": "record an owner verdict for every query",
    },
    "E_REL_WS_EVAL_STALE": {
        "kind": "error",
        "summary": "the federated acceptance run was judged against a stale federated contract",
        "way_out": "re-run `okfy eval run <workspace> --suite acceptance`",
    },
    "E_REL_WS_ADVERSARIAL_STALE": {
        "kind": "error",
        "summary": "the federated adversarial run was judged against a stale federated contract",
        "way_out": "re-run `okfy eval run <workspace> --suite adversarial`",
    },
    "E_REL_WS_ACCEPTANCE_INVALID": {
        "kind": "error",
        "summary": "the workspace's acceptance.min_owner_pass or min_adversarial_pass is not an integer",
        "way_out": "fix meta/workspace.md's acceptance bar to an integer",
    },
    "E_REL_WS_EVAL_POLICY": {
        "kind": "error",
        "summary": "federated acceptance owner passes are below the policy minimum",
        "way_out": "improve federated retrieval, or have the owner lower the bar",
    },
    "E_REL_WS_ADVERSARIAL_POLICY": {
        "kind": "error",
        "summary": "federated adversarial owner passes are below the policy minimum",
        "way_out": "improve federated retrieval, or have the owner lower the bar",
    },
    "E_EVAL_COMPARE_SUITE": {
        "kind": "error",
        "summary": "`okfy eval compare`'s base and head runs are of different suites",
        "way_out": "pick two runs of the same suite (`okfy eval metrics --run` "
                   "lists each run's suite), or pass --suite when a record "
                   "holds both",
    },
}
