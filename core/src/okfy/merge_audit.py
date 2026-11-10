"""Shadow consolidation audit: a deterministic diff between the Draft Concepts a
run produced and the final concepts consolidation merged them into.

Consolidation is the one pipeline step that destroys information without leaving
an artifact — `okfy cluster` groups drafts, the merge judge picks one survivor,
and whatever the losers held disappears. The ledger's `merge_map` records WHAT
merged; nothing records what was dropped on the way. This module reconstructs the
groups from `merge_map`, recovers the drafts (from the working tree while they are
still there, or from git history after the consolidate commit deleted them), and
reports asymmetric loss on machine-comparable fields.

ZERO NETWORK, ZERO MODEL CALLS. Only git, the filesystem, and okfy's own modules.
Free-text body comparison is deliberately out of scope: paraphrase-vs-real-loss is
not machine-decidable and trying makes the report noise. What IS compared is
structure — `sources`, archetype-declared enum fields, link targets, and date or
numeric literals in frontmatter.

This is a REPORT, not a gate. It never returns an exit code that fails a build and
it is not wired into `okfy release-check`. Findings are candidates for owner
attention, not proven defects: a consolidator may drop a source deliberately
because a sibling draft carried the same citation more precisely.

Recovery states are explicit and fail closed. `unreachable-ref` and `git-error`
produce a non-empty `unverifiable` list, never an empty-findings "clean" result —
the audit-round-8 defect (a failed `git diff` collapsing to `[]` and reading as
"nothing changed") is the exact failure this module must not reproduce.
"""
import re
import subprocess

from okfy import frontmatter
from okfy.bundle import Bundle
from okfy.gitenv import run_git
from okfy.ledger import read_rows
from okfy.validate import LINK_RE, resolve_link

KINDS = ("lost-source", "enum-collapse", "lost-link", "lost-date")

# Temporally-material literals in frontmatter values: ISO dates, plausible years,
# and percentages. Deliberately NOT bare integers — measured against the 33 merge
# groups of a real regulatory bundle, an unrestricted numeric pattern produced 854
# hits of which the overwhelming majority were citation fragments ("67 FR 53146",
# "Rule 41.22", "17 CFR 242"), i.e. noise that would bury the real signal. Rates
# keep their `%` because a margin percentage IS the temporal fact in this domain.
_LITERAL_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\b(?:19|20)\d{2}\b|\d+(?:\.\d+)?%")

# Fields excluded from the literal scan. `sources` carries line anchors
# (`path#L120-L140`) whose numbers mean nothing here and whose loss has its own
# check. `aliases` are retrieval synonyms — 83% of the raw literal hits on a real
# bundle came from citation numerals inside them, and an alias is a retrieval
# concern rather than a temporal one.
_LITERAL_SKIP_FIELDS = {"sources", "aliases"}


def _git(bundle: Bundle, *args: str) -> subprocess.CompletedProcess:
    return run_git(bundle.root, *args, capture_output=True, text=True)


def _is_git_repo(bundle: Bundle) -> bool:
    return _git(bundle, "rev-parse", "--git-dir").returncode == 0


def merge_groups(bundle: Bundle) -> tuple[str, list[dict]]:
    """(state, groups) where each group is {'final': id, 'drafts': [id, ...]}.

    Unions `merge_map` across every ledger row and inverts it. Only groups with
    two or more drafts are returned — a one-to-one draft is not a merge and has
    nothing to lose. state is 'ok' when at least one row carried a merge_map,
    'no-merge-map' when none did (which includes 'no ledger at all')."""
    merged: dict[str, str] = {}
    for row in read_rows(bundle):
        mm = row.get("merge_map")
        if isinstance(mm, dict):
            merged.update(mm)
    if not merged:
        return ("no-merge-map", [])
    inverted: dict[str, list[str]] = {}
    for draft, final in merged.items():
        inverted.setdefault(str(final), []).append(str(draft))
    groups = [{"final": f, "drafts": sorted(d)}
              for f, d in inverted.items() if len(d) >= 2]
    return ("ok", sorted(groups, key=lambda g: g["final"]))


