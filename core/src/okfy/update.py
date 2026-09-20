"""Deterministic incremental re-extraction support (ADR-0005): snapshot diff,
affected-concept reverse lookup, snapshot refresh. LLM re-extraction itself is
orchestrated by the /okfy:update plugin command."""
import datetime
import hashlib
import json
from fnmatch import fnmatch
from pathlib import Path

from okfy import frontmatter, memory
from okfy.bundle import Bundle
from okfy.gitenv import run_git
from okfy.init import _corpus_git_sha, _manifest

# `okfy.sourcemap` imports `ANCHOR_LINE_RE`/`heading_spans` from `okfy.validate`,
# and `okfy.validate` imports `_embedded_prefix`/`_source_path` from THIS module
# — a module-level `from okfy.sourcemap import ...` here would close the cycle
# mid-import (validate.py's own `_check_source_map` takes the same lazy-import
# route for the identical reason). So `cited_span`/`span_text` are imported
# locally, inside the two functions that use them.

E_DIFF_PARTIAL_LISTING = "E_DIFF_PARTIAL_LISTING"

SOURCE_PINS = "meta/source-pins.json"
SOURCE_PINS_SCHEMA = "okfy-source-pins@1"


def _snapshot(bundle: Bundle) -> dict:
    c = bundle.get("meta/corpus")
    if c is None:
        raise FileNotFoundError("meta/corpus.md missing — not an OKFy bundle?")
    if c.meta.get("exported"):
        raise ValueError("exported fusion — diff/update/snapshot are not "
                         "supported; re-export from the workspace instead")
    return c.meta


def _embedded_prefix(bundle: Bundle, corpus: Path) -> str | None:
    """For an --embed bundle living inside the corpus, the corpus-relative path
    prefix of the bundle's own files. These must never count as corpus changes
    (the bundle rides the corpus git repo, so git diff would otherwise report
    .okf/** as added). None for standalone bundles outside the corpus."""
    root = bundle.root.resolve()
    corpus = corpus.resolve()
    if root != corpus and root.is_relative_to(corpus):
        return root.relative_to(corpus).as_posix() + "/"
    return None


def corpus_diff(bundle: Bundle) -> dict:
    snap = _snapshot(bundle)
    corpus = Path(snap["corpus"])
    prefix = _embedded_prefix(bundle, corpus)

    def keep(p: str) -> bool:
        return prefix is None or not p.startswith(prefix)

    old_sha = snap.get("git_sha")
    new_sha = _corpus_git_sha(corpus)
    if old_sha and new_sha:
        # git mode: the listing comes from `git diff --name-status` against a
        # committed tree on both ends, so it is complete by construction — git
        # itself refuses to diff against a corpus it cannot read, there is no
        # "silently skipped an unreadable subtree" failure mode the way a bare
        # filesystem walk has. E_DIFF_PARTIAL_LISTING below is manifest-mode-only.
        out = run_git(
            corpus, "diff", "--name-status", "-M", old_sha, "HEAD",
            capture_output=True, text=True, check=True).stdout
        changed, added, removed = [], [], []
        for line in out.splitlines():
            parts = line.split("\t")
            status = parts[0]
            if status.startswith("R"):          # rename: treat as remove+add
                removed.append(parts[1])
                added.append(parts[2])
            elif status == "A":
                added.append(parts[1])
            elif status == "D":
                removed.append(parts[1])
            else:                               # M, T, ...
                changed.append(parts[1])
        return {"mode": "git", "old": old_sha, "new": new_sha,
                "changed": sorted(p for p in changed if keep(p)),
                "added": sorted(p for p in added if keep(p)),
                "removed": sorted(p for p in removed if keep(p)),
                # Not applicable: git tracks a dangling symlink as ordinary
                # committed blob content, not a listing failure — there is
                # nothing here for this count to report (see manifest mode).
                "skipped_dangling_symlinks": 0}
    manifest_file = bundle.root / "meta" / "corpus-manifest.json"
    old = (json.loads(manifest_file.read_text(encoding="utf-8"))
           if manifest_file.is_file() else {})
    errors: list[str] = []
    skipped_symlinks: list[str] = []
    new = _manifest(corpus, errors=errors, skipped_symlinks=skipped_symlinks)
    if errors:
        first = sorted(errors)[0]
        raise ValueError(
            f"{E_DIFF_PARTIAL_LISTING}: the corpus listing is incomplete "
            f"({len(errors)} unreadable path(s), first: {first}) — a partial "
            "listing would report every unlisted file as removed and mark its "
            "concepts stale candidates. Fix the permissions or point "
            "meta/corpus at the right directory, then run okfy diff again")
    if old and not new:
        # The walk itself raised nothing (no permission errors, no missing
        # root — that case IS caught above via os.walk's onerror on a missing
        # top), but came back with zero files where the previous snapshot had
        # some. That is the same blast radius as a partial listing — every
        # previously-known file would read as removed — so it gets the same
        # refusal, not a false "everything was deleted".
        raise ValueError(
            f"{E_DIFF_PARTIAL_LISTING}: the new corpus listing is empty while "
            "the previous snapshot was not — a partial listing would report "
            "every unlisted file as removed and mark its concepts stale "
            "candidates. Check the corpus path in meta/corpus.md, then run "
            "okfy diff again")
    return {"mode": "manifest", "old": None, "new": None,
            "changed": sorted(p for p in old if p in new and old[p] != new[p] and keep(p)),
            "added": sorted(p for p in new if p not in old and keep(p)),
            "removed": sorted(p for p in old if p not in new and keep(p)),
            # v0.24: a dangling symlink (or other non-regular file: FIFO,
            # socket) is skipped, not a listing failure (finding 20) — v0.23's
            # rglob()+is_file() walk skipped these silently; this just makes
            # the count visible instead of reviving the old silence outright.
            "skipped_dangling_symlinks": len(skipped_symlinks)}


