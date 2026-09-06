"""
tests/test_task2_gateway.py

Unit tests for Task 2: Gateway with Authentication and Authorization
"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from task2_gateway.authorization.auth import (
    AuthenticationError,
    Principal,
    authenticate,
    parse_bearer_token,
    resolve_principal,
)
from task2_gateway.authorization.authorization import (
    AuthorizationError,
    authorize_request,
    requires_admin,
)
from task2_gateway.proxy import (
    DownstreamToolError,
    JsonRpcRequest,
    MockDownstreamServer,
    router,
)

from databaseinit.models import ApiKeyRole


# ============================================================================
# Tests for auth.py
# ============================================================================

class TestAuth:
    """Test suite for authentication module."""

    def test_parse_bearer_token_valid(self):
        """Test parsing valid bearer token."""
        token = parse_bearer_token("Bearer valid-token-123")
        assert token == "valid-token-123"

    def test_parse_bearer_token_whitespace(self):
        """Test parsing token with whitespace."""
        token = parse_bearer_token("  Bearer   valid-token-123  ")
        assert token == "valid-token-123"

    def test_parse_bearer_token_missing(self):
        """Test error when Authorization header is missing."""
        with pytest.raises(AuthenticationError) as exc:
            parse_bearer_token(None)
        assert "Missing Authorization header" in str(exc.value)

    def test_parse_bearer_token_empty(self):
        """Test error when token is empty."""
        with pytest.raises(AuthenticationError) as exc:
            parse_bearer_token("Bearer ")
        assert "Bearer <token>" in str(exc.value)

    def test_parse_bearer_token_wrong_scheme(self):
        """Test error when scheme is not Bearer."""
        with pytest.raises(AuthenticationError) as exc:
            parse_bearer_token("Basic token-123")
        assert "Bearer <token>" in str(exc.value)

    def test_parse_bearer_token_invalid_format(self):
        """Test error when format is invalid."""
        with pytest.raises(AuthenticationError) as exc:
            parse_bearer_token("Bearer")
        assert "Bearer <token>" in str(exc.value)

    @pytest.mark.asyncio
    async def test_resolve_principal_cache_hit(self, mock_db):
        """Test resolving principal from cache."""
        principal = Principal(
            token="cached-token",
            tenant_name="test-tenant",
            role=ApiKeyRole.ADMIN
        )
        # Should hit cache (mock cache set)
        result = await resolve_principal("cached-token", mock_db)
        # Note: In actual test, you'd need to mock the cache

    @pytest.mark.asyncio
    async def test_resolve_principal_cache_miss(self, mock_db):
        """Test resolving principal from database."""
        mock_api_key = AsyncMock()
        mock_api_key.key = "db-token"
        mock_api_key.tenant_name = "db-tenant"
        mock_api_key.role = ApiKeyRole.VIEWER
        mock_api_key.is_active = True

        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = mock_api_key
        mock_db.execute.return_value = mock_result

        principal = await resolve_principal("db-token", mock_db)
        assert principal.token == "db-token"
        assert principal.tenant_name == "db-tenant"
        assert principal.role == ApiKeyRole.VIEWER

    @pytest.mark.asyncio
    async def test_resolve_principal_unknown_token(self, mock_db):
        """Test error when token is unknown."""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(AuthenticationError) as exc:
            await resolve_principal("unknown-token", mock_db)
        assert "Invalid or unknown API token" in str(exc.value)

    @pytest.mark.asyncio
    async def test_resolve_principal_inactive_token(self, mock_db):
        """Test error when token is inactive."""
        mock_api_key = AsyncMock()
        mock_api_key.is_active = False
        mock_api_key.tenant_name = "test-tenant"

        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = mock_api_key
        mock_db.execute.return_value = mock_result

        with pytest.raises(AuthenticationError) as exc:
            await resolve_principal("inactive-token", mock_db)
        assert "This API token has been deactivated" in str(exc.value)

    def test_principal_properties(self):
        """Test Principal properties."""
        admin = Principal("token", "tenant", ApiKeyRole.ADMIN)
        viewer = Principal("token", "tenant", ApiKeyRole.VIEWER)
        
        assert admin.is_admin is True
        assert viewer.is_admin is False


# ============================================================================
# Tests for authorization.py
# ============================================================================

class TestAuthorization:
    """Test suite for authorization module."""

    def test_requires_admin(self):
        """Test admin prefix detection."""
        assert requires_admin("admin_reset_key") is True
        assert requires_admin("admin_something") is True
        assert requires_admin("get_customer_record") is False
        assert requires_admin("trigger_refund") is False
        assert requires_admin("tools/list") is False

    def test_authorize_request_tools_list(self):
        """Test tools/list always passes auth."""
        admin = Principal("token", "tenant", ApiKeyRole.ADMIN)
        viewer = Principal("token", "tenant", ApiKeyRole.VIEWER)
        
        # Both should pass
        authorize_request("tools/list", None, admin)
        authorize_request("tools/list", None, viewer)

    def test_authorize_request_non_admin_tool(self):
        """Test non-admin tool with any role."""
        viewer = Principal("token", "tenant", ApiKeyRole.VIEWER)
        authorize_request(
            "tools/call",
            {"name": "get_customer_record"},
            viewer
        )
        # No exception raised = pass

    def test_authorize_request_admin_tool_with_admin(self):
        """Test admin tool with admin role."""
        admin = Principal("token", "tenant", ApiKeyRole.ADMIN)
        authorize_request(
            "tools/call",
            {"name": "admin_reset_key"},
            admin
        )
        # No exception raised = pass

    def test_authorize_request_admin_tool_with_viewer(self):
        """Test admin tool with viewer role (should fail)."""
        viewer = Principal("token", "tenant", ApiKeyRole.VIEWER)
        
        with pytest.raises(AuthorizationError) as exc:
            authorize_request(
                "tools/call",
                {"name": "admin_reset_key"},
                viewer
            )
        assert "requires admin role" in str(exc.value)
        assert exc.value.code == -32001

    def test_authorize_request_unknown_method(self):
        """Test unknown method passes through."""
        viewer = Principal("token", "tenant", ApiKeyRole.VIEWER)
        # Should not raise an error
        authorize_request("unknown_method", None, viewer)


# ============================================================================
# Tests for proxy.py
# ============================================================================

class TestMockDownstreamServer:
    """Test suite for MockDownstreamServer."""

    @pytest.mark.asyncio
    async def test_list_tools(self):
        """Test listing tools."""
        server = MockDownstreamServer()
        tools = await server.list_tools()
        
        assert len(tools) == 3
        tool_names = [t["name"] for t in tools]
        assert "get_customer_record" in tool_names
        assert "trigger_refund" in tool_names
        assert "admin_reset_key" in tool_names

    @pytest.mark.asyncio
    @patch("task2_gateway.proxy.get_customer_record")
    async def test_call_tool_get_customer(self, mock_get_customer, mock_db):
        """Test calling get_customer_record tool."""
        mock_get_customer.return_value = AsyncMock()
        mock_get_customer.return_value.model_dump.return_value = {
            "customer_id": "CUST-10001",
            "name": "Test Customer"
        }

        server = MockDownstreamServer()
        result = await server.call_tool(
            "get_customer_record",
            {"customer_id": "CUST-10001"},
            mock_db
        )
        
        assert result["customer_id"] == "CUST-10001"
        mock_get_customer.assert_called_once()

    @pytest.mark.asyncio
    @patch("task2_gateway.proxy.trigger_refund")
    async def test_call_tool_trigger_refund(self, mock_trigger_refund, mock_db):
        """Test calling trigger_refund tool."""
        mock_trigger_refund.return_value = AsyncMock()
        mock_trigger_refund.return_value.model_dump.return_value = {
            "refund_id": "ref-001",
            "status": "completed"
        }

        server = MockDownstreamServer()
        result = await server.call_tool(
            "trigger_refund",
            {"customer_id": "CUST-10001", "amount": "25.00", "reason": "Test"},
            mock_db
        )
        
        assert result["refund_id"] == "ref-001"
        mock_trigger_refund.assert_called_once()

    @pytest.mark.asyncio
    async def test_call_tool_admin_reset_key(self, mock_db):
        """Test calling admin_reset_key tool."""
        server = MockDownstreamServer()
        result = await server.call_tool(
            "admin_reset_key",
            {"tenant_name": "test-tenant"},
            mock_db
        )
        
        assert result["tenant_name"] == "test-tenant"
        assert result["status"] == "rotated"

    @pytest.mark.asyncio
    async def test_call_tool_unknown(self, mock_db):
        """Test error for unknown tool."""
        server = MockDownstreamServer()
        
        with pytest.raises(DownstreamToolError) as exc:
            await server.call_tool(
                "unknown_tool",
                {},
                mock_db
            )
        assert exc.value.code == -32601
        assert "Unknown tool" in str(exc.value)

    @pytest.mark.asyncio
    async def test_call_tool_downstream_error(self, mock_db):
        """Test error propagation from downstream."""
        server = MockDownstreamServer()
        
        with pytest.raises(DownstreamToolError):
            await server.call_tool(
                "get_customer_record",
                {"customer_id": "CUST-99999"},  # Non-existent customer
                mock_db
            )


class TestJsonRpcRequest:
    """Test suite for JSON-RPC request model."""

    def test_valid_request(self):
        """Test valid JSON-RPC request."""
        data = JsonRpcRequest(
            jsonrpc="2.0",
            id=1,
            method="tools/list",
            params={"name": "test"}
        )
        assert data.jsonrpc == "2.0"
        assert data.id == 1
        assert data.method == "tools/list"
        assert data.params == {"name": "test"}

    def test_request_without_params(self):
        """Test request without params."""
        data = JsonRpcRequest(jsonrpc="2.0", id=1, method="tools/list")
        assert data.params is None