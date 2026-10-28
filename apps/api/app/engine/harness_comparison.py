"""Immutable, deployment-opt-in prompt representation choices (no I/O).

Token counts are conservative application heuristics, not provider billing.
One message per historical row preserves interleaving and bubble boundaries;
roles represent human input versus the whole cast, never fictional identities.
"""
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from app.engine.prompt_harness import approx_tokens, approx_tokens_for_char_count


@dataclass(frozen=True)
class HistoryAccounting:
    """Marginal history cost in an otherwise frozen serialized system envelope.

    Transcript rounding belongs to the whole system message, not each row or
    section. Store only character counts so post-commit projections can reuse
    the same envelope without persisting any additional prompt text. A new
    generation evaluation always rebuilds this after a successful Arc fold.
    """

    base_characters: int = 0
    history_header_characters: int = 0

    @classmethod
    def from_sections(cls, sections, *, included_keys=None):
        included = [section for section in sections
                    if (included_keys is None or section.key in included_keys)
                    and section.key != 'recent_messages'
                    and (section.content or '').strip()]
        base = len('\n\n'.join(f'[{section.title}]\n{section.content}' for section in included))
        history = next((section for section in sections if section.key == 'recent_messages'), None)
        header = len(f'[{history.title}]\n') + (2 if base else 0) if history else 0
        return cls(base, header)

    @classmethod
    def from_harness(cls, harness):
        return cls.from_sections(harness.sections,
                                 included_keys={entry.key for entry in harness.ledger if entry.included})

    @property
    def base_tokens(self) -> int:
        return approx_tokens_for_char_count(self.base_characters)

    def tokens(self, public_rows: Sequence[str], *, history_serialization: str) -> int:
        if history_serialization == 'native':
            return rendered_input_tokens([{'content': text} for text in public_rows],
                                         history_serialization='native')
        if not public_rows:
            return 0
        characters = sum(map(len, public_rows)) + len(public_rows) - 1
        return (approx_tokens_for_char_count(self.base_characters + self.history_header_characters + characters)
                - approx_tokens_for_char_count(self.base_characters))

ROLE_OVERHEAD_TOKENS = 4


@dataclass(frozen=True)
class ComparisonOptions:
    history_serialization: Literal['transcript', 'native'] = 'transcript'
    context_policy: Literal['compact', 'expanded', 'full_history'] = 'compact'
    input_budget_tokens: int | None = None

    @classmethod
    def from_settings(cls, settings):
        return cls(getattr(settings, 'chat_history_serialization', 'transcript'),
                   getattr(settings, 'chat_context_policy', 'compact'),
                   getattr(settings, 'chat_context_input_budget_tokens', None))


@dataclass(frozen=True)
class PriorMessage:
    role: Literal['user', 'assistant']
    content: str

    def as_dict(self) -> dict:
        return {'role': self.role, 'content': self.content}


def rendered_input_tokens(messages: Sequence[Mapping], *, history_serialization: str = 'transcript') -> int:
    overhead = ROLE_OVERHEAD_TOKENS if history_serialization == 'native' else 0
    return sum(approx_tokens(str(message.get('content') or '')) + overhead for message in messages)


def source_role_overhead(speaker_type: str, serialization: str) -> int:
    # System + current source + assistant-source user continuation.
    return (3 if speaker_type == 'character' else 2) * ROLE_OVERHEAD_TOKENS if serialization == 'native' else 0


def generation_input_estimate(harness, source_text: str, speaker_type: str, control_tokens: int) -> int:
    """Complete initial input plus unchanged maximum retry/schema control reserve.

    The control reserve includes continuation text; do not count it twice.
    Native message framing is additive and independently visible.
    """
    return (harness.used_tokens + approx_tokens(source_text) + control_tokens
            + rendered_input_tokens([m.as_dict() for m in harness.prior_messages],
                                    history_serialization=harness.history_serialization)
            + source_role_overhead(speaker_type, harness.history_serialization))
