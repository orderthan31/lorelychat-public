"""Bounded, deterministic text grammar; never HTML and never a semantic classifier.

Reserved delimiters inside backtick code spans/fences or escaped with a backslash
are literal. Unknown HTML-like text remains inert prose. Invalid reserved syntax
fails the entire turn; no salvage, truncation, or guessed speaker assignment.
"""
from __future__ import annotations

import json
import re
from app.core.config import get_settings
from app.schemas.dialogue import CharacterReply

MAX_RESPONSE_CHARS = 100_000
MAX_REPLIES = 32
PROTOCOLS = {'free_body_json', 'tagged_text'}
DEFAULT_FREE_BODY_CORE = """Write only as the listed female character(s), never as a generic assistant or the human user.
Keep each character's identity and voice distinct. Use character-card, appearance, world, and room facts implicitly; do not recite them as exposition.
The latest source input and recent raw history define the current turn and override stale story-arc state. Rolling Story Arc supplies durable history only.
User notes are explicit room canon. World setting defines the stage. Room roles guide speaking priority. Relationship archetype is an authored pacing preset, not a numeric relationship fact.
Never invent the user's dialogue, action, thought, feelings, or decision. Address the actual current counterpart rather than names/honorifics copied from examples, including character-authored turns.
Do not let appearance, genre policy, or background lore hijack an unrelated scene.
Preserve rich, specific roleplay prose, the character's vocabulary, speech rhythm, honorific level and subtext, and natural pacing without repeating the same beat. Private character thoughts are not spoken or facts known by other characters."""
DEFAULT_FREE_BODY_RULES = """Write one continuous body per natural bubble; there is no minimum bubble count or obligation to fill semantic fields. Plain body text is spoken dialogue for a character, narration for N. Optional <a>visible action</a> and <t>private character inner thought</t> may alternate with dialogue in their original order. Do not invent missing actions or thoughts. N cannot have private thoughts. Parentheses and Markdown emphasis are literal presentation, never semantic markers. Quote literal delimiter examples in backticks or escape the opening angle bracket. Do not nest semantic or speaker tags. Emotion is optional; omit it when unnecessary. At least one nonempty onstage character part is required, which may be action or private thought only. Keep the desired richness and length, not filler bubbles. Never emit internal controls or tool calls."""
FREE_BODY_RETRY = "[System retry instruction]\nThe previous response failed the free-body contract. Regenerate the entire complete turn using the supplied speaker aliases and envelope. Close every delimiter. Preserve prose and semantic order; do not add mandatory fields, filler bubbles or shorten the scene."
FREE_BODY_CONTINUATION = "Continue after the preceding character-authored turn as the listed onstage cast, not as the human user. Return a complete turn using the supplied free-body envelope and speaker aliases."


class FreeBodyError(ValueError):
    code = 'free_body_invalid'


def current_protocol() -> str:
    return get_settings().chat_response_protocol


def speaker_aliases(characters, roles=None) -> dict[str, str | None]:
    # Preserve the caller's stable cast/participant order; names never resolve identity.
    speaking = [c for c in characters
                if str((roles or {}).get(c.id, '')).strip().lower().replace('-', '_').replace(' ', '_') != 'silent']
    if len({c.id for c in speaking}) != len(speaking):
        raise FreeBodyError('Duplicate cast identity')
    return {**{f'C{i}': c.id for i, c in enumerate(speaking, 1)}, 'N': None}


def response_format(protocol: str, aliases=None) -> dict | None:
    if protocol == 'tagged_text':
        return None
    who: dict = {'type': 'string'}
    if aliases is not None:
        who['enum'] = list(aliases)
    return {'type': 'json_object', 'name': 'free_body_replies', 'strict': False, 'schema': {
        'type': 'object', 'additionalProperties': False, 'required': ['replies'],
        'properties': {'replies': {'type': 'array', 'minItems': 1, 'maxItems': MAX_REPLIES,
            'items': {'type': 'object', 'additionalProperties': False, 'required': ['who', 'text'],
                'properties': {'who': who, 'text': {'type': 'string', 'minLength': 1},
                               'emotion': {'type': 'string'}}}}}}}


