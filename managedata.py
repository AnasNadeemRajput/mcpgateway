"""
manage_data.py

Interactive CLI for adding/inspecting data in the MCP Gateway Suite's
database, without hand-editing seed_data.py or writing one-off scripts
every time.

Run it directly:

    (mcpgateway) E:\\projects\\mcpgateway> python manage_data.py

It uses the SAME validation and business logic as the real app --
adding a customer goes straight through the ORM models, but refunds go
through the actual trigger_refund() handler (Task 1's real code), so
anything you do here is exactly as valid/invalid as it would be through
the live API. You can't accidentally create a refund that violates the
real rules by using this tool.

Menu:
  1. Add customer
  2. Add invoice for a customer
  3. Add API key
  4. View customer record (name, plan, invoices)
  5. List customers
  6. Trigger a refund
  7. View rate-limit status for a token
  0. Exit
"""

import asyncio
import sys
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from databaseinit.db import AsyncSessionLocal, init_db
from databaseinit.models import (
    ApiKey,
    ApiKeyRole,
    BillingStatus,
    Customer,
    Invoice,
    InvoiceStatus,
)
from task1_mcp_server.apperror import McpError
from task1_mcp_server.schemas import CUSTOMER_ID_PATTERN
from task1_mcp_server.tools.get_customer_record import get_customer_record
from task1_mcp_server.tools.trigger_refund import trigger_refund


# ---------------------------------------------------------------------------
# Small input helpers
# ---------------------------------------------------------------------------

def ask(prompt: str, default: str | None = None, required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if not value and not required:
            return ""
        if value:
            return value
        print("  This field is required.")


def ask_choice(prompt: str, choices: dict[str, str]) -> str:
    """choices: {key_typed: internal_value}. Prints options, returns the value."""
    print(f"{prompt}")
    for key, label in choices.items():
        print(f"  {key}) {label}")
    while True:
        picked = input("> ").strip()
        if picked in choices:
            return picked
        print("  Invalid choice, try again.")


def ask_decimal(prompt: str) -> Decimal:
    while True:
        raw = input(f"{prompt}: ").strip()
        try:
            return Decimal(raw)
        except InvalidOperation:
            print("  Not a valid number, try again (e.g. 25.00).")


def ask_customer_id(prompt: str = "Customer ID (CUST-XXXXX)") -> str:
    while True:
        raw = input(f"{prompt}: ").strip()
        if CUSTOMER_ID_PATTERN.match(raw):
            return raw
        print("  Must match CUST-XXXXX (5 digits), e.g. CUST-10001.")


def print_json_like(d: dict, indent: int = 2) -> None:
    import json
    print(json.dumps(d, indent=indent, default=str))


# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

async def add_customer():
    print("\n--- Add Customer ---")
    customer_id = ask_customer_id()

    async with AsyncSessionLocal() as db:
        existing = await db.execute(
            select(Customer.customer_id).where(Customer.customer_id == customer_id)
        )
        if existing.scalar_one_or_none() is not None:
            print(f"  Customer {customer_id} already exists. Aborting.")
            return

        name = ask("Name")
        email = ask("Email")
        plan = ask("Plan", default="free")
        billing_status_key = ask_choice(
            "Billing status:",
            {"1": "active", "2": "past_due", "3": "canceled", "4": "trialing"},
        )
        billing_status_map = {
            "1": BillingStatus.ACTIVE, "2": BillingStatus.PAST_DUE,
            "3": BillingStatus.CANCELED, "4": BillingStatus.TRIALING,
        }
        usage_raw = ask("Monthly usage units", default="0")
        try:
            usage = int(usage_raw)
        except ValueError:
            usage = 0

        db.add(Customer(
            customer_id=customer_id, name=name, email=email, plan=plan,
            billing_status=billing_status_map[billing_status_key],
            monthly_usage_units=usage,
        ))
        await db.commit()
        print(f"  Created {customer_id}.")


async def add_invoice():
    print("\n--- Add Invoice ---")
    customer_id = ask_customer_id()

    async with AsyncSessionLocal() as db:
        exists = await db.execute(
            select(Customer.customer_id).where(Customer.customer_id == customer_id)
        )
        if exists.scalar_one_or_none() is None:
            print(f"  No customer {customer_id} found. Add the customer first.")
            return

        amount = ask_decimal("Invoice amount")
        description = ask("Description", default="", required=False)
        status_key = ask_choice(
            "Invoice status:",
            {"1": "paid", "2": "open", "3": "refunded", "4": "partially_refunded"},
        )
        status_map = {
            "1": InvoiceStatus.PAID, "2": InvoiceStatus.OPEN,
            "3": InvoiceStatus.REFUNDED, "4": InvoiceStatus.PARTIALLY_REFUNDED,
        }

        invoice = Invoice(
            customer_id=customer_id, amount=amount,
            description=description or None, status=status_map[status_key],
        )
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)
        print(f"  Created invoice {invoice.id} for {customer_id}: ${amount}")


