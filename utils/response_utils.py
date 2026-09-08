"""
utils/response_utils.py

Shared HTTP response utilities.
"""

from typing import Any, Optional

from fastapi.responses import JSONResponse


def json_success(data: Any, status_code: int = 200) -> JSONResponse:
    """Returns a JSON success response."""
    return JSONResponse(
        status_code=status_code,
        content=data,
    )


def json_error(
    message: str,
    status_code: int = 400,
    details: Optional[dict] = None
) -> JSONResponse:
    """Returns a JSON error response."""
    content = {"error": message}
    if details:
        content["details"] = details
    return JSONResponse(
        status_code=status_code,
        content=content,
    )


def jsonrpc_error_response(
    request_id: Any,
    code: int,
    message: str,
    data: Optional[Any] = None
) -> dict:
    """Creates a JSON-RPC error response."""
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}