def output_instructions(protocol, characters, roles=None, *, auxiliary=False):
    names = {c.id: c.name for c in characters}
    aliases = speaker_aliases(characters, roles)
    identity = '\n'.join(f'{alias} = {names.get(cid, "narrator")} [{cid or "narration"}]' for alias, cid in aliases.items())
    envelope = ('Return only <m c="C1" e="optional emotion">body</m> bubbles. No JSON envelope.'
                if protocol == 'tagged_text' else
                'Return only {"replies":[{"who":"C1","text":"body","emotion":"optional"}]}. Only who and text are required.')
    if auxiliary:
        envelope = ('Primary: <m c="C1" e="optional emotion">body</m> bubbles, followed by the command panels suffix. No JSON.'
                    if protocol == 'tagged_text' else
                    'Return {"replies":[{"who":"C1","text":"body","emotion":"optional"}],"panels":[command panels]}. Only who and text are required per reply.')
    return f'{envelope}\nExact speaker alias map (silent roles excluded):\n{identity}\nSafety ceiling: {MAX_REPLIES} bubbles, not a target.'


def _masked(text):
    """Mask literal code/escaped delimiters in linear space/time, preserving offsets."""
    result = list(text)
    ticks = list(re.finditer(r'`+', text))
    next_same = {}
    closing_for = {}
    for index in range(len(ticks) - 1, -1, -1):
        width = ticks[index].end() - ticks[index].start()
        if width in next_same:
            closing_for[index] = next_same[width]
        next_same[width] = index
    index = 0
    while index < len(ticks):
        closing = closing_for.get(index)
        if closing is None:
            index += 1
            continue
        start, end = ticks[index].start(), ticks[closing].end()
        result[start:end] = ' ' * (end - start)
        index = closing + 1
    for escaped in re.finditer(r'\\<', text):
        result[escaped.start():escaped.end()] = '  '
    return ''.join(result)


def parse_body(body: str) -> list[dict[str, str]]:
    if not isinstance(body, str) or len(body) > MAX_RESPONSE_CHARS:
        raise FreeBodyError('Body must be bounded text')
    masked = _masked(body)
    parts = []
    cursor = 0
    active = None
    start = 0
    for token in re.finditer(r'<\s*(/?)\s*(m|a|t)\b[^>]*(?:>|$)', masked, re.I):
        closing, tag = token.group(1), token.group(2).lower()
        if tag == 'm' or not re.fullmatch(r'<\s*/?\s*[at]\s*>', token.group(), re.I):
            raise FreeBodyError('Unexpected or malformed body delimiter')
        if not closing:
            if active:
                raise FreeBodyError('Nested semantic delimiter')
            if token.start() > cursor:
                parts.append({'type': 'dialogue', 'text': body[cursor:token.start()]})
            active, start = tag, token.end()
        else:
            if active != tag:
                raise FreeBodyError('Unmatched semantic delimiter')
            parts.append({'type': 'action' if tag == 'a' else 'thought', 'text': body[start:token.start()]})
            active = None
            cursor = token.end()
    if active:
        raise FreeBodyError('Unclosed semantic delimiter')
    if cursor < len(body):
        parts.append({'type': 'dialogue', 'text': body[cursor:]})
    # Whitespace is retained with text; whitespace-only pieces have no semantic content.
    parts = [part for part in parts if part['text'].strip()]
    if not parts:
        raise FreeBodyError('Empty body')
    return parts


def aggregates(parts):
    return {kind: '\n'.join(part['text'] for part in parts if part['type'] == kind)
            for kind in ('dialogue', 'action', 'thought')}


def metadata_for_reply(reply):
    if reply.response_protocol not in PROTOCOLS:
        return {}
    return {'response_protocol': reply.response_protocol, 'raw_body': reply.raw_body,
            'render_parts': reply.render_parts}


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        canonical = key.lower() if isinstance(key, str) else key
        if canonical in obj:
            raise FreeBodyError('Duplicate JSON key')
        obj[canonical] = value
    return obj


