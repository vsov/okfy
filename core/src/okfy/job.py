"""Worker job artifact (external review round 4, item 3). The core freezes
what a Worker is about to consume — input paths with their lines/chars spans
and content hashes, the corpus snapshot, the archetype, the exact prompt
text's SHA-256, the schema version — into one canonical JSON artifact
(`meta/jobs/<segment>.json`). The plugin stays the LLM orchestrator but hands
the agent this artifact, and the ledger records its digest: a reproducible
version instead of a hand-maintained label. This closes the class of
core<->prompt drift where the segmenter's output shape changes and the worker
prompt silently keeps describing the old one.

EXECUTION IDENTITY (v0.10). The block above freezes what a worker CONSUMES and says
nothing about what RAN it. The same job artifact executed by a different model, a
different provider, or at a different temperature produces a different bundle, and
until now nothing recorded which — so "frozen prompt" never meant "reproducible run".
The optional `execution` block closes the gap, with one honest limit: it is an
ATTESTATION SUPPLIED BY THE HARNESS, NOT A MEASUREMENT MADE BY THE CORE. ADR-0002
keeps the core agent-neutral — it cannot observe which model ran, so it can only
record what the orchestrator declares. A harness that reports the wrong model passes
this check. What the block buys is that the claim is written down, covered by the
digest, and diffable across runs — not that it is true."""
import hashlib
import json
from pathlib import Path

from okfy.bundle import Bundle
from okfy.ledger import _manifest

JOB_SCHEMA = "okfy-worker-job@1"

# Closed vocabulary for the execution attestation. Closed on purpose: an open
# mapping invites each harness to invent its own field names, and a claim nobody
# can compare across runs is not provenance.
EXECUTION_FIELDS = ("model", "provider", "sampling", "harness_version")


def check_execution(execution: dict) -> dict:
    """Validate an execution attestation, returning it normalised in field order.
    Unknown keys and blank values are refused: a half-filled attestation is worse
    than none because it reads as complete."""
    if not isinstance(execution, dict):
        raise ValueError("execution must be a mapping of "
                         f"{', '.join(EXECUTION_FIELDS)}")
    unknown = sorted(set(execution) - set(EXECUTION_FIELDS))
    if unknown:
        raise ValueError(f"unknown execution field(s): {', '.join(unknown)} "
                         f"(allowed: {', '.join(EXECUTION_FIELDS)})")
    out = {}
    for key in EXECUTION_FIELDS:
        if key not in execution:
            raise ValueError(f"execution is missing {key!r} — record all of "
                             f"{', '.join(EXECUTION_FIELDS)} or omit the block")
        value = execution[key]
        text = json.dumps(value, sort_keys=True) if isinstance(value, dict) \
            else str(value).strip()
        if not text or text in ("{}", "None"):
            raise ValueError(f"execution field {key!r} is empty — an attestation "
                             "with blanks reads as complete and is not")
        out[key] = value
    return out


def freeze_prompt(bundle: Bundle, prompt_file: Path) -> tuple[str, str]:
    """Copy the exact prompt text into the bundle as an immutable,
    content-addressed artifact (external review round 5: a SHA alone proves
    the text existed, not what it said — the bundle must carry the text).
    Stored as .txt: prompt copies are not concepts and carry no frontmatter."""
    data = Path(prompt_file).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    rel = f"meta/prompts/{sha}.txt"
    out = bundle.root / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        out.write_bytes(data)
    return rel, sha


def job_digest(job: dict) -> str:
    """SHA-256 over the canonical JSON of the job, digest field excluded."""
    body = {k: v for k, v in job.items() if k != "digest"}
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_job(bundle: Bundle, segment_id: str, prompt_file: Path,
              execution: dict | None = None) -> dict:
    plan = bundle.plan()
    if plan is None:
        raise FileNotFoundError("meta/extraction-plan.md missing — run /okfy:new first")
    seg = next((s for s in plan.meta.get("segments", []) if s["id"] == segment_id),
               None)
    if seg is None:
        raise KeyError(f"unknown segment: {segment_id}")
    corpus = bundle.get("meta/corpus")
    manifest = _manifest(bundle)
    inputs = []
    for entry in seg["files"]:
        if isinstance(entry, str):
            entry = {"path": entry}
        i = {"path": entry["path"]}
        for span in ("lines", "chars"):
            if span in entry:
                i[span] = entry[span]
        i["sha256"] = manifest.get(entry["path"], "unknown")
        inputs.append(i)
    prompt_path, prompt_sha = freeze_prompt(bundle, prompt_file)
    job = {
        "schema": JOB_SCHEMA,
        "segment": segment_id,
        "corpus": {"path": str(corpus.meta.get("corpus", "")) if corpus else "",
                   "git_sha": (corpus.meta.get("git_sha") if corpus else None)},
        "archetype": {"name": plan.meta.get("archetype"),
                      "version": plan.meta.get("archetype_version")},
        "inputs": inputs,
        # `execution` is spliced in below rather than declared here so a job built
        # without one stays byte-identical to a pre-v0.10 artifact — the stored
        # digests of already-accepted bundles must not move.
        "prompt_path": prompt_path,
        "prompt_sha256": prompt_sha,
    }
    if execution is not None:
        job["execution"] = check_execution(execution)
    job["digest"] = job_digest(job)
    return job


def load_job(bundle: Bundle, segment_id: str) -> dict:
    p = bundle.root / "meta" / "jobs" / f"{segment_id}.json"
    if not p.is_file():
        raise FileNotFoundError(f"no job artifact for segment: {segment_id} "
                                f"(expected {p}) — run `okfy job` first")
    return json.loads(p.read_text(encoding="utf-8"))


def write_job(bundle: Bundle, job: dict) -> Path:
    out = bundle.root / "meta" / "jobs" / f"{job['segment']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(job, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    return out
