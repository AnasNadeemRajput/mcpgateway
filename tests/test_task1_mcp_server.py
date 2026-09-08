"""
tests/test_task1_mcp_server.py

Unit tests for Task 1: MCP Server tools
- get_customer_record
- trigger_refund
"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from databaseinit.models import BillingStatus, InvoiceStatus, RefundStatus
from task1_mcp_server.apperror import (
    CustomerNotFoundError,
    InvoiceNotFoundError,
    NoEligibleInvoiceError,
    RefundExceedsAvailableError,
)
from task1_mcp_server.schemas import (
    GetCustomerRecordInput,
    GetCustomerRecordOutput,
    TriggerRefundInput,
    TriggerRefundOutput,
)
from task1_mcp_server.tools.get_customer_record import get_customer_record
from task1_mcp_server.tools.trigger_refund import trigger_refund


# ============================================================================
# Test Constants
# ============================================================================

VALID_CUSTOMER_ID = "CUST-10001"
VALID_INVOICE_ID = "inv-001"
VALID_AMOUNT = Decimal("25.00")
VALID_REFUND_REASON = "Test refund reason"  # 20 chars, >10 minimum
INVALID_CUSTOMER_ID = "CUST-99999"
NO_INVOICE_CUSTOMER = "CUST-10004"


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_db() -> AsyncMock:
    """Create a mock database session."""
    return AsyncMock(spec=AsyncSession)


def _make_mock_invoice(
    id=VALID_INVOICE_ID,
    amount=Decimal("100.00"),
    amount_refunded=Decimal("0.00"),
    status=InvoiceStatus.PAID,
    description="Test invoice",
    issued_at=None,
    customer_id=VALID_CUSTOMER_ID,
) -> MagicMock:
    """
    Builds a MagicMock standing in for an ORM Invoice row.

    Uses real InvoiceStatus enum members (not plain strings) since
    get_customer_record.py reads `inv.status.value`, and a real datetime
    (not a pre-formatted string) since it calls `inv.issued_at.isoformat()`
    itself -- a string doesn't have .isoformat().
    """
    invoice = MagicMock()
    invoice.id = id
    invoice.amount = amount
    invoice.amount_refunded = amount_refunded
    invoice.status = status
    invoice.description = description
    invoice.issued_at = issued_at or datetime(2024, 1, 1, 0, 0, 0)
    invoice.customer_id = customer_id
    return invoice


def _make_mock_customer(
    customer_id=VALID_CUSTOMER_ID,
    name="Test Customer",
    email="test@example.com",
    plan="pro",
    billing_status=BillingStatus.ACTIVE,
    monthly_usage_units=1000,
    invoices=None,
) -> MagicMock:
    """
    Builds a MagicMock standing in for an ORM Customer row, complete
    with a real .invoices list -- get_customer_record.py accesses these
    as object attributes, so a plain dict (as the original fixture used)
    breaks on the very first `customer.invoices` access.
    """
    customer = MagicMock()
    customer.customer_id = customer_id
    customer.name = name
    customer.email = email
    customer.plan = plan
    customer.billing_status = billing_status
    customer.monthly_usage_units = monthly_usage_units
    customer.invoices = invoices if invoices is not None else [_make_mock_invoice()]
    return customer


@pytest.fixture
def mock_customer_data() -> MagicMock:
    """Sample customer object for testing (see _make_mock_customer)."""
    return _make_mock_customer()


@pytest.fixture
def mock_invoice() -> MagicMock:
    """Create a mock invoice."""
    return _make_mock_invoice()


@pytest.fixture
def mock_refund() -> MagicMock:
    """
    Create a mock refund. Uses the real RefundStatus enum (not a plain
    string) since trigger_refund.py's output construction reads
    `refund.status.value`.
    """
    refund = MagicMock()
    refund.id = "ref-001"
    refund.amount = VALID_AMOUNT
    refund.reason = VALID_REFUND_REASON
    refund.status = RefundStatus.COMPLETED
    refund.created_at = datetime(2024, 1, 15, 10, 0, 0)
    refund.invoice_id = VALID_INVOICE_ID
    refund.customer_id = VALID_CUSTOMER_ID
    return refund


# ============================================================================
# Tests for get_customer_record
# ============================================================================

class TestGetCustomerRecord:
    """Test suite for get_customer_record tool."""

    @pytest.mark.asyncio
    async def test_get_customer_success(
        self, 
        mock_db: AsyncMock, 
        mock_customer_data: dict
    ) -> None:
        """Test successfully retrieving a customer record."""
        # Mock the database query
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_customer_data
        mock_db.execute.return_value = mock_result

        # Call the handler
        result = await get_customer_record(
            {"customer_id": VALID_CUSTOMER_ID},
            mock_db
        )

        # Assertions
        assert isinstance(result, GetCustomerRecordOutput)
        assert result.customer_id == VALID_CUSTOMER_ID
        assert result.name == "Test Customer"
        assert result.email == "test@example.com"
        assert result.plan == "pro"
        assert len(result.invoices) == 1
        assert result.invoices[0].amount == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_get_customer_not_found(self, mock_db: AsyncMock) -> None:
        """Test error when customer doesn't exist."""
        # Mock empty result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Should raise CustomerNotFoundError
        with pytest.raises(CustomerNotFoundError) as exc:
            await get_customer_record(
                {"customer_id": INVALID_CUSTOMER_ID},
                mock_db
            )
        
        assert "No customer found" in str(exc.value)

    @pytest.mark.asyncio
    async def test_get_customer_invalid_format(self, mock_db: AsyncMock) -> None:
        """Test validation for invalid customer_id format."""
        # Invalid format (missing CUST- prefix)
        with pytest.raises(ValueError) as exc:
            await get_customer_record(
                {"customer_id": "INVALID-12345"},
                mock_db
            )
        assert "customer_id must match the format" in str(exc.value)

        # Invalid format (wrong number of digits)
        with pytest.raises(ValueError) as exc:
            await get_customer_record(
                {"customer_id": "CUST-1234"},
                mock_db
            )
        assert "customer_id must match the format" in str(exc.value)

        # Invalid format (lowercase)
        with pytest.raises(ValueError) as exc:
            await get_customer_record(
                {"customer_id": "cust-12345"},
                mock_db
            )
        assert "customer_id must match the format" in str(exc.value)

    @pytest.mark.asyncio
    async def test_get_customer_with_multiple_invoices(
        self, 
        mock_db: AsyncMock
    ) -> None:
        """Test retrieving customer with multiple invoices."""
        customer_data = _make_mock_customer(
            customer_id="CUST-10003",
            name="Maria Lopez",
            email="maria.lopez@example.com",
            plan="enterprise",
            billing_status=BillingStatus.ACTIVE,
            monthly_usage_units=98000,
            invoices=[
                _make_mock_invoice(
                    id="inv-001", amount=Decimal("499.00"),
                    amount_refunded=Decimal("0.00"), status=InvoiceStatus.PAID,
                    description="Enterprise plan - monthly",
                    issued_at=datetime(2024, 1, 1, 0, 0, 0),
                    customer_id="CUST-10003",
                ),
                _make_mock_invoice(
                    id="inv-002", amount=Decimal("499.00"),
                    amount_refunded=Decimal("100.00"),
                    status=InvoiceStatus.PARTIALLY_REFUNDED,
                    description="Enterprise plan - monthly",
                    issued_at=datetime(2024, 2, 1, 0, 0, 0),
                    customer_id="CUST-10003",
                ),
            ],
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = customer_data
        mock_db.execute.return_value = mock_result

        result = await get_customer_record(
            {"customer_id": "CUST-10003"},
            mock_db
        )

        assert len(result.invoices) == 2
        # Check sorting (most recent first)
        assert result.invoices[0].issued_at > result.invoices[1].issued_at

    @pytest.mark.asyncio
    async def test_get_customer_whitespace_trimming(self, mock_db: AsyncMock) -> None:
        """Test that whitespace is trimmed from customer_id."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Should trim whitespace and validate
        with pytest.raises(CustomerNotFoundError):
            await get_customer_record(
                {"customer_id": "  CUST-12345  "},
                mock_db
            )


# ============================================================================
# Tests for trigger_refund
# ============================================================================

class TestTriggerRefund:
    """Test suite for trigger_refund tool."""

    @pytest.mark.asyncio
    async def test_trigger_refund_success(
        self,
        mock_db: AsyncMock,
        mock_invoice: AsyncMock,
        mock_refund: AsyncMock
    ) -> None:
        """Test successfully issuing a refund."""
        # Mock customer exists
        mock_customer_result = MagicMock()
        mock_customer_result.scalar_one_or_none.return_value = VALID_CUSTOMER_ID

        # Mock invoice found
        mock_invoice_result = MagicMock()
        mock_invoice_result.scalar_one_or_none.return_value = mock_invoice
        mock_db.execute.side_effect = [mock_customer_result, mock_invoice_result]

        # Mock refund creation
        mock_db.refresh.return_value = None

        # Patch refund creation
        with patch("task1_mcp_server.tools.trigger_refund.Refund") as MockRefund:
            MockRefund.return_value = mock_refund
            
            # Call the handler
            result = await trigger_refund(
                {
                    "customer_id": VALID_CUSTOMER_ID,
                    "amount": str(VALID_AMOUNT),
                    "reason": VALID_REFUND_REASON
                },
                mock_db
            )

            assert isinstance(result, TriggerRefundOutput)
            assert result.customer_id == VALID_CUSTOMER_ID
            assert result.amount == VALID_AMOUNT
            assert result.status == "completed"
            # Verify invoice was updated
            assert mock_invoice.amount_refunded == VALID_AMOUNT

    @pytest.mark.asyncio
    async def test_trigger_refund_customer_not_found(
        self, 
        mock_db: AsyncMock
    ) -> None:
        """Test error when customer doesn't exist."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(CustomerNotFoundError) as exc:
            await trigger_refund(
                {
                    "customer_id": INVALID_CUSTOMER_ID,
                    "amount": str(VALID_AMOUNT),
                    "reason": VALID_REFUND_REASON
                },
                mock_db
            )
        assert "No customer found" in str(exc.value)

    @pytest.mark.asyncio
    async def test_trigger_refund_invalid_amount(self, mock_db: AsyncMock) -> None:
        """Test validation for invalid amount."""
        # Amount must be positive
        with pytest.raises(ValueError) as exc:
            await trigger_refund(
                {
                    "customer_id": VALID_CUSTOMER_ID,
                    "amount": "-25.00",
                    "reason": VALID_REFUND_REASON
                },
                mock_db
            )
        assert "greater than 0" in str(exc.value)

        # Amount with too many decimals
        with pytest.raises(ValueError) as exc:
            await trigger_refund(
                {
                    "customer_id": VALID_CUSTOMER_ID,
                    "amount": "25.005",
                    "reason": VALID_REFUND_REASON
                },
                mock_db
            )
        assert "at most 2 decimal places" in str(exc.value)

        # Amount with non-numeric characters
        with pytest.raises(ValueError) as exc:
            await trigger_refund(
                {
                    "customer_id": VALID_CUSTOMER_ID,
                    "amount": "abc",
                    "reason": VALID_REFUND_REASON
                },
                mock_db
            )

    @pytest.mark.asyncio
    async def test_trigger_refund_reason_too_short(self, mock_db: AsyncMock) -> None:
        """Test validation for reason length."""
        # Reason must be at least 10 non-whitespace characters
        short_reasons = [
            "Too short",  # 9 chars including space
            "123456789",  # 9 chars
            "          ",  # 10 spaces
            "",           # Empty
        ]
        
        for reason in short_reasons:
            with pytest.raises(ValueError) as exc:
                await trigger_refund(
                    {
                        "customer_id": VALID_CUSTOMER_ID,
                        "amount": str(VALID_AMOUNT),
                        "reason": reason
                    },
                    mock_db
                )
            # Two different validators can fire depending on length:
            # Pydantic's own min_length=10 catches raw-too-short strings;
            # our custom validator catches "10+ chars but all whitespace".
            msg = str(exc.value)
            assert (
                "at least 10 non-whitespace characters" in msg
                or "at least 10 characters" in msg
            )

    @pytest.mark.asyncio
    async def test_trigger_refund_exceeds_balance(
        self, 
        mock_db: AsyncMock
    ) -> None:
        """Test error when refund exceeds available balance."""
        # Mock customer exists
        mock_customer_result = MagicMock()
        mock_customer_result.scalar_one_or_none.return_value = VALID_CUSTOMER_ID

        # Mock invoice with small remaining balance
        mock_invoice = AsyncMock()
        mock_invoice.id = VALID_INVOICE_ID
        mock_invoice.amount = Decimal("100.00")
        mock_invoice.amount_refunded = Decimal("90.00")  # Only $10 left
        mock_invoice.status = "partially_refunded"
        mock_invoice.customer_id = VALID_CUSTOMER_ID
        
        mock_invoice_result = MagicMock()
        mock_invoice_result.scalar_one_or_none.return_value = mock_invoice
        mock_db.execute.side_effect = [mock_customer_result, mock_invoice_result]

        with pytest.raises(RefundExceedsAvailableError) as exc:
            await trigger_refund(
                {
                    "customer_id": VALID_CUSTOMER_ID,
                    "amount": "25.00",  # More than remaining $10
                    "reason": VALID_REFUND_REASON
                },
                mock_db
            )
        assert "exceeds the remaining refundable balance" in str(exc.value)

    @pytest.mark.asyncio
    async def test_trigger_refund_no_eligible_invoice(
        self, 
        mock_db: AsyncMock
    ) -> None:
        """Test error when customer has no eligible invoice."""
        # Mock customer exists
        mock_customer_result = MagicMock()
        mock_customer_result.scalar_one_or_none.return_value = NO_INVOICE_CUSTOMER

        # Mock no invoice found
        mock_invoice_result = MagicMock()
        mock_invoice_result.scalar_one_or_none.return_value = None
        mock_db.execute.side_effect = [mock_customer_result, mock_invoice_result]

        with pytest.raises(NoEligibleInvoiceError) as exc:
            await trigger_refund(
                {
                    "customer_id": NO_INVOICE_CUSTOMER,
                    "amount": str(VALID_AMOUNT),
                    "reason": VALID_REFUND_REASON
                },
                mock_db
            )
        assert "no invoice with a remaining refundable balance" in str(exc.value)

    @pytest.mark.asyncio
    async def test_trigger_refund_specific_invoice(
        self,
        mock_db: AsyncMock,
        mock_invoice: AsyncMock,
        mock_refund: AsyncMock
    ) -> None:
        """Test refund against a specific invoice."""
        # Mock customer exists
        mock_customer_result = MagicMock()
        mock_customer_result.scalar_one_or_none.return_value = VALID_CUSTOMER_ID

        # Mock specific invoice
        mock_invoice.id = "inv-002"
        mock_invoice.amount = Decimal("200.00")
        mock_invoice.amount_refunded = Decimal("0.00")
        
        mock_invoice_result = MagicMock()
        mock_invoice_result.scalar_one_or_none.return_value = mock_invoice
        mock_db.execute.side_effect = [mock_customer_result, mock_invoice_result]

        mock_db.refresh.return_value = None

        # Patch refund creation. Uses side_effect (reflecting the actual
        # constructor kwargs trigger_refund.py passes) rather than a fixed
        # return_value -- a fixed mock_refund.amount would silently pass
        # this test regardless of what amount was actually requested,
        # which isn't a meaningful assertion.
        with patch("task1_mcp_server.tools.trigger_refund.Refund") as MockRefund:
            def _build_refund(**kwargs):
                refund = MagicMock()
                refund.id = "ref-002"
                refund.amount = kwargs["amount"]
                refund.reason = kwargs["reason"]
                refund.status = RefundStatus.COMPLETED
                refund.created_at = datetime(2024, 1, 15, 10, 0, 0)
                refund.invoice_id = kwargs["invoice_id"]
                refund.customer_id = kwargs["customer_id"]
                return refund
            MockRefund.side_effect = _build_refund

            result = await trigger_refund(
                {
                    "customer_id": VALID_CUSTOMER_ID,
                    "amount": "50.00",
                    "reason": "Specific invoice refund",
                    "invoice_id": "inv-002"
                },
                mock_db
            )

            assert result.invoice_id == "inv-002"
            assert result.amount == Decimal("50.00")

    @pytest.mark.asyncio
    async def test_trigger_refund_invoice_not_belonging_to_customer(
        self,
        mock_db: AsyncMock
    ) -> None:
        """Test error when invoice doesn't belong to customer."""
        # Mock customer exists
        mock_customer_result = MagicMock()
        mock_customer_result.scalar_one_or_none.return_value = VALID_CUSTOMER_ID

        # The real query filters by BOTH Invoice.id AND Invoice.customer_id
        # in the SQL WHERE clause -- a real database, given a mismatched
        # customer_id, returns no row at all. It's never "returns the
        # wrong customer's invoice and lets Python catch it after the
        # fact." Mocking scalar_one_or_none() to return None is what
        # actually matches that behavior and is what makes
        # InvoiceNotFoundError fire.
        mock_invoice_result = MagicMock()
        mock_invoice_result.scalar_one_or_none.return_value = None
        mock_db.execute.side_effect = [mock_customer_result, mock_invoice_result]

        with pytest.raises(InvoiceNotFoundError) as exc:
            await trigger_refund(
                {
                    "customer_id": VALID_CUSTOMER_ID,
                    "amount": "25.00",
                    "reason": VALID_REFUND_REASON,
                    "invoice_id": "inv-999"
                },
                mock_db
            )
        assert "not found" in str(exc.value)


# ============================================================================
# Tests for Schemas
# ============================================================================

class TestSchemas:
    """Test suite for Pydantic schemas validation."""

    def test_get_customer_record_input_valid(self) -> None:
        """Test valid GetCustomerRecordInput."""
        data = GetCustomerRecordInput(customer_id=VALID_CUSTOMER_ID)
        assert data.customer_id == VALID_CUSTOMER_ID

    def test_get_customer_record_input_whitespace(self) -> None:
        """Test whitespace trimming in customer_id."""
        data = GetCustomerRecordInput(customer_id="  CUST-12345  ")
        assert data.customer_id == "CUST-12345"

    def test_get_customer_record_input_invalid(self) -> None:
        """Test invalid customer_id formats."""
        invalid_ids = [
            "CUST-1234",      # Only 4 digits
            "CUST-123456",    # 6 digits
            "CUST-12a45",     # Letters in digits
            "CUST12345",      # Missing hyphen
            "cust-12345",     # Lowercase
            "12345",          # No prefix
            "CUST-",          # No digits
        ]
        for invalid_id in invalid_ids:
            with pytest.raises(ValueError):
                GetCustomerRecordInput(customer_id=invalid_id)

    def test_trigger_refund_input_valid(self) -> None:
        """Test valid TriggerRefundInput."""
        data = TriggerRefundInput(
            customer_id=VALID_CUSTOMER_ID,
            amount=VALID_AMOUNT,
            reason=VALID_REFUND_REASON
        )
        assert data.customer_id == VALID_CUSTOMER_ID
        assert data.amount == VALID_AMOUNT
        assert data.reason == VALID_REFUND_REASON

    def test_trigger_refund_input_amount_precision(self) -> None:
        """Test amount decimal precision validation."""
        # Valid - 2 decimal places
        data = TriggerRefundInput(
            customer_id=VALID_CUSTOMER_ID,
            amount=Decimal("25.00"),
            reason=VALID_REFUND_REASON
        )
        assert data.amount == Decimal("25.00")

        # Valid - 1 decimal place (should be normalized)
        data = TriggerRefundInput(
            customer_id=VALID_CUSTOMER_ID,
            amount=Decimal("25.5"),
            reason=VALID_REFUND_REASON
        )
        assert data.amount == Decimal("25.50")

        # Invalid - 3 decimal places
        with pytest.raises(ValueError) as exc:
            TriggerRefundInput(
                customer_id=VALID_CUSTOMER_ID,
                amount=Decimal("25.005"),
                reason=VALID_REFUND_REASON
            )
        assert "at most 2 decimal places" in str(exc.value)

    def test_trigger_refund_input_reason_whitespace(self) -> None:
        """Test reason whitespace validation."""
        # Valid - exactly 10 non-whitespace chars
        data = TriggerRefundInput(
            customer_id=VALID_CUSTOMER_ID,
            amount=VALID_AMOUNT,
            reason="1234567890"  # 10 chars
        )
        assert data.reason == "1234567890"

        # Valid - leading/trailing spaces are NOT stripped from reason
        # (only customer_id gets .strip() in schemas.py -- reason keeps
        # whatever the caller wrote, as long as it has >=10 non-whitespace
        # chars).
        data = TriggerRefundInput(
            customer_id=VALID_CUSTOMER_ID,
            amount=VALID_AMOUNT,
            reason="  1234567890  "
        )
        assert data.reason == "  1234567890  "

        # Invalid - 10 spaces (no non-whitespace)
        with pytest.raises(ValueError) as exc:
            TriggerRefundInput(
                customer_id=VALID_CUSTOMER_ID,
                amount=VALID_AMOUNT,
                reason="          "  # 10 spaces
            )
        assert "at least 10 non-whitespace characters" in str(exc.value)

        # Invalid - only whitespace characters
        with pytest.raises(ValueError) as exc:
            TriggerRefundInput(
                customer_id=VALID_CUSTOMER_ID,
                amount=VALID_AMOUNT,
                reason="\t\n\r\t\n\r"
            )
        msg = str(exc.value)
        assert (
            "at least 10 non-whitespace characters" in msg
            or "at least 10 characters" in msg
        )

    def test_trigger_refund_input_amount_zero_or_negative(self) -> None:
        """Test amount must be positive."""
        for amount in [Decimal("0.00"), Decimal("-0.01"), Decimal("-100.00")]:
            with pytest.raises(ValueError) as exc:
                TriggerRefundInput(
                    customer_id=VALID_CUSTOMER_ID,
                    amount=amount,
                    reason=VALID_REFUND_REASON
                )
            assert "greater than 0" in str(exc.value)