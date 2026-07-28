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
    return subprocess.run(["git", "-C", str(bundle.root), *args],
                          capture_output=True, text=True)


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


def _resolve_ref(bundle: Bundle, ref: str | None) -> tuple[str, str | None]:
    """(state, resolved-sha). state is 'ok', 'unreachable-ref', or 'git-error'."""
    if not _is_git_repo(bundle):
        return ("git-error", None)
    if ref is None:
        log = _git(bundle, "log", "--format=%H", "--diff-filter=D", "--", "drafts/*")
        if log.returncode != 0:
            return ("git-error", None)
        shas = log.stdout.split()
        if not shas:
            return ("unreachable-ref", None)
        ref = f"{shas[0]}^"
    probe = _git(bundle, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if probe.returncode != 0:
        return ("unreachable-ref", None)
    return ("ok", probe.stdout.strip())


def recover_drafts(bundle: Bundle, draft_ids, ref: str | None = None
                   ) -> tuple[str, dict[str, dict]]:
    """(state, {draft_id: {'meta': dict, 'body': str}}).

    Resolution order: the working tree first (drafts are still live between
    `okfy cluster` and consolidation), then git history at the auto-detected
    parent of the commit that deleted `drafts/`. An EXPLICIT `ref` overrides the
    working tree entirely — a caller who names a ref means that ref, and quietly
    auditing something else would be a fail-open surprise. state is one of
    'live', 'git', 'unreachable-ref', 'git-error'. A draft that cannot be read
    or parsed is simply absent from the mapping — the caller reports its group
    as unverifiable rather than as clean."""
    ids = list(draft_ids)
    if not ids:
        return ("live", {})

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
        return ("live", out)

    state, sha = _resolve_ref(bundle, ref)
    if state != "ok":
        return (state, {})
    out = {}
    for did in ids:
        show = _git(bundle, "show", f"{sha}:{did}.md")
        if show.returncode != 0:
            continue
        _parse(did, show.stdout, out)
    return ("git", out)


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

    Returns {state, recovery, ref, groups_total, groups_with_findings, findings,
    unverifiable, by_kind, notes}. `state` is 'live' when drafts came from the
    working tree, 'ok' when they were recovered from git, and otherwise the
    failure state ('no-merge-map', 'unreachable-ref', 'git-error'). Any failure
    state guarantees a non-empty `unverifiable` list: this function never reports
    "no findings" for work it could not actually inspect."""
    out = {"state": "ok", "recovery": None, "ref": None, "groups_total": 0,
           "groups": [], "groups_with_findings": 0, "findings": [],
           "unverifiable": [], "by_kind": dict.fromkeys(KINDS, 0), "notes": []}

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
    rstate, drafts = recover_drafts(bundle, draft_ids, ref=ref)
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
        _, sha = _resolve_ref(bundle, ref)
        out["ref"] = sha

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
