"""Worker job artifact (external review round 4, item 3). The core freezes
what a Worker is about to consume — input paths with their lines/chars spans
and content hashes, the corpus snapshot, the archetype, the exact prompt
text's SHA-256, the schema version — into one canonical JSON artifact
(`meta/jobs/<segment>.json`). The plugin stays the LLM orchestrator but hands
the agent this artifact, and the ledger records its digest: a reproducible
version instead of a hand-maintained label. This closes the class of
core<->prompt drift where the segmenter's output shape changes and the worker
prompt silently keeps describing the old one."""
import hashlib
import json
from pathlib import Path

from okfy.bundle import Bundle
from okfy.ledger import _manifest

JOB_SCHEMA = "okfy-worker-job@1"


def job_digest(job: dict) -> str:
    """SHA-256 over the canonical JSON of the job, digest field excluded."""
    body = {k: v for k, v in job.items() if k != "digest"}
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_job(bundle: Bundle, segment_id: str, prompt_file: Path) -> dict:
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
    job = {
        "schema": JOB_SCHEMA,
        "segment": segment_id,
        "corpus": {"path": str(corpus.meta.get("corpus", "")) if corpus else "",
                   "git_sha": (corpus.meta.get("git_sha") if corpus else None)},
        "archetype": {"name": plan.meta.get("archetype"),
                      "version": plan.meta.get("archetype_version")},
        "inputs": inputs,
        "prompt_sha256": hashlib.sha256(
            Path(prompt_file).read_bytes()).hexdigest(),
    }
    job["digest"] = job_digest(job)
    return job


def write_job(bundle: Bundle, job: dict) -> Path:
    out = bundle.root / "meta" / "jobs" / f"{job['segment']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(job, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    return out
