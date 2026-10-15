"""Who is calling: verify the Supabase access token and return its user id.

The API trusts only the signed token, never a user id sent in the request body.
New Supabase projects sign tokens with asymmetric keys (ES256/RS256) published at
the project's JWKS endpoint; older projects use a shared HS256 secret. Both work.
"""

import asyncio
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

AUDIENCE = "authenticated"
ASYMMETRIC_ALGS = ("ES256", "RS256")

_bearer = HTTPBearer(auto_error=False)
_jwks_client: jwt.PyJWKClient | None = None


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        if not settings.supabase_url:
            raise jwt.InvalidTokenError("SUPABASE_URL is not configured")
        url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _jwks_client = jwt.PyJWKClient(url, cache_keys=True, lifespan=600)
    return _jwks_client


async def decode_token(token: str) -> dict:
    alg = jwt.get_unverified_header(token).get("alg")
    if alg == "HS256":
        if not settings.supabase_jwt_secret:
            raise jwt.InvalidTokenError("HS256 tokens are not accepted")
        key = settings.supabase_jwt_secret
    elif alg in ASYMMETRIC_ALGS:
        # Fetching the JWKS is blocking network I/O; keep it off the event loop.
        key = (await asyncio.to_thread(_jwks().get_signing_key_from_jwt, token)).key
    else:
        raise jwt.InvalidAlgorithmError(f"unsupported alg {alg!r}")
    issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1" if settings.supabase_url else None
    return jwt.decode(
        token,
        key,
        algorithms=[alg],
        audience=AUDIENCE,
        issuer=issuer,
        options={"require": ["exp", "sub"]},
    )


async def current_user_id(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UUID:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None:
        raise unauthorized
    try:
        claims = await decode_token(creds.credentials)
        return UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError):
        raise unauthorized from None
