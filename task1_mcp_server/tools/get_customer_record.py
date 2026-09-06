"""
task1_mcp_server/tools/get_customer_record.py

Handler for the `get_customer_record` MCP tool.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from databaseinit.models import Customer
from task1_mcp_server.apperror import CustomerNotFoundError
from task1_mcp_server.schemas import (
    GetCustomerRecordInput,
    GetCustomerRecordOutput,
    InvoiceSummary,
)


async def get_customer_record(
    raw_input: dict, db: AsyncSession
) -> GetCustomerRecordOutput:
    """
    Main entrypoint called by server.py's tool dispatcher.
    """
    parsed = GetCustomerRecordInput.model_validate(raw_input)

    stmt = (
        select(Customer)
        .where(Customer.customer_id == parsed.customer_id)
        .options(selectinload(Customer.invoices))
    )
    result = await db.execute(stmt)
    customer = result.scalar_one_or_none()

    if customer is None:
        raise CustomerNotFoundError(
            message=f"No customer found with id '{parsed.customer_id}'",
            data={"customer_id": parsed.customer_id},
        )

    sorted_invoices = sorted(
        customer.invoices, key=lambda inv: inv.issued_at, reverse=True
    )

    return GetCustomerRecordOutput(
        customer_id=customer.customer_id,
        name=customer.name,
        email=customer.email,
        plan=customer.plan,
        billing_status=customer.billing_status.value,
        monthly_usage_units=customer.monthly_usage_units,
        invoices=[
            InvoiceSummary(
                id=inv.id,
                amount=inv.amount,
                amount_refunded=inv.amount_refunded,
                status=inv.status.value,
                description=inv.description,
                issued_at=inv.issued_at.isoformat(),
            )
            for inv in sorted_invoices
        ],
    )