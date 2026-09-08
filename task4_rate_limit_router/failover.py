"""
task4_rate_limit_router/failover.py

The actual router that sits between the rate limiter and the upstream
providers. Implements failover with primary → fallback.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from databaseinit.db import get_db
from task2_gateway.authorization.auth import (
    AuthenticationError,
    Principal,
    authenticate,
)
from task2_gateway.authorization.authorization import (
    AuthorizationError,
    authorize_request,
)
from task2_gateway.authorization.proxy import _error_response, _success_response
from task4_rate_limit_router.providers.fallback_provider import FallbackProvider
from task4_rate_limit_router.providers.primary_provider import PrimaryProvider
from task4_rate_limit_router.rate_limiter import (
    RateLimitExceededError,
    check_and_consume,
    get_rate_limit_status,
)

logger = logging.getLogger("task4.failover_router")

router = APIRouter(prefix="/router", tags=["rate-limited-router"])

_primary = PrimaryProvider()
_fallback = FallbackProvider()


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Any] = None
    method: str
    params: Optional[dict] = None


@router.post("/mcp")
async def routed_mcp_request(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """Single entrypoint for all JSON-RPC traffic through the rate-limited router."""
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

    try:
        decision = await check_and_consume(principal.token, db)
    except RateLimitExceededError as exc:
        logger.warning(
            "Rate limit exceeded: tenant=%s usage=%d limit=%d",
            exc.tenant_name, exc.current_usage, exc.limit
        )
        return JSONResponse(
            status_code=429,
            content=_error_response(
                rpc_request.id,
                -32006,
                f"Rate limit exceeded: {exc.current_usage}/{exc.limit} tokens used",
                data={
                    "limit": exc.limit,
                    "current_usage": exc.current_usage,
                    "window_seconds": 60,
                },
            ),
        )

    if rpc_request.method == "tools/list":
        try:
            tools = await _primary.list_tools()
            return JSONResponse(
                content=_success_response(rpc_request.id, {"tools": tools})
            )
        except Exception as exc:
            logger.warning("Primary failed for tools/list: %s", exc)
            try:
                tools = await _fallback.list_tools()
                return JSONResponse(
                    content=_success_response(rpc_request.id, {"tools": tools})
                )
            except Exception as exc2:
                logger.error("Both providers failed for tools/list: %s", exc2)
                return JSONResponse(
                    content=_error_response(
                        rpc_request.id,
                        -32007,
                        "All upstream providers are currently unavailable",
                    )
                )

    if rpc_request.method == "tools/call":
        params = rpc_request.params or {}
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        try:
            result = await _primary.call_tool(tool_name, arguments, db)
            logger.info("Primary provider succeeded for tool '%s'", tool_name)
            return JSONResponse(
                content=_success_response(
                    rpc_request.id,
                    {"content": result, "isError": False},
                )
            )
        except Exception as exc:
            logger.warning(
                "Primary provider failed for tool '%s': %s. Trying fallback.",
                tool_name, exc
            )

            try:
                result = await _fallback.call_tool(tool_name, arguments, db)
                logger.info("Fallback provider succeeded for tool '%s'", tool_name)
                return JSONResponse(
                    content=_success_response(
                        rpc_request.id,
                        {"content": result, "isError": False},
                    )
                )
            except Exception as exc2:
                logger.error(
                    "Both providers failed for tool '%s': %s",
                    tool_name, exc2
                )
                return JSONResponse(
                    content=_error_response(
                        rpc_request.id,
                        -32007,
                        "All upstream providers are currently unavailable",
                        data={"tool": tool_name},
                    )
                )

    return JSONResponse(
        content=_error_response(
            rpc_request.id,
            -32601,
            f"Method not found: '{rpc_request.method}'",
        )
    )


@router.get("/status")
async def rate_limit_status(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """Returns the current rate limit status for the caller."""
    try:
        principal: Principal = await authenticate(authorization, db)
    except AuthenticationError as exc:
        return JSONResponse(
            status_code=401,
            content={"error": exc.message},
        )

    decision = await get_rate_limit_status(principal.token, db)

    return {
        "tenant": principal.tenant_name,
        "role": principal.role.value,
        "current_usage": decision.current_usage,
        "limit": decision.limit,
        "window_seconds": decision.window_seconds,
        "remaining": max(0, decision.limit - decision.current_usage),
        "reset_after_seconds": decision.reset_after_seconds,
    }