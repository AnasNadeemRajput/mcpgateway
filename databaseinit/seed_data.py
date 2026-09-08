"""
databaseinit/seed_data.py

Populates the database with mock data for all four tasks.
SQLite version - uses the same async session pattern.
"""

import logging

from sqlalchemy import select

from databaseinit.db import AsyncSessionLocal
from databaseinit.models import (
    ApiKey,
    ApiKeyRole,
    BillingStatus,
    Customer,
    Invoice,
    InvoiceStatus,
)

logger = logging.getLogger(__name__)


MOCK_CUSTOMERS = [
    {
        "customer_id": "CUST-10001",
        "name": "Ayesha Khan",
        "email": "ayesha.khan@example.com",
        "plan": "pro",
        "billing_status": BillingStatus.ACTIVE,
        "monthly_usage_units": 4200,
        "invoices": [
            {"amount": "49.00", "status": InvoiceStatus.PAID,
             "description": "Pro plan - monthly subscription"},
            {"amount": "49.00", "status": InvoiceStatus.PAID,
             "description": "Pro plan - monthly subscription"},
        ],
    },
    {
        "customer_id": "CUST-10002",
        "name": "Daniel Osei",
        "email": "daniel.osei@example.com",
        "plan": "starter",
        "billing_status": BillingStatus.PAST_DUE,
        "monthly_usage_units": 850,
        "invoices": [
            {"amount": "19.00", "status": InvoiceStatus.OPEN,
             "description": "Starter plan - monthly subscription"},
        ],
    },
    {
        "customer_id": "CUST-10003",
        "name": "Maria Lopez",
        "email": "maria.lopez@example.com",
        "plan": "enterprise",
        "billing_status": BillingStatus.ACTIVE,
        "monthly_usage_units": 98000,
        "invoices": [
            {"amount": "499.00", "status": InvoiceStatus.PAID,
             "description": "Enterprise plan - monthly subscription"},
            {"amount": "499.00", "status": InvoiceStatus.PARTIALLY_REFUNDED,
             "description": "Enterprise plan - monthly subscription"},
        ],
    },
    {
        "customer_id": "CUST-10004",
        "name": "Wei Zhang",
        "email": "wei.zhang@example.com",
        "plan": "free",
        "billing_status": BillingStatus.TRIALING,
        "monthly_usage_units": 120,
        "invoices": [],
    },
    {
        "customer_id": "CUST-10005",
        "name": "Fatima Al-Sayed",
        "email": "fatima.alsayed@example.com",
        "plan": "pro",
        "billing_status": BillingStatus.CANCELED,
        "monthly_usage_units": 0,
        "invoices": [
            {"amount": "49.00", "status": InvoiceStatus.REFUNDED,
             "description": "Pro plan - monthly subscription (final month)"},
        ],
    },
]

MOCK_API_KEYS = [
    {
        "key": "admin-token-abc123",
        "tenant_name": "internal-support-admin",
        "role": ApiKeyRole.ADMIN,
    },
    {
        "key": "viewer-token-xyz789",
        "tenant_name": "internal-support-viewer",
        "role": ApiKeyRole.VIEWER,
    },
]


async def seed_customers(session) -> None:
    existing = await session.execute(select(Customer.customer_id))
    existing_ids = {row[0] for row in existing.all()}

    inserted = 0
    for cust in MOCK_CUSTOMERS:
        if cust["customer_id"] in existing_ids:
            continue

        customer = Customer(
            customer_id=cust["customer_id"],
            name=cust["name"],
            email=cust["email"],
            plan=cust["plan"],
            billing_status=cust["billing_status"],
            monthly_usage_units=cust["monthly_usage_units"],
        )
        for inv in cust["invoices"]:
            customer.invoices.append(
                Invoice(
                    customer_id=cust["customer_id"],
                    amount=inv["amount"],
                    status=inv["status"],
                    description=inv["description"],
                )
            )

        session.add(customer)
        inserted += 1

    if inserted:
        logger.info("Seeded %d new customer(s).", inserted)
    else:
        logger.info("Customers already seeded — skipping.")


async def seed_api_keys(session) -> None:
    existing = await session.execute(select(ApiKey.key))
    existing_keys = {row[0] for row in existing.all()}

    inserted = 0
    for entry in MOCK_API_KEYS:
        if entry["key"] in existing_keys:
            continue
        session.add(
            ApiKey(
                key=entry["key"],
                tenant_name=entry["tenant_name"],
                role=entry["role"],
            )
        )
        inserted += 1

    if inserted:
        logger.info("Seeded %d new API key(s).", inserted)
    else:
        logger.info("API keys already seeded — skipping.")


async def run_seed() -> None:
    """
    Entry point called from app.py startup, after init_db() has created
    tables. Wraps everything in one session/commit.
    """
    async with AsyncSessionLocal() as session:
        try:
            await seed_customers(session)
            await seed_api_keys(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Seeding failed — rolled back.")
            raise


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_seed())