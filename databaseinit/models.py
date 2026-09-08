"""
databaseinit/models.py

ORM table definitions, built on the shared `Base` from db.py.

SQLite Notes:
  - SQLite handles UUIDs as strings (no native UUID type).
  - Numeric types map to DECIMAL in SQLite.
  - Enums are stored as strings.
  - Foreign key constraints require PRAGMA foreign_keys=ON.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from databaseinit.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class BillingStatus(str, enum.Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    TRIALING = "trialing"


class InvoiceStatus(str, enum.Enum):
    PAID = "paid"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"
    OPEN = "open"


class RefundStatus(str, enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class ApiKeyRole(str, enum.Enum):
    ADMIN = "admin"
    VIEWER = "viewer"


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------

class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="free")
    billing_status: Mapped[BillingStatus] = mapped_column(
        Enum(BillingStatus),
        nullable=False,
        default=BillingStatus.ACTIVE,
    )
    monthly_usage_units: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    invoices: Mapped[list["Invoice"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    refunds: Mapped[list["Refund"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Customer {self.customer_id} plan={self.plan}>"


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.customer_id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Numeric] = mapped_column(Numeric(10, 2), nullable=False)
    amount_refunded: Mapped[Numeric] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus),
        nullable=False,
        default=InvoiceStatus.PAID,
    )
    description: Mapped[str] = mapped_column(Text, nullable=True)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    customer: Mapped["Customer"] = relationship(back_populates="invoices")
    refunds: Mapped[list["Refund"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_invoices_customer_issued", "customer_id", "issued_at"),
    )

    def __repr__(self) -> str:
        return f"<Invoice {self.id} customer={self.customer_id} amount={self.amount}>"


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------

class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.customer_id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Numeric] = mapped_column(Numeric(10, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RefundStatus] = mapped_column(
        Enum(RefundStatus),
        nullable=False,
        default=RefundStatus.COMPLETED,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    invoice: Mapped["Invoice"] = relationship(back_populates="refunds")
    customer: Mapped["Customer"] = relationship(back_populates="refunds")

    __table_args__ = (
        Index("ix_refunds_customer_created", "customer_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Refund {self.id} invoice={self.invoice_id} amount={self.amount}>"


# ---------------------------------------------------------------------------
# ApiKey
# ---------------------------------------------------------------------------

class ApiKey(Base):
    __tablename__ = "api_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[ApiKeyRole] = mapped_column(
        Enum(ApiKeyRole),
        nullable=False,
        default=ApiKeyRole.VIEWER,
    )
    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ✅ FIXED: back_populates matches "api_key_rel" in RateLimitEvent
    rate_limit_events: Mapped[list["RateLimitEvent"]] = relationship(
        back_populates="api_key_rel", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ApiKey tenant={self.tenant_name} role={self.role}>"


# ---------------------------------------------------------------------------
# RateLimitEvent
# ---------------------------------------------------------------------------

class RateLimitEvent(Base):
    __tablename__ = "rate_limit_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    api_key: Mapped[str] = mapped_column(
        ForeignKey("api_keys.key", ondelete="CASCADE"), nullable=False
    )
    tokens_used: Mapped[int] = mapped_column(nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # ✅ This is the relationship attribute that ApiKey points to
    api_key_rel: Mapped["ApiKey"] = relationship(
        back_populates="rate_limit_events"
    )

    __table_args__ = (
        Index("ix_rate_limit_key_time", "api_key", "occurred_at"),
    )

    def __repr__(self) -> str:
        return f"<RateLimitEvent key={self.api_key} tokens={self.tokens_used}>"