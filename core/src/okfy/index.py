import json

from okfy.bm25 import tokenize
from okfy.bundle import CACHE_DIR, Bundle, Concept
from okfy.validate import LINK_RE, resolve_link

FIELD_WEIGHTS = {"title": 3, "aliases": 3, "tags": 2, "description": 2, "body": 1}


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


def build_index(bundle: Bundle) -> dict:
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
    return {"concepts": concepts}


def index_path(bundle: Bundle):
    return bundle.root / CACHE_DIR / "index.json"


def save_index(bundle: Bundle, idx: dict) -> None:
    p = index_path(bundle)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")


def load_index(bundle: Bundle) -> dict:
    p = index_path(bundle)
    if not p.is_file():
        raise FileNotFoundError("index missing — run: okfy index <bundle>")
    return json.loads(p.read_text(encoding="utf-8"))
