"""Owner-mutator verbs (ADR-0007 refinement loop, ADR-0013 reviewed staleness):
agents propose, the owner reviews/refines/flags staleness. accept/refine/reject
and set_stale/clear_stale are the sanctioned mutators — they commit with
--no-verify because they ARE the authority the pre-commit hook defers to."""
import datetime
import re
import subprocess
from pathlib import Path

from okfy import frontmatter
from okfy.bundle import PROPOSAL_DIR, Bundle, Concept
from okfy.package import append_log
from okfy.validate import Report, _check_archetype

ACTIONS = {"create", "update", "delete"}


def _kebab(s: str) -> str:
    out = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return out or "proposal"


def _commit(bundle: Bundle, paths: list[str], message: str) -> None:
    """Sanctioned mutator commit: --no-verify past the policy hook. Silently
    skipped when the bundle has no own git repo (embed bundles ride the corpus
    PR flow instead). Paths are staged one by one with check=False: a deleted
    never-tracked file (e.g. an uncommitted proposal being consumed) is a fatal
    pathspec for `git add` and must not abort staging of the other paths."""
    if not (bundle.root / ".git").exists():
        return
    for p in paths:
        subprocess.run(["git", "-C", str(bundle.root), "add", "-A", "--", p],
                       check=False, capture_output=True)
    # Nothing staged (e.g. rejecting a never-committed proposal touched no
    # tracked file) is benign — skip. A real commit failure must NOT be
    # swallowed: the mutation is already on disk, and leaving it uncommitted
    # would trap the next commit against the policy hook.
    staged = subprocess.run(["git", "-C", str(bundle.root), "diff", "--cached",
                             "--quiet"], capture_output=True)
    if staged.returncode == 0:
        return
    subprocess.run(["git", "-C", str(bundle.root), "commit", "-q", "--no-verify",
                    "-m", message], check=True, capture_output=True)


def propose(bundle: Bundle, meta: dict, body: str, target: str | None = None,
            action: str = "update", note: str = "") -> Path:
    if action not in ACTIONS:
        raise ValueError(f"bad action {action!r} (use: {sorted(ACTIONS)})")
    if action in {"update", "delete"} and not target:
        raise ValueError(f"action {action!r} requires a target concept id")
    meta = dict(meta)
    if action == "delete":
        meta.setdefault("type", "Proposal")
        meta.setdefault("title", f"Delete {target}")
        meta.setdefault("description", note or f"proposal to delete {target}")
    meta["proposal"] = {"action": action, "target": target, "note": note,
                        "created": datetime.date.today().isoformat()}
    pdir = bundle.root / PROPOSAL_DIR
    pdir.mkdir(exist_ok=True)
    base = _kebab(target or str(meta.get("title", "")))
    path = pdir / f"{base}.md"
    n = 2
    while path.exists():
        path = pdir / f"{base}-{n}.md"
        n += 1
    path.write_text(frontmatter.serialize(meta, body), encoding="utf-8")
    return path


def list_proposals(bundle: Bundle) -> list[dict]:
    out = []
    for c in bundle.concepts(include_proposals=True):
        if not c.id.startswith(f"{PROPOSAL_DIR}/"):
            continue
        env = c.meta.get("proposal") or {}
        action = env.get("action", "create")
        target = env.get("target")
        issues: list[str] = []
        target_exists = bool(target) and bundle.get(target) is not None
        if action in {"update", "delete"}:
            if not target:
                issues.append("missing target for " + action)
            elif not target_exists:
                issues.append(f"target does not exist: {target}")
        if action == "create" and target and target_exists:
            issues.append(f"create target already exists: {target}")
        if action != "delete" and not str(c.meta.get("type", "")).strip():
            issues.append("proposed concept has no type")
        out.append({"id": c.id, "action": action, "target": target,
                    "target_exists": target_exists,
                    "note": env.get("note", ""), "created": env.get("created"),
                    "valid": not issues, "issues": issues})
    return sorted(out, key=lambda d: d["id"])


def _load_proposal(bundle: Bundle, proposal_id: str) -> Concept:
    c = bundle.get(proposal_id)
    if c is None or not proposal_id.startswith(f"{PROPOSAL_DIR}/"):
        raise KeyError(f"proposal not found: {proposal_id}")
    return c


