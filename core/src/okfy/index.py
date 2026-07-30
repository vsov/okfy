import hashlib
import json

from okfy.bm25 import tokenize
from okfy.bundle import CACHE_DIR, Bundle, Concept
from okfy.validate import LINK_RE, resolve_link

FIELD_WEIGHTS = {"title": 3, "aliases": 3, "tags": 2, "description": 2, "body": 1}
INDEX_SCHEMA = "okfy-index@1"


def concept_tokens(c: Concept) -> list[str]:
    fields = {
        "title": str(c.meta.get("title", "")),
        "aliases": " ".join(map(str, c.meta.get("aliases") or [])),
        "tags": " ".join(map(str, c.meta.get("tags") or [])),
        "description": str(c.meta.get("description", "")),
        "body": c.body,
    }
    out: list[str] = []
    for name, weight in FIELD_WEIGHTS.items():
        out.extend(tokenize(fields[name]) * weight)
    return out


def concept_links(bundle: Bundle, c: Concept) -> list[str]:
    ids = []
    for target in LINK_RE.findall(c.body):
        cid = resolve_link(bundle, c.path, target)
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def source_fingerprint(bundle: Bundle) -> str:
    """Digest of every file that ENTERS the index, `meta/*` included.

    Deliberately NOT `validate.package_fingerprint`, which skips meta: the index
    carries meta concepts (query filters them at read time), so a fingerprint
    that ignores them would call a cache fresh after meta/lexicon.md changed.
    Hashes bytes without parsing — the point is to be cheaper than a rebuild
    while still deciding freshness on the same inputs the rebuild would read."""
    lines = [f"{bundle.concept_id(p)}:{hashlib.sha256(p.read_bytes()).hexdigest()}"
             for p in bundle.iter_md_files()]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def content_digest(concepts: list) -> str:
    """Digest of the index payload itself — the index's own identity, used to
    detect a cache whose contents were corrupted or replaced."""
    return hashlib.sha256(
        json.dumps(concepts, sort_keys=True, ensure_ascii=False)
        .encode("utf-8")).hexdigest()


def retrieval_digest(idx: dict) -> str:
    """Digest of the answerable content — the index MINUS meta concepts.

    This is the half that belongs in `retrieval_fingerprint`, and it is narrower
    than `content_digest` on purpose. `meta/*` concepts sit in the index so that
    `query --include-meta` can reach them, but an eval run queries without it, so
    pinning meta bodies would put `meta/lexicon.md`'s PROSE into the acceptance
    contract — and then normalising the lexicon rows buys nothing, because
    rewriting the human rendering underneath them invalidates the eval anyway.
    Pin what the recorded evidence was actually gathered from."""
    return content_digest([c for c in (idx.get("concepts") or [])
                           if not str(c.get("id", "")).startswith("meta/")])


def build_index(bundle: Bundle) -> dict:
    """The deterministic index. `iter_md_files` yields sorted paths, so the same
    bundle bytes always produce the same payload and the same digest."""
    concepts = []
    for c in bundle.concepts():
        e = {
            "id": c.id,
            "type": c.meta.get("type"),
            "title": c.meta.get("title", ""),
            "description": c.meta.get("description", ""),
            "tags": c.meta.get("tags") or [],
            "aliases": c.meta.get("aliases") or [],
            "links": concept_links(bundle, c),
            "tokens": concept_tokens(c),
        }
        if c.meta.get("stale"):        # absent when not stale; readers .get()
            e["stale"] = True          # so pre-v0.5 indexes still load
            e["stale_reason"] = str(c.meta.get("stale_reason") or "")
        concepts.append(e)
    return {"schema": INDEX_SCHEMA,
            "source_fingerprint": source_fingerprint(bundle),
            "content_fingerprint": content_digest(concepts),
            "concepts": concepts}


def index_path(bundle: Bundle):
    return bundle.root / CACHE_DIR / "index.json"


def save_index(bundle: Bundle, idx: dict) -> None:
    """Write the cache AND the digests that make it trustable.

    Only `okfy index` and `okfy package` call this — read commands must stay
    read-only, so a stale cache is worked around in memory rather than silently
    repaired under a reader.

    The manifest half matters as much as the cache half: the expected digests go
    into `meta/package.json`, which is tracked, so re-anchoring what the cache is
    allowed to claim requires a change that shows up in a diff. A cache written
    without updating the manifest is simply refused."""
    p = index_path(bundle)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    mp = bundle.root / "meta" / "package.json"
    if not mp.is_file():
        return          # `okfy package` will write the whole manifest
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    data["index_content_fingerprint"] = idx.get("content_fingerprint")
    data["retrieval_digest"] = retrieval_digest(idx)
    mp.write_text(json.dumps(data, indent=None) + "\n", encoding="utf-8")


def manifest_digests(bundle: Bundle) -> dict:
    """The index digests recorded in the TRACKED `meta/package.json`.

    The cache's own envelope can only prove internal consistency: a payload of
    `{"concepts": []}` carrying the correct `content_fingerprint` of that empty
    list and the correct live `source_fingerprint` satisfied every check the
    envelope could make, so `okfy query` answered nothing while a fresh
    `build_index` — which is what `retrieval_fingerprint` hashes — held six
    concepts. Evidence and fingerprint described different indexes.

    The missing piece is an assertion, made outside the cache, about what
    `build_index` is SUPPOSED to produce. `meta/package.json` is versioned in the
    bundle's git history, so putting the expected digests there means a cache can
    only be trusted against a claim that is itself reviewable in a diff."""
    p = bundle.root / "meta" / "package.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cache_state(bundle: Bundle) -> tuple[str, dict | None]:
    """Why the cache can or cannot be used: `usable`, `missing`, `corrupt`,
    `foreign-schema`, `stale`, or `unmanifested`. Named states rather than a
    bool, because 'cannot use the cache' is a different fact from 'the bundle is
    empty' and the two used to be indistinguishable."""
    p = index_path(bundle)
    if not p.is_file():
        return "missing", None
    try:
        cached = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "corrupt", None
    if not isinstance(cached, dict) or cached.get("schema") != INDEX_SCHEMA:
        return "foreign-schema", None
    concepts = cached.get("concepts")
    if not isinstance(concepts, list):
        return "corrupt", None
    if cached.get("content_fingerprint") != content_digest(concepts):
        return "corrupt", None
    if cached.get("source_fingerprint") != source_fingerprint(bundle):
        return "stale", None
    # ...and the payload must be what the tracked manifest says build_index
    # produces. Without this the envelope only proved the cache agreed with
    # itself.
    expected = manifest_digests(bundle).get("index_content_fingerprint")
    if not expected:
        return "unmanifested", None
    if expected != cached["content_fingerprint"]:
        return "unmanifested", None
    return "usable", cached


def load_index(bundle: Bundle) -> dict:
    """The index to answer from — always one that matches the bundle on disk.

    This used to read `.okfy-cache/index.json` unconditionally, so a missing,
    emptied, corrupt or simply out-of-date cache produced a confidently wrong
    answer (`{"concepts": []}` returned zero hits) while `release-check` stayed
    green. The cache is derived and gitignored; it is a speed-up, never
    evidence. Anything but a verified-fresh cache falls back to a fresh
    in-memory build: slower, and right.

    Note the consequence, which is deliberate: while `meta/package.json` is
    absent or out of date, the cache is refused and every read rebuilds. The
    cache is a speed-up only for a bundle whose package manifest is current."""
    state, cached = cache_state(bundle)
    if state == "usable":
        return cached
    return build_index(bundle)
