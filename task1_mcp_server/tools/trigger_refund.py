"""
task1_mcp_server/tools/trigger_refund.py

Handler for the `trigger_refund` MCP tool.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from databaseinit.models import Customer, Invoice, InvoiceStatus, Refund
from task1_mcp_server.apperror import (
    CustomerNotFoundError,
    InvoiceNotFoundError,
    NoEligibleInvoiceError,
    RefundExceedsAvailableError,
)
from task1_mcp_server.schemas import TriggerRefundInput, TriggerRefundOutput


async def _resolve_target_invoice(
    db: AsyncSession, customer_id: str, requested_invoice_id: str | None
) -> Invoice:
    """
    Returns the invoice to refund against.
    """
    if requested_invoice_id:
        stmt = (
            select(Invoice)
            .where(
                Invoice.id == requested_invoice_id,
                Invoice.customer_id == customer_id,
            )
        )
        result = await db.execute(stmt)
        invoice = result.scalar_one_or_none()
        if invoice is None:
            raise InvoiceNotFoundError(
                message=(
                    f"Invoice '{requested_invoice_id}' not found for "
                    f"customer '{customer_id}'"
                ),
                data={"invoice_id": requested_invoice_id},
            )
        return invoice

    stmt = (
        select(Invoice)
        .where(
            Invoice.customer_id == customer_id,
            Invoice.amount_refunded < Invoice.amount,
        )
        .order_by(Invoice.issued_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    invoice = result.scalar_one_or_none()
    if invoice is None:
        raise NoEligibleInvoiceError(
            message=(
                f"Customer '{customer_id}' has no invoice with a "
                "remaining refundable balance"
            ),
            data={"customer_id": customer_id},
        )
    return invoice


async def trigger_refund(
    raw_input: dict, db: AsyncSession
) -> TriggerRefundOutput:
    """
    Main entrypoint called by server.py's tool dispatcher.
    """
    parsed = TriggerRefundInput.model_validate(raw_input)

    customer_exists = await db.execute(
        select(Customer.customer_id).where(
            Customer.customer_id == parsed.customer_id
        )
    )
    if customer_exists.scalar_one_or_none() is None:
        raise CustomerNotFoundError(
            message=f"No customer found with id '{parsed.customer_id}'",
            data={"customer_id": parsed.customer_id},
        )

    invoice = await _resolve_target_invoice(
        db, parsed.customer_id, parsed.invoice_id
    )

    remaining_balance: Decimal = invoice.amount - invoice.amount_refunded
    if parsed.amount > remaining_balance:
        raise RefundExceedsAvailableError(
            message=(
                f"Requested refund of {parsed.amount} exceeds the "
                f"remaining refundable balance of {remaining_balance} "
                f"on invoice '{invoice.id}'"
            ),
            data={
                "invoice_id": invoice.id,
                "requested_amount": str(parsed.amount),
                "remaining_balance": str(remaining_balance),
            },
        )

    refund = Refund(
        invoice_id=invoice.id,
        customer_id=parsed.customer_id,
        amount=parsed.amount,
        reason=parsed.reason,
    )
    db.add(refund)

    invoice.amount_refunded += parsed.amount
    invoice.status = (
        InvoiceStatus.REFUNDED
        if invoice.amount_refunded >= invoice.amount
        else InvoiceStatus.PARTIALLY_REFUNDED
    )

    await db.commit()
    await db.refresh(refund)

    return TriggerRefundOutput(
        refund_id=refund.id,
        invoice_id=invoice.id,
        customer_id=parsed.customer_id,
        amount=refund.amount,
        reason=refund.reason,
        status=refund.status.value,
        created_at=refund.created_at.isoformat(),
    )