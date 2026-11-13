"""Reject-only ingress budgets, never truncate authored text or validate old reads."""
from typing import ClassVar
from pydantic import BaseModel, model_validator

MAX_AUTHORED_CHARS = 262144
MAX_INPUT_NODES = 8192
MAX_INPUT_DEPTH = 16
MAX_INPUT_TEXT = 1048576


def check_input_budget(value, *, root_string_limits=None):
    stack = [(value, 0, '')]
    nodes = text_size = 0
    while stack:
        item, depth, key = stack.pop()
        nodes += 1
        if nodes > MAX_INPUT_NODES or depth > MAX_INPUT_DEPTH:
            raise ValueError('Request structure exceeds depth/node budget')
        if isinstance(item, str):
            limit = 2048 if key.endswith(('_id', '_url', '_key')) or key in {'id','name','title','model','alias','speaker_id'} else MAX_AUTHORED_CHARS
            # Explicit DTO-owned exceptions apply only to direct fields. Keep
            # nested budgets and total text accounting unchanged.
            if depth == 1 and root_string_limits:
                limit = root_string_limits.get(key, limit)
            if len(item) > limit:
                raise ValueError(f'{key or "text"} exceeds {limit} characters')
            text_size += len(item)
            if text_size > MAX_INPUT_TEXT:
                raise ValueError('Request text exceeds aggregate character budget')
        elif isinstance(item, dict):
            for name, child in item.items():
                if not isinstance(name, str) or len(name) > 256:
                    raise ValueError('Object keys must be strings of at most 256 characters')
                stack.append((child, depth + 1, name))
        elif isinstance(item, (list, tuple)):
            limit = 64 if key in {'participants','postprocess_block_types','postprocess_context_options','context_sources'} else 1024
            if len(item) > limit:
                raise ValueError(f'{key or "collection"} exceeds {limit} entries')
            stack.extend((child, depth + 1, key) for child in item)
    return value


class RequestModel(BaseModel):
    # Nullable JSON DB columns retain their existing clear/reset semantics.
    validate_inputs: ClassVar[bool] = True
    root_string_limits: ClassVar[dict[str, int]] = {}
    nonnull_fields: ClassVar[frozenset[str]] = frozenset()
    nonblank_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode='before')
    @classmethod
    def validate_request_boundary(cls, data):
        if not cls.validate_inputs:
            return data  # historical response DTOs are not ingress
        check_input_budget(data, root_string_limits=cls.root_string_limits)
        if isinstance(data, dict):
            for key in cls.nonnull_fields:
                if key in data and data[key] is None:
                    raise ValueError(f'{key} cannot be null; omit it to preserve the existing value')
            for key in cls.nonblank_fields:
                if key in data and isinstance(data[key], str) and not data[key].strip():
                    raise ValueError(f'{key} cannot be blank')
        return data
