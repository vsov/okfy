"""Strict YAML frontmatter per OKF spec: every non-reserved .md starts with ---."""
import yaml


class FrontmatterError(ValueError):
    pass


class _NoDuplicateLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys.

    Plain YAML silently keeps the LAST duplicate. The generated pre-commit hook
    cannot run PyYAML — it is `sh` and `okfy` may not be on PATH — so it reads
    write_policy with `sed | head -1`, which keeps the FIRST. Two lines of

        write_policy: direct
        write_policy: proposals

    therefore had the core reporting a gated bundle while the hook permitted the
    direct commit. The cheap general fix is to make the file mean one thing:
    whichever parser reads it, a duplicated key is now a hard error rather than
    a silent choice between two answers."""


def _no_duplicates(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise FrontmatterError(
                f"duplicate frontmatter key {key!r} — YAML keeps the last and "
                "the pre-commit hook reads the first, so a duplicated key means "
                "two different things to two readers")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_NoDuplicateLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    lambda loader, node: _no_duplicates(loader, node))


def parse(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise FrontmatterError("missing frontmatter opening '---'")
    end = text.find("\n---\n", 4)
    if end == -1:
        if text.rstrip("\n").endswith("\n---"):
            end = text.rfind("\n---")
            body_start = len(text)
        else:
            raise FrontmatterError("missing frontmatter closing '---'")
    else:
        body_start = end + len("\n---\n")
    try:
        meta = yaml.load(text[4:end], Loader=_NoDuplicateLoader)  # noqa: S506
    except yaml.YAMLError as e:
        raise FrontmatterError(f"invalid YAML: {e}") from e
    if not isinstance(meta, dict):
        raise FrontmatterError("frontmatter is not a mapping")
    return meta, text[body_start:]


def serialize(meta: dict, body: str) -> str:
    dumped = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{dumped}\n---\n{body}"