async def add_api_key():
    print("\n--- Add API Key ---")
    key = ask("Token string (e.g. admin-token-newperson)")
    tenant_name = ask("Tenant name")
    role_key = ask_choice("Role:", {"1": "viewer", "2": "admin"})
    role = ApiKeyRole.ADMIN if role_key == "2" else ApiKeyRole.VIEWER

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(ApiKey.key).where(ApiKey.key == key))
        if existing.scalar_one_or_none() is not None:
            print(f"  A key '{key}' already exists. Aborting.")
            return
        db.add(ApiKey(key=key, tenant_name=tenant_name, role=role))
        await db.commit()
        print(f"  Created {role.value} key '{key}' for tenant '{tenant_name}'.")


async def view_customer_record():
    print("\n--- View Customer Record ---")
    customer_id = ask_customer_id()
    async with AsyncSessionLocal() as db:
        try:
            result = await get_customer_record({"customer_id": customer_id}, db)
        except McpError as exc:
            print(f"  [{exc.code}] {exc.message}")
            return
        print_json_like(result.model_dump(mode="json"))


async def list_customers():
    print("\n--- Customers ---")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Customer))
        customers = result.scalars().all()
        if not customers:
            print("  No customers found.")
            return
        for c in customers:
            print(f"  {c.customer_id}  {c.name:<25} plan={c.plan:<10} "
                  f"status={c.billing_status.value}")


async def do_trigger_refund():
    print("\n--- Trigger Refund ---")
    customer_id = ask_customer_id()

    async with AsyncSessionLocal() as db:
        # Show invoices so the person can pick one / see remaining balance.
        result = await db.execute(
            select(Invoice).where(Invoice.customer_id == customer_id)
        )
        invoices = result.scalars().all()
        if not invoices:
            print(f"  {customer_id} has no invoices.")
            return
        print("  Invoices:")
        for inv in invoices:
            remaining = inv.amount - inv.amount_refunded
            print(f"    {inv.id}  amount=${inv.amount}  "
                  f"refunded=${inv.amount_refunded}  remaining=${remaining}  "
                  f"status={inv.status.value}")

    amount = ask_decimal("Refund amount")
    reason = ask("Reason (min 10 characters)")
    invoice_id = ask(
        "Specific invoice ID (leave blank for most recent eligible)",
        required=False,
    )

    payload = {
        "customer_id": customer_id,
        "amount": str(amount),
        "reason": reason,
    }
    if invoice_id:
        payload["invoice_id"] = invoice_id

    async with AsyncSessionLocal() as db:
        try:
            result = await trigger_refund(payload, db)
        except McpError as exc:
            print(f"  Refund rejected: [{exc.code}] {exc.message}")
            if exc.data:
                print_json_like(exc.data)
            return
        except Exception as exc:  # e.g. pydantic ValidationError
            print(f"  Invalid input: {exc}")
            return

        print("  Refund succeeded:")
        print_json_like(result.model_dump(mode="json"))


async def view_rate_limit_status():
    print("\n--- Rate Limit Status ---")
    token = ask("API token")
    from task4_rate_limit_router.rate_limiter import get_rate_limit_status
    async with AsyncSessionLocal() as db:
        status = await get_rate_limit_status(token, db)
        print(f"  usage: {status.current_usage}/{status.limit} "
              f"(window: {status.window_seconds}s)")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

MENU = """
============================================
 MCP Gateway Suite - Data Manager
============================================
 1) Add customer
 2) Add invoice for a customer
 3) Add API key
 4) View customer record
 5) List customers
 6) Trigger a refund
 7) View rate-limit status for a token
 0) Exit
============================================
"""

ACTIONS = {
    "1": add_customer,
    "2": add_invoice,
    "3": add_api_key,
    "4": view_customer_record,
    "5": list_customers,
    "6": do_trigger_refund,
    "7": view_rate_limit_status,
}


async def main():
    print("Initializing database (safe to run every time; won't drop data)...")
    await init_db()

    while True:
        print(MENU)
        choice = input("Choose an option: ").strip()
        if choice == "0":
            print("Bye.")
            return
        action = ACTIONS.get(choice)
        if action is None:
            print("Invalid choice.")
            continue
        try:
            await action()
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"  Unexpected error: {exc}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)