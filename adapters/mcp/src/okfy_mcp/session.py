"""v0.24 (a): an in-process, ordered trace of ONE server session's
`okfy_query`/`okfy_show` tool calls.

The core cannot see whether an agent searched or read anything before it
wrote a proposal. The stdio MCP server is one process PER AGENT SESSION, so
it — and only it — can observe its own tool calls. What it observes proves
TOOL CALLS HAPPENED, not that the agent understood anything; every surface
this trace reaches (the `observed` block on a proposal, `review show`) keeps
that label attached rather than letting a bare `true` read as more than it
is.
"""
import hashlib
from importlib.metadata import PackageNotFoundError, version

from okfy.proposals import normalize_query

OBSERVED_LABEL = "attested by adapter process, MCP reads only"

# `observed.query_sha256` keeps the last 20; `observed.read_set` keeps the 50
# newest distinct concept ids (deduped, latest sha wins) — bounds so a very
# long session's `observed` block stays a fixed, bounded size on the wire.
_MAX_QUERY_SHA = 20
_MAX_READ_SET = 50


def query_sha256(text: str) -> str:
    """Sha256 of the NORMALIZED query text — same normalization
    `okfy.proposals.normalize_query` uses for a `gap` proposal's dedup key,
    reused here so the two "what did this query mean" hashes never drift."""
    return hashlib.sha256(normalize_query(text).encode("utf-8")).hexdigest()


def adapter_by() -> str:
    try:
        v = version("okfy-mcp")
    except PackageNotFoundError:
        v = "0.0.0"
    return f"okfy-mcp/{v}"


class Session:
    """One instance per server process. Never persisted, never shared across
    processes — a fresh server (a fresh agent session) starts with an empty
    trace, by construction."""

    def __init__(self) -> None:
        self._events: list[dict] = []
        self._queries = 0
        self._query_sha256: list[str] = []
        self._read_set: dict[str, str] = {}   # id -> sha256; insertion order = latest-shown order
        self._read_set_dropped = 0

    def record_query(self, text: str, top_ids: list[str]) -> None:
        qh = query_sha256(text)
        self._queries += 1
        self._query_sha256.append(qh)
        self._events.append({"n": len(self._events) + 1, "tool": "query",
                             "query_sha256": qh, "top_ids": list(top_ids)})

    def record_show(self, concept_id: str, sha256: str) -> None:
        self._events.append({"n": len(self._events) + 1, "tool": "show",
                             "id": concept_id, "sha256": sha256})
        if concept_id in self._read_set:
            del self._read_set[concept_id]        # re-insert at the end below
        elif len(self._read_set) >= _MAX_READ_SET:
            oldest = next(iter(self._read_set))
            del self._read_set[oldest]
            self._read_set_dropped += 1
        self._read_set[concept_id] = sha256

    def observed(self, target_id: str | None) -> dict:
        """The `observed` block `propose()` stores verbatim. `target_id`
        falsy (no real target — e.g. `create`) -> `target_shown: null`."""
        read_set = [{"id": cid, "sha256": sha} for cid, sha in self._read_set.items()]
        return {
            "by": adapter_by(),
            "label": OBSERVED_LABEL,
            "queries": self._queries,
            "query_sha256": self._query_sha256[-_MAX_QUERY_SHA:],
            "read_set": read_set,
            "read_set_truncated": self._read_set_dropped,
            "searched_before_propose": self._queries > 0,
            "target_shown": (target_id in self._read_set) if target_id else None,
        }
