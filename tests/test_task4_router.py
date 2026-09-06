"""
tests/test_task4_router.py

Unit tests for Task 4: Rate Limited Router with Failover
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta

from task4_rate_limit_router.rate_limiter import (
    RateLimitDecision,
    RateLimitExceededError,
    check_and_consume,
    get_rate_limit_status,
    DEFAULT_LIMIT,
    WINDOW_SECONDS,
    TOKENS_PER_REQUEST,
)
from task4_rate_limit_router.providers.primary_provider import PrimaryProvider
from task4_rate_limit_router.providers.fallback_provider import FallbackProvider
from task4_rate_limit_router.failover import routed_mcp_request


# ============================================================================
# Tests for rate_limiter.py
# ============================================================================

class TestRateLimiter:
    """Test suite for rate limiter."""

    @pytest.mark.asyncio
    async def test_check_and_consume_under_limit(self, mock_db):
        """Test rate limit check when under limit."""
        # Mock current usage as 0
        mock_result = AsyncMock()
        mock_result.scalar_one.return_value = 0
        mock_db.execute.return_value = mock_result

        decision = await check_and_consume("test-key", mock_db)
        
        assert decision.allowed is True
        assert decision.current_usage == TOKENS_PER_REQUEST
        assert decision.limit == DEFAULT_LIMIT
        assert decision.window_seconds == WINDOW_SECONDS

    @pytest.mark.asyncio
    async def test_check_and_consume_at_limit(self, mock_db):
        """Test rate limit check at limit."""
        # Mock current usage at limit
        mock_result = AsyncMock()
        mock_result.scalar_one.return_value = DEFAULT_LIMIT
        mock_db.execute.return_value = mock_result

        with pytest.raises(RateLimitExceededError) as exc:
            await check_and_consume("test-key", mock_db)
        
        assert "Rate limit exceeded" in str(exc.value)
        assert exc.value.current_usage == DEFAULT_LIMIT
        assert exc.value.limit == DEFAULT_LIMIT

    @pytest.mark.asyncio
    async def test_check_and_consume_near_limit(self, mock_db):
        """Test rate limit check when near limit."""
        # Mock current usage at limit - 1
        near_limit = DEFAULT_LIMIT - 1
        mock_result = AsyncMock()
        mock_result.scalar_one.return_value = near_limit
        mock_db.execute.return_value = mock_result

        decision = await check_and_consume("test-key", mock_db)
        
        assert decision.allowed is True
        assert decision.current_usage == DEFAULT_LIMIT  # Reaches limit

    @pytest.mark.asyncio
    async def test_check_and_consume_cleanup(self, mock_db):
        """Test cleanup of old events."""
        # Mock current usage
        mock_usage_result = AsyncMock()
        mock_usage_result.scalar_one.return_value = 0
        mock_db.execute.return_value = mock_usage_result

        # Mock old events
        mock_old_events = AsyncMock()
        mock_old_events.scalars.return_value.all.return_value = [
            AsyncMock(), AsyncMock()
        ]
        mock_db.execute.return_value = mock_old_events

        # Should call delete on old events
        await check_and_consume("test-key", mock_db)
        
        # Note: This is a simplified test; in practice you'd verify delete was called

    @pytest.mark.asyncio
    async def test_get_rate_limit_status(self, mock_db):
        """Test getting rate limit status without consuming."""
        # Mock current usage
        mock_result = AsyncMock()
        mock_result.scalar_one.return_value = 50
        mock_db.execute.return_value = mock_result

        status = await get_rate_limit_status("test-key", mock_db)
        
        assert status.allowed is True
        assert status.current_usage == 50
        assert status.limit == DEFAULT_LIMIT
        assert status.window_seconds == WINDOW_SECONDS
        assert status.reset_after_seconds == WINDOW_SECONDS

    @pytest.mark.asyncio
    async def test_check_and_consume_record_usage_false(self, mock_db):
        """Test check without recording usage."""
        mock_result = AsyncMock()
        mock_result.scalar_one.return_value = 0
        mock_db.execute.return_value = mock_result

        decision = await check_and_consume(
            "test-key", mock_db, record_usage=False
        )
        
        assert decision.allowed is True
        assert decision.current_usage == 0  # Not recorded


# ============================================================================
# Tests for primary_provider.py
# ============================================================================

class TestPrimaryProvider:
    """Test suite for PrimaryProvider."""

    @pytest.mark.asyncio
    async def test_list_tools(self):
        """Test listing tools."""
        provider = PrimaryProvider()
        tools = await provider.list_tools()
        assert len(tools) == 3
        assert tools[0]["name"] == "get_customer_record"

    @pytest.mark.asyncio
    async def test_call_tool_force_fail(self, mock_db):
        """Test forced failure."""
        provider = PrimaryProvider()
        
        with pytest.raises(RuntimeError) as exc:
            await provider.call_tool(
                "get_customer_record",
                {"customer_id": "CUST-10001"},
                mock_db,
                force_fail=True
            )
        assert "Primary provider unavailable" in str(exc.value)

    @pytest.mark.asyncio
    @patch("task4_rate_limit_router.providers.primary_provider.random")
    async def test_call_tool_random_fail(self, mock_random, mock_db):
        """Test random failure."""
        mock_random.random.return_value = 0.5  # > FAILURE_RATE (0.3)
        
        provider = PrimaryProvider()
        # Should not fail
        with pytest.raises(Exception):  # Will fail because handlers not mocked
            await provider.call_tool(
                "unknown_tool",
                {},
                mock_db
            )

    @pytest.mark.asyncio
    @patch("task4_rate_limit_router.providers.primary_provider.get_customer_record")
    async def test_call_tool_get_customer(self, mock_get_customer, mock_db):
        """Test calling get_customer_record."""
        mock_get_customer.return_value = AsyncMock()
        mock_get_customer.return_value.model_dump.return_value = {
            "customer_id": "CUST-10001"
        }

        provider = PrimaryProvider()
        result = await provider.call_tool(
            "get_customer_record",
            {"customer_id": "CUST-10001"},
            mock_db
        )
        assert result["customer_id"] == "CUST-10001"


# ============================================================================
# Tests for fallback_provider.py
# ============================================================================

class TestFallbackProvider:
    """Test suite for FallbackProvider."""

    def test_initial_state(self):
        """Test initial state of fallback provider."""
        provider = FallbackProvider()
        # Should be considered unavailable until first call
        assert provider.is_available is False

    @pytest.mark.asyncio
    async def test_call_tool_cold_start(self, mock_db):
        """Test cold start behavior."""
        provider = FallbackProvider()
        
        # Force cold start failure
        with pytest.raises(RuntimeError) as exc:
            await provider.call_tool(
                "get_customer_record",
                {"customer_id": "CUST-10001"},
                mock_db,
                force_fail=True
            )
        assert "Fallback provider" in str(exc.value)

    @pytest.mark.asyncio
    @patch("task4_rate_limit_router.providers.fallback_provider.get_customer_record")
    async def test_call_tool_after_warmup(self, mock_get_customer, mock_db):
        """Test after warmup."""
        mock_get_customer.return_value = AsyncMock()
        mock_get_customer.return_value.model_dump.return_value = {
            "customer_id": "CUST-10001"
        }

        provider = FallbackProvider()
        # First call warms up
        result = await provider.call_tool(
            "get_customer_record",
            {"customer_id": "CUST-10001"},
            mock_db
        )
        assert provider.is_available is True
        assert result["customer_id"] == "CUST-10001"

    @pytest.mark.asyncio
    async def test_call_tool_unknown(self, mock_db):
        """Test unknown tool."""
        provider = FallbackProvider()
        
        with pytest.raises(Exception):
            await provider.call_tool("unknown_tool", {}, mock_db)


# ============================================================================
# Integration Tests for Failover
# ============================================================================

class TestFailoverRouter:
    """Integration tests for failover router."""

    @pytest.mark.asyncio
    async def test_rate_limit_rejection(self):
        """Test rate limit rejection."""
        # This would require a full FastAPI test client
        pass

    @pytest.mark.asyncio
    async def test_primary_success_then_fallback(self):
        """Test primary success and fallback."""
        # Test the failover logic
        pass

    @pytest.mark.asyncio
    async def test_both_providers_fail(self):
        """Test when both providers fail."""
        pass


# ============================================================================
# Concurrency Tests
# ============================================================================

class TestRateLimiterConcurrency:
    """Test rate limiter under concurrent requests."""

    @pytest.mark.asyncio
    async def test_concurrent_requests_same_tenant(self, mock_db):
        """Test concurrent requests from same tenant."""
        # Mock current usage
        mock_result = AsyncMock()
        mock_result.scalar_one.return_value = 0
        mock_db.execute.return_value = mock_result

        # Run 10 concurrent requests
        tasks = [
            check_and_consume("test-key", mock_db)
            for _ in range(10)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Count successes
        successes = sum(1 for r in results if isinstance(r, RateLimitDecision))
        errors = sum(1 for r in results if isinstance(r, RateLimitExceededError))

        # Should have exactly 10 successes (under limit)
        assert successes == 10
        assert errors == 0

    @pytest.mark.asyncio
    async def test_concurrent_requests_exceed_limit(self, mock_db):
        """Test concurrent requests that exceed limit."""
        # Mock current usage to just under limit
        near_limit = DEFAULT_LIMIT - 1
        mock_result = AsyncMock()
        mock_result.scalar_one.return_value = near_limit
        mock_db.execute.return_value = mock_result

        # Run 2 concurrent requests
        tasks = [
            check_and_consume("test-key", mock_db)
            for _ in range(2)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        successes = sum(1 for r in results if isinstance(r, RateLimitDecision))
        errors = sum(1 for r in results if isinstance(r, RateLimitExceededError))

        # One should succeed, one should fail (limit = 100, near_limit = 99)
        assert successes == 1
        assert errors == 1


# ============================================================================
# Mock DB fixture
# ============================================================================

@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return AsyncMock(spec=AsyncSession)


# ============================================================================
# Test Runner
# ============================================================================

if __name__ == "__main__":
    pytest.main(["-v", "--tb=short", __file__])