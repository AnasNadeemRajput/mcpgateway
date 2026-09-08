"""
task2_gateway/authorization/authorization.py

The actual gate: decides whether a given JSON-RPC request is allowed to
proceed, based on the resolved Principal from auth.py.
"""

import logging

from task2_gateway.authorization.auth import Principal

logger = logging.getLogger("task2_gateway.authorization")

ADMIN_TOOL_PREFIX = "admin_"
UNAUTHORIZED_TOOL_CALL_CODE = -32001


class AuthorizationError(Exception):
    """Raised when a principal is not permitted to make a given tool call."""
    code = UNAUTHORIZED_TOOL_CALL_CODE

    def __init__(self, message: str, tool_name: str | None = None):
        self.message = message
        self.tool_name = tool_name
        super().__init__(message)


def requires_admin(tool_name: str) -> bool:
    """Returns True if a tool name is admin-gated by naming convention."""
    return tool_name.startswith(ADMIN_TOOL_PREFIX)


def authorize_request(
    method: str, params: dict | None, principal: Principal
) -> None:
    """Checks whether `principal` is allowed to make this JSON-RPC request."""
    if method == "tools/list":
        return

    if method == "tools/call":
        tool_name = (params or {}).get("name", "")

        if not requires_admin(tool_name):
            return

        if not principal.is_admin:
            logger.warning(
                "Blocked admin tool call: tenant='%s' role='%s' tool='%s'",
                principal.tenant_name, principal.role.value, tool_name,
            )
            raise AuthorizationError(
                message=(
                    f"Tool '{tool_name}' requires admin role; "
                    f"caller has role '{principal.role.value}'"
                ),
                tool_name=tool_name,
            )
        return

    logger.debug("No authorization rule for method '%s' — forwarding.", method)
    return