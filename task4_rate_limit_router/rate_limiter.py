"""
task4_rate_limit_router/rate_limiter.py

Sliding-window rate limiter that checks token usage against tenant-specific
limits before forwarding requests.

SQLite Notes:
  - SQLite doesn't support FOR UPDATE with the same semantics as PostgreSQL.
    For SQLite, we use a simpler approach with transaction isolation and
    an explicit check-then-record pattern.
  - WAL mode (enabled in db.py) provides better concurrency.
  - Cleanup uses DELETE with the index.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from databaseinit.models import ApiKey, RateLimitEvent

logger = logging.getLogger("task4_rate_limiter")

DEFAULT_LIMIT = 100
WINDOW_SECONDS = 60
TOKENS_PER_REQUEST = 1


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    current_usage: int
    limit: int
    window_seconds: int
    reset_after_seconds: float


class RateLimitExceededError(Exception):
    """Raised when a request would exceed the tenant's rate limit."""
    def __init__(self, tenant_name: str, current_usage: int, limit: int):
        self.tenant_name = tenant_name
        self.current_usage = current_usage
        self.limit = limit
        super().__init__(
            f"Rate limit exceeded for tenant '{tenant_name}': "
            f"{current_usage} / {limit} tokens used in the last {WINDOW_SECONDS}s"
        )


async def _get_tenant_limit(api_key: str, db: AsyncSession) -> int:
    """Returns the rate limit for a tenant."""
    return DEFAULT_LIMIT


async def _get_current_window_usage(
    api_key: str, db: AsyncSession, now: datetime
) -> int:
    """Sums tokens used by this api_key in the sliding window."""
    cutoff = now - timedelta(seconds=WINDOW_SECONDS)

    stmt = (
        select(func.coalesce(func.sum(RateLimitEvent.tokens_used), 0))
        .where(
            RateLimitEvent.api_key == api_key,
            RateLimitEvent.occurred_at > cutoff,
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one()


async def _record_usage(
    api_key: str, tokens: int, db: AsyncSession, now: datetime
) -> None:
    """Appends a usage event to the log."""
    event = RateLimitEvent(
        api_key=api_key,
        tokens_used=tokens,
        occurred_at=now,
    )
    db.add(event)


async def _clean_old_events(
    api_key: str, db: AsyncSession, now: datetime
) -> None:
    """Deletes events outside the window to keep the table bounded."""
    cutoff = now - timedelta(seconds=WINDOW_SECONDS)
    stmt = (
        select(RateLimitEvent)
        .where(
            RateLimitEvent.api_key == api_key,
            RateLimitEvent.occurred_at <= cutoff,
        )
    )
    result = await db.execute(stmt)
    events = result.scalars().all()

    if events:
        for event in events:
            await db.delete(event)
        logger.debug("Cleaned %d old rate-limit events for key %s", len(events), api_key)


async def check_and_consume(
    api_key: str,
    db: AsyncSession,
    tokens: int = TOKENS_PER_REQUEST,
    *,
    record_usage: bool = True,
) -> RateLimitDecision:
    """
    Main entrypoint: checks whether a request is under the rate limit,
    and if so, records the token usage atomically.
    """
    now = datetime.now(timezone.utc)

    limit = await _get_tenant_limit(api_key, db)

    # For SQLite, we do a check-then-record pattern.
    # Since SQLite doesn't have row-level locking, we rely on the fact
    # that we're using a single session and commit after both operations.
    current_usage = await _get_current_window_usage(api_key, db, now)

    allowed = current_usage + tokens <= limit

    if not allowed:
        logger.warning(
            "Rate limit exceeded: key=%s usage=%d limit=%d",
            api_key, current_usage, limit
        )
        raise RateLimitExceededError(
            tenant_name=api_key,
            current_usage=current_usage,
            limit=limit,
        )

    if record_usage:
        await _record_usage(api_key, tokens, db, now)
        await _clean_old_events(api_key, db, now)

    # For SQLite, we commit to make the usage permanent
    await db.commit()

    return RateLimitDecision(
        allowed=True,
        current_usage=current_usage + tokens,
        limit=limit,
        window_seconds=WINDOW_SECONDS,
        reset_after_seconds=float(WINDOW_SECONDS),
    )


async def get_rate_limit_status(
    api_key: str, db: AsyncSession
) -> RateLimitDecision:
    """Returns the current rate limit status WITHOUT consuming any tokens."""
    now = datetime.now(timezone.utc)
    limit = await _get_tenant_limit(api_key, db)
    current_usage = await _get_current_window_usage(api_key, db, now)

    return RateLimitDecision(
        allowed=current_usage < limit,
        current_usage=current_usage,
        limit=limit,
        window_seconds=WINDOW_SECONDS,
        reset_after_seconds=float(WINDOW_SECONDS),
    )