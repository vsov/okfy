"""Secret-shaped values in a proposal, refused before they enter git history.

`meta/memory.jsonl` and every accepted concept are committed to git and
re-sent to an agent every turn (ROADMAP: the resident core is the billed
cost) — a credential that lands there is not a leak that gets rotated once,
it is a standing re-send.

Prefix-anchored rules ONLY. Provider token formats are distinctive enough
that a prefix match is precise; a generic long-string or `key=value` rule
would flag every sha256 this project prints and every `api_key: str`
doc line — measured before, on the same kind of first-cut scanner: 18
findings, 0 true positives over 1611 files. No such rule
ships here. The scan never returns the matched text, only the rule name and
line — the caller must not be able to echo the secret back.
"""
import re

RULES: list[tuple[str, re.Pattern]] = [
    ("openai-style", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("stripe-style", re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    ("aws-key-id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def scan(text: str) -> list[tuple[str, int]]:
    """One pass, line by line. Returns (rule_name, 1-indexed line) pairs —
    never the matched substring."""
    out: list[tuple[str, int]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        for name, pattern in RULES:
            if pattern.search(line):
                out.append((name, n))
    return out


def scan_values(values: list[str]) -> list[tuple[str, int]]:
    """v0.24: scan a list of author-controlled strings — e.g. every
    frontmatter VALUE (walked recursively) plus a proposal's `note`/`reopen`/
    `query` — instead of a YAML dump. PyYAML folds a long plain scalar at
    ~80 columns (the pem-private-key rule's own match text has spaces), and
    `scan`'s per-line match would miss a token whose fold splits it; scanning
    the values themselves never goes through the dump at all.

    Each value is scanned twice: once as given, once with its own internal
    newlines collapsed to a single space, so a fold-shaped split INSIDE one
    value cannot hide a secret either. No rule changes — this only widens
    what text reaches the same `RULES`."""
    out: list[tuple[str, int]] = []
    per_line = "\n".join(v for v in values if v)
    collapsed = "\n".join(re.sub(r"\s*\n\s*", " ", v).strip() for v in values if v)
    for text in (per_line, collapsed):
        out.extend(scan(text))
    # One-line values are identical in both passes; report each finding once.
    return sorted(set(out), key=out.index)
