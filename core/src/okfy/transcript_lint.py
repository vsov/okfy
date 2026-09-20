"""Read a HOST session transcript after the fact and report STRUCTURAL FACTS
ONLY — never a quality verdict.

OKFy's open problem this closes a corner of: every scenario measured so far is
scripted (a fixed sequence of `okfy` calls a test drives). Nothing measures
whether a REAL agent, running inside some host's own CLI, actually searched
the bundle before acting. A host session transcript is the only record of
that. This module reads one and reports what happened, mechanically:

* the ORDER and COUNT of tool calls, classified into four coarse buckets
  (SEARCH / SHOW / PROPOSE / MUTATION);
* which concept-id-shaped strings the assistant's own TEXT named, checked
  against the bundle (shown earlier in-session, exists-but-never-shown, or
  unknown).

No prose matching, no quality verdict, no LLM — a transcript that never calls
`okfy` at all is a completely valid, boring report of zero tool calls. Read
the `label` this module always emits before trusting anything above it: what
is observed here proves tool calls happened, not that the agent understood
anything. It is the same honesty line `okfy_propose`'s `observed` block
already carries for the MCP adapter's own one-process-per-session view (see
GUIDE.md "What the adapter observes") — this is the same idea applied after
the fact, to a transcript the adapter never saw.

## Input shape

Claude Code session JSONL, one JSON object per line. Only this documented
subset is understood — everything else is ignored, not an error:

* `{"type": "assistant", "message": {"content": [...]}}` — content is a list
  of blocks. `{"type": "text", "text": ...}` blocks are scanned for cited
  concept ids; `{"type": "tool_use", "name": ..., "input": {...}}` blocks are
  classified and counted, in the order they appear.
* `{"type": "user", ...}` — ignored. A `tool_result` block inside one is not
  a tool CALL and carries no information this module trusts (it is the
  tool's own claim about what it did, not a call this module observed).
* Anything else (system lines, summaries, unrecognized types) — ignored.

## The Bash mutation heuristic, and its ceiling

`Edit` / `Write` / `NotebookEdit` / `MultiEdit` tool calls are unambiguously
mutations. A `Bash` call is not self-describing, so this module falls back to
a small CLOSED list of mutating starts/operators found anywhere in
`input.command`: ` > `, `>>`, `rm `, `mv `, `cp `, `sed -i`, `git commit`,
`git add`, `tee `. This is a NAIVE heuristic, stated plainly:

* it MISSES any mutation made through a program not on the list (`python
  -c "open(...).write(...)"`, a package manager, an editor macro, `perl -i`,
  ...) — the list is not, and cannot be, complete;
* it can flag a command that merely CONTAINS one of these substrings without
  it being the command's own effect (inside a quoted string, a comment, or a
  subshell that never runs).

The upgrade path is a host-provided mutation flag on the tool call itself,
which this module would trust directly instead of guessing from `command`
text. Until a host provides that, this is what "did the agent mutate
anything" can mean from a transcript alone.

A Bash call running `okfy propose` is classified PROPOSE, never MUTATION,
even though it can also match the naive list (e.g. its own git-add-shaped
text) — the more specific match wins.

A single Bash `command` chaining multiple `okfy` invocations with `&&`,
`||`, `;`, `|`, or a newline (e.g. `okfy query ... && okfy propose ...`)
contributes ONE class per invocation, in textual order — never only the
first match. `searched_before`/`target_shown` (below) are computed from
that full, in-order sequence, so a search or a show earlier in the SAME
Bash call counts.

## Cited-id extraction

A concept-id-shaped string is two or three `/`-separated lower-kebab
segments (`glossary/gamma`, `strategies/widget-straddle`) found in assistant
TEXT. This is shape matching, not comprehension: `and/or` has the same shape
and is not a concept id. The extraction:

* strips `http(s)://` URLs first (a URL's own path segments must never be
  read as a citation);
* excludes anything starting with `meta/` (meta concepts are not usage
  citations);
* excludes a match with a file extension (a trailing `.ext` breaks the
  match, so `docs/readme.md` is not reported) — but a SENTENCE-FINAL period
  (not followed by a word character) does not: `glossary/gamma.` at the end
  of a sentence is still reported as the id `glossary/gamma`;
* excludes filesystem-looking paths — anything immediately preceded by `/`,
  `.` or `~` (so `/Users/x/y`, `./rel/path`, `~/rel/path` never match), and
  anything with more than three `/`-separated segments;
* THEN drops every remaining shape-match whose first segment is not an
  actual top-level directory of the bundle — the filter that turns `and/or`
  into nothing rather than into `unknown`.

What survives is classified per id: `shown-in-session` (a `SHOW` of that
exact id was seen earlier in the scan), `exists-not-shown` (not shown, but
`bundle.get(id)` resolves), or `unknown` (neither).
"""
import json
import re
import shlex
from pathlib import Path

