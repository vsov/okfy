from dataclasses import dataclass
from pathlib import Path

from okfy import frontmatter

RESERVED_FILES = {"index.md", "log.md"}
DRAFT_DIR = "drafts"
PROPOSAL_DIR = "proposals"
CACHE_DIR = ".okfy-cache"
SKIP_DIRS = {CACHE_DIR, ".git"}
ROOT_DOC_FILES = {"README.md", "AGENTS.md", "CLAUDE.md"}  # generated docs, not concepts
# v0.24: generated, non-resident output directories. `index/` holds the
# per-directory listings `okfy package --shard-index` moves out of index.md;
# `protocols/` holds packaged discipline text (e.g. protocols/memory.md) that
# AGENTS.md only points at. Both are written by `okfy package`, never by an
# owner or agent, so they are reserved the same way index.md/log.md are:
# excluded from concept discovery unconditionally, at the bundle root only.
RESERVED_DIRS = {"index", "protocols"}
# What `okfy init` writes: a bundle is known by these, not by its folder name,
# because `--embed` may put one at any path inside the corpus.
BUNDLE_MARKERS = {"meta/purpose.md": "Purpose", "meta/corpus.md": "CorpusSnapshot"}


def is_bundle_dir(path) -> bool:
    """The marker files with the frontmatter `type` init gives them: a corpus
    folder that merely has its own meta/purpose.md and meta/corpus.md is corpus."""
    for rel, typ in BUNDLE_MARKERS.items():
        try:
            meta, _ = frontmatter.parse((Path(path) / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):            # absent, undecodable, not frontmatter
            return False
        if meta.get("type") != typ:
            return False
    return True


def drop_bundle_paths(root, rels) -> list[str]:
    """`rels` (posix, relative to `root`) minus every path inside a bundle
    nested under `root` — a corpus listing must not read a bundle as corpus."""
    seen: dict[str, bool] = {}

    def inside(rel: str) -> bool:
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d not in seen:
                seen[d] = is_bundle_dir(Path(root) / d)
            if seen[d]:
                return True
        return False
    return [r for r in rels if not inside(r)]


@dataclass
class Concept:
    id: str
    path: Path
    meta: dict
    body: str


class Bundle:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"bundle root not found: {self.root}")

    def iter_md_files(self, include_drafts=False, include_proposals=False):
        for p in sorted(self.root.rglob("*.md")):
            rel = p.relative_to(self.root)
            parts = rel.parts
            if parts[0] in SKIP_DIRS or parts[0] in RESERVED_DIRS or p.name in RESERVED_FILES:
                continue
            if len(parts) == 1 and p.name in ROOT_DOC_FILES:
                continue
            if parts[0] == DRAFT_DIR and not include_drafts:
                continue
            if parts[0] == PROPOSAL_DIR and not include_proposals:
                continue
            yield p

    def concept_id(self, path: Path) -> str:
        return path.relative_to(self.root).with_suffix("").as_posix()

    def load(self, path: Path) -> Concept:
        meta, body = frontmatter.parse(path.read_text(encoding="utf-8"))
        return Concept(self.concept_id(path), path, meta, body)

    def concepts(self, include_drafts=False, include_proposals=False) -> list[Concept]:
        return [self.load(p) for p in self.iter_md_files(include_drafts, include_proposals)]

    def get(self, cid: str) -> Concept | None:
        p = (self.root / f"{cid}.md").resolve()
        if not p.is_relative_to(self.root) or not p.is_file():
            return None
        return self.load(p)

    def purpose(self) -> dict:
        c = self.get("meta/purpose")
        return c.meta if c else {}

    def plan(self) -> Concept | None:
        return self.get("meta/extraction-plan")
