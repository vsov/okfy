"""Extraction Ledger (ADR-0013, v0.5 engineering): append-only meta/ledger.jsonl,
one row per pipeline artifact transition (a Worker or a Consolidation step).
Deliberately SHALLOW — segment-level provenance, not per-claim. Rows are written
AFTER the artifact commit, so `commit` pins the bundle HEAD that already contains
the outputs; `input_hashes` resolve from the corpus manifest so a third party can
tell exactly what each transition consumed (or that a hash was 'unknown')."""
import json
import subprocess
from pathlib import Path

from okfy.bundle import Bundle
from okfy.proposals import _commit

LEDGER = "meta/ledger.jsonl"

_REQUIRED_STR = ("run_id", "segment", "prompt_version", "validation")
_REQUIRED_LIST = ("inputs", "outputs")


def ledger_path(bundle: Bundle) -> Path:
    return bundle.root / "meta" / "ledger.jsonl"


def parse_merge_map(spec: str | None) -> dict | None:
    """Parse a CLI --merge-map string 'draft=final,draft2=final2' into a dict.
    Empty/None yields None (no merge_map key on the row)."""
    if not spec:
        return None
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"bad --merge-map entry {part!r} (want draft=final)")
        draft, final = part.split("=", 1)
        out[draft.strip()] = final.strip()
    return out or None


def _manifest(bundle: Bundle) -> dict:
    p = bundle.root / "meta" / "corpus-manifest.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _head(bundle: Bundle) -> str:
    r = subprocess.run(["git", "-C", str(bundle.root), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def _check(row: dict) -> None:
    for k in _REQUIRED_STR:
        v = row[k]
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"ledger row: {k} must be a non-empty string")
    for k in _REQUIRED_LIST:
        v = row[k]
        if not isinstance(v, list) or not v:
            raise ValueError(f"ledger row: {k} must be a non-empty list")


def add_row(bundle: Bundle, run_id: str, segment: str, inputs, prompt_version: str,
            outputs, validation: str, merge_map: dict | None = None) -> dict:
    """Append one transition row to meta/ledger.jsonl and commit the ledger
    --no-verify. input_hashes come from the corpus manifest ('unknown' when the
    path is absent); commit captures the current bundle HEAD (the artifact commit
    this row records)."""
    row = {
        "run_id": run_id,
        "segment": segment,
        "inputs": list(inputs) if isinstance(inputs, (list, tuple)) else inputs,
        "prompt_version": prompt_version,
        "outputs": list(outputs) if isinstance(outputs, (list, tuple)) else outputs,
        "validation": validation,
    }
    _check(row)
    manifest = _manifest(bundle)
    # inputs/input_hashes kept adjacent; commit last per the documented shape.
    row = {
        "run_id": run_id, "segment": segment, "inputs": row["inputs"],
        "input_hashes": {i: manifest.get(i, "unknown") for i in row["inputs"]},
        "prompt_version": prompt_version, "outputs": row["outputs"],
        "validation": validation, "commit": _head(bundle),
    }
    if merge_map:
        row["merge_map"] = dict(merge_map)

    path = ledger_path(bundle)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _commit(bundle, [LEDGER], f"ledger: {run_id} {segment}")
    return row


def read_rows(bundle: Bundle, run_id: str | None = None) -> list:
    """All ledger rows in order (one JSON object per line), optionally filtered
    to a single run_id."""
    path = ledger_path(bundle)
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if run_id is not None:
        rows = [r for r in rows if r.get("run_id") == run_id]
    return rows
