"""`path_scope`: the grammar predicate and the matcher (ADR 0018).

Dependency-free on purpose (stdlib only, no okfy import): the validator, the
write paths and the pre-edit lookup all call `path_scope_problems`, and none of
them may reach a different answer about what a valid value is.

A `path_scope` is a YAML list of corpus-relative POSIX strings:

- an entry ending in `/` is a recursive directory prefix (whole segments:
  `src/a/` matches `src/a/x.c` and never `src/ab.c`);
- an entry without a trailing `/` is an exact file;
- the single entry `./` means the whole root;
- an entry is canonical: no empty segment (`//`) and no `.` segment (`src//a/`,
  `src/./a/`, `src/a/.` are invalid; the owner's value is never rewritten);
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
        if entry != WHOLE_ROOT:
            segs = entry.split("/")
            if segs[-1] == "":                       # a directory entry: one trailing `/`
                segs = segs[:-1]
            if "" in segs:
                problems.append(f"{label} has an empty segment (`//`) — the lookup compares "
                                "against normalised paths and would never match it; write "
                                "single `/` separators")
            if "." in (segs[1:] if entry.startswith("./") else segs):   # `./x` has its own message
                problems.append(f"{label} has a `.` segment — the lookup would never match it; "
                                "drop it (only the single entry `./` may use it)")
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


def entry_contains(entry: str, operand: str) -> bool:
    """Directed containment (R21-6): does `entry` contain EVERYTHING `operand`
    names? Unlike `entry_matches`, which asks whether the two overlap (a file
    inside a directory operand does), this is true only when the whole operand
    lies inside the entry:

    - a file operand is contained by an equal file entry, an ancestor directory
      entry, or `./`;
    - a directory operand (trailing `/`) is contained by the same directory
      entry, an ancestor directory entry, or `./`; a file entry never contains
      a directory, and a directory inside the operand does not either.

    Whole path segments, NFC keys, so `engine/` never contains `engine2/x.py`."""
    e = nfc(entry)
    if e == WHOLE_ROOT:
        return True
    if operand.endswith("/"):
        d = nfc(operand)[:-1]
        return e.endswith("/") and (d == e[:-1] or d.startswith(e))
    p = nfc(operand)
    if e.endswith("/"):
        return p.startswith(e)
    return e == p


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