from okfy.bundle import SKIP_DIRS, Bundle

SCHEMA = "okfy-transcript-lint@1"
LABEL = "observed tool calls and id-shaped strings — not what the agent understood"

MUTATION_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}

# The naive closed list, documented above. Substring containment on purpose —
# see the module docstring for exactly what that misses.
BASH_MUTATION_MARKERS = (" > ", ">>", "rm ", "mv ", "cp ", "sed -i",
                         "git commit", "git add", "tee ")

BASH_QUERY_RE = re.compile(r"\bokfy query\b")
BASH_SHOW_RE = re.compile(r"\bokfy show\b")
BASH_PROPOSE_RE = re.compile(r"\bokfy propose\b")

# Shell operators a Bash tool_use's `command` can chain multiple `okfy`
# invocations with — each is its own simple command, classified separately
# (see `_split_bash_command`).
_BASH_SEPARATORS = {"&&", "||", ";", "|", "\n"}

_SEGMENT = r"[a-z0-9]+(?:-[a-z0-9]+)*"
# Trailing boundary: never followed by a word char, '/' or '-' (that would
# extend the id or make it look like a filesystem path); a '.' immediately
# after IS allowed unless a word char follows it (a sentence-final period is
# fine, `docs/readme.md`'s extension is not).
CONCEPT_ID_RE = re.compile(
    rf"(?<![\w/.~-])({_SEGMENT}(?:/{_SEGMENT}){{1,2}})(?![\w/-])(?!\.\w)")
URL_RE = re.compile(r"https?://\S+")


def _top_dirs(bundle: Bundle) -> set[str]:
    """Immediate subdirectories of the bundle root — the universe a cited
    id's first segment must belong to. Read-only: a directory listing."""
    return {p.name for p in bundle.root.iterdir()
           if p.is_dir() and p.name not in SKIP_DIRS}


def _bash_show_concept_id(tokens: list[str]) -> str | None:
    """Best-effort: the first non-flag token after the bundle argument that
    follows `show`, within ONE already-split simple command's own tokens.
    `None` when the command cannot be parsed this way."""
    if "show" not in tokens:
        return None
    i = tokens.index("show")
    rest = tokens[i + 2:]  # skip "show" and its bundle argument
    for tok in rest:
        if not tok.startswith("-"):
            return tok
    return None


def _bash_target(tokens: list[str]) -> str | None:
    """`--target X` or `--target=X`, read from ONE already-split simple
    command's own tokens — never from the whole (possibly chained)
    command, so a second `--target` on a LATER chained `okfy propose` is
    never mistaken for this one's."""
    for i, tok in enumerate(tokens):
        if tok == "--target" and i + 1 < len(tokens):
            return tokens[i + 1].strip("\"'")
        if tok.startswith("--target="):
            return tok[len("--target="):].strip("\"'")
    return None


def _is_bash_mutation(command: str) -> bool:
    return any(marker in command for marker in BASH_MUTATION_MARKERS)


