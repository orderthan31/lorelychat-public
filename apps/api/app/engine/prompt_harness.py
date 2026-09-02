from __future__ import annotations

import math
from dataclasses import dataclass


def approx_tokens(text: str) -> int:
    return int(math.ceil(len(text or "") / 2.2))


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


def _clamp_to_budget(text: str, budget_tokens: int) -> str:
    value = text or ""
    max_chars = max(0, int(max(0, budget_tokens) * 2.2))
    if not max_chars or len(value) <= max_chars:
        return value
    marker = "\n…[section compacted to prompt budget]…\n"
    available = max(0, max_chars - len(marker))
    head_chars = int(available * 0.65)
    tail_chars = available - head_chars
    return value[:head_chars].rstrip() + marker + value[-tail_chars:].lstrip()


def compile_prompt_harness(*, sections: list[PromptSection], total_budget_tokens: int) -> PromptHarness:
    """Compile prompt sections with an auditable context-budget ledger.

    Required sections are compacted to their own per-section budget and kept.
    Optional sections are included only when their compacted form fits the
    remaining total budget. The ledger is metadata for preview/diagnostics and
    is intentionally not injected into the prompt text.
    """
    remaining = max(0, total_budget_tokens)
    rendered_sections: list[str] = []
    ledger: list[PromptLedgerEntry] = []

    for section in sections:
        raw_content = section.content or ""
        raw_tokens = approx_tokens(raw_content)
        compacted = _clamp_to_budget(raw_content, section.budget_tokens)
        include = bool(compacted.strip())
        excluded_reason = ""

        rendered_section = f"[{section.title}]\n{compacted}"
        current_text = "\n\n".join(rendered_sections)
        candidate_text = "\n\n".join([*rendered_sections, rendered_section])
        rendered_tokens = max(
            0,
            approx_tokens(candidate_text) - approx_tokens(current_text),
        )

        if include and not section.required and rendered_tokens > remaining:
            include = False
            excluded_reason = "total_budget_exhausted"
        # Required sections survive total-budget pressure after their own
        # section-level compaction; optional context never displaces them.

        if not include and not excluded_reason:
            excluded_reason = "empty"

        if include:
            rendered_sections.append(rendered_section)
            remaining = max(0, remaining - rendered_tokens)

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
