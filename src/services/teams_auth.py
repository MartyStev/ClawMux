"""
ClawMux — Microsoft Teams inbound request verification.

Two defenses for POST /api/v1/teams/messages:
  1. verify_inbound_token() — validates the Bot Framework Bearer JWT
     (signature via Microsoft JWKS, audience, issuer, expiry).
  2. is_allowed_service_url() — whitelists activity serviceUrl hosts so the
     adapter's OAuth token is never sent to an attacker-controlled URL (SSRF).
"""

from urllib.parse import urlparse

import jwt
import structlog
from jwt import PyJWKClient

from src.core.config import settings

logger = structlog.get_logger(__name__)


class TeamsAuthError(Exception):
    """Raised when an inbound Teams request fails verification."""


_jwk_clients: dict[str, PyJWKClient] = {}


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_jwk_client(url: str) -> PyJWKClient:
    if url not in _jwk_clients:
        _jwk_clients[url] = PyJWKClient(url, lifespan=86400)
    return _jwk_clients[url]


def verify_inbound_token(authorization: str | None) -> None:
    """
    Verify the `Authorization: Bearer <jwt>` header of an inbound activity.

    Raises TeamsAuthError on any failure (missing header, bad signature,
    wrong audience/issuer, expired token).
    """
    audience = settings.teams_jwt_audience.strip() or settings.teams_app_id.strip()
    if not audience:
        raise TeamsAuthError("Teams inbound verification disabled: TEAMS_APP_ID/TEAMS_JWT_AUDIENCE not configured")

    if not authorization or not authorization.startswith("Bearer "):
        raise TeamsAuthError("Missing Bearer token")
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise TeamsAuthError("Empty Bearer token")

    issuers = _split_csv(settings.teams_allowed_issuers)
    jwks_urls = _split_csv(settings.teams_jwks_urls)

    last_error: Exception | None = None
    for jwks_url in jwks_urls:
        try:
            signing_key = _get_jwk_client(jwks_url).get_signing_key_from_jwt(token)
            jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=audience,
                issuer=issuers,
                options={"require": ["exp", "iss", "aud"]},
            )
            return
        except jwt.PyJWTError as e:
            last_error = e
        except Exception as e:
            # JWKS endpoint unreachable / malformed — try the next URL
            last_error = e

    raise TeamsAuthError(f"Token verification failed: {last_error}")


def is_allowed_service_url(service_url: str) -> bool:
    """
    True if the serviceUrl is HTTPS and its host matches the configured
    whitelist (exact host or subdomain of a whitelisted entry).
    """
    parsed = urlparse(service_url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    for allowed in (h.lower() for h in _split_csv(settings.teams_allowed_service_url_hosts)):
        if host == allowed or host.endswith("." + allowed):
            return True
    return False
