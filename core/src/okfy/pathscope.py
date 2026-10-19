"""`path_scope`: the grammar predicate and the matcher (ADR 0018).

Dependency-free on purpose (stdlib only, no okfy import): the validator, the
write paths and the pre-edit lookup all call `path_scope_problems`, and none of
them may reach a different answer about what a valid value is.

A `path_scope` is a YAML list of corpus-relative POSIX strings:

- an entry ending in `/` is a recursive directory prefix (whole segments:
  `src/a/` matches `src/a/x.c` and never `src/ab.c`);
- an entry without a trailing `/` is an exact file;
- the single entry `./` means the whole root;
- `[]` means "reviewed, applies to no path"; an absent key means "unmapped".

There is no glob DSL. Comparison keys are NFC-normalised and case-sensitive.
"""
import unicodedata

GLOB_CHARS = "*?[]"
WHOLE_ROOT = "./"


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _show(x) -> str:
    try:
        text = repr(x)
    except Exception:                                  # a hostile __repr__
        text = f"<{type(x).__name__}>"
    return text if len(text) <= 60 else text[:57] + "..."


def path_scope_problems(value) -> list[str]:
    """Why `value` is not a valid `path_scope`, one message per cause; `[]` when
    it is valid. Never raises. Invalid is neither provably in nor out of scope,
    so every caller fails closed on a non-empty answer."""
    if not isinstance(value, list):
        return [f"path_scope must be a list of corpus-relative path strings, "
                f"got {type(value).__name__} {_show(value)} — write it as a "
                "YAML list, e.g. `path_scope: [src/auth/]`, or `[]` for "
                "reviewed-and-applies-to-no-path"]
    problems: list[str] = []
    seen: dict[str, int] = {}
    for i, entry in enumerate(value):
        if not isinstance(entry, str):
            problems.append(f"entry {i} is not a string ({type(entry).__name__} "
                            f"{_show(entry)}) — every entry is a path string")
            continue
        label = f"entry {i} {_show(entry)}"
        if entry == "":
            problems.append(f"{label} is empty — name a file, a directory ending in `/`, or `./`")
            continue
        if "\x00" in entry:
            problems.append(f"{label} contains a NUL byte")
        if "\\" in entry:
            problems.append(f"{label} contains a backslash — use `/` separators")
        if entry.startswith("/"):
            problems.append(f"{label} is absolute — paths are relative to the corpus root")
        if ".." in entry.split("/"):
            problems.append(f"{label} contains a `..` segment — a scope cannot leave the root")
        bad = [c for c in GLOB_CHARS if c in entry]
        if bad:
            problems.append(f"{label} contains glob character(s) {bad} — there is no glob "
                            "syntax; list a directory prefix ending in `/` or exact files")
        if entry.startswith("./") and entry != WHOLE_ROOT:
            problems.append(f"{label} starts with `./` — drop the prefix; only the single "
                            "entry `./` (whole root) may use it")
        key = nfc(entry)
        if key in seen:
            problems.append(f"{label} duplicates entry {seen[key]}")
        else:
            seen[key] = i
    return problems


# ---------------------------------------------------------------- matching
def entry_matches_file(entry: str, path: str) -> bool:
    """Does `path_scope` entry `entry` cover the file operand `path`? Both are
    compared as NFC keys; `path` is a normalised relative path with no
    trailing slash."""
    e, p = nfc(entry), nfc(path)
    if e == WHOLE_ROOT:
        return True
    if e.endswith("/"):
        return p == e[:-1] or p.startswith(e)
    return e == p


def entry_matches_dir(entry: str, directory: str) -> bool:
    """Does `entry` match the directory operand `directory` (given without its
    trailing slash)? Everything under the directory is touched, so an entry
    matches when it covers the directory itself (by the file rule) or lies
    inside it."""
    if entry_matches_file(entry, directory):
        return True
    e, d = nfc(entry).rstrip("/"), nfc(directory)
    return e == d or e.startswith(d + "/")


def entry_matches(entry: str, operand: str) -> bool:
    """`operand` is a normalised relative path; a trailing `/` marks a directory
    operand."""
    if operand.endswith("/"):
        return entry_matches_dir(entry, operand[:-1])
    return entry_matches_file(entry, operand)


# ---------------------------------------------------------------- input paths
def normalize_relative(raw: str) -> tuple[str | None, str | None]:
    """Lexical normalisation of one relative POSIX operand: drop empty and `.`
    segments, fold `..`. Returns `(normalised, None)` or `(None, reason)` with
    reason one of `empty`, `nul`, `backslash`, `escapes_root`, `names_root`.
    A trailing `/` in `raw` is kept (a directory operand). Absolute inputs are
    the caller's business: they are converted to relative first."""
    if raw == "":
        return None, "empty"
    if "\x00" in raw:
        return None, "nul"
    if "\\" in raw:
        return None, "backslash"
    parts: list[str] = []
    for seg in raw.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None, "escapes_root"
            parts.pop()
            continue
        parts.append(seg)
    if not parts:
        return None, "names_root"
    out = "/".join(parts)
    return (out + "/" if raw.endswith("/") else out), None