def _split_bash_command(command: str) -> list[list[str]]:
    """Split a Bash `command` string into one TOKEN LIST per simple command,
    on `&&`, `||`, `;`, `|` and newline — the separators a host's Bash tool
    call can chain multiple `okfy` invocations with. shlex-aware: a
    separator INSIDE a quoted argument (`echo "a && b"`) is not a split
    point, and a quoted multi-word argument stays one token (so a later
    re-parse of just that command's own tokens, e.g. `_bash_target`, never
    needs to re-tokenize free text). Falls back to a plain, NOT
    quote-aware, regex split on the same separator text when shlex cannot
    parse the command at all (unbalanced quotes) — an occasional over-split
    there beats silently treating a chained command as one."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=";|&\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return [seg.split() for seg in re.split(r"&&|\|\||[;|\n]", command)
               if seg.strip()]
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in _BASH_SEPARATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _classify_tool_use(block: dict
                       ) -> tuple[list[tuple[str, str | None, str | None]], bool]:
    """(classes, malformed).

    `classes` is EVERY (class, concept_id_for_SHOW, target_for_PROPOSE) this
    ONE tool_use block contributes, in the order its own effect happened —
    almost always a single-item list. A `Bash` `command` chaining multiple
    `okfy` invocations (`&&`, `||`, `;`, `|`, or a newline) contributes one
    entry per invocation, in textual order, instead of only the first match
    winning (see `_split_bash_command`). A v0.24 batch
    `okfy_show(concept_ids=[...])` contributes one SHOW per listed id — every
    one of them was shown, not just a single `concept_id`.

    `malformed` is True when a field that should have been a string (or a
    list of strings) was some other JSON-legal shape instead, and had to be
    treated as absent rather than raise — the caller counts this toward
    `skipped_blocks`/`partial` instead of trusting the corrupted value."""
    name = block.get("name")
    if name is None:
        name = ""
    elif not isinstance(name, str):
        return [], True
    tool_input = block.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    if name in MUTATION_TOOLS:
        return [("MUTATION", None, None)], False
    if name.endswith("okfy_query"):
        return [("SEARCH", None, None)], False
    if name.endswith("okfy_show"):
        malformed = False
        concept_id = tool_input.get("concept_id") or tool_input.get("id")
        if concept_id is not None and not isinstance(concept_id, str):
            concept_id, malformed = None, True
        concept_ids = tool_input.get("concept_ids")
        if isinstance(concept_ids, list) and concept_ids:
            clean_ids = []
            for cid in concept_ids:
                if isinstance(cid, str):
                    clean_ids.append(cid)
                else:
                    malformed = True
            classes = [("SHOW", cid, None) for cid in clean_ids] or [("SHOW", None, None)]
            return classes, malformed
        return [("SHOW", concept_id, None)], malformed
    if name.endswith("okfy_propose"):
        target = tool_input.get("target_id") or tool_input.get("target")
        malformed = False
        if target is not None and not isinstance(target, str):
            target, malformed = None, True
        return [("PROPOSE", None, target)], malformed
    if name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return [], command is not None
        out: list[tuple[str, str | None, str | None]] = []
        for tokens in _split_bash_command(command):
            text = " ".join(tokens)
            # More specific match wins WITHIN a segment: `okfy propose` is
            # never a MUTATION even though it can also contain e.g. a
            # `git add`-shaped substring.
            if BASH_PROPOSE_RE.search(text):
                out.append(("PROPOSE", None, _bash_target(tokens)))
            elif BASH_QUERY_RE.search(text):
                out.append(("SEARCH", None, None))
            elif BASH_SHOW_RE.search(text):
                out.append(("SHOW", _bash_show_concept_id(tokens), None))
        if out:
            return out, False
        # No `okfy` invocation anywhere in this command: fall back to the
        # naive mutation-marker heuristic over the FULL raw text (a marker
        # can straddle a separator, e.g. `foo > bar; baz`, which per-segment
        # text would miss).
        if _is_bash_mutation(command):
            return [("MUTATION", None, None)], False
        return [], False
    return [], False


def _extract_cited_ids(text: str, top_dirs: set[str]) -> list[str]:
    cleaned = URL_RE.sub(" ", text or "")
    out = []
    for m in CONCEPT_ID_RE.finditer(cleaned):
        cid = m.group(1)
        if cid.startswith("meta/"):
            continue
        first = cid.split("/", 1)[0]
        if first not in top_dirs:
            continue
        out.append(cid)
    return out


def lint_transcript(bundle: Bundle, session_path: Path) -> dict:
    """Read-only. Never writes to the bundle, never touches git. Never raises
    on malformed transcript content: a truncated/garbage LINE is counted in
    `skipped_lines`, and well-formed JSON of a shape this module doesn't
    expect (a `message` that isn't a dict, a `name`/`text`/concept-id/target
    that isn't a string where one is expected, ...) is counted in
    `skipped_blocks` instead of raising or trusting a corrupted value. Either
    counter above zero sets `partial` — the report comes back partial, never
    a crash. A genuinely missing session file raises `FileNotFoundError`
    (the CLI turns that into exit 2, same as every other bundle-path
    error)."""
    session_path = Path(session_path)
    if not session_path.is_file():
        raise FileNotFoundError(f"session transcript not found: {session_path}")

    top_dirs = _top_dirs(bundle)

    tool_calls = {"SEARCH": 0, "SHOW": 0, "PROPOSE": 0, "MUTATION": 0}
    first_search = None
    first_mutation = None
    proposes: list[dict] = []
    cited: list[dict] = []
    cited_seen: set[str] = set()
    shown_ids: set[str] = set()
    search_seen = False
    tool_index = 0
    skipped_lines = 0
    skipped_blocks = 0
    partial = False

    raw = session_path.read_text(encoding="utf-8", errors="replace")
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            skipped_lines += 1
            partial = True
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue  # user lines (tool_result included) and anything else
        message = obj.get("message")
        if message is None:
            continue                     # no message at all: nothing to read
        if not isinstance(message, dict):
            skipped_lines += 1            # present but the wrong shape
            partial = True
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")

            if btype == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    skipped_blocks += 1
                    partial = True
                    continue
                for cid in _extract_cited_ids(text, top_dirs):
                    if cid in cited_seen:
                        continue
                    cited_seen.add(cid)
                    if cid in shown_ids:
                        status = "shown-in-session"
                    elif bundle.get(cid) is not None:
                        status = "exists-not-shown"
                    else:
                        status = "unknown"
                    cited.append({"id": cid, "status": status})
                continue

            if btype != "tool_use":
                continue

            classes, malformed = _classify_tool_use(block)
            if malformed:
                skipped_blocks += 1
                partial = True

            for cls, concept_id, target in classes:
                call_index = tool_index
                tool_index += 1

                if cls == "SEARCH":
                    tool_calls["SEARCH"] += 1
                    search_seen = True
                    if first_search is None:
                        first_search = {"index": call_index}
                elif cls == "SHOW":
                    tool_calls["SHOW"] += 1
                    if concept_id:
                        shown_ids.add(concept_id)
                elif cls == "PROPOSE":
                    tool_calls["PROPOSE"] += 1
                    proposes.append({
                        "index": call_index,
                        "searched_before": search_seen,
                        "target_shown": (target in shown_ids) if target else None,
                    })
                elif cls == "MUTATION":
                    tool_calls["MUTATION"] += 1
                    if first_mutation is None:
                        first_mutation = {"index": call_index, "tool": block.get("name")}

    if first_mutation is None:
        searched_before_first_mutation = None
    else:
        searched_before_first_mutation = (
            first_search is not None and first_search["index"] < first_mutation["index"])

    return {
        "schema": SCHEMA,
        "bundle": str(bundle.root),
        "session": str(session_path),
        "tool_calls": tool_calls,
        "searched_before_first_mutation": searched_before_first_mutation,
        "first_mutation": first_mutation,
        "first_search": first_search,
        "proposes": proposes,
        "cited_ids": cited,
        "label": LABEL,
        "partial": partial,
        "skipped_lines": skipped_lines,
        "skipped_blocks": skipped_blocks,
    }
