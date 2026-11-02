"""Monotonic reserved-tag recognition shared by ordinary protocol paths.

Possessive whitespace and tail scans never repartition whitespace on a near
miss. A recognized token consumes its entire tail, including malformed EOF.
Unknown tags, Markdown and literal domains are not rewritten here.
"""
import re

_PATTERNS = {}

def reserved_tags(text, names):
    names = tuple(names)
    if names not in _PATTERNS:
        _PATTERNS[names] = re.compile(
            r'<\s*+(/?)\s*+(' + '|'.join(re.escape(n) for n in names) + r')\b[^>]*+(?:>|$)', re.I)
    return _PATTERNS[names].finditer(text)


def require_unicode_scalars(value):
    """Reject, never replace, strings that cannot bind to SQLite UTF-8 TEXT.

    Walk iteratively: even an invalid auxiliary tree must not leak Python's
    recursion exception before its independently owned fallback validator.
    """
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise ValueError('Invalid Unicode scalar in generated output')
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def check_legacy_ownership(content):
    """Lex original keys before repair; duplicates cannot disappear in repair.

    Keep unambiguous ancillary objects and historical near-JSON repair. Lists
    of grouped speakers remain one root, not competing top-level envelopes.
    """
    import json
    stack, roots, pos = [], [], 0
    while pos < len(content):
        char = content[pos]
        if char in '{[':
            if len(stack) >= 64:
                raise ValueError('Legacy JSON nesting limit')
            node = {'kind': char, 'keys': set(), 'primary': False}
            if not stack:
                roots.append(node)
            stack.append(node)
            pos += 1
        elif char in '}]':
            if stack:
                stack.pop()
            pos += 1
        elif char == '"':
            start = pos
            pos += 1
            while pos < len(content):
                if content[pos] == '\\':
                    pos += 2
                elif content[pos] == '"':
                    pos += 1
                    break
                else:
                    pos += 1
            end = pos
            while pos < len(content) and content[pos].isspace():
                pos += 1
            if pos < len(content) and content[pos] == ':' and stack and stack[-1]['kind'] == '{':
                try:
                    key = json.loads(content[start:end])
                except ValueError:
                    continue
                keys = stack[-1]['keys']
                if key in keys:
                    raise ValueError('Duplicate legacy JSON key')
                keys.add(key)
                if key in {'replies', 'dialogue', 'text'}:
                    roots[-1]['primary'] = True
        else:
            pos += 1
    if sum(root['primary'] for root in roots) > 1:
        raise ValueError('Multiple primary legacy envelopes')
    for root in roots:
        if 'replies' in root['keys'] and root['keys'] != {'replies'}:
            raise ValueError('Unknown legacy envelope fields')
