"""Closed command output contract; reject whole payloads, never salvage or clip prose."""
from __future__ import annotations

import json
import math
from copy import deepcopy

from app.engine.output_guard import internal_control_token_reason


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _array(items: dict, **bounds) -> dict:
    return {"type": "array", "items": items, **bounds}


_STRING = {"type": "string"}
_NONEMPTY = {"type": "string", "minLength": 1}
_BLOCK_FIELDS = {
    "text": {"text": _NONEMPTY},
    "note": {"text": _NONEMPTY},
    "table": {"headers": _array(_NONEMPTY, minItems=1), "rows": _array(_array(_STRING, minItems=1), minItems=1)},
    "comments": {"comments": _array(_object({"author": _STRING, "text": _NONEMPTY}), minItems=1)},
    "commentary": {"comments": _array(_object({"author": _STRING, "text": _NONEMPTY}), minItems=1)},
    "checklist": {"items": _array(_object({"text": _NONEMPTY, "checked": {"type": "boolean"}}), minItems=1)},
    "timeline": {"items": _array(_object({"time": _STRING, "text": _NONEMPTY}), minItems=1)},
    "vote": {"options": _array(_object({"label": _NONEMPTY, "value": {"anyOf": [{"type": "number"}, {"type": "null"}]}}), minItems=1)},
    "cards": {"cards": _array(_object({"title": _STRING, "text": _NONEMPTY}), minItems=1)},
}


def command_response_format(count: int, *, overlay: bool = False, block_types=None) -> dict:
    blocks = {"anyOf": [
        _object({"type": {"type": "string", "enum": [kind]}, "title": _STRING, **fields})
        for kind, fields in _BLOCK_FIELDS.items() if not block_types or kind in block_types
    ]}
    return {"type": "json_object", "name": "chat_command_messages", "schema": _object({
        "messages": _array(_object({"content": _STRING, "blocks": _array(blocks, minItems=1 if overlay else 0)}), minItems=count, maxItems=count),
    })}


def block_examples(block_types=None):
    def example(schema):
        if "enum" in schema:
            return schema["enum"][0]
        if "anyOf" in schema:
            return example(schema["anyOf"][0])
        kind = schema["type"]
        if kind == "object":
            return {key: example(value) for key, value in schema["properties"].items()}
        if kind == "array":
            return [example(schema["items"])]
        return {"string": "example", "boolean": False, "number": 50, "null": None}[kind]
    return [example(schema) for schema in command_response_format(1, block_types=block_types)["schema"]["properties"]["messages"]["items"]["properties"]["blocks"]["items"]["anyOf"]]


def _matches(value: object, schema: dict) -> bool:
    if "anyOf" in schema:
        return any(_matches(value, child) for child in schema["anyOf"])
    kind = schema["type"]
    if kind == "object":
        return (isinstance(value, dict) and set(value) == set(schema["properties"])
                and all(_matches(value[key], child) for key, child in schema["properties"].items()))
    if kind == "array":
        return (isinstance(value, list) and len(value) >= schema.get("minItems", 0)
                and len(value) <= schema.get("maxItems", len(value))
                and all(_matches(item, schema["items"]) for item in value))
    if kind == "string":
        return (isinstance(value, str) and (not schema.get("minLength") or bool(value.strip()))
                and ("enum" not in schema or value in schema["enum"]))
    if kind == "boolean":
        return type(value) is bool
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return value is None if kind == "null" else False


def parse_command_payload(raw: str, count: int, *, overlay: bool = False, block_types=None) -> list[dict] | None:
    if not isinstance(raw, str) or len(raw) > 100_000:
        return None
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        data = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, TypeError, RecursionError):
        return None
    # A missing decorative title is losslessly canonicalized; no text, targets or blocks are dropped.
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        for item in data["messages"]:
            if isinstance(item, dict) and isinstance(item.get("blocks"), list):
                for block in item["blocks"]:
                    if isinstance(block, dict):
                        block.setdefault("title", "")
    if not _matches(data, command_response_format(count, overlay=overlay, block_types=block_types)["schema"]):
        return None
    if internal_control_token_reason(data):
        return None
    for item in data["messages"]:
        if not item["content"].strip() and not item["blocks"]:
            return None
        for block in item["blocks"]:
            if block["type"] == "table" and any(len(row) != len(block["headers"]) or not any(cell.strip() for cell in row) for row in block["rows"]):
                return None
    return deepcopy(data["messages"])
