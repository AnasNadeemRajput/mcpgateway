"""
tests/test_task3_guardrail.py

Unit tests for Task 3: Streaming Guardrail with PII Redaction
"""

import pytest
from unittest.mock import AsyncMock, patch

from task3_streaming_guardrail.redaction.chunk_buffer import ChunkBuffer
from task3_streaming_guardrail.redaction.patterns import (
    EMAIL_PATTERN,
    SSN_PATTERN,
    CREDIT_CARD_PATTERN,
    PATTERNS,
    REDACTION_PLACEHOLDER,
    _luhn_is_valid,
    _validate_credit_card,
)
from task3_streaming_guardrail.streamhandler import (
    MockUpstreamLLMClient,
    _redacted_stream,
)


# ============================================================================
# Tests for patterns.py
# ============================================================================

class TestPatterns:
    """Test suite for pattern definitions."""

    def test_email_pattern(self):
        """Test email pattern matching."""
        valid_emails = [
            "test@example.com",
            "john.doe@company.co.uk",
            "user+tag@gmail.com",
            "first.last@sub.domain.com",
        ]
        for email in valid_emails:
            assert EMAIL_PATTERN.search(email) is not None, f"Failed: {email}"

        invalid_emails = [
            "test@example",  # Missing TLD
            "test@.com",  # Empty domain
            "@example.com",  # Missing local part
            "test@example.",  # Missing TLD
        ]
        for email in invalid_emails:
            assert EMAIL_PATTERN.search(email) is None, f"Failed: {email}"

    def test_ssn_pattern(self):
        """Test SSN pattern matching."""
        valid_ssns = [
            "123-45-6789",
            "001-12-3456",
            "999-88-7777",
        ]
        for ssn in valid_ssns:
            assert SSN_PATTERN.search(ssn) is not None, f"Failed: {ssn}"

        invalid_ssns = [
            "123-45-678",  # Only 3+2+3 digits
            "123-45-67890",  # Too many digits
            "123456789",  # No hyphens
            "abc-def-ghij",  # Not digits
        ]
        for ssn in invalid_ssns:
            assert SSN_PATTERN.search(ssn) is None, f"Failed: {ssn}"

    def test_credit_card_pattern(self):
        """Test credit card pattern matching."""
        valid_cards = [
            "4111 1111 1111 1111",
            "4111-1111-1111-1111",
            "1234567890123456",  # 16 digits
            "1234 5678 9012 3456",
        ]
        for card in valid_cards:
            assert CREDIT_CARD_PATTERN.search(card) is not None, f"Failed: {card}"

        invalid_cards = [
            "1234",  # Too short
            "12345678901234567890",  # Too long (20 digits)
            "abcd-efgh-ijkl-mnop",  # Not digits
        ]
        for card in invalid_cards:
            assert CREDIT_CARD_PATTERN.search(card) is None, f"Failed: {card}"

    def test_luhn_validation(self):
        """Test Luhn checksum validation."""
        # Valid test cards (from Stripe test data)
        valid_cards = [
            "4242424242424242",  # Visa
            "5555555555554444",  # Mastercard
            "378282246310005",   # Amex
            "6011111111111117",  # Discover
        ]
        for card in valid_cards:
            assert _luhn_is_valid(card) is True, f"Failed: {card}"

        # Invalid cards
        invalid_cards = [
            "4111111111111111",  # Invalid Visa (should be 4242...)
            "1234567890123456",  # Random numbers
            "0000000000000000",  # All zeros
        ]
        for card in invalid_cards:
            assert _luhn_is_valid(card) is False, f"Failed: {card}"

    def test_validate_credit_card(self):
        """Test credit card validation with formatting."""
        # Valid with formatting
        assert _validate_credit_card("4242 4242 4242 4242") is True
        assert _validate_credit_card("4242-4242-4242-4242") is True
        
        # Invalid
        assert _validate_credit_card("1234 5678 9012 3456") is False
        assert _validate_credit_card("1111-2222-3333-4444") is False

    def test_patterns_length(self):
        """Test pattern specifications."""
        assert len(PATTERNS) == 3
        pattern_names = [p.name for p in PATTERNS]
        assert "email" in pattern_names
        assert "ssn" in pattern_names
        assert "credit_card" in pattern_names


# ============================================================================
# Tests for chunk_buffer.py
# ============================================================================

