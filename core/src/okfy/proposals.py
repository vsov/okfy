"""Owner-mutator verbs (ADR-0007 refinement loop, ADR-0013 reviewed staleness):
agents propose, the owner reviews/refines/flags staleness. accept/refine/reject
and set_stale/clear_stale are the sanctioned mutators — they commit with
--no-verify because they ARE the authority the pre-commit hook defers to."""
import contextlib
import datetime
import fcntl
import hashlib
import re
import subprocess
from pathlib import Path

from okfy import frontmatter, memory
from okfy.actor import check_actor, owner_actor, utc_now
from okfy.bundle import PROPOSAL_DIR, Bundle, Concept
from okfy.package import append_log
from okfy.validate import Report, _check_archetype, _norm, archetype_applies, review_due_date

ACTIONS = {"create", "update", "delete"}
# What a proposal says its claim rests on. Closed: an unrecognised kind is a
# refusal, not a new category. Only `agent-inference` may omit `ref` — a test
# run, an owner decision and an external source each have something to point
# at, and a claim to one of them without the pointer is the agent's self-report
# wearing a better label.
EVIDENCE_KINDS = ("test-run", "owner-decision", "external-source", "agent-inference")
E_EVIDENCE = "E_PROPOSAL_EVIDENCE"


E_BASE_MOVED = "E_PROPOSAL_BASE_MOVED"
E_INJECTION = "E_PROPOSAL_INJECTION"
E_REJECTED = "E_PROPOSAL_REJECTED"
E_DUPLICATE = "E_PROPOSAL_DUPLICATE"
W_UNBASED = "W_PROPOSAL_UNBASED"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextlib.contextmanager
def review_lock(bundle: Bundle):
    """One accept or reject at a time per bundle. The base check and the write
    must be one step: checked apart, two accepts from the same base can both
    pass the check and the second still erases the first.

    The lock lives beside the retrieval cache, which is already gitignored —
    an untracked lock under proposals/ would dirty every bundle's worktree."""
    from okfy.index import index_path
    lock = index_path(bundle).parent / "review.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield lock
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def content_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def check_evidence(evidence) -> dict | None:
    if evidence is None:
        return None
    how = "pass it as `okfy propose --evidence <kind>=<ref>`"
    if not isinstance(evidence, dict):
        raise ValueError(f"{E_EVIDENCE}: evidence must be a mapping with kind and ref — {how}")
    unknown = sorted(set(evidence) - {"kind", "ref"})
    if unknown:
        raise ValueError(f"{E_EVIDENCE}: unknown evidence key(s) {unknown}, only kind and ref — {how}")
    kind = evidence.get("kind")
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"{E_EVIDENCE}: kind {kind!r} is not one of "
                         f"{list(EVIDENCE_KINDS)} — {how}")
    ref = str(evidence.get("ref") or "").strip()
    if kind != "agent-inference" and not ref:
        raise ValueError(f"{E_EVIDENCE}: {kind} evidence needs a ref (a run id, "
                         f"commit, decision or URL) — `--evidence {kind}=<ref>`")
    return {"kind": kind, "ref": ref} if ref else {"kind": kind}


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
            action: str = "update", note: str = "", *, actor: str,
            evidence: dict | None = None, extends: str | None = None,
            reopen: str | None = None) -> Path:
    """File a change into proposals/. `actor` is required and has no default: a
    proposal whose author is a guess records nothing worth keeping.

    `extends` turns a create into an update of that existing concept — the way
    out of E_PROPOSAL_DUPLICATE. `reopen` states what changed since an identical
    text was rejected — the way out of E_PROPOSAL_REJECTED."""
    if extends is not None:
        if bundle.get(extends) is None:
            raise ValueError(f"--extends target does not exist: {extends}")
        target, action = extends, "update"
    if action not in ACTIONS:
        raise ValueError(f"bad action {action!r} (use: {sorted(ACTIONS)})")
    if action in {"update", "delete"} and not target:
        raise ValueError(f"action {action!r} requires a target concept id")
    actor = check_actor(actor)
    meta = dict(meta)
    if "verified" in meta:
        raise ValueError(f"{E_EVIDENCE}: a proposal cannot carry `verified` — "
                         "verification is written only by `okfy review accept`")
    ev = check_evidence(evidence if evidence is not None else meta.get("evidence"))
    meta.pop("evidence", None)
    if ev is not None:
        meta["evidence"] = ev
    _gate(bundle, meta, body, target, action, reopen)
    meta["generated"] = {"by": actor, "at": utc_now()}
    if action == "delete":
        meta.setdefault("type", "Proposal")
        meta.setdefault("title", f"Delete {target}")
        meta.setdefault("description", note or f"proposal to delete {target}")
    meta["proposal"] = {"action": action, "target": target, "note": note,
                        "created": datetime.date.today().isoformat()}
    # The bytes this change was written against. accept refuses if they moved.
    tfile = bundle.root / f"{target}.md" if target else None
    if action in {"update", "delete"} and tfile is not None and tfile.is_file():
        meta["proposal"]["base_sha256"] = _file_sha256(tfile)
    pdir = bundle.root / PROPOSAL_DIR
    pdir.mkdir(exist_ok=True)
    base = _kebab(target or str(meta.get("title", "")))
    path = pdir / f"{base}.md"
    n = 2
    while path.exists():
        path = pdir / f"{base}-{n}.md"
        n += 1
    path.write_text(frontmatter.serialize(meta, body), encoding="utf-8")
    memory.record(bundle, "propose", actor=actor, proposal=bundle.concept_id(path),
                  target=target, action=action, content_sha256=content_sha256(body),
                  base_sha256=meta["proposal"].get("base_sha256"),
                  reopen=reopen.strip() if reopen else None)
    return path



