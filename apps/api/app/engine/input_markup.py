from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class RenderPart:
    type: Literal["dialogue", "action", "thought"]
    text: str


@dataclass(frozen=True)
class ParsedInputMarkup:
    dialogue: str
    action: str
    had_markup: bool = False
    parts: list[RenderPart] = field(default_factory=list)


def normalize_segment_text(value: str) -> str:
    return " ".join(value.split())


def is_single_asterisk_marker(text: str, index: int) -> bool:
    previous_char = text[index - 1] if index > 0 else ""
    next_char = text[index + 1] if index + 1 < len(text) else ""
    return text[index] == "*" and previous_char != "*" and next_char != "*"


def find_next_action_marker(text: str, cursor: int) -> tuple[int, Literal["*"]] | None:
    for index in range(cursor, len(text)):
        if is_single_asterisk_marker(text, index):
            return index, "*"
    return None


def find_closing_action_marker(text: str, cursor: int, marker: Literal["*"]) -> int:
    for index in range(cursor, len(text)):
        if is_single_asterisk_marker(text, index):
            return index
    return -1


def parse_input_markup(raw: str) -> ParsedInputMarkup:
    """Split Lorechat composer input into dialogue and action text.

    Paired single asterisks mark narration/action: ``*walks closer* hello``.
    Double asterisks are ignored so markdown-style bold text is not treated as
    action. Text outside paired markers remains dialogue. Unclosed markers are
    treated as normal dialogue so casual text does not eat the rest of the
    message.
    """
    text = raw or ""
    dialogue_parts: list[str] = []
    action_parts: list[str] = []
    parts: list[RenderPart] = []
    cursor = 0
    had_markup = False

    while cursor < len(text):
        start = find_next_action_marker(text, cursor)
        if start is None:
            dialogue_text = normalize_segment_text(text[cursor:])
            if dialogue_text:
                dialogue_parts.append(dialogue_text)
                parts.append(RenderPart(type="dialogue", text=dialogue_text))
            break
        start_index, marker = start
        marker_length = len(marker)
        end = find_closing_action_marker(text, start_index + marker_length, marker)
        if end < 0:
            dialogue_text = normalize_segment_text(text[cursor:])
            if dialogue_text:
                dialogue_parts.append(dialogue_text)
                parts.append(RenderPart(type="dialogue", text=dialogue_text))
            break

        dialogue_text = normalize_segment_text(text[cursor:start_index])
        if dialogue_text:
            dialogue_parts.append(dialogue_text)
            parts.append(RenderPart(type="dialogue", text=dialogue_text))
        action_text = normalize_segment_text(text[start_index + marker_length:end])
        if action_text:
            action_parts.append(action_text)
            parts.append(RenderPart(type="action", text=action_text))
            had_markup = True
        cursor = end + marker_length

    dialogue = normalize_segment_text(" ".join(dialogue_parts))
    action = normalize_segment_text(" ".join(action_parts))
    return ParsedInputMarkup(dialogue=dialogue, action=action, had_markup=had_markup, parts=parts)
