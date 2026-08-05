"""One token counter, with the method it used declared in every output.

`okfy cost` and `okfy budget` both need to count tokens. Two callers with two
counters is the defect shape the audits kept finding — a check whose two halves
live in two modules and drift apart. So this module is the single predicate both
of them call, and neither is allowed its own arithmetic.

`tiktoken` is the accurate path, but it is a ~10 MB wheel with a Rust extension
and the core declares PyYAML as its only runtime dependency by design (ADR-0002
lineage: agent-neutral, deterministic, cheap to install). The import is therefore
optional. It is never silent: `token_method()` returns a different string on each
path and every report that prints a token figure prints the method beside it.
"""
import os
from functools import lru_cache
from pathlib import Path

TIKTOKEN_ENCODING = "cl100k_base"

# The conventional English words-to-tokens ratio, and the same constant
# book-to-skill's discovery_tax.py uses. A constant-factor error here does not
# change the RATIO between retrieval strategies, which is what the cost report
# leads with; the absolute figures are labelled estimates on this path.
WORDS_PER_TOKEN = 0.75

METHOD_TIKTOKEN = f"tiktoken:{TIKTOKEN_ENCODING}"
METHOD_HEURISTIC = f"heuristic:words/{WORDS_PER_TOKEN}"


@lru_cache(maxsize=1)
def _encoder():
    """Resolve the encoder once per process.

    Tests that need to exercise the other path clear this with
    `_encoder.cache_clear()` after patching `sys.modules["tiktoken"]`.
    """
    try:
        import tiktoken
    except Exception:
        return None
    try:
        return tiktoken.get_encoding(TIKTOKEN_ENCODING)
    except Exception:
        # tiktoken importable but unusable — no network for the BPE file, or a
        # corrupt download cache. A read-only report degrades to the heuristic
        # and says so; it does not crash and does not pretend to be measured.
        return None


def token_method() -> str:
    """Name the counting method actually in use. Print this next to any count."""
    return METHOD_TIKTOKEN if _encoder() is not None else METHOD_HEURISTIC


def is_measured() -> bool:
    """True when counts come from the real BPE rather than the word heuristic."""
    return _encoder() is not None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _encoder()
    if enc is not None:
        return len(enc.encode(text, disallowed_special=()))
    # Whitespace-only text still occupies context, so it never reports zero:
    # zero is reserved for genuinely empty input.
    words = len(text.split()) or 1
    return max(1, round(words / WORDS_PER_TOKEN))


def count_path(path: Path | str) -> int:
    """Tokens in one file. Undecodable bytes are replaced, not fatal."""
    return count_tokens(Path(path).read_text(encoding="utf-8", errors="replace"))


def count_tree(root: Path | str, *, skip=None) -> dict:
    """Walk `root` and count tokens file by file.

    `skip` is an optional predicate over the POSIX path relative to `root`; it is
    consulted for directories too, so a caller can prune `.okfy-cache` without
    reading it.

    Returns `{files, tokens, bytes, skipped}`. Every path that could not be
    counted lands in `skipped` WITH a reason: a file that cannot be read must
    never contribute a silent zero, because "cannot check" is not "clean".
    Reading is per file — the tree is never concatenated into one string.
    """
    root = Path(root)
    files = tokens = nbytes = 0
    skipped: list[dict] = []

    def note(path: Path, reason: str) -> None:
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = str(path)
        skipped.append({"path": rel, "reason": reason})

    def on_error(err: OSError) -> None:
        note(Path(getattr(err, "filename", None) or root),
             f"unreadable directory: {type(err).__name__}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        base = Path(dirpath)
        rel_base = base.relative_to(root)
        if skip is not None:
            dirnames[:] = [d for d in sorted(dirnames)
                           if not skip((rel_base / d).as_posix())]
        else:
            dirnames.sort()
        for name in sorted(filenames):
            p = base / name
            rel = (rel_base / name).as_posix()
            if skip is not None and skip(rel):
                continue
            if not p.is_file():  # broken symlink, socket, fifo
                note(p, "not a regular file")
                continue
            try:
                data = p.read_bytes()
            except OSError as e:
                note(p, f"unreadable: {type(e).__name__}")
                continue
            files += 1
            nbytes += len(data)
            tokens += count_tokens(data.decode("utf-8", errors="replace"))

    return {"files": files, "tokens": tokens, "bytes": nbytes, "skipped": skipped}
