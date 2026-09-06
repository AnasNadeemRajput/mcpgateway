"""
task2_gateway/authorization/auth.py

Handles the first half of Task 2: reading the incoming `Bearer <token>`
header and resolving it to a role (admin / viewer) + tenant identity.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from databaseinit.models import ApiKey, ApiKeyRole

logger = logging.getLogger("task2_gateway.auth")

CACHE_TTL_SECONDS = 30


class AuthenticationError(Exception):
    """Raised when a bearer token is missing, malformed, unknown, or deactivated."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class Principal:
    """The resolved identity behind a bearer token."""
    token: str
    tenant_name: str
    role: ApiKeyRole

    @property
    def is_admin(self) -> bool:
        return self.role == ApiKeyRole.ADMIN


class _TokenCache:
    """Minimal TTL cache: token -> (Principal, expires_at_monotonic)."""

    def __init__(self, ttl_seconds: int = CACHE_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[Principal, float]] = {}

    def get(self, token: str) -> Optional[Principal]:
        entry = self._store.get(token)
        if entry is None:
            return None
        principal, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._store[token]
            return None
        return principal

    def set(self, token: str, principal: Principal) -> None:
        self._store[token] = (principal, time.monotonic() + self._ttl)

    def invalidate(self, token: str) -> None:
        self._store.pop(token, None)

    def clear(self) -> None:
        self._store.clear()


_token_cache = _TokenCache()


def parse_bearer_token(authorization_header: Optional[str]) -> str:
    """Extracts the raw token from an `Authorization: Bearer <token>` header."""
    if not authorization_header:
        raise AuthenticationError("Missing Authorization header")

    parts = authorization_header.strip().split(" ", 1)
    if len(parts) != 2 or parts[0] != "Bearer" or not parts[1].strip():
        raise AuthenticationError(
            "Authorization header must be in the form: Bearer <token>"
        )

    return parts[1].strip()


async def resolve_principal(token: str, db: AsyncSession) -> Principal:
    """Resolves a raw bearer token to a Principal."""
    cached = _token_cache.get(token)
    if cached is not None:
        return cached

    result = await db.execute(select(ApiKey).where(ApiKey.key == token))
    api_key = result.scalar_one_or_none()

    if api_key is None:
        logger.warning("Rejected request with unknown bearer token")
        raise AuthenticationError("Invalid or unknown API token")

    if not api_key.is_active:
        logger.warning(
            "Rejected request with deactivated token for tenant '%s'",
            api_key.tenant_name,
        )
        raise AuthenticationError("This API token has been deactivated")

    principal = Principal(
        token=token, tenant_name=api_key.tenant_name, role=api_key.role
    )
    _token_cache.set(token, principal)
    return principal


async def authenticate(
    authorization_header: Optional[str], db: AsyncSession
) -> Principal:
    """Convenience entrypoint combining parse + resolve."""
    token = parse_bearer_token(authorization_header)
    return await resolve_principal(token, db)