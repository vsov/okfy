"""Read a HOST session transcript after the fact and report STRUCTURAL FACTS
ONLY — never a quality verdict.

OKFy's open problem this closes a corner of: every scenario measured so far is
scripted (a fixed sequence of `okfy` calls a test drives). Nothing measures
whether a REAL agent, running inside some host's own CLI, actually searched
the bundle before acting. A host session transcript is the only record of
that. This module reads one and reports what happened, mechanically:

* the ORDER and COUNT of tool calls, classified into four coarse buckets
  (SEARCH / SHOW / PROPOSE / MUTATION), plus a fifth, UNKNOWN, for a Bash
  call whose `command` text this module could not reliably tokenize at
  all;
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
mutations. A `Bash` call is not self-describing, so a `command` string is
first split into simple commands (`_split_bash_command`, on `&&`, `||`,
`;`, `|`, and newline) and EACH segment is classified independently, by the
command actually being run — never by regex-matching the segment's joined
argument text. A segment is:

* an `okfy` invocation when its OWN `tokens[0]` is `okfy` (bare, or a path
  ending in `/okfy`, e.g. `/usr/local/bin/okfy`, `./okfy`) and `tokens[1]`
  is `query` / `show` / `propose` — classified SEARCH / SHOW / PROPOSE.
  `printf '%s\n' 'okfy query ...'` is NOT a search: the command run is
  `printf`, and the query text is only an argument it prints;
* otherwise checked against a small CLOSED list of mutating
  starts/operators found anywhere in the segment's own text: ` > `, `>>`,
  `rm `, `mv `, `cp `, `sed -i`, `git commit`, `git add`, `tee ` — MUTATION
  on a hit. This is a NAIVE heuristic, stated plainly:
  * it MISSES any mutation made through a program not on the list (`python
    -c "open(...).write(...)"`, a package manager, an editor macro,
    `perl -i`, ...) — the list is not, and cannot be, complete;
  * it can flag a segment that merely CONTAINS one of these substrings
    without it being the segment's own effect (inside a quoted string, a
    comment, or a subshell that never runs).

The upgrade path is a host-provided mutation flag on the tool call itself,
which this module would trust directly instead of guessing from `command`
text. Until a host provides that, this is what "did the agent mutate
anything" can mean from a transcript alone.

A Bash call running `okfy propose` is classified PROPOSE, never MUTATION,
even though its own argument text can also match the naive mutation list
(e.g. a `--note` containing git-add-shaped text) — an `okfy` invocation
recognized by position always wins over the naive marker check, WITHIN
that one segment.

A single Bash `command` chaining multiple simple commands with `&&`, `||`,
`;`, `|`, or a newline (e.g. `printf changed > file; okfy query ...`)
contributes ONE class per segment, independently — recognizing an `okfy`
invocation in one segment never discards another segment's own
MUTATION (or any other class). `searched_before`/`target_shown` (below)
are computed from that full, in-order sequence, so a search or a show
earlier in the SAME Bash call counts.

`_split_bash_command` tokenizes with `shlex`, TWICE — once in POSIX mode
(quotes stripped, the tokens every other function in this module reads)
and once in non-POSIX mode (quotes left in place on the token text) — and
compares the two token-by-token. `shlex(..., posix=True)` strips quoting
*before* a token is checked against the separator set, so a quoted `;`
argument (`printf '%s\n' ';' ...`) arrives indistinguishable, by the time
it is a bare string `";"`, from a real `;` chaining two commands: the POSIX
pass alone cannot tell them apart. The non-POSIX pass can, because it never
strips the quote characters — a quoted `';'` stays the three-character
token `"';'"`, which can never equal the bare separator. A token counts as
a real separator only when BOTH passes agree it is one (the POSIX token
equals the non-POSIX token exactly, meaning nothing was quoted around it);
the dequoted POSIX token is still what every other function in this module
reads, so ordinary quoted arguments behave exactly as before. When the two
passes tokenize `command` into a different NUMBER of tokens (a rarer
ambiguity than the quoted-separator case above, e.g. a backslash-escaped
separator outside quotes, which the two shlex modes disagree on how to
split), position can no longer be matched between them at all, and this is
treated the same as an unparseable command: classified UNKNOWN rather than
guessed at. This is two stdlib `shlex` passes and a positional diff, not a
shell interpreter — it recovers quote PROVENANCE, not shell semantics
(pipelines, subshells, variable expansion, and the rest of a real shell
grammar are still entirely out of scope, as before).

When `command` has unbalanced quotes, shlex cannot parse it at all (in
either mode), and this module falls back to a naive, NOT quote-aware,
regex split. Token POSITION from that fallback is not trustworthy (the
words may not correspond to real shell arguments at all), so no
conclusion — okfy invocation or otherwise — is drawn from it: the whole
Bash call is classified UNKNOWN instead, a third state distinct from both
"searched" and "no search". A caller that reads UNKNOWN as "searched"
learns nothing from it; this module instead reads it as neither confirming
nor denying a search happened, so an UNKNOWN segment never sets
`search_seen`, `first_search`, or `first_mutation` — it also never counts
as a MUTATION it can't actually see. The cost is that a real search or
mutation inside an unparseable (or ambiguously escaped) command goes
uncounted rather than guessed at.

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


def _is_okfy_invocation(tokens: list[str]) -> bool:
    """True when `tokens[0]` — the command actually being run in this ONE
    already-split simple command — IS `okfy`: bare, or a path ending in
    `/okfy` (`/usr/local/bin/okfy`, `./okfy`). Never a substring match
    against later argument text: `printf '%s\n' 'okfy query ...'` is
    `printf`, not an okfy invocation, however its arguments read."""
    return bool(tokens) and (tokens[0] == "okfy" or tokens[0].endswith("/okfy"))


def _classify_okfy_invocation(tokens: list[str]
                              ) -> tuple[str, str | None, str | None] | None:
    """The (class, concept_id, target) for ONE already-split simple
    command's tokens, when it is recognized as `okfy query` / `okfy show` /
    `okfy propose` by position (see `_is_okfy_invocation`; `tokens[1]` is
    the subcommand). `None` when `tokens[0]` is not `okfy` at all, OR it is
    `okfy` running some OTHER subcommand this module does not track
    (`okfy sync`, ...) — in neither case does `None` mean "not a
    mutation": the caller still checks the naive marker heuristic on this
    segment's own text."""
    if not _is_okfy_invocation(tokens) or len(tokens) < 2:
        return None
    sub = tokens[1]
    if sub == "propose":
        return ("PROPOSE", None, _bash_target(tokens))
    if sub == "query":
        return ("SEARCH", None, None)
    if sub == "show":
        return ("SHOW", _bash_show_concept_id(tokens), None)
    return None