# A commit id as git prints it: 40 hex digits, or 64 in a SHA-256 repository.
_SHA_RE = re.compile(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def _commit(bundle: Bundle, rev: str) -> str | None:
    """The commit `rev` names, or None when it names none."""
    probe = _git(bundle, "rev-parse", "--verify", f"{rev}^{{commit}}")
    return probe.stdout.strip() if probe.returncode == 0 else None


def _deletions(bundle: Bundle) -> tuple[str, dict[str, list[str]]]:
    """(state, {draft path: [sha, ...]}): every commit that deleted each draft
    path, newest first. state is 'ok' or 'git-error'.

    One `git log` over `drafts/` lists each deleting commit with the draft
    paths it deleted.

    `--full-history`: with a path filter git simplifies history by default, and
    at a merge whose tree matches one parent for `drafts/*` it follows that
    parent only. A bundle consolidated on a branch and merged with a merge
    commit into a line that never held `drafts/` then lost its deletion commit
    (vsov/okfy#10), and a re-extraction merged that way recovered the PREVIOUS
    run's drafts.

    `--diff-merges=first-parent`: a merge can itself delete `drafts/` (the
    consolidation done while resolving it). With no diff for merges, git never
    listed it and the lookup fell back to an OLDER deletion, auditing the
    previous run's drafts. Diffed against its first parent, such a merge is
    the deletion. A merge that brings in a branch's deletion is listed only
    when its first parent still held drafts, and that parent is then the
    state the deletion removed.

    `--date-order`: deletions on every merged line are listed, and the first
    one listed for a path counts as its newest. Commit dates alone do not
    promise that (clock skew, rebased commits); this order never shows a
    commit before its descendants and falls back to dates only between lines
    that are not ancestors of each other.

    `--no-renames`: a draft moved inside `drafts/` is a deletion of its path.
    `--relative`: paths relative to the bundle root, which an `--embed`
    bundle does not share with its repository. `-z`: paths verbatim.

    The output is each commit id, then a line feed and its NUL-terminated
    paths. A token that is neither fails the lookup closed as 'git-error':
    skipping it could hide the deletion a draft should be read at, and the
    draft would be read at an older one."""
    log = run_git(bundle.root, "log", "-z", "--format=%H", "--full-history",
                  "--date-order", "--diff-merges=first-parent", "--no-renames",
                  "--relative", "--name-only", "--diff-filter=D",
                  "--", "drafts/*", capture_output=True)
    if log.returncode != 0:
        return ("git-error", {})
    out: dict[str, list[str]] = {}
    sha = None
    for tok in log.stdout.split(b"\0"):
        tok = tok.removeprefix(b"\n")
        if not tok:
            continue
        if _SHA_RE.fullmatch(tok):
            sha = tok.decode("ascii")
        elif sha is not None and tok.startswith(b"drafts/"):
            out.setdefault(tok.decode("utf-8", "surrogateescape"), []).append(sha)
        else:
            return ("git-error", {})
    return ("ok", out)


def _newest_common(lists: list[list[str]]) -> str | None:
    """The newest sha present in every list. Each list is newest first and
    taken from the same `git log`, so the first common sha of any one of them
    is the newest."""
    rest = [set(shas) for shas in lists[1:]]
    return next((s for s in lists[0] if all(s in r for r in rest)), None)


def _deletion_per_draft(bundle: Bundle, ids: list[str],
                        deletions: dict[str, list[str]]) -> dict[str, str]:
    """{draft_id: sha of the commit that deleted it}; the draft is read at
    that commit's parent. A draft no commit deleted is left out.

    Each consolidation run deletes its own drafts, so one commit for the whole
    bundle reads only the last run's: a merge group that an `/okfy:update`
    grew held drafts of both runs and read as unverifiable (vsov/okfy#15).
    The newest deletion of each path is not the answer either, because a
    draft id is not unique across runs: re-extraction reuses ids, and two
    branches can each extract and delete the same id with different content
    when only one of their rows maps it. So the drafts are resolved per
    ledger row:

    - a draft belongs to the last row whose `merge_map` names it, the
      "later row wins" rule `merge_groups` applies;
    - a row's drafts are read at the newest commit that deleted all of them
      (a draft that no commit deleted does not count). That is the row's
      consolidation, or a re-extraction that deleted the same ids again;
    - if no one commit deleted all of them (deleted piecemeal), each draft of
      that row is read at the newest deletion of its own path, and so is a
      draft that no row names."""
    rows: list[list[str]] = []
    owner: dict[str, int] = {}
    for row in read_rows(bundle):
        mm = row.get("merge_map")
        if isinstance(mm, dict) and mm:
            rows.append([str(d) for d in mm])
            for d in rows[-1]:
                owner[d] = len(rows) - 1

    def deleted(did: str) -> list[str]:
        return deletions.get(f"{did}.md", [])

    common: dict[int, str | None] = {}
    out: dict[str, str] = {}
    for did in ids:
        if not deleted(did):
            continue
        i = owner.get(did)
        if i is not None and i not in common:
            common[i] = _newest_common([deleted(d) for d in rows[i] if deleted(d)])
        out[did] = (common[i] if i is not None else None) or deleted(did)[0]
    return out


def recover_drafts(bundle: Bundle, draft_ids, ref: str | None = None
                   ) -> tuple[str, dict[str, dict]]:
    """(state, {draft_id: {'meta': dict, 'body': str}}).

    Resolution order: the working tree first (drafts are still live between
    `okfy cluster` and consolidation), then git history, each draft at the
    parent of the commit that deleted it (see `_deletion_per_draft`). An
    EXPLICIT `ref` overrides the working tree and that lookup entirely — a
    caller who names a ref means that ref, and quietly auditing something else
    would be a fail-open surprise. state is one of 'live', 'git',
    'unreachable-ref', 'git-error'. A draft that cannot be read or parsed is
    simply absent from the mapping — the caller reports its group as
    unverifiable rather than as clean."""
    state, drafts, _ = _recover(bundle, draft_ids, ref)
    return (state, drafts)


def _recover(bundle: Bundle, draft_ids, ref: str | None
             ) -> tuple[str, dict[str, dict], dict[str, str]]:
    """`recover_drafts`, plus {draft_id: sha} for each draft looked up in git:
    the commit it was read at, whether or not it could be read there."""
    ids = list(draft_ids)
    if not ids:
        return ("live", {}, {})

    def _parse(did: str, text: str, out: dict) -> None:
        try:
            meta, body = frontmatter.parse(text)
        except (frontmatter.FrontmatterError, ValueError):
            return
        out[did] = {"meta": meta if isinstance(meta, dict) else {}, "body": body}

    paths = {did: bundle.root / f"{did}.md" for did in ids}
    if ref is None and all(p.is_file() for p in paths.values()):
        out: dict[str, dict] = {}
        for did, p in paths.items():
            try:
                _parse(did, p.read_text(encoding="utf-8"), out)
            except OSError:
                continue
        return ("live", out, {})

    if not _is_git_repo(bundle):
        return ("git-error", {}, {})
    if ref is not None:
        sha = _commit(bundle, ref)
        if sha is None:
            return ("unreachable-ref", {}, {})
        at = dict.fromkeys(ids, sha)
    else:
        state, deletions = _deletions(bundle)
        if state != "ok":
            return (state, {}, {})
        if not deletions:
            return ("unreachable-ref", {}, {})
        parent: dict[str, str | None] = {}
        at = {}
        for did, sha in _deletion_per_draft(bundle, ids, deletions).items():
            if sha not in parent:
                parent[sha] = _commit(bundle, f"{sha}^")
            if parent[sha] is not None:
                at[did] = parent[sha]
        if parent and not at:
            # every deletion found has no parent to read (a shallow clone)
            return ("unreachable-ref", {}, {})
    out = {}
    for did, sha in at.items():
        # `./`: in `<rev>:<path>` git reads <path> from the repository root,
        # not the current directory. An --embed bundle sits below that root,
        # so the bare form failed on every draft and this returned ('git', {})
        # (vsov/okfy#11); `./` makes it relative to the bundle root, which is
        # the repository root for a standalone bundle.
        show = _git(bundle, "show", f"{sha}:./{did}.md")
        if show.returncode != 0:
            continue
        _parse(did, show.stdout, out)
    return ("git", out, at)


def _load_archetype(bundle: Bundle):
    """(archetype-or-None, note-or-None). A bundle whose plan names no archetype,
    or names one that will not load, skips the enum check with a note — it never
    crashes and never silently passes."""
    plan = bundle.plan()
    name = (plan.meta.get("archetype") if plan else None)
    if not name:
        return (None, "no archetype in meta/extraction-plan.md — "
                      "enum-collapse check skipped")
    try:
        from okfy.archetype import load_archetype
        return (load_archetype(str(name)), None)
    except (FileNotFoundError, KeyError, ValueError) as e:
        return (None, f"archetype {name!r} did not load ({e}) — "
                      "enum-collapse check skipped")


def _source_parts(entry) -> tuple[str, str]:
    text = str(entry).strip()
    path, _, anchor = text.partition("#")
    return (path.strip(), anchor.strip())


def _links_of(bundle: Bundle, cid: str, body: str) -> list[str]:
    """Link targets a concept points at, resolved to concept ids using the same
    helper the validator and index use. Works for drafts recovered from git:
    resolve_link is pure path arithmetic and does not stat the file."""
    path = bundle.root / f"{cid}.md"
    out = []
    for target in LINK_RE.findall(body):
        rid = resolve_link(bundle, path, target)
        if rid and rid not in out:
            out.append(rid)
    return out


def _literals_of(meta: dict) -> set[str]:
    """Date-shaped and numeric literals in frontmatter VALUES. Frontmatter only —
    body prose is out of scope. `sources` is skipped: its line anchors are
    numbers that mean nothing here and the source itself has its own check."""
    found: set[str] = set()
    for field, value in (meta or {}).items():
        if field in _LITERAL_SKIP_FIELDS:
            continue
        items = value if isinstance(value, (list, tuple)) else [value]
        for item in items:
            if isinstance(item, (dict, list, tuple)):
                continue
            found.update(_LITERAL_RE.findall(str(item)))
    return found


def _enum_fields(archetype) -> set[str]:
    fields: set[str] = set()
    for per_type in (getattr(archetype, "field_enums", None) or {}).values():
        fields.update(per_type.keys())
    return fields


def _audit_group(bundle: Bundle, group: dict, drafts: dict[str, dict],
                 enum_fields: set[str]) -> tuple[list[dict], list[str]]:
    """(findings, notes) for one merge group. Every finding names the final
    concept, the drafts that held the lost value, and the value itself."""
    final = bundle.get(group["final"])
    findings: list[dict] = []
    notes: list[str] = []
    fmeta = final.meta or {}

    # lost-source: union of the drafts' sources minus the final's, compared on
    # the path part. A draft citing `a.md#L1-L9` against a final citing `a.md`
    # is a match on path — recorded as an anchor note, not a lost source.
    final_paths = {_source_parts(s)[0] for s in (fmeta.get("sources") or [])}
    final_anchors = {_source_parts(s) for s in (fmeta.get("sources") or [])}
    for did in group["drafts"]:
        for entry in ((drafts.get(did, {}).get("meta") or {}).get("sources") or []):
            path, anchor = _source_parts(entry)
            if not path:
                continue
            if path not in final_paths:
                findings.append({"kind": "lost-source", "group": group["final"],
                                 "drafts": [did], "value": str(entry),
                                 "detail": "source cited by a draft, absent from "
                                           "the merged concept"})
            elif anchor and (path, anchor) not in final_anchors:
                notes.append(f"{group['final']}: anchor narrowed — draft {did} "
                             f"cited {path}#{anchor}")

    # enum-collapse: drafts disagreeing on an archetype-declared closed vocabulary
    # while the final keeps exactly one value.
    for field in sorted(enum_fields):
        by_value: dict[str, list[str]] = {}
        for did in group["drafts"]:
            v = (drafts.get(did, {}).get("meta") or {}).get(field)
            if v in (None, ""):
                continue
            by_value.setdefault(str(v), []).append(did)
        if len(by_value) < 2:
            continue
        kept = str(fmeta.get(field)) if fmeta.get(field) is not None else None
        for value, holders in sorted(by_value.items()):
            if value != kept:
                findings.append({
                    "kind": "enum-collapse", "group": group["final"],
                    "drafts": sorted(holders), "value": f"{field}: {value}",
                    "detail": f"drafts disagreed on {field}; merged concept kept "
                              f"{kept!r}"})

    # lost-link: link targets a draft pointed at that the final no longer does.
    # Only targets that RESOLVE to a concept still in the bundle count. Drafts
    # routinely link to ids the worker anticipated and consolidation then renamed
    # or absorbed; measured on two real bundles, 78% of raw hits were such
    # never-live targets. A dangling draft link is a different defect (and the
    # validator already reports dangling links on finals) — counted as a note.
    final_links = set(_links_of(bundle, final.id, final.body))
    dangling = 0
    for did in group["drafts"]:
        d = drafts.get(did)
        if d is None:
            continue
        for target in _links_of(bundle, did, d["body"]):
            if target in final_links:
                continue
            if bundle.get(target) is None:
                dangling += 1
                continue
            findings.append({"kind": "lost-link", "group": group["final"],
                             "drafts": [did], "value": target,
                             "detail": "link target present in a draft, "
                                       "absent from the merged concept"})
    if dangling:
        notes.append(f"{group['final']}: {dangling} draft link(s) pointed at ids "
                     f"absent from the bundle (never-live targets, not counted)")

    # lost-date: date/numeric literals in draft frontmatter absent from the final's.
    final_literals = _literals_of(fmeta)
    for did in group["drafts"]:
        d = drafts.get(did)
        if d is None:
            continue
        for lit in sorted(_literals_of(d["meta"]) - final_literals):
            findings.append({"kind": "lost-date", "group": group["final"],
                             "drafts": [did], "value": lit,
                             "detail": "literal in draft frontmatter, absent "
                                       "from the merged concept's frontmatter"})
    return (findings, notes)


def audit_merge(bundle: Bundle, ref: str | None = None,
                only_group: str | None = None) -> dict:
    """Compare every multi-draft merge group against the concept it produced.

    Returns {state, recovery, ref, refs, groups_total, groups_with_findings,
    findings, unverifiable, by_kind, notes}. `state` is 'live' when drafts came
    from the working tree, 'ok' when they were recovered from git, and otherwise
    the failure state ('no-merge-map', 'unreachable-ref', 'git-error'). Any
    failure state guarantees a non-empty `unverifiable` list: this function never
    reports "no findings" for work it could not actually inspect.

    `refs` maps each draft looked up in git to the commit it was read at. Drafts
    of different consolidation runs are read at different commits, so `ref` is
    that commit only when it is the same for every draft, and None otherwise:
    one commit named for all of them would send a reader to the wrong drafts."""
    out = {"state": "ok", "recovery": None, "ref": None, "refs": {},
           "groups_total": 0, "groups": [], "groups_with_findings": 0,
           "findings": [], "unverifiable": [],
           "by_kind": dict.fromkeys(KINDS, 0), "notes": []}

    gstate, groups = merge_groups(bundle)
    if gstate != "ok":
        out["state"] = gstate
        out["unverifiable"] = [{"group": "*", "reason":
                                "no merge_map in any ledger row — merge groups "
                                "cannot be reconstructed"}]
        out["notes"].append("nothing was audited: run `okfy ledger add "
                            "--merge-map ...` at consolidation to record it")
        return out

    if only_group is not None:
        selected = [g for g in groups if g["final"] == only_group]
        if not selected:
            out["unverifiable"] = [{"group": only_group, "reason":
                                    "no multi-draft merge group with this final "
                                    "concept id"}]
            out["notes"].append(f"--group {only_group} matched no merge group of "
                                f"{len(groups)} found")
            return out
        groups = selected

    # the groups themselves, not just their count: an adjudication workflow needs
    # the draft membership of every group, including the ones with no findings —
    # zero findings is a claim to be tested, not a group to skip
    out["groups"] = [{"final": g["final"], "drafts": list(g["drafts"])}
                     for g in groups]
    out["groups_total"] = len(groups)
    draft_ids = sorted({d for g in groups for d in g["drafts"]})
    rstate, drafts, refs = _recover(bundle, draft_ids, ref)
    out["recovery"] = rstate
    if rstate in ("unreachable-ref", "git-error"):
        out["state"] = rstate
        reason = ("pre-consolidation ref could not be resolved"
                  if rstate == "unreachable-ref"
                  else "bundle is not a usable git repository")
        out["unverifiable"] = [{"group": g["final"], "reason": reason}
                               for g in groups]
        out["notes"].append(f"{len(groups)} group(s) UNVERIFIED — {reason}. "
                            "This is not a clean result.")
        return out

    out["state"] = "live" if rstate == "live" else "ok"
    if rstate == "git":
        out["refs"] = refs
        shas = set(refs.values())
        out["ref"] = shas.pop() if len(shas) == 1 else None

    archetype, anote = _load_archetype(bundle)
    if anote:
        out["notes"].append(anote)
    enum_fields = _enum_fields(archetype) if archetype else set()

    for g in groups:
        if bundle.get(g["final"]) is None:
            out["unverifiable"].append({"group": g["final"], "reason":
                                        "merged concept not found in the bundle"})
            continue
        missing = [d for d in g["drafts"] if d not in drafts]
        if missing:
            out["unverifiable"].append({"group": g["final"], "reason":
                                        f"{len(missing)} of {len(g['drafts'])} "
                                        f"draft(s) unreadable: "
                                        f"{', '.join(sorted(missing))}"})
            continue
        findings, notes = _audit_group(bundle, g, drafts, enum_fields)
        out["findings"].extend(findings)
        out["notes"].extend(notes)

    for f in out["findings"]:
        out["by_kind"][f["kind"]] += 1
    out["groups_with_findings"] = len({f["group"] for f in out["findings"]})
    return out
