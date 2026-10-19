"""Streamable-HTTP transport: one shared bearer token, or JWTs from an issuer.

stdio is one process per agent session on the same machine. This is the other
shape: one long-running process on a host, reached over HTTP by every client,
the same eight tools, the same bundle, every client seeing every other's
writes on its next call. Who may call is decided in one of two ways:

`--token-file`: one shared token (`BearerToken`). Read from a FILE, never a
flag (a flag is visible in `ps` and shell history), long enough that guessing
is hopeless, checked before the MCP layer sees a byte. No users, scopes or
sessions: one token, one bundle, every holder equal, and the `actor` on a
write is whatever the client claims.

`--oidc-issuer`: every request carries a JWT access token minted by one
OpenID issuer (Authentik, Keycloak, ...), checked by `OidcVerifier` against
the issuer's published keys. The token says WHO (`sub`, which becomes the
ledger `actor`, so a write is signed by its author's identity and not by a
self-declared string) and WHAT (`scope`: `okfy:read` to call anything,
`okfy:write` for `okfy_write`/`okfy_propose`). Users, revocation and the
login itself live in the issuer, where they are managed once for every
service that trusts it. The MCP SDK's own auth middleware does the HTTP side
(401/403 in the OAuth shape, protected-resource metadata so an MCP client can
find the issuer); this module only verifies the token and names the actor.

Encryption is the proxy's job either way: this serves plain HTTP, so bind it
to loopback or a private interface and terminate TLS in front.
"""
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path

import anyio
import httpx
import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from okfy.actor import ACTOR_RE, E_ACTOR, MAX_ACTOR_CHARS

E_TOKEN = "E_MCP_HTTP_TOKEN"
E_UNAUTHORIZED = "E_MCP_UNAUTHORIZED"
E_ISSUER = "E_MCP_OIDC_ISSUER"
E_FORBIDDEN = "E_MCP_FORBIDDEN"
MIN_TOKEN_CHARS = 32
# What the server remembers about a request it could not satisfy, so that an
# anonymous caller cannot make every request cost the issuer a fetch: a
# discovery that failed, a `kid` the issuer does not publish. Bounded, and it
# expires, so a repaired issuer or a freshly published key is picked up.
FAILURE_MEMORY_S = 60
MAX_REMEMBERED = 1024
# What may follow an `--oidc-issuer-prefix`: plain path segments. Anything that
# could walk out of the prefix once the URL is fetched (`..`, `%2e`, `?`, `#`,
# `\`, `@`, whitespace, an empty segment) is not an issuer.
_BELOW_PREFIX = re.compile(r"[A-Za-z0-9_.~-]+(?:/[A-Za-z0-9_.~-]+)*/?")
READ_SCOPE = "okfy:read"
WRITE_SCOPE = "okfy:write"
log = logging.getLogger(__name__)


def load_token(path: Path | None) -> str:
    """The bearer token from `path` (or `$OKFY_MCP_TOKEN_FILE`), stripped.
    Refuses (`E_MCP_HTTP_TOKEN`) when no file was named, it cannot be read,
    or the token is shorter than MIN_TOKEN_CHARS: HTTP without a token would
    hand the bundle to anyone who can reach the port, so there is no
    unauthenticated mode to fall into."""
    if path is None and os.environ.get("OKFY_MCP_TOKEN_FILE"):
        path = Path(os.environ["OKFY_MCP_TOKEN_FILE"])
    if path is None:
        raise ValueError(
            f"{E_TOKEN}: --http needs a bearer token — pass --token-file <file> "
            "(or set OKFY_MCP_TOKEN_FILE); make one with "
            "`openssl rand -hex 32 > <file>; chmod 600 <file>`")
    try:
        token = Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError as e:
        raise ValueError(f"{E_TOKEN}: cannot read token file {path}: "
                         f"{e.strerror or e}") from e
    if len(token) < MIN_TOKEN_CHARS or any(ch.isspace() for ch in token):
        raise ValueError(
            f"{E_TOKEN}: the token in {path} must be one line of at least "
            f"{MIN_TOKEN_CHARS} characters with no whitespace — "
            "`openssl rand -hex 32 > <file>` makes a fitting one")
    return token


