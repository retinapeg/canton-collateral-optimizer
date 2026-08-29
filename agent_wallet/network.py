"""Which Canton to talk to, and how to authenticate to it.

Three targets, detected from the environment:

  sandbox   a local `daml sandbox`.  Authentication is disabled, so no token.
  localnet  your own Canton in Docker.  A self-signed HS256 JWT is accepted.
  devnet    the shared Cantor8 node.  A Keycloak client-credentials token.

The variable names are the toolkit's own (`C8_BASE`, `C8_IDP`, ...), so the
credentials the organisers hand out on the day work here with no translation:

    export C8_BASE=https://api.validator.dev.digik.cantor8.tech/api/ledger
    export C8_IDP=https://auth.dev.digik.cantor8.tech
    export C8_CLIENT_ID=hackathon
    export C8_CLIENT_SECRET=<from the Cantor8 team>
    python -m agent_wallet.deploy

Two things are worth knowing before you point this at a shared node.

**A token says who you are; it does not give you rights over a party.** Acting
as a party is a separate `CanActAs` grant, and a valid token without it gets a
403.  `deploy.py` grants them.

**Only parties local to the node you are talking to can submit.**  Allocating
`Alice` on the shared node gives you *your* Alice; you are not spending anyone
else's money, and nobody else's agent can touch yours.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backend.canton import CantonClient

SANDBOX = "sandbox"
LOCALNET = "localnet"
DEVNET = "devnet"

# Where our own sandbox layout puts Canton: the wallet takes 7575 and proxies
# through to this.  See serve.py.
DEFAULT_SANDBOX_URL = "http://localhost:7576"


class AuthError(RuntimeError):
    """We could not obtain a token, so there is no point trying the ledger."""


@dataclass(frozen=True)
class Target:
    """One Canton to talk to."""

    kind: str
    base_url: str
    user_id: str
    admin_user_id: str
    idp_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    jwt_secret: str = "unsafe"
    audience: str = "https://canton.network.global"

    @property
    def needs_token(self) -> bool:
        return self.kind != SANDBOX

    def describe(self) -> str:
        how = {
            SANDBOX: "no authentication (sandbox runs with it disabled)",
            LOCALNET: "self-signed HS256 JWT",
            DEVNET: f"Keycloak client credentials as {self.client_id!r}",
        }[self.kind]
        return f"{self.kind} at {self.base_url} - {how}"


def from_env(base_url_override: str | None = None) -> Target:
    """Work out which network we are pointed at."""
    base = base_url_override or os.environ.get("C8_BASE", "")
    idp = os.environ.get("C8_IDP", "")
    user = os.environ.get("C8_USER", "agent-wallet")
    admin = os.environ.get("C8_ADMIN_USER", "participant_admin")

    kind = os.environ.get("AGENT_WALLET_NETWORK", "").strip().lower()
    if not kind:
        if idp:
            kind = DEVNET
        elif base and "localhost" not in base and "127.0.0.1" not in base:
            kind = DEVNET
        elif base:
            kind = LOCALNET if base.endswith(("2975", "3975", "4975")) else SANDBOX
        else:
            kind = SANDBOX
    if kind not in (SANDBOX, LOCALNET, DEVNET):
        raise AuthError(
            f"AGENT_WALLET_NETWORK={kind!r} is not one of "
            f"{SANDBOX}, {LOCALNET}, {DEVNET}"
        )

    if kind == DEVNET and not idp:
        raise AuthError(
            "This looks like DevNet but C8_IDP is not set, so there is nowhere "
            "to fetch a token from. Set C8_IDP, C8_CLIENT_ID and "
            "C8_CLIENT_SECRET (the team issues the secret on the day)."
        )
    if kind == DEVNET and not os.environ.get("C8_CLIENT_SECRET"):
        raise AuthError(
            "C8_IDP is set but C8_CLIENT_SECRET is not. Ask the Cantor8 team "
            "for the client secret; without it every call returns 401."
        )

    return Target(
        kind=kind,
        base_url=base or DEFAULT_SANDBOX_URL,
        user_id=user,
        admin_user_id=admin,
        idp_url=idp,
        client_id=os.environ.get("C8_CLIENT_ID", "hackathon"),
        client_secret=os.environ.get("C8_CLIENT_SECRET", ""),
        jwt_secret=os.environ.get("C8_JWT_SECRET", "unsafe"),
        audience=os.environ.get("C8_AUD", "https://canton.network.global"),
    )


# -- tokens -------------------------------------------------------------------

_cache: dict[str, tuple[str, float]] = {}
_TOKEN_TTL = 240.0   # refresh well inside Keycloak's default 5 minutes


def _b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _self_signed(target: Target, subject: str) -> str:
    """The HS256 JWT a LocalNet accepts. Never use this against anything real."""
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(
        json.dumps({"sub": subject, "aud": target.audience}, separators=(",", ":")).encode()
    )
    signature = _b64(
        hmac.new(
            target.jwt_secret.encode(), header + b"." + payload, hashlib.sha256
        ).digest()
    )
    return (header + b"." + payload + b"." + signature).decode()


def _keycloak(target: Target) -> str:
    key = f"{target.idp_url}|{target.client_id}"
    cached = _cache.get(key)
    if cached and monotonic() < cached[1]:
        return cached[0]
    data = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": target.client_id,
            "client_secret": target.client_secret,
        }
    ).encode()
    url = f"{target.idp_url}/realms/master/protocol/openid-connect/token"
    try:
        with urlopen(Request(url, data=data), timeout=30) as response:
            body: dict[str, Any] = json.loads(response.read())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        if exc.code in (400, 401):
            raise AuthError(
                f"{target.idp_url} rejected the credentials "
                f"(HTTP {exc.code}): {detail}\n  "
                f"client_id={target.client_id!r}. The secret is wrong, expired, "
                "or for a different client. Ask the Cantor8 team to confirm both."
            ) from exc
        raise AuthError(
            f"{target.idp_url} returned HTTP {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise AuthError(
            f"Cannot reach the identity provider at {target.idp_url}: "
            f"{exc.reason}. Check the URL and that you are on a network that "
            "can see it."
        ) from exc
    except (ValueError, OSError) as exc:
        raise AuthError(
            f"Could not get a token from {target.idp_url}: {exc}"
        ) from exc
    token = body.get("access_token")
    if not token:
        raise AuthError(f"{target.idp_url} returned no access_token: {body}")
    _cache[key] = (token, monotonic() + _TOKEN_TTL)
    return token


def token_provider(target: Target, subject: str | None = None):
    """A callable the ledger client uses to fetch a bearer token per request."""
    if not target.needs_token:
        return None
    who = subject or target.user_id
    if target.kind == DEVNET:
        return lambda: _keycloak(target)
    return lambda: _self_signed(target, who)


def client_from_env(base_url_override: str | None = None) -> tuple[CantonClient, Target]:
    """The client every entry point should use.

    They all share one API user deliberately: on a shared node `CanActAs` is
    granted per user, and `deploy.py` grants it to exactly one.  Three
    components inventing three user ids would mean two of them getting 403.
    """
    target = from_env(base_url_override)
    return client(target), target


def client(target: Target, *, as_admin: bool = False, timeout: float = 30.0) -> CantonClient:
    """A ledger client wired for this target.

    `as_admin` is for party allocation and rights grants, which a shared node
    restricts to an administrative user.
    """
    subject = target.admin_user_id if as_admin else target.user_id
    return CantonClient(
        target.base_url,
        user_id=target.user_id,
        timeout=timeout,
        token_provider=token_provider(target, subject),
    )
