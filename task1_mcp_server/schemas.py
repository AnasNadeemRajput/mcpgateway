"""
task1_mcp_server/schemas.py

Pydantic schemas for the two MCP tools:
  - get_customer_record
  - trigger_refund
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from pydantic import BaseModel, Field, field_validator

CUSTOMER_ID_PATTERN = re.compile(r"^CUST-\d{5}$")


# ---------------------------------------------------------------------------
# get_customer_record
# ---------------------------------------------------------------------------

class GetCustomerRecordInput(BaseModel):
    customer_id: str = Field(
        ...,
        description="Customer identifier in the format CUST-XXXXX (5 digits).",
        examples=["CUST-10001"],
    )

    @field_validator("customer_id")
    @classmethod
    def validate_customer_id_format(cls, value: str) -> str:
        value = value.strip()
        if not CUSTOMER_ID_PATTERN.match(value):
            raise ValueError(
                "customer_id must match the format CUST-XXXXX "
                "(e.g. CUST-10001)"
            )
        return value


class InvoiceSummary(BaseModel):
    id: str
    amount: Decimal
    amount_refunded: Decimal
    status: str
    description: Optional[str] = None
    issued_at: str


class GetCustomerRecordOutput(BaseModel):
    customer_id: str
    name: str
    email: str
    plan: str
    billing_status: str
    monthly_usage_units: int
    invoices: list[InvoiceSummary] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# trigger_refund
# ---------------------------------------------------------------------------

class TriggerRefundInput(BaseModel):
    customer_id: str = Field(
        ...,
        description="Customer identifier in the format CUST-XXXXX.",
        examples=["CUST-10001"],
    )
    amount: Decimal = Field(
        ...,
        gt=0,
        description="Refund amount. Must be a positive number.",
        examples=["25.00"],
    )
    reason: str = Field(
        ...,
        min_length=10,
        description="Human-readable refund justification, minimum 10 characters.",
        examples=["Customer reported duplicate charge on invoice."],
    )
    invoice_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional specific invoice to refund against. If omitted, "
            "the customer's most recent eligible invoice is used."
        ),
    )

    @field_validator("customer_id")
    @classmethod
    def validate_customer_id_format(cls, value: str) -> str:
        value = value.strip()
        if not CUSTOMER_ID_PATTERN.match(value):
            raise ValueError(
                "customer_id must match the format CUST-XXXXX "
                "(e.g. CUST-10001)"
            )
        return value

    @field_validator("reason")
    @classmethod
    def validate_reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 10:
            raise ValueError(
                "reason must contain at least 10 non-whitespace characters"
            )
        return value

    @field_validator("amount")
    @classmethod
    def validate_amount_precision(cls, value: Decimal) -> Decimal:
        try:
            exponent = value.as_tuple().exponent
        except (InvalidOperation, AttributeError) as exc:
            raise ValueError("amount must be a valid decimal number") from exc

        if isinstance(exponent, int) and exponent < -2:
            raise ValueError("amount must have at most 2 decimal places")
        return value


class TriggerRefundOutput(BaseModel):
    refund_id: str
    invoice_id: str
    customer_id: str
    amount: Decimal
    reason: str
    status: str
    created_at: str