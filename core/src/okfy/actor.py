"""Who wrote a proposal, and who accepted it (OKF v0.2 §7 actor strings).

An actor is a declaration, not a signature: `claude-code/1.0` says which agent
claims to have written a proposal and `human:victor` which local role accepted
it. Nothing here authenticates either. What the strings buy is that authorship
and verification are recorded at all, in a grammar other OKF tools read, and
that an accepted text's verification names the bytes it verified.
"""
import datetime
import re

from okfy.gitenv import run_git

# `<producer>/<version>` or `<prefix>:<id>` — the §7 family is open, not a
# whitelist of prefixes.
ACTOR_RE = re.compile(r"^(?:[a-zA-Z][\w.-]*:\S+|[^\s/]+/[^\s/]+)$")
E_ACTOR = "E_PROPOSAL_ACTOR"
E_OWNER_ACTION_REQUIRED = "E_OWNER_ACTION_REQUIRED"


def check_actor(actor) -> str:
    a = str(actor).strip() if actor is not None else ""
    if not ACTOR_RE.match(a):
        raise ValueError(
            f"{E_ACTOR}: {actor!r} is not an OKF v0.2 actor — use "
            "`<producer>/<version>` (e.g. claude-code/1.0) or `<prefix>:<id>` "
            "(e.g. human:alice); pass it with `okfy propose --as <actor>`")
    return a


def owner_actor(bundle) -> str:
    """The accepting role: `human:<git user.name, kebab-cased>` from the bundle's
    git config, else `human:owner`. A local role, never an identity claim."""
    r = run_git(bundle.root, "config", "user.name",
                capture_output=True, text=True, check=False)
    name = re.sub(r"[^\w]+", "-", r.stdout.strip().casefold()).strip("-")
    return f"human:{name or 'owner'}"


def check_owner_actor(bundle, as_actor: str | None, *, verb: str, retry: str) -> str:
    """v0.24 (c): who is performing an OWNER-ONLY verb (`review accept`,
    `review reject` — the two that grew an optional `--as`). Default
    (`as_actor` is None) is `owner_actor(bundle)`, exactly as before this
    existed. An explicit `--as` is honoured only when it is a `human:` actor
    — an agent DECLARING itself the owner (`--as claude-code/1.0`) is refused
    with `E_OWNER_ACTION_REQUIRED`, naming the exact command to hand to the
    real owner.

    This is honest-agent ergonomics, not security: actor strings are
    declarations everywhere in this module (see the module docstring), and an
    agent with shell access could edit the ledger or the concept file
    directly regardless of what this function decides. What it buys is that
    an honestly-behaving agent cannot accidentally (or hopefully) walk into
    owner authority by passing its own actor string to an owner verb."""
    if as_actor is None:
        return owner_actor(bundle)
    actor = check_actor(as_actor)
    if not actor.startswith("human:"):
        raise ValueError(
            f"{E_OWNER_ACTION_REQUIRED}: {verb} is the owner's decision — "
            f"ask the owner to run: {retry}")
    return actor


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