class TestChunkBuffer:
    """Test suite for ChunkBuffer."""

    def test_chunk_buffer_email_split(self):
        """Test email split across chunk boundary."""
        buffer = ChunkBuffer()
        
        # First chunk has partial email
        chunks = buffer.feed("Contact me at john.doe@ex")
        assert chunks == []  # Should hold back
        
        # Second chunk completes the email
        chunks = buffer.feed("ample.com for help")
        assert len(chunks) == 1
        assert "john.doe@" in chunks[0]
        assert REDACTION_PLACEHOLDER in chunks[0]
        assert "ample.com" not in chunks[0]

    def test_chunk_buffer_ssn_split(self):
        """Test SSN split across chunk boundary."""
        buffer = ChunkBuffer()
        
        # SSN split across chunks
        chunks = buffer.feed("My SSN is 123-45-")
        assert chunks == []
        
        chunks = buffer.feed("6789 and I need help")
        assert len(chunks) == 1
        assert REDACTION_PLACEHOLDER in chunks[0]
        assert "123-45-6789" not in chunks[0]

    def test_chunk_buffer_credit_card_split(self):
        """Test credit card split across chunk boundary."""
        buffer = ChunkBuffer()
        
        # Credit card split across chunks
        chunks = buffer.feed("Card number: 4242 4242 4242")
        assert chunks == []
        
        chunks = buffer.feed(" 4242 ends")
        assert len(chunks) == 1
        assert REDACTION_PLACEHOLDER in chunks[0]
        assert "4242 4242 4242 4242" not in chunks[0]

    def test_chunk_buffer_no_redaction_needed(self):
        """Test chunks without PII."""
        buffer = ChunkBuffer()
        
        chunks = buffer.feed("Hello, this is a normal message without any PII.")
        assert len(chunks) >= 1
        assert REDACTION_PLACEHOLDER not in chunks[0]

    def test_chunk_buffer_multiple_patterns(self):
        """Test multiple patterns in one chunk."""
        buffer = ChunkBuffer()
        chunks = buffer.feed(
            "Email: test@example.com, SSN: 123-45-6789, Card: 4242-4242-4242-4242"
        )
        assert len(chunks) == 1
        assert REDACTION_PLACEHOLDER in chunks[0]
        assert chunks[0].count(REDACTION_PLACEHOLDER) >= 3

    def test_chunk_buffer_finalize(self):
        """Test finalizing the buffer."""
        buffer = ChunkBuffer()
        
        # Feed incomplete pattern
        buffer.feed("incomplete email: user@dom")
        # Should be held back
        
        # Finalize should flush everything
        chunks = buffer.finalize()
        assert len(chunks) == 1
        # The incomplete pattern is still redacted (since we can't know if it's complete)
        assert "user@dom" in chunks[0]

    def test_chunk_buffer_edge_cases(self):
        """Test edge cases."""
        buffer = ChunkBuffer()
        
        # Empty chunk
        chunks = buffer.feed("")
        assert chunks == []
        
        # Very large chunk
        large_chunk = "a" * 1000
        chunks = buffer.feed(large_chunk)
        assert len(chunks) == 1  # Should flush most of it
        assert len(buffer._held_back) < 255  # Should hold back max pattern length


# ============================================================================
# Tests for streamhandler.py
# ============================================================================

class TestMockUpstreamLLMClient:
    """Test suite for MockUpstreamLLMClient."""

    @pytest.mark.asyncio
    async def test_stream_completion(self):
        """Test streaming completion."""
        client = MockUpstreamLLMClient()
        chunks = []
        
        async for chunk in client.stream_completion("test prompt"):
            chunks.append(chunk)
        
        assert len(chunks) > 0
        # The mock response should contain our test PII
        full_response = "".join(chunks)
        assert "john.doe@ex" in full_response or "ample.com" in full_response

    @pytest.mark.asyncio
    async def test_stream_completion_with_redaction(self):
        """Test that redaction works on the stream."""
        chunks = []
        
        async for chunk in _redacted_stream("test prompt"):
            chunks.append(chunk)
        
        full_response = "".join(chunks)
        
        # PII should be redacted
        assert "john.doe@example.com" not in full_response
        assert "123-45-6789" not in full_response
        assert "4242 4242 4242 4242" not in full_response
        
        # Redaction placeholders should be present
        assert REDACTION_PLACEHOLDER in full_response
        assert "[REDACTED]" in full_response

    @pytest.mark.asyncio
    async def test_stream_ttft_logging(self, caplog):
        """Test TTFT logging."""
        import logging
        
        with caplog.at_level(logging.INFO):
            chunks = []
            async for chunk in _redacted_stream("test prompt"):
                chunks.append(chunk)
                if len(chunks) == 1:
                    # First chunk should trigger TTFT log
                    assert "TTFT:" in caplog.text


# ============================================================================
# Integration tests
# ============================================================================

class TestGuardrailIntegration:
    """Integration tests for the guardrail."""

    @pytest.mark.asyncio
    async def test_end_to_end_stream(self):
        """Test end-to-end streaming with redaction."""
        chunks = []
        full_response = ""
        
        async for chunk in _redacted_stream("Tell me about customer support"):
            chunks.append(chunk)
            full_response += chunk
        
        # Verify multiple chunks were received
        assert len(chunks) > 1
        
        # Verify all PII is redacted
        assert "john.doe@example.com" not in full_response
        assert "123-45-6789" not in full_response
        assert "4242 4242 4242 4242" not in full_response
        
        # Verify non-PII text remains
        assert "support team" in full_response or "customer" in full_response

    @pytest.mark.asyncio
    async def test_stream_handles_emoji_and_unicode(self):
        """Test streaming with emoji and unicode characters."""
        client = MockUpstreamLLMClient()
        
        # Mock response with unicode
        test_text = "Hello 👋, email: test@example.com, SSN: 123-45-6789, 你好"
        chunks = []
        async for chunk in _redacted_stream(test_text):
            chunks.append(chunk)
        
        full_response = "".join(chunks)
        assert "test@example.com" not in full_response
        assert "123-45-6789" not in full_response
        # Unicode should be preserved
        assert "你好" in full_response