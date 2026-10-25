"""Bearer tokens (JWT) for API clients that can't keep the browser's session
cookie - mobile apps, scripts, other services. A client signs in at
POST /api/auth/token and sends `Authorization: Bearer <access_token>` on
/api/* requests; the browser app keeps using its cookie.

Access tokens are short-lived; the refresh token gets a new pair from
POST /api/auth/token/refresh. Both carry the account's session version
(auth/utils.py start_session), so a password reset, "Sign out everywhere",
two-step sign-in change, deactivation or deletion ends them too."""

import time
import uuid

import jwt

from config import Config

ALGORITHM = "HS256"
ISSUER = "avir-ai"


def _secret() -> str:
    return Config.JWT_SECRET_KEY or Config.SECRET_KEY


def _encode(user_id: int, version: int, kind: str, ttl: int) -> str:
    now = int(time.time())
    claims = {"sub": str(user_id), "sv": version, "typ": kind, "iss": ISSUER,
              "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex}
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def issue_tokens(user_id: int) -> dict:
    """A new access + refresh token pair for the user, as the token endpoints return it."""
    from db import get_session_version

    version = get_session_version(user_id) or 0
    return {
        "access_token": _encode(user_id, version, "access", Config.JWT_ACCESS_TTL_SECONDS),
        "refresh_token": _encode(user_id, version, "refresh", Config.JWT_REFRESH_TTL_SECONDS),
        "token_type": "Bearer",
        "expires_in": Config.JWT_ACCESS_TTL_SECONDS,
    }


def user_id_from_token(token: str, kind: str = "access") -> int | None:
    """The user a token belongs to, or None if it is forged, expired, the
    wrong kind (a refresh token can't be used as an access token), or its
    sessions were ended since it was issued."""
    from db import get_session_version

    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALGORITHM], issuer=ISSUER,
                            options={"require": ["sub", "sv", "typ", "exp", "iat"]})
        user_id = int(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None
    if claims["typ"] != kind:
        return None
    version = get_session_version(user_id)
    if version is None or int(claims["sv"]) != version:
        return None
    return user_id


def bearer_token(authorization: str | None) -> str | None:
    """The token from an `Authorization: Bearer <token>` header, else None."""
    scheme, _, token = (authorization or "").partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None
