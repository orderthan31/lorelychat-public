"""Opt-in auxiliary panels. Primary replies and command panels never share identity.

The list-compatible turn object is private in-process transport, including through
persist callbacks. It is never serialized into CharacterReply or public history.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from app.core.config import get_settings
from app.engine import free_body_protocol as body
from app.services.chat_command_contract import block_examples, command_response_format, parse_command_payload

SUPPORTED_TYPES = {'text', 'note', 'comments', 'commentary', 'table'}
SUPPORTED_CONTEXT = {'scene', 'world', 'characters', 'turn_messages'}
MAX_PANEL_CHARS = 24_000
MAX_PANELS = 32


@dataclass
class OverlayPlan:
    eligible: dict = field(default_factory=dict)
    outcomes: dict = field(default_factory=dict)
    supplemental_context: str = ""


class GeneratedTurn(list):
    def __init__(self, replies, panels, outcomes):
        super().__init__(replies)
        self.command_panels = panels
        self.command_outcomes = outcomes


def build_plan(commands, protocol=None):
    protocol = protocol or body.current_protocol()
    plan = OverlayPlan()
    mixed = any(c.postprocess_target == 'all_bubbles' for c in commands)
    for command in commands:
        if command.id in plan.outcomes:
            continue
        kinds = command.postprocess_block_types or []
        context = command.postprocess_context_options
        reason = None
        if get_settings().chat_command_postprocess_mode != 'combined_overlay':
            reason = 'mode_separate'
        elif protocol not in body.PROTOCOLS:
            reason = 'legacy_protocol'
        elif command.postprocess_target not in {'first_bubble', 'last_bubble'}:
            reason = 'not_overlay'
        elif not kinds:
            reason = 'generic_contract'
        elif set(kinds) - SUPPORTED_TYPES:
            reason = 'unsupported_block_types'
        elif set(context or []) - SUPPORTED_CONTEXT:
            # A differently scoped recent suffix or retired numeric relationship
            # data cannot be claimed to exist in the primary harness.
            reason = 'unsupported_context'
        elif mixed:
            reason = 'mixed_mutating_commands'
        elif len(plan.eligible) >= MAX_PANELS:
            reason = 'panel_limit'
        if reason:
            plan.outcomes[command.id] = {'status': 'standalone', 'reason': reason}
        else:
            alias = f'K{len(plan.eligible) + 1}'
            plan.eligible[alias] = command
            plan.outcomes[command.id] = {'status': 'fallback', 'reason': 'missing_panel', 'alias': alias}
    return plan


def instructions(plan, protocol):
    if not plan or not plan.eligible:
        return ''
    lines = [
        'Auxiliary command panels: keep the complete primary scene and its existing speaker/body grammar unchanged. '
        'Never shorten the primary prose to fit panels. Panels are independent command-owned UI, not character speech. '
        'Use only public events in the primary turn, never private <t> thoughts. '
        'Reuse the already supplied scene/world/cast and source turn; do not repeat those inputs or character dialogue. '
        'Insertion first/last is a UI position, not a scene start/end. Follow each explicit command constraint below.'
    ]
    if protocol == 'tagged_text':
        lines.append('After ALL complete <m> bubbles, append <panels><panel k="K1">typed blocks</panel></panels>. '
                     'No JSON. Exact command aliases only; never use K aliases in <m>. Escape XML text (&lt;, &amp;) inside panels. '
                     'Literal panel delimiters in primary prose must be backtick-quoted or escaped. Each alias occurs once.')
    else:
        lines.append('Compact extension: {"replies":[existing who/text/emotion bubbles],"panels":[{"key":"K1","blocks":[typed blocks]}]}. '
                     'replies MUST be first, panels last. No content/action/thought fields in this extension. Each exact alias occurs once.')
    for alias, command in plan.eligible.items():
        lines.append(f'{alias}: insert={command.postprocess_target}; context={",".join(command.postprocess_context_options or [])}; '
                     f'constraint={command.postprocess_prompt or command.prompt}')
        if protocol == 'free_body_json':
            lines.append('Block shape examples (choose as needed): ' + json.dumps(block_examples(command.postprocess_block_types), ensure_ascii=False))
        else:
            samples = {
                'text': '<text title="optional">text</text>',
                'note': '<note title="optional">status</note>',
                'comments': '<comments title="optional"><c author="viewer">reaction</c></comments>',
                'commentary': '<commentary title="optional"><c author="viewer">reaction</c></commentary>',
                'table': '<table title="optional"><headers><cell>column</cell></headers><row><cell>value</cell></row></table>',
            }
            lines.append('Allowed typed block examples: ' + ' '.join(samples[k] for k in dict.fromkeys(command.postprocess_block_types)))
    if plan.supplemental_context:
        lines.append(plan.supplemental_context)
    return '\n'.join(lines)


def response_format(protocol, aliases, plan):
    result = body.response_format(protocol, aliases)
    if result is None or not plan or not plan.eligible:
        return result
    result = deepcopy(result)
    variants = []
    for alias, command in plan.eligible.items():
        blocks = command_response_format(1, overlay=True, block_types=command.postprocess_block_types)['schema']['properties']['messages']['items']['properties']['blocks']
        variants.append({'type': 'object', 'additionalProperties': False, 'required': ['key', 'blocks'],
                         'properties': {'key': {'type': 'string', 'enum': [alias]}, 'blocks': blocks}})
    result['schema']['properties']['panels'] = {'type': 'array', 'maxItems': MAX_PANELS, 'items': {'anyOf': variants}}
    return result


def _xml_blocks(raw):
    # No entities/DTD/processing instructions. Bound depth BEFORE XML parsing.
    if len(raw) > MAX_PANEL_CHARS or '<!' in raw or '<?' in raw:
        raise ValueError('unsafe_panel')
    depth = 0
    for token in re.finditer(r'<[^>]*>', raw):
        if token.group().startswith('</'):
            depth -= 1
        elif not token.group().endswith('/>'):
            depth += 1
        if not 0 <= depth <= 5:
            raise ValueError('panel_depth')
    root = ET.fromstring(raw)
    if root.tag != 'panel' or set(root.attrib) != {'k'} or (root.text or '').strip():
        raise ValueError('panel_shape')
    blocks = []
    def leaf(node):
        if len(node):
            raise ValueError('nested_leaf')
        return node.text or ''
    for node in root:
        if set(node.attrib) - {'title'} or (node.tail or '').strip():
            raise ValueError('block_shape')
        item: dict = {'type': node.tag, 'title': node.get('title', '')}
        if node.tag in {'text', 'note'}:
            item['text'] = leaf(node)
        elif node.tag in {'comments', 'commentary'}:
            if (node.text or '').strip():
                raise ValueError('block_text')
            item['comments'] = []
            for child in node:
                if child.tag != 'c' or set(child.attrib) != {'author'} or (child.tail or '').strip():
                    raise ValueError('comment_shape')
                item['comments'].append({'author': child.get('author'), 'text': leaf(child)})
        elif node.tag == 'table':
            if (node.text or '').strip() or not len(node) or node[0].tag != 'headers':
                raise ValueError('table_shape')
            rows = []
            for index, row in enumerate(node):
                if row.tag != ('headers' if index == 0 else 'row') or row.attrib or (row.text or '').strip() or (row.tail or '').strip():
                    raise ValueError('row_shape')
                values = []
                for cell in row:
                    if cell.tag != 'cell' or cell.attrib or (cell.tail or '').strip():
                        raise ValueError('cell_shape')
                    values.append(leaf(cell))
                rows.append(values)
            item.update(headers=rows[0], rows=rows[1:])
        else:
            raise ValueError('unsupported_block')
        blocks.append(item)
    return {'key': root.get('k'), 'blocks': blocks}


def _split_tagged(raw):
    masked = body._masked(raw)
    # Only a top-level panels opening starts the auxiliary suffix. A literal
    # inside an m bubble cannot consume the rest of a primary turn.
    depth = 0
    for token in re.finditer(r'<\s*(/?)\s*(m|panels)\b[^>]*(?:>|$)', masked, re.I):
        close, tag = token.group(1), token.group(2).lower()
        if tag == 'm':
            depth += -1 if close else 1
        elif not close and depth == 0:
            return raw[:token.start()], raw[token.start():]
    return raw, ''


def _split_json(raw):
    # Parse the primary array independently. A corrupt auxiliary tail must not
    # cause a second primary generation. Envelope order is explicit on the wire.
    start = re.match(r'\s*\{\s*"replies"\s*:\s*', raw, re.I)
    if not start:
        raise body.FreeBodyError('Expected replies first')
    try:
        replies, end = json.JSONDecoder(object_pairs_hook=body._unique_object).raw_decode(raw, start.end())
    except (ValueError, RecursionError) as exc:
        raise body.FreeBodyError('Invalid primary array') from exc
    tail = raw[end:]
    if re.fullmatch(r'\s*}\s*', tail):
        return raw[:end] + '}', None
    marker = re.match(r'\s*,\s*"panels"\s*:\s*', tail, re.I)
    if not marker:
        raise body.FreeBodyError('Unknown or duplicate primary envelope key')
    auxiliary = tail[marker.end():].rstrip()
    try:
        _, panel_end = json.JSONDecoder(object_pairs_hook=body._unique_object).raw_decode(auxiliary)
    except (ValueError, RecursionError):
        # Unreadable auxiliary value; the already closed primary is authoritative.
        return raw[:end] + '}', None
    if not re.fullmatch(r'\s*}\s*', auxiliary[panel_end:]):
        raise body.FreeBodyError('Trailing or duplicate envelope fields')
    return raw[:end] + '}', auxiliary[:panel_end]


def split_response(raw, protocol, plan):
    if not isinstance(raw, str) or len(raw) > body.MAX_RESPONSE_CHARS:
        raise body.FreeBodyError('Oversized combined response')
    fenced = re.fullmatch(r'\s*```(?:json|xml|text)?\s*\n([\s\S]*?)\n```\s*', raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()
    outcomes = deepcopy(plan.outcomes)
    entries = []
    bad_aliases = set()
    if protocol == 'tagged_text':
        primary, auxiliary = _split_tagged(raw)
        if auxiliary:
            masked_aux = body._masked(auxiliary)
            # Auxiliary corruption may fall back, but never hide primary output.
            # Scan before recovery, including inside panels and unclosed wrappers.
            if re.search(r'<\s*/?\s*m\b', masked_aux, re.I):
                raise body.FreeBodyError('Primary bubble after auxiliary envelope')
            wrapper_end = masked_aux.rfind('</panels>')
            if wrapper_end >= 0 and masked_aux[wrapper_end + len('</panels>'):].strip():
                auxiliary = ''  # malformed auxiliary tail, not a primary repair
        # Recover only independently closed panel records, never primary pieces.
        # Bound the tail separately; oversize auxiliary => affected commands fall back.
        if auxiliary and len(auxiliary) <= MAX_PANEL_CHARS:
            masked = body._masked(auxiliary)
            tokens = list(re.finditer(r'<panel\b[^>]*>|</panel\s*>', masked))
            if len(tokens) > MAX_PANELS * 4:
                tokens = []
            stack = []
            for token in tokens:
                if token.group().startswith('</'):
                    if not stack:
                        continue
                    record = stack.pop()
                    keys = record['keys']
                    entry = None
                    if not record['nested'] and len(keys) == 1:
                        try:
                            entry = _xml_blocks(auxiliary[record['start']:token.end()])
                        except (ValueError, ET.ParseError, RecursionError):
                            pass
                    if entry is not None:
                        entries.append(entry)
                    else:
                        entries.extend({'key': key, 'blocks': None} for key in keys)
                else:
                    keys = re.findall(r"\bk\s*=\s*[\"']([^\"']+)[\"']", token.group())
                    for record in stack:
                        record['nested'] = True
                    stack.append({'keys': keys, 'start': token.start(), 'nested': bool(stack)})
            for record in stack:
                entries.extend({'key': key, 'blocks': None} for key in record['keys'])

    else:
        primary, auxiliary = _split_json(raw)
        if auxiliary and len(auxiliary) <= MAX_PANEL_CHARS:
            try:
                entries = json.loads(auxiliary, object_pairs_hook=body._unique_object)
            except (ValueError, RecursionError):
                entries = []
            if not isinstance(entries, list) or len(entries) > MAX_PANELS:
                entries = []
    panels, seen, unknown = {}, set(), []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('key'), str):
            continue
        alias = entry['key']
        if alias not in plan.eligible:
            unknown.append(alias[:80])
            continue
        command = plan.eligible[alias]
        if alias in seen:
            bad_aliases.add(alias)
            panels.pop(command.id, None)
            outcomes[command.id].update(status='fallback', reason='duplicate_alias')
            continue
        seen.add(alias)
        parsed = None
        if set(entry) == {'key', 'blocks'}:
            parsed = parse_command_payload(json.dumps({'messages': [{'content': '', 'blocks': entry['blocks']}]}), 1,
                                           overlay=True, block_types=command.postprocess_block_types)
        if parsed and alias not in bad_aliases:
            panels[command.id] = parsed
            outcomes[command.id].update(status='combined_success', reason='validated_panel')
        else:
            outcomes[command.id].update(status='fallback', reason='invalid_panel')
    if unknown:
        for command in plan.eligible.values():
            outcomes[command.id]['unknown_aliases'] = sorted(set(unknown))
    return primary, panels, outcomes
