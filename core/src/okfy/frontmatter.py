"""Strict YAML frontmatter per OKF spec: every non-reserved .md starts with ---."""
import yaml


class FrontmatterError(ValueError):
    pass


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
        meta = yaml.safe_load(text[4:end])
    except yaml.YAMLError as e:
        raise FrontmatterError(f"invalid YAML: {e}") from e
    if not isinstance(meta, dict):
        raise FrontmatterError("frontmatter is not a mapping")
    return meta, text[body_start:]


def serialize(meta: dict, body: str) -> str:
    dumped = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{dumped}\n---\n{body}"
