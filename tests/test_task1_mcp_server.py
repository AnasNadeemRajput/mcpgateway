"""
tests/test_task1_mcp_server.py

Unit tests for Task 1: MCP Server tools
- get_customer_record
- trigger_refund
"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

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


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def mock_customer_data():
    """Sample customer data for testing."""
    return {
        "customer_id": "CUST-10001",
        "name": "Test Customer",
        "email": "test@example.com",
        "plan": "pro",
        "billing_status": "active",
        "monthly_usage_units": 1000,
        "invoices": [
            {
                "id": "inv-001",
                "amount": Decimal("100.00"),
                "amount_refunded": Decimal("0.00"),
                "status": "paid",
                "description": "Test invoice",
                "issued_at": "2024-01-01T00:00:00"
            }
        ]
    }


# ============================================================================
# Tests for get_customer_record
# ============================================================================

class TestGetCustomerRecord:
    """Test suite for get_customer_record tool."""

    @pytest.mark.asyncio
    async def test_get_customer_success(self, mock_db, mock_customer_data):
        """Test successfully retrieving a customer record."""
        # Mock the database query
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = mock_customer_data
        mock_db.execute.return_value = mock_result

        # Call the handler
        result = await get_customer_record(
            {"customer_id": "CUST-10001"},
            mock_db
        )

        # Assertions
        assert isinstance(result, GetCustomerRecordOutput)
        assert result.customer_id == "CUST-10001"
        assert result.name == "Test Customer"
        assert result.email == "test@example.com"
        assert result.plan == "pro"
        assert len(result.invoices) == 1
        assert result.invoices[0].amount == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_get_customer_not_found(self, mock_db):
        """Test error when customer doesn't exist."""
        # Mock empty result
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Should raise CustomerNotFoundError
        with pytest.raises(CustomerNotFoundError) as exc:
            await get_customer_record(
                {"customer_id": "CUST-99999"},
                mock_db
            )
        
        assert "No customer found" in str(exc.value)

    @pytest.mark.asyncio
    async def test_get_customer_invalid_format(self, mock_db):
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

    @pytest.mark.asyncio
    async def test_get_customer_with_invoices(self, mock_db):
        """Test retrieving customer with multiple invoices."""
        customer_data = {
            "customer_id": "CUST-10003",
            "name": "Maria Lopez",
            "email": "maria.lopez@example.com",
            "plan": "enterprise",
            "billing_status": "active",
            "monthly_usage_units": 98000,
            "invoices": [
                {
                    "id": "inv-001",
                    "amount": Decimal("499.00"),
                    "amount_refunded": Decimal("0.00"),
                    "status": "paid",
                    "description": "Enterprise plan - monthly",
                    "issued_at": "2024-01-01T00:00:00"
                },
                {
                    "id": "inv-002",
                    "amount": Decimal("499.00"),
                    "amount_refunded": Decimal("100.00"),
                    "status": "partially_refunded",
                    "description": "Enterprise plan - monthly",
                    "issued_at": "2024-02-01T00:00:00"
                }
            ]
        }

        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = customer_data
        mock_db.execute.return_value = mock_result

        result = await get_customer_record(
            {"customer_id": "CUST-10003"},
            mock_db
        )

        assert len(result.invoices) == 2
        # Check sorting (most recent first)
        assert result.invoices[0].issued_at > result.invoices[1].issued_at


# ============================================================================
# Tests for trigger_refund
# ============================================================================

