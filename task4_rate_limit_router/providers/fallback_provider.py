"""
task4_rate_limit_router/providers/fallback_provider.py

Secondary/fallback upstream provider.
"""

import logging
import random
import os

from sqlalchemy.ext.asyncio import AsyncSession

from task2_gateway.authorization.proxy import DownstreamToolError, TOOL_CATALOG

logger = logging.getLogger("task4.fallback_provider")

FAILURE_RATE = float(os.getenv("FALLBACK_FAILURE_RATE", "0.01"))


class FallbackProvider:
    """Fallback upstream provider. Very reliable."""
    
    _initialized = False

    async def list_tools(self) -> list[dict]:
        return TOOL_CATALOG

    async def call_tool(
        self,
        name: str,
        arguments: dict,
        db: AsyncSession,
        *,
        force_fail: bool = False,
    ) -> dict:
        if force_fail or (not FallbackProvider._initialized and random.random() < 0.05):
            logger.warning("Fallback provider cold-start failure (simulated)")
            FallbackProvider._initialized = True
            raise RuntimeError("Fallback provider initializing...")

        FallbackProvider._initialized = True

        if name == "get_customer_record":
            from task1_mcp_server.tools.get_customer_record import (
                get_customer_record,
            )
            try:
                result = await get_customer_record(arguments, db)
            except Exception as exc:
                raise DownstreamToolError(
                    code=getattr(exc, "code", -32603),
                    message=str(exc),
                    data=getattr(exc, "data", None),
                ) from exc
            return result.model_dump(mode="json")

        if name == "trigger_refund":
            from task1_mcp_server.tools.trigger_refund import trigger_refund
            try:
                result = await trigger_refund(arguments, db)
            except Exception as exc:
                raise DownstreamToolError(
                    code=getattr(exc, "code", -32603),
                    message=str(exc),
                    data=getattr(exc, "data", None),
                ) from exc
            return result.model_dump(mode="json")

        if name == "admin_reset_key":
            tenant = arguments.get("tenant_name", "unknown-tenant")
            return {
                "tenant_name": tenant,
                "status": "rotated",
                "note": "Mock action from fallback provider.",
            }

        raise DownstreamToolError(code=-32601, message=f"Unknown tool: '{name}'")

    @property
    def is_available(self) -> bool:
        return FallbackProvider._initialized