def _gate(bundle: Bundle, meta: dict, body: str, target, action: str, reopen) -> None:
    """Three refusals, cheapest first. Each names its way out."""
    from okfy.injection import scan_text
    label = f"{PROPOSAL_DIR}/{target or meta.get('title', '')}"
    found = scan_text(label, frontmatter.serialize(meta, body))
    if found:
        f = found[0]
        raise ValueError(
            f"{E_INJECTION}: {len(found)} injection finding(s) — {f.rule} at line "
            f"{f.line}: {f.excerpt}. Agent-written memory is refused, not filed; "
            "if the text is legitimate the owner can write it with `okfy refine`")

    events, problems = memory.events(bundle)
    if problems:
        raise ValueError(
            f"{problems[0]}; the rejected-content gate cannot trust "
            f"{memory.MEMORY_FILE} while it has unreadable lines — `okfy validate` "
            "lists each one; the owner repairs the ledger, then propose again")

    if action != "delete":
        prior = memory.rejected_hashes(bundle).get(content_sha256(body))
        if prior is not None and not (reopen and str(reopen).strip()):
            raise ValueError(
                f"{E_REJECTED}: this exact text was rejected by {prior['actor']} on "
                f"{str(prior['at'])[:10]} ({prior.get('reason') or 'no reason given'}) "
                "— re-proposing it unchanged repeats a closed decision. If something "
                'changed, say what: `okfy propose ... --reopen "<new evidence>"`')

    # Title against existing titles AND aliases; proposed aliases are never
    # compared. Measured on the real bundles before this gate was written
    # (.supergoal/evidence/measurements-v23.md): title=title 0-5 pairs per
    # bundle, existing-alias=title 0-39, but alias=alias 556 on rayforce-api-okf
    # and 847 on sec-cftc-sfp-okf — aliases hold categories and authorities, so
    # that branch would refuse ordinary proposals.
    if action == "create" and str(meta.get("title", "")).strip():
        want = _norm(meta["title"])
        for c in bundle.concepts():
            if c.id.startswith("meta/") or c.id == target:
                continue
            as_ = ("title" if _norm(c.meta.get("title", "")) == want else
                   "alias" if want in {_norm(a) for a in c.meta.get("aliases") or []}
                   else None)
            if as_:
                raise ValueError(
                    f"{E_DUPLICATE}: {meta['title']!r} already names {c.id} (as its "
                    f"{as_}) — to add to that concept pass `--extends {c.id}`; if "
                    "this is a different thing, give it a title that says how")


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
        base = env.get("base_sha256")
        base_moved = (None if not base or not target_exists
                      else base != _file_sha256(bundle.root / f"{target}.md"))
        out.append({"id": c.id, "action": action, "target": target,
                    "target_exists": target_exists, "base_moved": base_moved,
                    "note": env.get("note", ""), "created": env.get("created"),
                    "valid": not issues, "issues": issues})
    return sorted(out, key=lambda d: d["id"])


def _load_proposal(bundle: Bundle, proposal_id: str) -> Concept:
    c = bundle.get(proposal_id)
    if c is None or not proposal_id.startswith(f"{PROPOSAL_DIR}/"):
        raise KeyError(f"proposal not found: {proposal_id}")
    return c


def accept(bundle: Bundle, proposal_id: str, archetype=None) -> str:
    with review_lock(bundle):
        return _accept(bundle, proposal_id, archetype)