class BearerToken:
    """ASGI middleware: every HTTP request must carry
    `Authorization: Bearer <token>`, compared in constant time. Anything
    else is answered 401 with the documented `{error, message, way_out}`
    envelope and never reaches `app`. Non-HTTP scopes (lifespan) pass
    through untouched so the MCP session manager still starts and stops; any
    other scope type (a websocket the app does not serve) is refused, not
    waved through."""

    def __init__(self, app, token: str):
        self.app = app
        self._expected = token.encode("utf-8")

    def _authorized(self, scope) -> bool:
        for name, value in scope.get("headers", ()):
            if name == b"authorization":
                scheme, _, presented = value.partition(b" ")
                return (scheme.lower() == b"bearer"
                        and hmac.compare_digest(presented.strip(), self._expected))
        return False

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind == "lifespan" or (kind == "http" and self._authorized(scope)):
            await self.app(scope, receive, send)
            return
        if kind == "websocket":                 # the app serves none; refuse, do not pass on
            await send({"type": "websocket.close", "code": 1008})
            return
        if kind != "http":
            return
        body = json.dumps({
            "error": E_UNAUTHORIZED,
            "message": f"{E_UNAUTHORIZED}: this request carries no valid bearer token",
            "way_out": "send the server's token as `Authorization: Bearer <token>` "
                       "on every request",
        }).encode("utf-8")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"www-authenticate", b"Bearer")]})
        await send({"type": "http.response.body", "body": body})