def _source_path(src: str) -> str:
    return str(src).split("#", 1)[0]


def affected_concepts(bundle: Bundle, files: list[str]) -> dict[str, list[str]]:
    """Reverse lookup: which concepts cite any of these corpus files in sources:."""
    fset = set(files)
    out: dict[str, list[str]] = {}
    for c in bundle.concepts():
        if c.id.startswith("meta/"):
            continue
        hits = sorted({_source_path(s) for s in (c.meta.get("sources") or [])
                       if _source_path(s) in fset})
        if hits:
            out[c.id] = hits
    return out


def _protected(bundle: Bundle, ids) -> dict[str, bool]:
    """True iff the concept has a non-empty `verified` frontmatter list OR
    meta/memory.jsonl has an `accept` event whose target is that concept.
    Nothing else — `log.md` is prose, not a decision record, and is never
    parsed for this."""
    accepted_targets = {row["target"] for row in memory.events(bundle)[0]
                        if row["event"] == "accept" and row.get("target")}
    out = {}
    for cid in ids:
        c = bundle.get(cid)
        verified = c.meta.get("verified") if c else None
        out[cid] = bool((isinstance(verified, list) and verified)
                        or cid in accepted_targets)
    return out


# --------------------------------------------------------------------------
# Span pins (v0.24, report-only). Built because the pooled anchored
# share over real bundles measured >= 30% (31.6%). ONE parser for anchors:
# `okfy.sourcemap.cited_span` — never restated here.
# --------------------------------------------------------------------------

def _unpinned_reason(source: str, corpus: Path | None) -> str:
    if corpus is None:
        return "corpus is not locally readable"
    path = _source_path(source)
    f = corpus / path
    try:
        f = f.resolve()
    except OSError:
        pass  # fall through to the not-found reason below
    if not (f.is_relative_to(corpus.resolve()) and f.is_file()):
        return "source file not found in the corpus"
    if "#" in source:
        return "anchor does not resolve (not a line range and no matching heading)"
    return "file unreadable as text"


def _anchor_kind(source: str) -> str:
    """Classify a cited source's anchor as "path" (no `#`, cites the whole
    file), "line" (`#L12-L40` or the ledger's dashless `#L12-40`), "char"
    (`#C12-40`), or "heading" (anything else after `#`) — WITHOUT touching the
    corpus. `_classify_spans` uses this to decide how a pin's span must be
    reclassified (finding 18): a bare path or heading anchor claims "the whole
    file" / "everything under this section", so growth of that span is itself
    a substantive change, and only a line/char anchor's FIXED window is the
    right thing to search for having moved. Mirrors `okfy.sourcemap.cited_span`
    is the one parser for the grammar; this reads the same three regexes,
    imported, rather than restating them."""
    from okfy.sourcemap import ANCHOR_CHAR_RE, LEDGER_LINE_RE
    from okfy.validate import ANCHOR_LINE_RE
    _, _, frag = str(source).partition("#")
    if not frag:
        return "path"
    if ANCHOR_LINE_RE.match(frag) or LEDGER_LINE_RE.match(frag):
        return "line"
    if ANCHOR_CHAR_RE.match(frag):
        return "char"
    return "heading"


