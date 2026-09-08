"""
task3_streaming_guardrail/redaction/chunk_buffer.py

Solves the split-pattern problem in streaming text.
"""

import re

from task3_streaming_guardrail.redaction.patterns import (
    MAX_PATTERN_LENGTH,
    PATTERNS,
    REDACTION_PLACEHOLDER,
)

HOLD_BACK_SIZE = MAX_PATTERN_LENGTH - 1


def _redact(text: str) -> str:
    """Applies all pattern specs to `text`."""
    for spec in PATTERNS:
        if spec.validator is None:
            text = spec.pattern.sub(REDACTION_PLACEHOLDER, text)
        else:
            text = spec.pattern.sub(
                lambda m: REDACTION_PLACEHOLDER if spec.validator(m.group(0)) else m.group(0),
                text,
            )
    return text


class ChunkBuffer:
    """Stateful, per-connection buffer for streaming redaction."""

    def __init__(self):
        self._held_back = ""

    def feed(self, chunk: str) -> list[str]:
        """Accepts a new chunk and returns safe-to-forward strings."""
        combined = self._held_back + chunk
        redacted = _redact(combined)

        if len(redacted) <= HOLD_BACK_SIZE:
            self._held_back = redacted
            return []

        flush_point = len(redacted) - HOLD_BACK_SIZE
        to_flush = redacted[:flush_point]
        self._held_back = redacted[flush_point:]

        return [to_flush] if to_flush else []

    def finalize(self) -> list[str]:
        """Releases whatever's still held back after the stream ends."""
        remaining = self._held_back
        self._held_back = ""
        return [remaining] if remaining else []