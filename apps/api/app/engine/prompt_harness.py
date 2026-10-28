from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.engine.harness_comparison import PriorMessage


def approx_tokens_for_char_count(char_count: int) -> int:
    return int(math.ceil(char_count / 2.2))


def approx_tokens(text: str) -> int:
    return approx_tokens_for_char_count(len(text or ""))


@dataclass(frozen=True)
class PromptSection:
    key: str
    title: str
    content: str
    source: str
    included_reason: str
    budget_tokens: int
    required: bool = False


@dataclass(frozen=True)
class PromptLedgerEntry:
    key: str
    title: str
    source: str
    included_reason: str
    included: bool
    approx_tokens: int
    budget_tokens: int
    used_tokens: int
    excluded_reason: str = ""


@dataclass(frozen=True)
class PromptHarness:
    compiled_text: str
    ledger: list[PromptLedgerEntry]
    total_budget_tokens: int
    used_tokens: int
    sections: list[PromptSection]
    prior_messages: tuple[PriorMessage, ...] = ()
    history_message_ids: tuple[str, ...] = ()
    history_sha256: str = ""
    history_serialization: str = "transcript"
    context_policy: str = "compact"

    def comparison_metadata(self) -> dict:
        if self.history_serialization == "transcript" and self.context_policy == "compact":
            return {}
        return {"history_serialization": self.history_serialization,
                "context_policy": self.context_policy,
                "history_message_ids": list(self.history_message_ids),
                "history_sha256": self.history_sha256}


def compile_prompt_harness(*, sections: list[PromptSection], total_budget_tokens: int) -> PromptHarness:
    """Compile prompt sections with an auditable context-budget ledger.

    Required sections are retained verbatim, even under pressure; used_tokens
    exceeding total_budget_tokens exposes overflow to the runtime guard/preview.
    Optional sections fit as complete units or are explicitly excluded. Section
    budgets remain diagnostic targets, never permission to cut authored facts.
    """
    rendered_sections: list[str] = []
    ledger: list[PromptLedgerEntry] = []

    for index, section in enumerate(sections):
        raw_content = section.content or ""
        raw_tokens = approx_tokens(raw_content)
        include = bool(raw_content.strip())
        excluded_reason = ""

        rendered_section = f"[{section.title}]\n{raw_content}"
        current_text = "\n\n".join(rendered_sections)
        candidate_text = "\n\n".join([*rendered_sections, rendered_section])
        rendered_tokens = max(
            0,
            approx_tokens(candidate_text) - approx_tokens(current_text),
        )

        # Reserve all later required units before admitting optional material.
        required_tail = [
            f"[{item.title}]\n{item.content}"
            for item in sections[index + 1:]
            if item.required and (item.content or "").strip()
        ]
        projected_tokens = approx_tokens("\n\n".join([*rendered_sections, rendered_section, *required_tail]))
        if include and not section.required and projected_tokens > total_budget_tokens:
            include = False
            excluded_reason = "total_budget_exhausted"

        if not include and not excluded_reason:
            excluded_reason = "empty"

        if include:
            rendered_sections.append(rendered_section)

        ledger.append(PromptLedgerEntry(
            key=section.key,
            title=section.title,
            source=section.source,
            included_reason=section.included_reason,
            included=include,
            approx_tokens=raw_tokens,
            budget_tokens=section.budget_tokens,
            used_tokens=rendered_tokens if include else 0,
            excluded_reason=excluded_reason if not include else "",
        ))

    used_tokens = approx_tokens("\n\n".join(rendered_sections))
    return PromptHarness(
        compiled_text="\n\n".join(rendered_sections),
        ledger=ledger,
        total_budget_tokens=total_budget_tokens,
        used_tokens=used_tokens,
        sections=sections,
    )
