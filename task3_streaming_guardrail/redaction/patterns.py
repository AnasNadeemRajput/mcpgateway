"""
task3_streaming_guardrail/redaction/patterns.py

Defines the PII patterns this guardrail detects and redacts.
"""

import re
from dataclasses import dataclass
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

CREDIT_CARD_PATTERN = re.compile(
    r"\b(?:\d[ -]?){12,18}\d\b"
)


def _luhn_is_valid(digits: str) -> bool:
    """
    Standard Luhn checksum validation.

    Guards against two edge cases the bare loop below doesn't handle on
    its own: an empty string (0 iterations -> sum 0 -> trivially "valid"
    despite not being a real number at all) and non-digit characters
    (int(char) would raise ValueError instead of returning a sensible
    False). In production this function only ever receives pre-filtered
    digit strings from _validate_credit_card, but making it robust to
    direct/arbitrary input is cheap and avoids a surprising crash.
    """
    if not digits or not digits.isdigit():
        return False

    total = 0
    should_double = False
    for char in reversed(digits):
        d = int(char)
        if should_double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        should_double = not should_double
    return total % 10 == 0


def _validate_credit_card(match_text: str) -> bool:
    digits = re.sub(r"[ -]", "", match_text)
    if not (13 <= len(digits) <= 19):
        return False
    return _luhn_is_valid(digits)


@dataclass(frozen=True)
class PatternSpec:
    name: str
    pattern: re.Pattern
    validator: Optional[Callable[[str], bool]] = None


PATTERNS: list[PatternSpec] = [
    PatternSpec(name="email", pattern=EMAIL_PATTERN),
    PatternSpec(name="ssn", pattern=SSN_PATTERN),
    PatternSpec(
        name="credit_card",
        pattern=CREDIT_CARD_PATTERN,
        validator=_validate_credit_card,
    ),
]

MAX_PATTERN_LENGTH = 254
REDACTION_PLACEHOLDER = "[REDACTED]"