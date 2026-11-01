"""Bounded, reject-only proof for minimal-JSON output-limit reply prefixes.

Only offsets from the original root.replies array are projected. No partial
member is decoded or repaired. The caller must run the ordinary identity/body
and quality guards before publishing this proof's data-only diagnostic marker.
The existing auxiliary-cut admission contract is deliberately unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from app.engine import free_body_protocol as body
from app.engine.combined_prefix_validation import _EOF, _JSONPrefix, WS


@dataclass(frozen=True)
class ReplyPrefixProof:
    primary: str
    retained_count: int
    discarded_tail_chars: int
    complete_envelope: bool

    def metadata(self, raw: str, finish_reason: str) -> dict:
        return {
            'original_finish_reason': finish_reason,
            'recovery_kind': 'complete_reply_prefix',
            'retained_count': self.retained_count,
            'discarded_tail_chars': self.discarded_tail_chars,
            'raw_sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
            'raw_char_count': len(raw),
        }


def prove_reply_prefix(raw: str, *, allow_panels: bool = False) -> ReplyPrefixProof:
    """Accept complete envelopes or an EOF-incomplete final reply OBJECT only.

    Strict original grammar is checked before strip, masking, or JSON decoding.
    The shared cursor bounds nesting and rejects duplicate keys/illegal controls.
    Only the canonical replies-first envelope is admitted; alternate arrangements
    and cuts outside the final object are conservatively left unrecovered.
    Linear scans, bounded by MAX_RESPONSE_CHARS, retain every complete member.
    """
    if not isinstance(raw, str) or len(raw) > body.MAX_RESPONSE_CHARS:
        raise body.FreeBodyError('Oversized prefix response')
    cursor = _JSONPrefix(raw)
    count, retained_end = 0, 0
    try:
        cursor.expect('{')
        if cursor.string().lower() != 'replies':
            raise body.FreeBodyError('Expected original root replies first')
        cursor.expect(':')
        cursor.expect('[')
        if cursor.peek() == ']':
            raise body.FreeBodyError('Empty original reply array')
        while True:
            if cursor.peek() != '{':
                raise body.FreeBodyError('Reply member must be an object')
            try:
                cursor.value(depth=2)
            except _EOF:
                if not count:
                    raise body.FreeBodyError('No complete reply prefix')
                return ReplyPrefixProof(raw[:retained_end] + ']}', count,
                                        len(raw) - retained_end, False)
            count += 1
            if count > body.MAX_REPLIES:
                raise body.FreeBodyError('Too many complete replies')
            retained_end = cursor.pos
            delimiter = cursor.peek()
            cursor.pos += 1
            if delimiter == ']':
                break
            if delimiter != ',':
                raise body.FreeBodyError('Invalid reply array delimiter')
        primary_end = cursor.pos
        delimiter = cursor.peek()
        if delimiter == ',':
            if not allow_panels:
                raise body.FreeBodyError('Unexpected auxiliary data')
            cursor.pos += 1
            if cursor.string().lower() != 'panels':
                raise body.FreeBodyError('Expected panels last')
            cursor.expect(':')
            if cursor.peek() != '[':
                raise body.FreeBodyError('Expected panels array')
            cursor.auxiliary = True
            cursor.value(depth=1)
        cursor.expect('}')
        while cursor.pos < len(raw) and raw[cursor.pos] in WS:
            cursor.pos += 1
        if cursor.pos != len(raw):
            raise body.FreeBodyError('Trailing original JSON content')
        return ReplyPrefixProof(raw[:primary_end] + '}', count, 0, True)
    except _EOF as exc:
        raise body.FreeBodyError('EOF outside a final reply object') from exc