def build_source_pins(bundle: Bundle, corpus: Path | None) -> dict:
    """{"schema", "pins": [...], "unpinned": [...]} for the CURRENT concept set
    against the CURRENT corpus — what `refresh_snapshot` writes to
    meta/source-pins.json. Every source that `cited_span` resolves to a line
    range (either line-anchor grammar, a char anchor, a bare path, or a
    heading the corpus can resolve) becomes a pin, tagged with its `kind`
    (see `_anchor_kind`) so `_classify_spans` knows how to reclassify it later;
    everything else is reported under `unpinned`, never dropped.

    A source whose resolved file lies outside the corpus (`../secret.txt#L1-L2`
    — `cited_span`'s line-anchor branch resolves this without ever touching
    the file) is routed to `unpinned` rather than read and hashed — a bundle
    only pins what it actually corpus-scopes. Any read failure (missing file,
    binary/non-UTF-8 file, or `corpus is None` while an anchor still resolved)
    is caught and routed to `unpinned` too, instead of raising (finding 14):
    `okfy snapshot` must not crash — and must not leave a half-refreshed
    snapshot — just because one cited file went away."""
    from okfy.sourcemap import cited_span, span_text
    pins, unpinned = [], []
    for c in bundle.concepts():
        if c.id.startswith("meta/"):
            continue
        for s in (c.meta.get("sources") or []):
            s = str(s)
            path, start, end = cited_span(s, corpus)
            if corpus is None or start is None:
                unpinned.append({"concept": c.id, "source": s,
                                 "reason": _unpinned_reason(s, corpus)})
                continue
            f = corpus / path
            try:
                f_resolved = f.resolve()
            except OSError:
                f_resolved = f
            if not f_resolved.is_relative_to(corpus.resolve()):
                unpinned.append({"concept": c.id, "source": s,
                                 "reason": "source file lies outside the corpus"})
                continue
            try:
                text = span_text(f, start, end)
            except (OSError, UnicodeDecodeError, TypeError) as e:
                unpinned.append({"concept": c.id, "source": s,
                                 "reason": f"source file unreadable "
                                          f"({type(e).__name__})"})
                continue
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            pins.append({"concept": c.id, "source": s, "file": path,
                        "start": start, "end": end, "sha256": sha,
                        "kind": _anchor_kind(s)})
    pins.sort(key=lambda p: (p["concept"], p["source"]))
    unpinned.sort(key=lambda p: (p["concept"], p["source"]))
    return {"schema": SOURCE_PINS_SCHEMA, "pins": pins, "unpinned": unpinned}


def _valid_pin_row(row) -> bool:
    """A pin row this module can safely index without KeyError/TypeError
    (finding 21): a dict with non-empty string `concept`/`source`, a string
    `sha256`, and integer (not bool) `start`/`end`."""
    return (isinstance(row, dict)
            and isinstance(row.get("concept"), str) and row["concept"]
            and isinstance(row.get("source"), str) and row["source"]
            and isinstance(row.get("sha256"), str) and row["sha256"]
            and isinstance(row.get("start"), int)
            and not isinstance(row.get("start"), bool)
            and isinstance(row.get("end"), int)
            and not isinstance(row.get("end"), bool))


def _valid_unpinned_row(row) -> bool:
    return (isinstance(row, dict)
            and isinstance(row.get("concept"), str) and row["concept"]
            and isinstance(row.get("source"), str) and row["source"])


def _load_source_pins(bundle: Bundle) -> dict:
    """The CURRENT meta/source-pins.json, degraded to "no usable pins"
    (schema-only, empty pins/unpinned) rather than raised on ANY corruption —
    unreadable file, invalid JSON, wrong schema, or (finding 21) well-formed
    JSON in the wrong SHAPE. A malformed row used to reach `_classify_spans`
    and crash it with a bare KeyError/TypeError; rows are now validated here,
    one at a time, so a single bad row degrades only that row to "no pin"
    instead of taking down `okfy diff` for the whole bundle. The way out is
    the same for every corruption class: `okfy snapshot` rewrites this file
    from the current corpus."""
    empty = {"schema": SOURCE_PINS_SCHEMA, "pins": [], "unpinned": []}
    p = bundle.root / SOURCE_PINS
    if not p.is_file():
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict) or data.get("schema") != SOURCE_PINS_SCHEMA:
        return empty
    return {"schema": SOURCE_PINS_SCHEMA,
            "pins": [r for r in (data.get("pins") or []) if _valid_pin_row(r)],
            "unpinned": [r for r in (data.get("unpinned") or [])
                        if _valid_unpinned_row(r)]}


