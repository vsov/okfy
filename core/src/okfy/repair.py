"""Deterministic dangling-link repair. Blind parallel workers predict final
concept paths (ADR-0008); consolidation sometimes names them differently.
This module rewrites unambiguous near-misses and reports the rest."""
from difflib import SequenceMatcher

from okfy import frontmatter
from okfy.bundle import Bundle
from okfy.validate import LINK_RE, resolve_link

SIM_THRESHOLD = 0.85


def find_dangling(bundle: Bundle) -> list[dict]:
    ids = {c.id for c in bundle.concepts()}
    out = []
    for c in bundle.concepts():
        for target in LINK_RE.findall(c.body):
            cid = resolve_link(bundle, c.path, target)
            if cid is not None and cid not in ids:
                out.append({"concept": c.id, "raw": target, "missing": cid})
    return out


def _basename(cid: str) -> str:
    return cid.rsplit("/", 1)[-1]


def suggest_target(missing: str, ids: set[str]) -> tuple[str | None, str]:
    base = _basename(missing)
    exact = sorted(i for i in ids if _basename(i) == base)
    if len(exact) == 1:
        return exact[0], "basename"
    if len(exact) > 1:
        return None, "ambiguous"
    close = sorted(
        (i for i in ids
         if SequenceMatcher(None, base, _basename(i)).ratio() >= SIM_THRESHOLD))
    if len(close) == 1:
        return close[0], "fuzzy"
    if len(close) > 1:
        return None, "ambiguous"
    return None, "unresolved"


def repair_links(bundle: Bundle, apply: bool = True) -> dict:
    ids = {c.id for c in bundle.concepts()}
    rewritten, ambiguous, unresolved = [], [], []
    by_concept: dict[str, list[dict]] = {}
    for d in find_dangling(bundle):
        by_concept.setdefault(d["concept"], []).append(d)
    for cid, items in by_concept.items():
        c = bundle.get(cid)
        body = c.body
        for d in items:
            target, how = suggest_target(d["missing"], ids)
            if target is None:
                (ambiguous if how == "ambiguous" else unresolved).append(d)
                continue
            anchor = ("#" + d["raw"].split("#", 1)[1]) if "#" in d["raw"] else ""
            body = body.replace(f"({d['raw']})", f"(/{target}.md{anchor})")
            rewritten.append({**d, "target": target, "how": how})
        if apply and body != c.body:
            c.path.write_text(frontmatter.serialize(c.meta, body), encoding="utf-8")
    return {"rewritten": rewritten, "ambiguous": ambiguous, "unresolved": unresolved}
