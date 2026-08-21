from __future__ import annotations

import re
from typing import Any


INTERNAL_CONTROL_TOKEN_PATTERNS = (
    (re.compile(r"(?i)(?<![\w])(?:dice|roll_dice|tool_call|function_call)\s*\("), "function-like control call"),
    (re.compile(r"(?i)</?(?:tool|tool_call|function_call)(?:\s+[^>]*)?>"), "tool control tag"),
    (re.compile(r"(?i)<\|(?:tool|tool_call|function_call|system)(?:[^|>]*)\|>"), "model control token"),
    (re.compile(r"(?i)\[\s*system(?:\s+retry\s+instruction)?\s*\]"), "system instruction marker"),
    (re.compile(r"(?i)<<\s*/?sys\s*>>|\[\s*/?inst\s*\]"), "instruction wrapper"),
    (re.compile(r"(?im)^\s*command_overlay\s*\|"), "command overlay marker"),
    (re.compile(r"(?i)\[\s*(?:user\s+dialogue|character\s+action(?:\s*:[^\]]*)?)\s*\]"), "prompt section marker"),
    (re.compile(r"\{\{[^{}\n]{1,128}\}\}"), "template marker"),
    (re.compile(r"(?i)__internal__"), "internal marker"),
)


def internal_control_token_reason(value: Any) -> str | None:
    """Return a high-confidence internal control marker found in nested output."""
    if isinstance(value, str):
        if not value:
            return None
        for pattern, reason in INTERNAL_CONTROL_TOKEN_PATTERNS:
            if pattern.search(value):
                return reason
        return None
    if isinstance(value, dict):
        for item in value.values():
            reason = internal_control_token_reason(item)
            if reason:
                return reason
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            reason = internal_control_token_reason(item)
            if reason:
                return reason
    return None