class OidcVerifier:
    """mcp `TokenVerifier`: a JWT access token from one of `issuers`, RS256,
    signed by a key that issuer publishes at its `jwks_uri`, not expired, and
    meant for one of `audiences` (the client ids the issuers registered for
    this bundle: an `aud` check is what keeps a token minted for another
    service out of this one). Several issuers because an Authentik puts each
    registered client app under its own issuer path, and one bundle serves
    Claude, Claude Code and Codex alike; the token's `iss` picks the key set.
    Every discovery document is read ONCE, at construction, so a wrong
    `--oidc-issuer` fails the start (`E_MCP_OIDC_ISSUER`) instead of failing
    every request later; keys are fetched and cached by `kid` as tokens
    arrive, so a key rotation needs no restart."""

    def __init__(self, issuers: list[str], audiences: list[str],
                 write_group: str | None = None, prefixes: list[str] = (),
                 read_group: str | None = None):
        if not issuers:
            raise ValueError(f"{E_ISSUER}: --oidc-issuer needs at least one "
                             "issuer URL")
        if not audiences:
            raise ValueError(f"{E_ISSUER}: --oidc-issuer needs at least one "
                             "--oidc-audience (the client id the issuer minted "
                             "this bundle's tokens for)")
        self._keys: dict[str, jwt.PyJWKClient] = {}
        for issuer in issuers:
            name, jwks_uri = self._discover(issuer)
            self._keys[name] = self._client(jwks_uri)
        self.issuers = list(self._keys)         # as the issuers state them
        self.audiences = list(audiences)
        # The scope says what the CLIENT may do for the user; the group says
        # what the USER may do. With a write group, okfy:write counts only
        # for users the issuer lists in it (`groups` claim), so a guest whose
        # client asked for every scope still reads only.
        self.write_group = write_group
        # A read group closes the bundle to everyone the issuer does not list
        # in it: one issuer can then serve several bundles, each to its own
        # people. Outside the group, every scope is withheld, so the SDK
        # answers 403 (insufficient scope) and nothing is served.
        self.read_group = read_group
        # Issuers under these prefixes are trusted as they turn up. An
        # Authentik that lets MCP clients register themselves puts every
        # registered client under its own issuer path, with a client id
        # nobody knew in advance: discovery is read on first sight, the
        # signature checked as for any issuer, the `aud` check skipped (the
        # issuer vouched for the client by registering it; the scopes still
        # bind the token to this bundle). An explicit issuer under a prefix
        # keeps its audience check.
        self.prefixes = [p.rstrip("/") + "/" for p in prefixes]
        self._registered: dict[str, jwt.PyJWKClient] = {}
        self._failed: dict[tuple, tuple[float, type, str]] = {}

    @staticmethod
    def _discover(issuer: str) -> tuple[str, str]:
        """(issuer as it states itself, jwks_uri) from the discovery document."""
        url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        try:
            r = httpx.get(url, timeout=10, follow_redirects=True)
            r.raise_for_status()
            meta = r.json()
            return str(meta["issuer"]), str(meta["jwks_uri"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            raise ValueError(f"{E_ISSUER}: cannot read the issuer's discovery "
                             f"document at {url} ({e.__class__.__name__}: {e}) — "
                             "pass the issuer URL exactly as its own "
                             "`.well-known/openid-configuration` states `issuer`"
                             ) from e

    @staticmethod
    def _client(jwks_uri: str) -> jwt.PyJWKClient:
        # The key fetch goes through urllib with its stock User-Agent, which
        # Cloudflare (in front of most issuers) answers 403; name the client.
        # cache_keys stays off: PyJWT's per-key cache has no expiry, so a key
        # the issuer stopped publishing would stay trusted for the process's
        # life, and every token it signed with it, whatever that token's exp.
        # The JWKS-set cache (cache_jwk_set, 300 s) is bounded and kept; an
        # unknown kid re-reads the set (newer PyJWT: at most every 30 s). A
        # token is accepted only while its key is in the cached set AND before
        # its exp; there is no per-token revocation.
        return jwt.PyJWKClient(jwks_uri, cache_keys=False, timeout=10,
                               headers={"User-Agent": "okfy-mcp"})

    def _recall(self, key: tuple) -> None:
        """Raise again, without asking the issuer, what failed for `key` less
        than FAILURE_MEMORY_S ago."""
        seen = self._failed.get(key)
        if seen is not None:
            if time.monotonic() < seen[0]:
                raise seen[1](seen[2])
            self._failed.pop(key, None)

    def _remember(self, key: tuple, exc: Exception) -> None:
        now = time.monotonic()
        if len(self._failed) >= MAX_REMEMBERED:
            self._failed = {k: v for k, v in self._failed.items() if v[0] > now}
            if len(self._failed) >= MAX_REMEMBERED:
                self._failed.clear()
        self._failed[key] = (now + FAILURE_MEMORY_S, type(exc), str(exc))

    def _below_prefix(self, iss: str) -> bool:
        """`iss` is a plain path below one of the prefixes: the only kind of
        token-supplied URL this server will fetch."""
        for prefix in self.prefixes:
            rest = iss[len(prefix):] if iss.startswith(prefix) else None
            if rest and _BELOW_PREFIX.fullmatch(rest) and not (
                    {".", ".."} & set(rest.split("/"))):
                return True
        return False

    def _keys_for(self, iss: str) -> tuple[jwt.PyJWKClient, bool]:
        """The key set for `iss`, and whether the audience is checked."""
        if iss in self._keys:
            return self._keys[iss], True
        if iss not in self._registered and self._below_prefix(iss):
            # This runs BEFORE the signature is checked, on a string the
            # caller chose: it is fetched only if it is a plain path below the
            # prefix, and a failure is remembered for a while, so a flood of
            # tokens naming bogus paths costs the issuer one fetch per path.
            self._recall(("iss", iss))
            try:
                name, jwks_uri = self._discover(iss)    # blocking fetch
            except ValueError as e:
                self._remember(("iss", iss), e)
                raise
            if name.rstrip("/") != iss.rstrip("/"):
                raise jwt.InvalidIssuerError(f"{iss!r} states its issuer as {name!r}")
            self._registered[iss] = self._client(jwks_uri)
        if iss in self._registered:
            return self._registered[iss], False
        raise jwt.InvalidIssuerError(f"issuer {iss!r} is not one of {self.issuers}"
                                     + (f" nor under {self.prefixes}" if self.prefixes else ""))

    def _claims(self, token: str) -> dict:
        iss = str(jwt.decode(token, options={"verify_signature": False}).get("iss") or "")
        keys, check_aud = self._keys_for(iss)
        # An unpublished `kid` makes PyJWT re-read the whole key set; the
        # token is not yet verified, so anyone can ask. Remember the answer.
        asked = ("kid", iss, str(jwt.get_unverified_header(token).get("kid")))
        self._recall(asked)
        try:
            key = keys.get_signing_key_from_jwt(token).key   # blocking fetch
        except jwt.PyJWKClientConnectionError:
            raise                                # the issuer is down: not the token's fault
        except jwt.PyJWKClientError as e:
            self._remember(asked, e)
            raise
        return jwt.decode(token, key, algorithms=["RS256"], issuer=iss,
                          audience=self.audiences if check_aud else None,
                          options={"require": ["exp", "sub", "aud", "iss"],
                                   "verify_aud": check_aud})

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await anyio.to_thread.run_sync(self._claims, token)
        except (jwt.PyJWTError, ValueError) as e:   # bad signature, expired, wrong aud/iss, unknown kid
            # the reason, never the token: the operator's only clue when a
            # client that logged in fine is still answered 401
            # %r on a bounded string: the text can carry what the caller wrote
            # (the header's `kid`, the `iss`), and must stay one log line
            log.warning("token refused: %s: %r", e.__class__.__name__, str(e)[:300])
            return None
        sub = str(claims["sub"])
        scopes = str(claims.get("scope") or "").split()
        groups = _groups(claims)
        if self.read_group and self.read_group not in groups:
            log.info("refused: %r is not in group %r", sub, self.read_group)
            scopes = [s for s in scopes if s not in (READ_SCOPE, WRITE_SCOPE)]
        if (self.write_group and WRITE_SCOPE in scopes
                and self.write_group not in groups):
            log.info("okfy:write withheld: %r is not in group %r", sub, self.write_group)
            scopes = [s for s in scopes if s != WRITE_SCOPE]   # every copy, not the first
        return AccessToken(token=token, client_id=str(claims.get("azp") or sub),
                           scopes=scopes, expires_at=int(claims["exp"]), subject=sub,
                           claims=claims)


def _groups(claims: dict) -> set[str]:
    """The `groups` claim as a set of group names. A list is the usual shape;
    a lone string is ONE group (matching it with `in` would test substrings,
    and `owners` would match `owners-admins`); anything else names none."""
    groups = claims.get("groups")
    if isinstance(groups, str):
        return {groups}
    if isinstance(groups, (list, tuple)):
        return {g for g in groups if isinstance(g, str)}
    return set()


ACTOR_CLAIM = "okfy_actor"


def actor_for(claimed: str | None, *, write: bool = False,
              verified: bool = False) -> str | None:
    """The actor of this tool call. Under `OidcVerifier` it is what the
    verified token says — the `okfy_actor` claim when the issuer sets one
    (an issuer that serves several agent apps can then tell them apart, e.g.
    `claude:vasyl` and `codex:vasyl` for one human), else `sub` — as itself
    when that already is an OKF actor string (`human:vasyl`, `agent:codex`),
    else `oidc:<sub>`. The client's own claim is dropped: an identity the
    issuer signed outranks a string the client typed. A write also needs
    WRITE_SCOPE on the token, else `E_MCP_FORBIDDEN`. Without a token (stdio,
    or the shared file token) the claim is returned as is, for `check_actor`
    to validate as before — unless `verified`, set when the server runs under
    `OidcVerifier`: there a missing token is `E_MCP_UNAUTHORIZED`."""
    tok = get_access_token()
    if tok is None:
        if verified:
            # the server was started to verify identities, so a call with no
            # verified token means the request context was lost: never fall
            # back to the client's own word, and never skip the scope check
            raise ValueError(
                f"{E_UNAUTHORIZED}: this tool call carries no verified token, "
                "and this server accepts only calls from verified identities")
        return claimed
    sub = str((tok.claims or {}).get(ACTOR_CLAIM) or tok.subject or tok.client_id)
    if write and WRITE_SCOPE not in tok.scopes:
        raise ValueError(
            f"{E_FORBIDDEN}: the token for {sub!r} carries scopes {sorted(tok.scopes)}, "
            f"not {WRITE_SCOPE}: it may read this bundle but not write to it — "
            "the issuer's admin grants the scope")
    actor = sub if ACTOR_RE.match(sub) else f"oidc:{sub}"
    # The actor lands in a ledger row, a git author name and a commit trailer.
    # The core's `check_actor` now refuses control characters and anything
    # over MAX_ACTOR_CHARS for a string the CLIENT typed; one the issuer
    # signed is an identity, and it is held to the same bar here, up front,
    # so a bad `sub` is an `E_PROPOSAL_ACTOR` naming the token and not a
    # refusal later that names only the string.
    if not (actor.isprintable() and len(actor) <= MAX_ACTOR_CHARS and ACTOR_RE.match(actor)):
        raise ValueError(
            f"{E_ACTOR}: the identity in this token ({actor[:60]!r}) is not one "
            f"printable OKF actor of at most {MAX_ACTOR_CHARS} characters — the "
            "issuer must sign a `sub` (or `okfy_actor`) without whitespace or "
            "control characters")
    return actor