def _classify_bash_segment(tokens: list[str]
                           ) -> tuple[str, str | None, str | None] | None:
    """ONE already-split simple command's own classification: an `okfy`
    invocation recognized by position wins when there is one (see
    `_classify_okfy_invocation`) — including over its OWN argument text
    that happens to also match the naive mutation-marker list (e.g. an
    `okfy propose --note 'git add screenshot'`). Otherwise, the naive
    marker heuristic runs on THIS segment's own text only, so one
    segment's conclusion never depends on another's. `None` when neither
    applies — this segment contributes nothing this module tracks."""
    okfy_class = _classify_okfy_invocation(tokens)
    if okfy_class is not None:
        return okfy_class
    if _is_bash_mutation(" ".join(tokens)):
        return ("MUTATION", None, None)
    return None


def _is_bash_mutation(command: str) -> bool:
    return any(marker in command for marker in BASH_MUTATION_MARKERS)


def _lex(command: str, *, posix: bool) -> list[str]:
    """One `shlex` pass over `command`, split on whitespace plus the chain
    separators (`_BASH_SEPARATORS`). `posix=True` is the dequoted token
    stream every other function in this module reads; `posix=False` keeps
    quote characters ON the token text, which is what lets
    `_split_bash_command` tell a quoted separator-shaped argument apart
    from a real one (see its docstring). Raises `ValueError` exactly when
    shlex cannot tokenize `command` at all (e.g. unbalanced quotes)."""
    lex = shlex.shlex(command, posix=posix, punctuation_chars=";|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    return list(lex)


def _naive_split(command: str) -> list[list[str]]:
    """The NOT quote-aware fallback split, used only when shlex cannot
    tokenize `command` at all in the relevant mode(s) — see
    `_split_bash_command`."""
    return [seg.split() for seg in re.split(r"&&|\|\||[;|\n]", command)
           if seg.strip()]


def _split_bash_command(command: str) -> tuple[list[list[str]], bool]:
    """Split a Bash `command` string into one TOKEN LIST per simple command,
    on `&&`, `||`, `;`, `|` and newline — the separators a host's Bash tool
    call can chain multiple `okfy` invocations with.

    Quote-PROVENANCE-aware, not just quote-aware: a separator-shaped token
    is only treated as a real split point when a SECOND, non-POSIX shlex
    pass over the same command agrees it was not sitting inside quotes (see
    the module docstring's "The Bash mutation heuristic" section for why
    the POSIX pass alone cannot tell `echo "a && b"`'s `&&` apart from a
    quoted, standalone `';'` argument to `printf`). A quoted multi-word
    argument still stays one (dequoted) token in the returned segments, so
    a later re-parse of just that command's own tokens, e.g.
    `_bash_target`, never needs to re-tokenize free text.

    Returns `(segments, reliable)`. `reliable` is False when shlex could
    not tokenize `command` at all in POSIX mode (unbalanced quotes), OR
    when the POSIX and non-POSIX passes disagree on how many tokens
    `command` splits into at all (an ambiguity the module docstring names,
    e.g. a backslash-escaped separator outside quotes) — in either case no
    conclusion drawn from token POSITION is trustworthy, including
    recognizing an `okfy` invocation. Callers must treat an unreliable
    split as UNKNOWN, never as "no `okfy` call found here"."""
    try:
        tokens = _lex(command, posix=True)
        raw_tokens = _lex(command, posix=False)
    except ValueError:
        return _naive_split(command), False
    if len(tokens) != len(raw_tokens):
        # The two passes could not even be lined up position-for-position —
        # no quote-provenance conclusion is possible; fall back exactly as
        # for an unparseable command (see docstring).
        return _naive_split(command), False

    segments: list[list[str]] = []
    current: list[str] = []
    for tok, raw in zip(tokens, raw_tokens):
        # A separator-shaped token is a REAL separator only when it arrived
        # bare in the non-POSIX pass too (raw == tok): a quoted `';'`
        # survives non-POSIX tokenization WITH its quote characters
        # (`"';'"`), which can never equal the bare operator string.
        if tok in _BASH_SEPARATORS and raw == tok:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments, True


def _classify_tool_use(block: dict
                       ) -> tuple[list[tuple[str, str | None, str | None]], bool]:
    """(classes, malformed).

    `classes` is EVERY (class, concept_id_for_SHOW, target_for_PROPOSE) this
    ONE tool_use block contributes, in the order its own effect happened —
    almost always a single-item list. A `Bash` `command` chaining multiple
    simple commands (`&&`, `||`, `;`, `|`, or a newline) contributes one
    entry per SEGMENT, classified independently (see `_split_bash_command`
    and `_classify_bash_segment`) — recognizing an `okfy` invocation in one
    segment never discards another segment's own class, e.g. a MUTATION. A
    Bash `command` shlex cannot tokenize at all (unbalanced quotes)
    contributes exactly one `("UNKNOWN", None, None)` for the whole call,
    never a guess drawn from unreliable token positions. A v0.24 batch
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
        segments, reliable = _split_bash_command(command)
        if not reliable:
            # shlex could not tokenize this command at all (unbalanced
            # quotes): no conclusion drawn from token POSITION is
            # trustworthy, so this reports UNKNOWN rather than a confident
            # "no search happened" — see `_split_bash_command` and the
            # module docstring.
            return [("UNKNOWN", None, None)], False
        out: list[tuple[str, str | None, str | None]] = []
        for tokens in segments:
            cls = _classify_bash_segment(tokens)
            if cls is not None:
                out.append(cls)
        return out, False
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

    tool_calls = {"SEARCH": 0, "SHOW": 0, "PROPOSE": 0, "MUTATION": 0, "UNKNOWN": 0}
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
                elif cls == "UNKNOWN":
                    # Neither confirms nor denies a search/mutation: never
                    # sets search_seen, first_search, or first_mutation.
                    tool_calls["UNKNOWN"] += 1

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