def accept(bundle: Bundle, proposal_id: str, archetype=None) -> str:
    c = _load_proposal(bundle, proposal_id)
    env = c.meta.get("proposal") or {}
    action = env.get("action", "create")
    target = env.get("target") or c.id.removeprefix(f"{PROPOSAL_DIR}/")
    existing = bundle.get(target)

    if action == "delete":
        if existing is None:
            raise ValueError(f"delete target does not exist: {target}")
        existing.path.unlink()
        c.path.unlink()
        append_log(bundle, f"review: accept delete {target} ({env.get('note', '')})")
        _commit(bundle, [f"{target}.md", f"{proposal_id}.md", "log.md"],
                f"review: accept delete {target}")
        return target

    if action == "update" and existing is None:
        raise ValueError(f"update target does not exist: {target}")
    if action == "create" and existing is not None:
        raise ValueError(f"create target already exists: {target}")

    meta = dict(c.meta)
    meta.pop("proposal", None)
    if not str(meta.get("type", "")).strip():
        raise ValueError("proposed concept has no type")
    if archetype is not None:
        r = Report()
        probe = Concept(target, bundle.root / f"{target}.md", meta, c.body)
        _check_archetype(probe, archetype, r)
        if not r.ok:
            raise ValueError("; ".join(f"{f.code}: {f.message}" for f in r.errors))

    tpath = bundle.root / f"{target}.md"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(frontmatter.serialize(meta, c.body), encoding="utf-8")
    c.path.unlink()
    append_log(bundle, f"review: accept {action} {target} ({env.get('note', '')})")
    _commit(bundle, [f"{target}.md", f"{proposal_id}.md", "log.md"],
            f"review: accept {action} {target}")
    return target


def reject(bundle: Bundle, proposal_id: str, reason: str = "") -> None:
    c = _load_proposal(bundle, proposal_id)
    c.path.unlink()
    append_log(bundle, f"review: reject {proposal_id} ({reason})")
    _commit(bundle, [f"{proposal_id}.md", "log.md"],
            f"review: reject {proposal_id}")


def refine(bundle: Bundle, concept_id: str, text: str, message: str = "") -> None:
    """Owner-directed direct edit (ADR-0007 channel 1). Validates and commits
    past the policy hook — this verb IS the sanctioned direct door."""
    existing = bundle.get(concept_id)
    if existing is None:
        raise KeyError(f"concept not found: {concept_id}")
    meta, _body = frontmatter.parse(text)          # raises FrontmatterError
    if not str(meta.get("type", "")).strip():
        raise ValueError("refined concept has no type")
    existing.path.write_text(text, encoding="utf-8")
    append_log(bundle, f"refine: {concept_id}" + (f" — {message}" if message else ""))
    _commit(bundle, [f"{concept_id}.md", "log.md"],
            message or f"refine: {concept_id}")


STALE_FIELDS = ("stale", "stale_reason", "stale_since")


def _rewrite_meta(bundle: Bundle, concept: Concept, meta: dict, message: str) -> None:
    concept.path.write_text(frontmatter.serialize(meta, concept.body), encoding="utf-8")
    append_log(bundle, message)
    _commit(bundle, [f"{concept.id}.md", "log.md"], message)


def set_stale(bundle: Bundle, concept_id: str, reason: str) -> None:
    """Reviewed staleness (ADR-0013): 'do not trust this as current'. Set only
    by an owner decision — automation merely reports Concepts as *affected*."""
    c = bundle.get(concept_id)
    if c is None:
        raise KeyError(f"concept not found: {concept_id}")
    if not reason.strip():
        raise ValueError("stale requires a reason — it is a reviewed decision")
    meta = dict(c.meta)
    meta["stale"] = True
    meta["stale_reason"] = reason
    meta["stale_since"] = datetime.date.today().isoformat()
    _rewrite_meta(bundle, c, meta, f"stale: {concept_id} — {reason}")


def clear_stale(bundle: Bundle, concept_id: str) -> None:
    c = bundle.get(concept_id)
    if c is None:
        raise KeyError(f"concept not found: {concept_id}")
    if not any(f in c.meta for f in STALE_FIELDS):
        raise KeyError(f"concept not stale: {concept_id}")
    meta = {k: v for k, v in c.meta.items() if k not in STALE_FIELDS}
    _rewrite_meta(bundle, c, meta, f"stale: clear {concept_id}")