class TestTriggerRefund:
    """Test suite for trigger_refund tool."""

    @pytest.mark.asyncio
    async def test_trigger_refund_success(self, mock_db):
        """Test successfully issuing a refund."""
        # Mock customer exists
        mock_customer_result = AsyncMock()
        mock_customer_result.scalar_one_or_none.return_value = "CUST-10001"
        mock_db.execute.return_value = mock_customer_result

        # Mock invoice
        mock_invoice = AsyncMock()
        mock_invoice.id = "inv-001"
        mock_invoice.amount = Decimal("100.00")
        mock_invoice.amount_refunded = Decimal("0.00")
        mock_invoice.status = "paid"
        mock_invoice.customer_id = "CUST-10001"
        
        mock_invoice_result = AsyncMock()
        mock_invoice_result.scalar_one_or_none.return_value = mock_invoice
        mock_db.execute.return_value = mock_invoice_result

        # Mock refund creation
        mock_refund = AsyncMock()
        mock_refund.id = "ref-001"
        mock_refund.amount = Decimal("25.00")
        mock_refund.reason = "Test refund"
        mock_refund.status = "completed"
        mock_refund.created_at = "2024-01-15T10:00:00"
        mock_db.refresh.return_value = None

        # Call the handler
        result = await trigger_refund(
            {
                "customer_id": "CUST-10001",
                "amount": "25.00",
                "reason": "Test refund reason"
            },
            mock_db
        )

        assert isinstance(result, TriggerRefundOutput)
        assert result.customer_id == "CUST-10001"
        assert result.amount == Decimal("25.00")
        assert result.status == "completed"
        # Verify invoice was updated
        assert mock_invoice.amount_refunded == Decimal("25.00")

    @pytest.mark.asyncio
    async def test_trigger_refund_customer_not_found(self, mock_db):
        """Test error when customer doesn't exist."""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(CustomerNotFoundError) as exc:
            await trigger_refund(
                {
                    "customer_id": "CUST-99999",
                    "amount": "25.00",
                    "reason": "Test refund reason"
                },
                mock_db
            )
        assert "No customer found" in str(exc.value)

    @pytest.mark.asyncio
    async def test_trigger_refund_invalid_amount(self, mock_db):
        """Test validation for invalid amount."""
        # Amount must be positive
        with pytest.raises(ValueError) as exc:
            await trigger_refund(
                {
                    "customer_id": "CUST-10001",
                    "amount": "-25.00",
                    "reason": "Test refund reason"
                },
                mock_db
            )
        assert "gt" in str(exc.value)

        # Amount with too many decimals
        with pytest.raises(ValueError) as exc:
            await trigger_refund(
                {
                    "customer_id": "CUST-10001",
                    "amount": "25.005",
                    "reason": "Test refund reason"
                },
                mock_db
            )
        assert "at most 2 decimal places" in str(exc.value)

    @pytest.mark.asyncio
    async def test_trigger_refund_reason_too_short(self, mock_db):
        """Test validation for reason length."""
        # Reason must be at least 10 characters
        with pytest.raises(ValueError) as exc:
            await trigger_refund(
                {
                    "customer_id": "CUST-10001",
                    "amount": "25.00",
                    "reason": "Too short"
                },
                mock_db
            )
        assert "at least 10 non-whitespace characters" in str(exc.value)

    @pytest.mark.asyncio
    async def test_trigger_refund_exceeds_balance(self, mock_db):
        """Test error when refund exceeds available balance."""
        # Mock customer exists
        mock_customer_result = AsyncMock()
        mock_customer_result.scalar_one_or_none.return_value = "CUST-10001"
        mock_db.execute.return_value = mock_customer_result

        # Mock invoice with small balance
        mock_invoice = AsyncMock()
        mock_invoice.id = "inv-001"
        mock_invoice.amount = Decimal("100.00")
        mock_invoice.amount_refunded = Decimal("90.00")  # Only $10 left
        mock_invoice.status = "partially_refunded"
        mock_invoice.customer_id = "CUST-10001"
        
        mock_invoice_result = AsyncMock()
        mock_invoice_result.scalar_one_or_none.return_value = mock_invoice
        mock_db.execute.return_value = mock_invoice_result

        with pytest.raises(RefundExceedsAvailableError) as exc:
            await trigger_refund(
                {
                    "customer_id": "CUST-10001",
                    "amount": "25.00",  # More than remaining $10
                    "reason": "Test refund reason"
                },
                mock_db
            )
        assert "exceeds the remaining refundable balance" in str(exc.value)

    @pytest.mark.asyncio
    async def test_trigger_refund_no_eligible_invoice(self, mock_db):
        """Test error when customer has no eligible invoice."""
        # Mock customer exists
        mock_customer_result = AsyncMock()
        mock_customer_result.scalar_one_or_none.return_value = "CUST-10004"
        mock_db.execute.return_value = mock_customer_result

        # Mock no invoice found
        mock_invoice_result = AsyncMock()
        mock_invoice_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_invoice_result

        with pytest.raises(NoEligibleInvoiceError) as exc:
            await trigger_refund(
                {
                    "customer_id": "CUST-10004",
                    "amount": "25.00",
                    "reason": "Test refund reason"
                },
                mock_db
            )
        assert "no invoice with a remaining refundable balance" in str(exc.value)

    @pytest.mark.asyncio
    async def test_trigger_refund_specific_invoice(self, mock_db):
        """Test refund against a specific invoice."""
        # Mock customer exists
        mock_customer_result = AsyncMock()
        mock_customer_result.scalar_one_or_none.return_value = "CUST-10001"
        mock_db.execute.return_value = mock_customer_result

        # Mock specific invoice
        mock_invoice = AsyncMock()
        mock_invoice.id = "inv-002"
        mock_invoice.amount = Decimal("200.00")
        mock_invoice.amount_refunded = Decimal("0.00")
        mock_invoice.status = "paid"
        mock_invoice.customer_id = "CUST-10001"
        
        mock_invoice_result = AsyncMock()
        mock_invoice_result.scalar_one_or_none.return_value = mock_invoice
        mock_db.execute.return_value = mock_invoice_result

        mock_refund = AsyncMock()
        mock_refund.id = "ref-002"
        mock_refund.amount = Decimal("50.00")
        mock_refund.reason = "Specific invoice refund"
        mock_refund.status = "completed"
        mock_refund.created_at = "2024-01-15T10:00:00"
        mock_db.refresh.return_value = None

        result = await trigger_refund(
            {
                "customer_id": "CUST-10001",
                "amount": "50.00",
                "reason": "Specific invoice refund",
                "invoice_id": "inv-002"
            },
            mock_db
        )

        assert result.invoice_id == "inv-002"
        assert result.amount == Decimal("50.00")