def _classify_spans(bundle: Bundle, corpus: Path | None, diff: dict,
                    affected: dict[str, list[str]]) -> dict:
    """{concept_id: {source: outcome}} for every affected concept's sources
    that cite a file which actually CHANGED (a removed file's sources are
    covered by `stale_candidates`/`reextract`, not by span classification —
    there is no new file content to search).

    `outcome` is the bare string "span_intact" / "span_changed" / "unpinned",
    or — when it carries extra data — a dict `{"outcome": ..., ...}`:
    `{"outcome": "span_moved", "moved_to": "L<a>-L<b>"}`, or
    `{"outcome": "span_changed", "ambiguous": True}` when the old text now
    matches more than one place. This is a judgment call: the spec names four
    bare-string outcomes plus a sibling `moved_to`/`ambiguous` fact, and a
    string cannot carry a fact, so those two outcomes alone are wrapped.

    How a pin is reclassified depends on its `kind` (finding 18 —
    `_anchor_kind`, recorded on the pin by `build_source_pins`):

    - "path" (bare path, claims the WHOLE file): re-hash the CURRENT whole
      file and compare to the pin. Equal -> `span_intact`; anything else ->
      `span_changed`. Never `span_moved` — there is nowhere else for "the
      whole file" to move to.
    - "heading" (claims everything under that heading): re-resolve the anchor
      against the CURRENT file via `cited_span` and hash THAT span. Equal to
      the pin -> `span_intact`; the heading no longer resolves, or resolves to
      different text -> `span_changed`. This also naturally absorbs a heading
      that moved position in the file (a reordered section), since resolution
      is by heading text, not by line number — so no separate "moved" case.
    - "line" / "char" (a FIXED window, or an old pin with no "kind" — every
      such pin predates this fix and can only be line/char, since bare-path
      and heading pins are new): unchanged from before — hash the OLD
      start/end window of the NEW file first (no search needed, in manifest
      mode none is available: edits happen in place, there is no history to
      read); if that fails, search every same-length window of the new file
      for the old hash. 0 matches -> `span_changed`; >1 -> `span_changed`
      (`ambiguous`); exactly 1 -> `span_moved`."""
    from okfy.sourcemap import cited_span, span_text
    old = _load_source_pins(bundle)
    pins_by_key = {(p["concept"], p["source"]): p for p in old.get("pins") or []}
    unpinned_keys = {(p["concept"], p["source"]) for p in old.get("unpinned") or []}
    changed_files = set(diff["changed"])

    out: dict[str, dict] = {}
    for cid, hit_files in affected.items():
        if not (set(hit_files) & changed_files):
            continue  # every hit is a removed file — nothing to classify
        c = bundle.get(cid)
        if c is None:
            continue
        result: dict = {}
        for s in (c.meta.get("sources") or []):
            s = str(s)
            path = _source_path(s)
            if path not in changed_files:
                continue
            key = (cid, s)
            if key in unpinned_keys or key not in pins_by_key:
                result[s] = "unpinned"
                continue
            pin = pins_by_key[key]
            old_sha = pin["sha256"]
            kind = pin.get("kind", "line")
            f = corpus / path if corpus else None

            if kind == "path":
                if f is None:
                    result[s] = "unpinned"
                    continue
                try:
                    text = f.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError, TypeError):
                    result[s] = "unpinned"
                    continue
                new_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                result[s] = "span_intact" if new_sha == old_sha else "span_changed"
                continue

            if kind == "heading":
                if corpus is None:
                    result[s] = "unpinned"
                    continue
                _, new_start, new_end = cited_span(s, corpus)
                if new_start is None:
                    result[s] = "span_changed"  # the heading no longer resolves
                    continue
                try:
                    text = span_text(f, new_start, new_end)
                except (OSError, UnicodeDecodeError, TypeError):
                    result[s] = "unpinned"
                    continue
                new_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                result[s] = "span_intact" if new_sha == old_sha else "span_changed"
                continue

            # kind in ("line", "char"): fixed-window compare, then search.
            old_start, old_end = pin["start"], pin["end"]
            length = old_end - old_start + 1
            if f is None:
                result[s] = "unpinned"
                continue
            try:
                lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
            except (OSError, UnicodeDecodeError, TypeError):
                result[s] = "unpinned"
                continue
            if old_start >= 1 and old_end <= len(lines):
                window = "".join(lines[old_start - 1:old_end])
                if hashlib.sha256(window.encode("utf-8")).hexdigest() == old_sha:
                    result[s] = "span_intact"
                    continue
            matches = []
            n = len(lines)
            for i in range(0, max(n - length + 1, 0)):
                window = "".join(lines[i:i + length])
                if hashlib.sha256(window.encode("utf-8")).hexdigest() == old_sha:
                    matches.append((i + 1, i + length))
            if len(matches) == 1:
                a, b = matches[0]
                moved_to = f"L{a}" if a == b else f"L{a}-L{b}"
                result[s] = {"outcome": "span_moved", "moved_to": moved_to}
            elif len(matches) > 1:
                result[s] = {"outcome": "span_changed", "ambiguous": True}
            else:
                result[s] = "span_changed"
        if result:
            out[cid] = result
    return out


