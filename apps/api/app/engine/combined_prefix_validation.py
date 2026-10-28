"""Reject-only admission for combined output-limit recovery.

Validate the ORIGINAL envelope, then return offsets as evidence, never repaired
prose or panel data. JSON has one bounded cursor grammar. Tagged primary prose is
NOT XML: only reserved structural tokens have syntax, and code/escaped delimiters
are literal text. XML validation is confined to the auxiliary grammar, with a
lossless text-token encoding for its explicit literals (never syntax masking).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, NoReturn, cast
from xml.etree import ElementTree as ET

from app.engine import free_body_protocol as body

WS = ' \t\r\n'
MAX_DEPTH = 32


@dataclass(frozen=True)
class AuxiliaryCutProof:
    payload: str  # Exact original payload, excluding only valid outer framing.
    primary_end: int
    auxiliary_start: int


class _EOF(Exception):
    """EOF reached in a grammar state that can be completed by appending."""


def _invalid(reason) -> NoReturn:
    raise body.FreeBodyError(reason)


class _JSONPrefix:
    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.auxiliary = False

    def peek(self):
        while self.pos < len(self.text) and self.text[self.pos] in WS:
            self.pos += 1
        if self.pos == len(self.text):
            raise _EOF
        return self.text[self.pos]

    def expect(self, char):
        if self.peek() != char:
            _invalid('Invalid combined JSON syntax')
        self.pos += 1

    def string(self):
        self.expect('"')
        start = self.pos - 1
        while self.pos < len(self.text):
            char = self.text[self.pos]
            self.pos += 1
            if char == '"':
                return json.loads(self.text[start:self.pos])
            if ord(char) < 32:
                _invalid('Illegal JSON string control')
            if char == '\\':
                if self.pos == len(self.text):
                    raise _EOF
                escape = self.text[self.pos]
                self.pos += 1
                if escape == 'u':
                    for _ in range(4):
                        if self.pos == len(self.text):
                            raise _EOF
                        if self.text[self.pos] not in '0123456789abcdefABCDEF':
                            _invalid('Invalid JSON unicode escape')
                        self.pos += 1
                elif escape not in '"\\/bfnrt':
                    _invalid('Invalid JSON escape')
        raise _EOF

    def value(self, depth=0):
        if depth > MAX_DEPTH:
            _invalid('Combined JSON nesting limit')
        char = self.peek()
        if char == '"':
            self.string()
        elif char in '[{':
            self.pos += 1
            closing = ']' if char == '[' else '}'
            keys = set()
            if self.peek() == closing:
                self.pos += 1
                return
            while True:
                if char == '{':
                    key = self.string().lower()
                    if key in keys:
                        _invalid('Duplicate JSON key')
                    if self.auxiliary and key in {'replies', 'who'}:
                        _invalid('Primary field after auxiliary envelope')
                    keys.add(key)
                    self.expect(':')
                self.value(depth + 1)
                delimiter = self.peek()
                self.pos += 1
                if delimiter == closing:
                    return
                if delimiter != ',':
                    _invalid('Invalid JSON delimiter')
        elif char in 'tfn':
            for expected in {'t': 'true', 'f': 'false', 'n': 'null'}[char]:
                if self.pos == len(self.text):
                    raise _EOF
                if self.text[self.pos] != expected:
                    _invalid('Invalid JSON keyword')
                self.pos += 1
        elif char in '-0123456789':
            # Delimit only on JSON whitespace, never Unicode/broad regex \s.
            start = self.pos
            while self.pos < len(self.text) and self.text[self.pos] not in WS + ',]}':
                self.pos += 1
            token = self.text[start:self.pos]
            if re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?', token):
                return
            if self.pos == len(self.text) and re.fullmatch(
                    r'-|(?:-?(?:0|[1-9][0-9]*))(?:\.|(?:\.[0-9]+)?[eE][+-]?)', token):
                raise _EOF
            _invalid('Invalid JSON number')
        else:
            _invalid('Invalid JSON value')

    def prove(self) -> AuxiliaryCutProof:
        # EOF is NOT recoverable until a complete replies value, exact envelope
        # separator/key/colon and the actual auxiliary array start were consumed.
        try:
            self.expect('{')
            if self.string().lower() != 'replies':
                _invalid('Expected replies first')
            self.expect(':')
            if self.peek() != '[':
                _invalid('Expected primary array')
            self.value()
            primary_end = self.pos
            self.expect(',')
            if self.string().lower() != 'panels':
                _invalid('Expected panels last')
            self.expect(':')
            if self.peek() != '[':
                _invalid('Expected auxiliary array start')
            auxiliary_start = self.pos
        except _EOF as exc:
            raise body.FreeBodyError('No complete primary and auxiliary start') from exc
        self.auxiliary = True
        try:
            self.value()
        except _EOF:
            return AuxiliaryCutProof(self.text, primary_end, auxiliary_start)
        # A closed auxiliary value is never truncation evidence, even if the
        # outer } is missing or later bytes are malformed/ambiguous/duplicated.
        _invalid('Auxiliary array complete at output limit')


def _literal_ends(text):
    """Index same-width backtick pairs without rewriting any original byte.

    A caller may enter a literal ONLY from a text state, never inside a tag or
    attribute. Pairing matches the free-body code convention in linear time.
    """
    ticks = list(re.finditer(r'`+', text))
    next_same, ends = {}, {}
    for token in reversed(ticks):
        width = token.end() - token.start()
        if width in next_same:
            ends[token.start()] = next_same[width]
        next_same[width] = token.end()
    return ends


def _tag_end(text, start):
    quote = None
    for pos in range(start + 1, len(text)):
        char = text[pos]
        if quote:
            if char == quote:
                quote = None
        elif char in '\"\'':
            quote = char
        elif char == '>':
            return pos + 1
    return len(text)


def _reserved_tag(text, pos, names='mat'):
    """Recognize a reserved name in one forward scan; validate raw syntax later.

    Broad whitespace recognition intentionally finds malformed reserved tokens
    too. No bytes are normalized or discarded: the original token still goes
    through the exact primary/XML grammar. Avoid adjacent whitespace repeats
    around an optional slash, which backtrack quadratically on near misses.
    """
    if pos >= len(text) or text[pos] != '<':
        return None
    cursor = pos + 1
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    closing = cursor < len(text) and text[cursor] == '/'
    if closing:
        cursor += 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
    if cursor == len(text) or text[cursor].lower() not in names:
        return None
    name = text[cursor].lower()
    cursor += 1
    if cursor < len(text) and (text[cursor].isalnum() or text[cursor] == '_'):
        return None
    return closing, name


def _tagged_primary_end(text, literals):
    pos, in_bubble, count = 0, False, 0
    opening = re.compile(
        r'''<[ \t\r\n]*m(?:[ \t\r\n]+[ce][ \t\r\n]*=[ \t\r\n]*(?:"[^"<>]*"|'[^'<>]*'|“[^”<>]*”|‘[^’<>]*’))+[ \t\r\n]*>''', re.I)
    while pos < len(text):
        if not in_bubble:
            if text[pos] in WS:
                pos += 1
                continue
            if count and text.startswith('<panels>', pos):
                return pos
        else:
            if pos in literals:
                pos = literals[pos]
                continue
            if text.startswith('\\<', pos):
                pos += 2
                continue
        token = _reserved_tag(text, pos)
        if token:
            end = _tag_end(text, pos)
            raw = text[pos:end]
            close, name = token
            if name == 'm':
                if close:
                    if not in_bubble or not re.fullmatch(r'<[ \t\r\n]*/[ \t\r\n]*m[ \t\r\n]*>', raw, re.I):
                        _invalid('Invalid primary close')
                    in_bubble = False
                    count += 1
                else:
                    if in_bubble or not opening.fullmatch(raw):
                        _invalid('Invalid primary opening')
                    in_bubble = True
            elif not in_bubble or not re.fullmatch(r'<[ \t\r\n]*(?:/[ \t\r\n]*)?[at][ \t\r\n]*>', raw, re.I):
                _invalid('Invalid semantic delimiter')
            pos = end
        elif in_bubble:
            # Arbitrary human prose, math, ampersands and unknown tags are NOT
            # XML. Only the product's reserved delimiters have grammar here.
            pos += 1
        else:
            _invalid('Invalid primary envelope syntax')
    _invalid('No complete primary and auxiliary start')


def _auxiliary_xml_view(text, start, literals):
    """Encode only explicit literal TEXT tokens for the XML proof adapter.

    No syntax is removed. Attribute/tag bytes go to XML verbatim; illegal raw
    characters have already failed the whole-input gate. This validation view
    and any append-only witness never reach the primary or recovered output.
    """
    pieces, pos, copied = [], start, start
    while pos < len(text):
        end = literals.get(pos)
        if end is None and text.startswith('\\<', pos):
            end = pos + 2
        if end is not None:
            pieces.append(text[copied:pos])
            pieces.append(text[pos:end].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
            pos = copied = end
        elif text[pos] == '<':
            if _reserved_tag(text, pos, 'm'):
                _invalid('Primary bubble after auxiliary envelope')
            if text.startswith(('<!', '<?'), pos):
                _invalid('Unsafe auxiliary XML')
            pos = _tag_end(text, pos)  # No code/escape processing in syntax.
        else:
            pos += 1
    pieces.append(text[copied:])
    return ''.join(pieces)


def _require_incomplete_xml(text):
    def feed(value):
        parser = ET.XMLPullParser(events=('start', 'end'))
        stack = []
        # Drain events in bounded chunks; enforce depth before building a large
        # element tree, including for malicious deeply nested prefixes.
        for pos in range(0, len(value), 1024):
            parser.feed(value[pos:pos + 1024])
            events = cast(Iterable[tuple[str, ET.Element]], parser.read_events())
            for event, element in events:
                if event == 'start':
                    stack.append(element.tag)
                    if len(stack) > MAX_DEPTH:
                        _invalid('Auxiliary XML nesting limit')
                else:
                    stack.pop()
                    element.clear()
        return parser, stack

    try:
        parser, stack = feed(text)
        try:
            parser.close()
        except ET.ParseError as exc:
            if exc.code == 3 and stack:  # EOF after complete tokens, open root.
                return
            if exc.code != 5:
                raise
        else:
            _invalid('Auxiliary wrapper complete at output limit')
    except (ET.ParseError, ValueError, RecursionError) as exc:
        raise body.FreeBodyError('Invalid auxiliary XML prefix') from exc

    # Unclosed-token errors alone are not proof (e.g. duplicate attributes or a
    # wrong partial close). Require an append-only well-formed witness. Failure
    # to prove a rare cut is conservative rejection, never token replacement.
    endings = ['>', '/>', '\">', "'>", '=\"\">', "=''>", '\"\">', "''>", 'panel/>']
    closing = re.search(r'</([A-Za-z][A-Za-z0-9]*)?$', text)
    if closing and stack and stack[-1].startswith(closing.group(1) or ''):
        endings.append(stack[-1][len(closing.group(1) or ''):] + '>')
    entity = re.search(r'&([A-Za-z]*|#[0-9]*|#x[0-9a-fA-F]*)$', text)
    if entity:
        prefix = entity.group(1)
        entities = [name[len(prefix):] + ';' for name in ('amp', 'lt', 'gt', 'quot', 'apos') if name.startswith(prefix)]
        if prefix.startswith('#x'):
            entities += ['20;']
        elif prefix.startswith('#'):
            entities += ['32;']
        endings += [end + quote for end in entities for quote in ('', '\">', "'>")]
    for ending in endings:
        try:
            candidate, open_tags = feed(text + ending)
            candidate.feed(''.join(f'</{tag}>' for tag in reversed(open_tags)))
            candidate.close()
        except (ET.ParseError, ValueError, RecursionError):
            continue
        return
    _invalid('Unproved auxiliary XML token cut')


def validate_auxiliary_cut(raw: str, protocol: str) -> AuxiliaryCutProof:
    """The sole LENGTH entry gate: original whole syntax before any splitting.

    Bounds, explicit framing, complete primary syntax, authentic auxiliary start
    and append-completable *incomplete auxiliary* are required. Speaker identity,
    semantic order, repetition and control-token policy still run downstream.
    """
    if not isinstance(raw, str) or len(raw) > body.MAX_RESPONSE_CHARS:
        _invalid('Oversized combined response')
    fence = re.fullmatch(r'[ \t\r\n]*```(?:json|xml|text)?[ \t]*\r?\n([\s\S]*?)\n```[ \t\r\n]*', raw, re.I)
    text = fence.group(1) if fence else raw
    if protocol == 'free_body_json':
        return _JSONPrefix(text).prove()
    if protocol != 'tagged_text':
        _invalid('Invalid combined protocol')
    # Validate ALL original characters before recognizing literal domains. XML's
    # character repertoire applies to tagged syntax and literal text alike.
    if any(not (c in '\t\r\n' or 0x20 <= ord(c) <= 0xD7FF or
                0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in text):
        _invalid('Illegal tagged character')
    literals = _literal_ends(text)
    end = _tagged_primary_end(text, literals)
    _require_incomplete_xml(_auxiliary_xml_view(text, end, literals))
    # Only now may canonical primary parsing mask literal text: the complete
    # original syntax and every character have already passed the raw gate.
    items = body._tagged_items(text[:end])
    if not items:
        _invalid('Missing complete primary bubbles')
    for item in items:
        body.parse_body(item['text'])
    return AuxiliaryCutProof(text, end, end)
