"""
task2_gateway/authorization/proxy.py

The actual reverse proxy: an HTTP endpoint that accepts JSON-RPC 2.0
requests, authenticates the caller, authorizes the specific call, and
forwards approved requests downstream.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from databaseinit.db import get_db
from task1_mcp_server.apperror import McpError
from task1_mcp_server.tools.get_customer_record import get_customer_record
from task1_mcp_server.tools.trigger_refund import trigger_refund
from task2_gateway.authorization.auth import AuthenticationError, Principal, authenticate
from task2_gateway.authorization.authorization import (
    AuthorizationError,
    authorize_request,
)

logger = logging.getLogger("task2_gateway.proxy")

router = APIRouter(prefix="/gateway", tags=["mcp-gateway"])


# ---------------------------------------------------------------------------
# JSON-RPC envelope models
# ---------------------------------------------------------------------------

class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Any] = None
    method: str
    params: Optional[dict] = None

    @field_validator("jsonrpc")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value != "2.0":
            raise ValueError("jsonrpc version must be exactly '2.0'")
        return value

    @field_validator("method")
    @classmethod
    def validate_method_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("method must not be empty")
        return value


def _success_response(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


# ---------------------------------------------------------------------------
# Downstream server interface + mock implementation
# ---------------------------------------------------------------------------

TOOL_CATALOG = [
    {
        "name": "get_customer_record",
        "description": "Look up a customer's account details.",
    },
    {
        "name": "trigger_refund",
        "description": "Issue a refund against a customer's invoice.",
    },
    {
        "name": "admin_reset_key",
        "description": (
            "Admin-only: rotate a tenant's API key. Exists specifically "
            "to exercise the gateway's admin_ authorization rule."
        ),
    },
]


class DownstreamToolError(Exception):
    """Raised by the downstream server for tool-execution failures."""
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


class MockDownstreamServer:
    """Default downstream implementation."""
    
    async def list_tools(self) -> list[dict]:
        return TOOL_CATALOG

    async def call_tool(
        self, name: str, arguments: dict, db: AsyncSession
    ) -> dict:
        if name == "get_customer_record":
            try:
                result = await get_customer_record(arguments, db)
            except ValidationError as exc:
                raise DownstreamToolError(
                    -32602, "Invalid parameters",
                    data={"field_errors": [
                        {"field": ".".join(str(x) for x in e["loc"]), "reason": e["msg"]}
                        for e in exc.errors()
                    ]},
                ) from exc
            except McpError as exc:
                raise DownstreamToolError(exc.code, exc.message, exc.data) from exc
            return result.model_dump(mode="json")

        if name == "trigger_refund":
            try:
                result = await trigger_refund(arguments, db)
            except ValidationError as exc:
                raise DownstreamToolError(
                    -32602, "Invalid parameters",
                    data={"field_errors": [
                        {"field": ".".join(str(x) for x in e["loc"]), "reason": e["msg"]}
                        for e in exc.errors()
                    ]},
                ) from exc
            except McpError as exc:
                raise DownstreamToolError(exc.code, exc.message, exc.data) from exc
            return result.model_dump(mode="json")

        if name == "admin_reset_key":
            tenant = arguments.get("tenant_name", "unknown-tenant")
            return {
                "tenant_name": tenant,
                "status": "rotated",
                "note": "Mock action — no real key was changed.",
            }

        raise DownstreamToolError(code=-32601, message=f"Unknown tool: '{name}'")


_downstream = MockDownstreamServer()


# ---------------------------------------------------------------------------
# The proxy endpoint
# ---------------------------------------------------------------------------

@router.post("/mcp")
async def proxy_mcp_request(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """Single entrypoint for all JSON-RPC traffic through the gateway."""
    body = await request.json()
    try:
        rpc_request = JsonRpcRequest.model_validate(body)
    except Exception:
        return JSONResponse(
            status_code=400,
            content=_error_response(None, -32600, "Invalid JSON-RPC request"),
        )

    try:
        principal: Principal = await authenticate(authorization, db)
    except AuthenticationError as exc:
        logger.info("Authentication failed: %s", exc.message)
        return JSONResponse(
            status_code=401,
            content=_error_response(rpc_request.id, -32000, exc.message),
        )

    try:
        authorize_request(rpc_request.method, rpc_request.params, principal)
    except AuthorizationError as exc:
        return JSONResponse(
            status_code=200,
            content=_error_response(rpc_request.id, exc.code, exc.message),
        )

    if rpc_request.method == "tools/list":
        tools = await _downstream.list_tools()
        return JSONResponse(
            content=_success_response(rpc_request.id, {"tools": tools})
        )

    if rpc_request.method == "tools/call":
        params = rpc_request.params or {}
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        try:
            result = await _downstream.call_tool(tool_name, arguments, db)
        except ValidationError as exc:
            return JSONResponse(
                content=_error_response(
                    rpc_request.id, -32602, "Invalid parameters",
                    data={"field_errors": [
                        {"field": ".".join(str(x) for x in e["loc"]), "reason": e["msg"]}
                        for e in exc.errors()
                    ]},
                )
            )
        except DownstreamToolError as exc:
            return JSONResponse(
                content=_error_response(
                    rpc_request.id, exc.code, exc.message, exc.data
                )
            )

        return JSONResponse(
            content=_success_response(
                rpc_request.id, {"content": result, "isError": False}
            )
        )

    return JSONResponse(
        content=_error_response(
            rpc_request.id, -32601, f"Method not found: '{rpc_request.method}'"
        )
    )