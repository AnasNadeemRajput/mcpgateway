"""
utils/jsonrpc_utils.py

Shared utilities for JSON-RPC 2.0 message handling.
"""

from typing import Any, Optional


def create_success_response(request_id: Any, result: Any) -> dict:
    """Creates a JSON-RPC 2.0 success response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def create_error_response(
    request_id: Any,
    code: int,
    message: str,
    data: Optional[Any] = None
) -> dict:
    """Creates a JSON-RPC 2.0 error response."""
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error,
    }


def is_jsonrpc_request(obj: dict) -> bool:
    """Checks if a dict is a valid JSON-RPC request."""
    return (
        obj.get("jsonrpc") == "2.0" and
        "method" in obj and
        isinstance(obj.get("method"), str)
    )