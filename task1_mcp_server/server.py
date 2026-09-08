"""
task1_mcp_server/server.py

The actual MCP server: connects via stdio transport, advertises the two
tools, and dispatches tools/call requests to their handlers.

Uses the official MCP Python SDK v2 pattern with `serve()`.
"""

import asyncio
import logging
import sys

from mcp.server import serve
from mcp.server.models import InitializationOptions
from mcp.types import Tool, TextContent

from databaseinit.db import AsyncSessionLocal
from task1_mcp_server.apperror import McpError
from task1_mcp_server.schemas import (
    GetCustomerRecordInput,
    TriggerRefundInput,
)
from task1_mcp_server.tools.get_customer_record import (
    get_customer_record as get_customer_record_handler,
)
from task1_mcp_server.tools.trigger_refund import (
    trigger_refund as trigger_refund_handler,
)

# ---------------------------------------------------------------------------
# Logging: stderr ONLY
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("task1_mcp_server")

SERVER_NAME = "customer-service-mcp-server"
SERVER_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Tool definitions (v2 SDK)
# ---------------------------------------------------------------------------

# Tool definitions for the MCP server
TOOLS = [
    Tool(
        name="get_customer_record",
        description="Look up a customer's account details, including plan, "
                    "billing status, usage, and invoice history.",
        inputSchema=GetCustomerRecordInput.model_json_schema(),
    ),
    Tool(
        name="trigger_refund",
        description="Issue a refund against a customer's most recent (or specified) "
                    "invoice. Requires a justification reason of at least 10 characters.",
        inputSchema=TriggerRefundInput.model_json_schema(),
    ),
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def handle_get_customer_record(params: dict) -> list[TextContent]:
    """Handler for get_customer_record tool."""
    async with AsyncSessionLocal() as db:
        try:
            result = await get_customer_record_handler(params, db)
            return [TextContent(type="text", text=result.model_dump_json())]
        except McpError as exc:
            logger.info("get_customer_record rejected: %s [%s]", exc.message, exc.code)
            return [TextContent(type="text", text=f"[{exc.code}] {exc.message}")]
        except Exception as exc:
            logger.exception("Unexpected error in get_customer_record")
            return [TextContent(type="text", text=f"Internal error: {str(exc)}")]


async def handle_trigger_refund(params: dict) -> list[TextContent]:
    """Handler for trigger_refund tool."""
    async with AsyncSessionLocal() as db:
        try:
            result = await trigger_refund_handler(params, db)
            return [TextContent(type="text", text=result.model_dump_json())]
        except McpError as exc:
            logger.info("trigger_refund rejected: %s [%s]", exc.message, exc.code)
            return [TextContent(type="text", text=f"[{exc.code}] {exc.message}")]
        except Exception as exc:
            logger.exception("Unexpected error in trigger_refund")
            return [TextContent(type="text", text=f"Internal error: {str(exc)}")]


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main():
    """Main entrypoint for the MCP server."""
    logger.info("Starting %s v%s over stdio transport", SERVER_NAME, SERVER_VERSION)

    async with serve(
        SERVER_NAME,
        version=SERVER_VERSION,
        tools=TOOLS,
    ) as server:
        # Register tool handlers
        @server.call_tool
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            if name == "get_customer_record":
                return await handle_get_customer_record(arguments)
            elif name == "trigger_refund":
                return await handle_trigger_refund(arguments)
            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

        # Keep the server running
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())