def _span_outcome(entry) -> str:
    return entry["outcome"] if isinstance(entry, dict) else entry


def _reextract_ids(affected: dict[str, list[str]], diff: dict, spans: dict) -> list[str]:
    """Affected concepts with at least one span_changed/unpinned source, or a
    removed source — the set the update skill re-extracts instead of trusting
    `span_intact`/`span_moved` to mean the text still holds."""
    removed = set(diff["removed"])
    out = []
    for cid, hits in affected.items():
        if set(hits) & removed:
            out.append(cid)
            continue
        if any(_span_outcome(v) in ("span_changed", "unpinned")
              for v in spans.get(cid, {}).values()):
            out.append(cid)
    return sorted(out)


def _reanchor_list(spans: dict) -> list[dict]:
    """[{"concept", "source", "moved_to"}] for every `span_moved` outcome in
    `spans` (finding 19b) — a TODO list for fixing the anchor's TEXT through a
    proposal, kept separate from `reextract` on purpose: `span_moved` means
    the cited text is unchanged and still needs no re-extraction, but its
    `#L..` anchor is now pointing at the wrong lines and needs to be rewritten
    to the current location before the next `okfy snapshot` — see
    `refresh_snapshot`, which itself refuses to silently re-pin a stale
    anchor."""
    out = [{"concept": cid, "source": s, "moved_to": entry["moved_to"]}
          for cid, hits in spans.items()
          for s, entry in hits.items()
          if isinstance(entry, dict) and entry.get("outcome") == "span_moved"]
    out.sort(key=lambda r: (r["concept"], r["source"]))
    return out


def anchored_source_share(bundle: Bundle) -> float | None:
    """Share of the CURRENT meta/source-pins.json's sources that are pinned
    (pins / (pins + unpinned)). None when the bundle has never been snapshotted
    with span pins (no source-pins.json yet)."""
    p = bundle.root / SOURCE_PINS
    if not p.is_file():
        return None
    data = _load_source_pins(bundle)
    total = len(data.get("pins") or []) + len(data.get("unpinned") or [])
    return (len(data.get("pins") or []) / total) if total else None


def update_plan(bundle: Bundle) -> dict:
    diff = corpus_diff(bundle)
    affected = affected_concepts(bundle, diff["changed"] + diff["removed"])

    plan = bundle.plan()
    seg = (plan.meta.get("segmentation") or {}) if plan else {}
    include = seg.get("include") or []
    exclude = seg.get("exclude") or []

    def in_scope(p: str) -> bool:
        if include and not any(fnmatch(p, g) for g in include):
            return False
        return not any(fnmatch(p, g) for g in exclude or [])

    covered: set[str] = set()
    stale_candidates: list[str] = []
    removed_set = set(diff["removed"])
    for c in bundle.concepts():
        if c.id.startswith("meta/"):
            continue
        srcs = {_source_path(s) for s in (c.meta.get("sources") or [])}
        covered |= srcs
        if srcs and srcs <= removed_set:
            stale_candidates.append(c.id)

    snap = bundle.get("meta/corpus")
    corpus = Path(str(snap.meta.get("corpus") or "")) if snap else None
    corpus = corpus if corpus and corpus.is_dir() else None
    spans = _classify_spans(bundle, corpus, diff, affected)

    # Report suggestion only — never the persisted `stale` flag (ADR-0013:
    # staleness is a reviewed owner decision via `okfy stale`, not automatic).
    return {
        "diff": diff,
        "affected": affected,
        "protected": _protected(bundle, affected),
        "spans": spans,
        "reextract": _reextract_ids(affected, diff, spans),
        "reanchor": _reanchor_list(spans),
        "uncovered_new": sorted(p for p in diff["added"]
                                if in_scope(p) and p not in covered),
        "stale_candidates": sorted(stale_candidates),
    }


