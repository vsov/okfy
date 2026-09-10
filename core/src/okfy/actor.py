"""Who wrote a proposal, and who accepted it (OKF v0.2 §7 actor strings).

An actor is a declaration, not a signature: `claude-code/1.0` says which agent
claims to have written a proposal and `human:victor` which local role accepted
it. Nothing here authenticates either. What the strings buy is that authorship
and verification are recorded at all, in a grammar other OKF tools read, and
that an accepted text's verification names the bytes it verified.
"""
import datetime
import re
import subprocess

# `<producer>/<version>` or `<prefix>:<id>` — the §7 family is open, not a
# whitelist of prefixes.
ACTOR_RE = re.compile(r"^(?:[a-zA-Z][\w.-]*:\S+|[^\s/]+/[^\s/]+)$")
E_ACTOR = "E_PROPOSAL_ACTOR"


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
    r = subprocess.run(["git", "-C", str(bundle.root), "config", "user.name"],
                       capture_output=True, text=True, check=False)
    name = re.sub(r"[^\w]+", "-", r.stdout.strip().casefold()).strip("-")
    return f"human:{name or 'owner'}"


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