# ============================================================================
# Tests for Schemas
# ============================================================================

class TestSchemas:
    """Test suite for Pydantic schemas validation."""

    def test_get_customer_record_input_valid(self):
        """Test valid GetCustomerRecordInput."""
        data = GetCustomerRecordInput(customer_id="CUST-12345")
        assert data.customer_id == "CUST-12345"

    def test_get_customer_record_input_whitespace(self):
        """Test whitespace trimming in customer_id."""
        data = GetCustomerRecordInput(customer_id="  CUST-12345  ")
        assert data.customer_id == "CUST-12345"

    def test_get_customer_record_input_invalid(self):
        """Test invalid customer_id formats."""
        invalid_ids = [
            "CUST-1234",  # Only 4 digits
            "CUST-123456",  # 6 digits
            "CUST-12a45",  # Letters in digits
            "CUST12345",  # Missing hyphen
            "cust-12345",  # Lowercase
            "12345",  # No prefix
        ]
        for invalid_id in invalid_ids:
            with pytest.raises(ValueError):
                GetCustomerRecordInput(customer_id=invalid_id)

    def test_trigger_refund_input_valid(self):
        """Test valid TriggerRefundInput."""
        data = TriggerRefundInput(
            customer_id="CUST-12345",
            amount=Decimal("25.00"),
            reason="Valid reason with more than ten chars"
        )
        assert data.customer_id == "CUST-12345"
        assert data.amount == Decimal("25.00")
        assert data.reason == "Valid reason with more than ten chars"

    def test_trigger_refund_input_amount_precision(self):
        """Test amount decimal precision validation."""
        # Valid - 2 decimal places
        data = TriggerRefundInput(
            customer_id="CUST-12345",
            amount=Decimal("25.00"),
            reason="Valid reason with more than ten chars"
        )
        assert data.amount == Decimal("25.00")

        # Invalid - 3 decimal places
        with pytest.raises(ValueError) as exc:
            TriggerRefundInput(
                customer_id="CUST-12345",
                amount=Decimal("25.005"),
                reason="Valid reason with more than ten chars"
            )
        assert "at most 2 decimal places" in str(exc.value)

    def test_trigger_refund_input_reason_whitespace(self):
        """Test reason whitespace validation."""
        # Valid - exactly 10 non-whitespace chars
        data = TriggerRefundInput(
            customer_id="CUST-12345",
            amount=Decimal("25.00"),
            reason="1234567890"  # 10 chars
        )
        assert data.reason == "1234567890"

        # Invalid - 10 spaces (no non-whitespace)
        with pytest.raises(ValueError) as exc:
            TriggerRefundInput(
                customer_id="CUST-12345",
                amount=Decimal("25.00"),
                reason="          "  # 10 spaces
            )
        assert "at least 10 non-whitespace characters" in str(exc.value)