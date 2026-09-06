"""
task1_mcp_server/apperror.py

Standard JSON-RPC 2.0 error codes + MCP-specific and domain-specific
error codes, plus a small exception hierarchy.
"""

from typing import Any, Optional


# ---------------------------------------------------------------------------
# Standard JSON-RPC 2.0 error codes
# ---------------------------------------------------------------------------

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# MCP / domain-specific error codes
# ---------------------------------------------------------------------------

UNAUTHORIZED_TOOL_CALL = -32001
CUSTOMER_NOT_FOUND = -32002
INVOICE_NOT_FOUND = -32003
NO_ELIGIBLE_INVOICE = -32004
REFUND_EXCEEDS_AVAILABLE = -32005
RATE_LIMIT_EXCEEDED = -32006
UPSTREAM_UNAVAILABLE = -32007


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class McpError(Exception):
    """Base exception for all MCP tool errors."""

    code: int = INTERNAL_ERROR
    message: str = "Internal server error"

    def __init__(self, message: Optional[str] = None, data: Optional[Any] = None):
        self.message = message or self.message
        self.data = data
        super().__init__(self.message)

    def to_jsonrpc_error(self) -> dict:
        error: dict = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


class InvalidParamsError(McpError):
    code = INVALID_PARAMS
    message = "Invalid parameters"


class MethodNotFoundError(McpError):
    code = METHOD_NOT_FOUND
    message = "Method not found"


class UnauthorizedToolCallError(McpError):
    code = UNAUTHORIZED_TOOL_CALL
    message = "Unauthorized tool call"


class CustomerNotFoundError(McpError):
    code = CUSTOMER_NOT_FOUND
    message = "Customer not found"


class InvoiceNotFoundError(McpError):
    code = INVOICE_NOT_FOUND
    message = "Invoice not found"


class NoEligibleInvoiceError(McpError):
    code = NO_ELIGIBLE_INVOICE
    message = "Customer has no invoice eligible for refund"


class RefundExceedsAvailableError(McpError):
    code = REFUND_EXCEEDS_AVAILABLE
    message = "Refund amount exceeds the invoice's remaining refundable balance"


class RateLimitExceededError(McpError):
    code = RATE_LIMIT_EXCEEDED
    message = "Rate limit exceeded"


class UpstreamUnavailableError(McpError):
    code = UPSTREAM_UNAVAILABLE
    message = "All upstream providers are currently unavailable"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def from_pydantic_validation_error(exc) -> InvalidParamsError:
    """Converts a pydantic.ValidationError into an InvalidParamsError."""
    field_errors = [
        {
            "field": ".".join(str(loc) for loc in err["loc"]),
            "reason": err["msg"],
        }
        for err in exc.errors()
    ]
    return InvalidParamsError(
        message="One or more parameters failed validation",
        data={"field_errors": field_errors},
    )


def build_jsonrpc_error_response(
    request_id: Optional[Any], error: McpError
) -> dict:
    """Wraps an McpError into a full JSON-RPC 2.0 error response envelope."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error.to_jsonrpc_error(),
    }


def build_internal_error_response(request_id: Optional[Any]) -> dict:
    """Fallback for truly unexpected exceptions."""
    return build_jsonrpc_error_response(request_id, McpError())