def _accept(bundle: Bundle, proposal_id: str, archetype=None) -> str:
    c = _load_proposal(bundle, proposal_id)
    env = c.meta.get("proposal") or {}
    action = env.get("action", "create")
    target = env.get("target") or c.id.removeprefix(f"{PROPOSAL_DIR}/")
    existing = bundle.get(target)

    # Exempt by construction: a proposal filed before v0.23 carries no base, so
    # there is nothing to compare. It is accepted and the log says so; it is
    # never refused, and a PRESENT base that no longer matches always is.
    unbased = ""
    if action in {"update", "delete"} and existing is not None:
        base = env.get("base_sha256")
        if base is None:
            unbased = (f" {W_UNBASED}: filed before base_sha256 existed, accepted without a "
                       "lost-update check; re-file with `okfy propose` to get one")
        elif base != _file_sha256(existing.path):
            raise ValueError(
                f"{E_BASE_MOVED}: {target} changed after {proposal_id} was "
                "written against it — accepting would erase that change. Re-read "
                f"the concept, file a fresh proposal (`okfy propose <bundle> "
                f"--target {target} --action {action} --as <actor>`), then "
                f"`okfy review reject <bundle> {proposal_id}`")

    if action == "delete":
        if existing is None:
            raise ValueError(f"delete target does not exist: {target}")
        existing.path.unlink()
        c.path.unlink()
        memory.record(bundle, "accept", actor=owner_actor(bundle), proposal=proposal_id,
                      target=target, action=action, content_sha256=content_sha256(c.body))
        append_log(bundle, f"review: accept delete {target} ({env.get('note', '')})"
                           f"{unbased}")
        _commit(bundle, [f"{target}.md", f"{proposal_id}.md", "log.md", memory.MEMORY_FILE],
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
    # Same predicate the validator uses. Applying the archetype schema to a
    # `meta/*` target refused every proposal against purpose.md, corpus.md or
    # the extraction plan for a missing `description` that `okfy validate` does
    # not require — which locked a `write_policy: proposals` bundle out of the
    # one flow its own pre-commit hook tells the owner to use.
    if archetype is not None and archetype_applies(target):
        r = Report()
        probe = Concept(target, bundle.root / f"{target}.md", meta, c.body)
        _check_archetype(probe, archetype, r)
        if not r.ok:
            raise ValueError("; ".join(f"{f.code}: {f.message}" for f in r.errors))

    # Authorship survives an update: the concept's first author stays in
    # `generated`, and who proposed THIS text is recorded on the verification
    # that accepted it. Each verification names the bytes it verified, so a
    # later edit leaves it historical instead of silently transferring it.
    proposed_by = (meta.get("generated") or {}).get("by") \
        if isinstance(meta.get("generated"), dict) else None
    prior: list = []
    if existing is not None:
        if isinstance(existing.meta.get("generated"), dict):
            meta["generated"] = existing.meta["generated"]
        if isinstance(existing.meta.get("verified"), list):
            prior = list(existing.meta["verified"])
    entry = {"by": owner_actor(bundle), "at": utc_now(),
             "content": content_sha256(c.body)}
    if proposed_by:
        entry["proposed_by"] = proposed_by
    meta["verified"] = prior + [entry]

    tpath = bundle.root / f"{target}.md"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(frontmatter.serialize(meta, c.body), encoding="utf-8")
    c.path.unlink()
    memory.record(bundle, "accept", actor=entry["by"], proposal=proposal_id,
                  target=target, action=action, content_sha256=entry["content"])
    append_log(bundle, f"review: accept {action} {target} ({env.get('note', '')})"
                       f"{unbased}")
    _commit(bundle, [f"{target}.md", f"{proposal_id}.md", "log.md", memory.MEMORY_FILE],
            f"review: accept {action} {target}")
    return target


def reject(bundle: Bundle, proposal_id: str, reason: str = "") -> None:
    with review_lock(bundle):
        _reject(bundle, proposal_id, reason)


def _reject(bundle: Bundle, proposal_id: str, reason: str = "") -> None:
    c = _load_proposal(bundle, proposal_id)
    env = c.meta.get("proposal") or {}
    c.path.unlink()
    memory.record(bundle, "reject", actor=owner_actor(bundle), proposal=proposal_id,
                  target=env.get("target"), action=env.get("action", "create"),
                  content_sha256=content_sha256(c.body), reason=reason or None)
    append_log(bundle, f"review: reject {proposal_id} ({reason})")
    _commit(bundle, [f"{proposal_id}.md", "log.md", memory.MEMORY_FILE],
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


def overdue(bundle: Bundle) -> list[dict]:
    """Concepts whose `review_due` has passed, most overdue first. A listing,
    never a flag: nothing here writes `stale` or anything else."""
    today = datetime.date.today()
    out = []
    for c in bundle.concepts():
        d = review_due_date(c.meta.get("review_due")) if "review_due" in c.meta else None
        if d is not None and d < today:
            out.append({"id": c.id, "review_due": d.isoformat(),
                        "days_overdue": (today - d).days})
    return sorted(out, key=lambda x: (-x["days_overdue"], x["id"]))


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