def refresh_snapshot(bundle: Bundle) -> None:
    """Rewrite meta/corpus-manifest.json, meta/source-pins.json and
    meta/corpus.md to the CURRENT corpus.

    Finding 14: the three files used to be written one at a time, manifest
    first — so a source citing a file that went missing crashed
    `build_source_pins` (unguarded `span_text`) AFTER the manifest was already
    rewritten, leaving corpus-manifest.json pointing at the new corpus state
    while source-pins.json/corpus.md still described the old one. The next
    `okfy diff` then compared new-against-new and reported nothing pending —
    the removed/edited files silently dropped out of the update plan. Fixed
    two ways: `build_source_pins` no longer raises on a missing/unreadable/
    outside-corpus source (routes it to `unpinned` instead), and everything
    below is computed in memory FIRST — manifest, pins, and the corpus.md
    text — with all three files written only after every computation above
    has already succeeded.

    Finding 19a: before overwriting, the OUTGOING pins are classified against
    the CURRENT corpus using the same diff/classify machinery `okfy diff`
    uses. A source whose classification comes back `span_moved` keeps its OLD
    pin (flagged `anchor_stale`, with `moved_to`) instead of being silently
    re-pinned to whatever text now happens to sit at the old line numbers —
    that re-pin is exactly what let a LATER real edit of the cited text read
    back as `span_intact`. A source that comes back `span_changed` DOES take
    the new pin — but flagged `repinned_after_change`, because taking it is
    the owner's own act of running `okfy snapshot`, not a verified match."""
    c = bundle.get("meta/corpus")
    if c.meta.get("exported"):
        raise ValueError("exported fusion — diff/update/snapshot are not "
                         "supported; re-export from the workspace instead")
    corpus_raw = Path(c.meta["corpus"])
    corpus = corpus_raw if corpus_raw.is_dir() else None

    # Classify the outgoing pins against the current corpus (finding 19a).
    # A genuine listing failure (E_DIFF_PARTIAL_LISTING, or the new-listing-
    # went-empty case) must not block `okfy snapshot` — refreshing the
    # snapshot from a fresh walk is itself the recovery path for corpus
    # drift — it just means no anchor-stability information is available
    # this round, same as a bundle's very first snapshot.
    try:
        diff = corpus_diff(bundle)
        affected = affected_concepts(bundle, diff["changed"] + diff["removed"])
        old_outcomes = _classify_spans(bundle, corpus, diff, affected)
    except ValueError:
        old_outcomes = {}

    new_manifest = _manifest(corpus_raw)
    new_pins_doc = build_source_pins(bundle, corpus)
    old_pins_by_key = {(p["concept"], p["source"]): p
                       for p in _load_source_pins(bundle).get("pins") or []}

    final_pins = []
    for pin in new_pins_doc["pins"]:
        key = (pin["concept"], pin["source"])
        entry = old_outcomes.get(pin["concept"], {}).get(pin["source"])
        outcome = _span_outcome(entry) if entry is not None else None
        if outcome == "span_moved" and key in old_pins_by_key:
            kept = dict(old_pins_by_key[key])
            kept["anchor_stale"] = True
            kept["moved_to"] = entry["moved_to"]
            final_pins.append(kept)
        elif outcome == "span_changed" and key in old_pins_by_key:
            taken = dict(pin)
            taken["repinned_after_change"] = True
            final_pins.append(taken)
        else:
            final_pins.append(pin)
    new_pins_doc = {**new_pins_doc, "pins": final_pins}

    new_meta = dict(c.meta)
    new_meta["git_sha"] = _corpus_git_sha(corpus_raw)
    new_meta["extracted_at"] = datetime.date.today().isoformat()
    manifest_text = json.dumps(new_manifest, indent=0, sort_keys=True)
    pins_text = json.dumps(new_pins_doc, indent=2, sort_keys=True,
                           ensure_ascii=False) + "\n"
    corpus_md_text = frontmatter.serialize(new_meta, c.body)

    # Only now write — every payload above already succeeded.
    (bundle.root / "meta" / "corpus-manifest.json").write_text(
        manifest_text, encoding="utf-8")
    (bundle.root / "meta" / "source-pins.json").write_text(
        pins_text, encoding="utf-8")
    c.path.write_text(corpus_md_text, encoding="utf-8")