def _tagged_items(text):
    masked = _masked(text)
    tokens = list(re.finditer(r'<\s*(/?)\s*m\b[^>]*(?:>|$)', masked, re.I))
    if not tokens:
        return None
    items, opening, cursor = [], None, 0
    for token in tokens:
        if not token.group(1):
            if opening or text[cursor:token.start()].strip():
                raise FreeBodyError('Nested or ambiguous speaker boundary')
            attrs_text = re.sub(r'^<\s*m\b|>\s*$', '', text[token.start():token.end()], flags=re.I)
            attrs, end = {}, 0
            for attr in re.finditer(r'''\s+([a-z]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|“([^”]*)”|‘([^’]*)’)''', attrs_text, re.I):
                key = attr.group(1).lower()
                if attrs_text[end:attr.start()].strip() or key not in {'c', 'e'} or key in attrs:
                    raise FreeBodyError('Ambiguous speaker attributes')
                attrs[key] = next(value for value in attr.groups()[1:] if value is not None)
                end = attr.end()
            if attrs_text[end:].strip() or 'c' not in attrs or not token.group().endswith('>'):
                raise FreeBodyError('Invalid speaker attributes')
            opening = (token.end(), attrs)
        else:
            if not opening or not re.fullmatch(r'<\s*/\s*m\s*>', token.group(), re.I):
                raise FreeBodyError('Unmatched speaker boundary')
            start, attrs = opening
            items.append({'who': attrs['c'], 'text': text[start:token.start()], 'emotion': attrs.get('e', 'neutral')})
            opening, cursor = None, token.end()
    if opening or text[cursor:].strip():
        raise FreeBodyError('Incomplete or trailing speaker content')
    return items


def parse_response(content: str, *, protocol: str, aliases: dict[str, str | None]) -> list[CharacterReply]:
    if protocol not in PROTOCOLS or not isinstance(content, str) or len(content) > MAX_RESPONSE_CHARS:
        raise FreeBodyError('Invalid protocol or oversized response')
    text = content.strip()
    fenced = re.fullmatch(r'```(?:json|xml|text)?\s*\n([\s\S]*?)\n```', text, re.I)
    if fenced:
        text = fenced.group(1).strip()
    if protocol == 'free_body_json':
        try:
            data = json.loads(text, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError) as exc:
            raise FreeBodyError('Invalid minimal JSON') from exc
        if not isinstance(data, dict) or set(data) != {'replies'}:
            raise FreeBodyError('Expected only replies envelope')
        items = data['replies']
    else:
        items = _tagged_items(text)
        if items is None:
            speaking = [alias for alias, cid in aliases.items() if cid is not None]
            # Conservative plain-text fallback: no markup, JSON, role headers, or fences.
            if len(speaking) != 1 or re.search(r'[<>{}\[\]`]|^\s*[^\n:]{1,80}:', text, re.M):
                raise FreeBodyError('Ambiguous plain response')
            items = [{'who': speaking[0], 'text': text}]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_REPLIES:
        raise FreeBodyError('Invalid reply count')
    replies = []
    for item in items:
        if not isinstance(item, dict) or not {'who', 'text'} <= set(item) or set(item) - {'who', 'text', 'emotion'}:
            raise FreeBodyError('Invalid body envelope')
        if not isinstance(item['who'], str) or item['who'].upper() not in aliases:
            raise FreeBodyError('Unknown speaker alias')
        cid = aliases[item['who'].upper()]
        parts = parse_body(item['text'])
        values = aggregates(parts)
        if cid is None and values['thought']:
            raise FreeBodyError('Narrator cannot own private character thoughts')
        emotion = item.get('emotion', 'neutral')
        if not isinstance(emotion, str) or len(emotion) > 120:
            raise FreeBodyError('Invalid emotion')
        replies.append(CharacterReply(character_id=cid, reply_type='character' if cid else 'storytelling',
            text=values['dialogue'], action=values['action'] or None, thought=values['thought'] or None,
            emotion=emotion or 'neutral', response_protocol=protocol, raw_body=item['text'], render_parts=parts))
    if not any(reply.reply_type == 'character' for reply in replies):
        raise FreeBodyError('At least one onstage character part is required')
    return replies
