from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import hashlib
import json
import logging
import random
import re
import threading
import time
from uuid import uuid4

import httpx
from pydantic import BaseModel
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.db.models import LLMUsageEvent
from app.db.session import engine
from app.engine.provider_capabilities import capabilities, safe_count, safe_model_version, thinking_off_parameters
from app.engine.llm_error_types import (
    LLMTerminalError, LLMPolicyBlockedError, LLMAuthenticationError,
    LLMRequestError, LLMConfigurationError, LLMCapabilityError,
    LLMResponseEvidenceError, LLMTaskDeadlineError,
)
from app.engine.llm_transport_deadline import TASK_DEADLINE, deadline_seconds, remaining_seconds, within_deadline

logger = logging.getLogger(__name__)

_GEMINI_FINISH_REASONS = {
    "STOP", "MAX_TOKENS", "SAFETY", "RECITATION", "OTHER", "BLOCKLIST",
    "PROHIBITED_CONTENT", "SPII", "MALFORMED_FUNCTION_CALL",
    "UNEXPECTED_TOOL_CALL", "MODEL_ARMOR", "IMAGE_SAFETY",
}
_GEMINI_BLOCK_REASONS = {"SAFETY", "OTHER", "BLOCKLIST", "PROHIBITED_CONTENT", "IMAGE_SAFETY"}


def _safe_gemini_reason(value: object, allowed: set[str]) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) and value in allowed else "UNKNOWN"


def _gemini_empty_response_diagnostics(data: dict) -> dict[str, int | str | bool | None]:
    """Only fixed-shape, non-content fields; never log provider text or prompts."""
    candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
    candidates = candidates or []
    candidate = (candidates[0] if candidates and isinstance(candidates[0], dict) else {}) or {}
    content = (candidate.get("content") if isinstance(candidate.get("content"), dict) else {}) or {}
    parts = (content.get("parts") if isinstance(content.get("parts"), list) else []) or []
    parts = [part for part in parts if isinstance(part, dict)]
    feedback = (data.get("promptFeedback") if isinstance(data.get("promptFeedback"), dict) else {}) or {}
    usage = (data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}) or {}
    ratings = (candidate.get("safetyRatings") if isinstance(candidate.get("safetyRatings"), list) else []) or []
    prompt_ratings = (feedback.get("safetyRatings") if isinstance(feedback.get("safetyRatings"), list) else []) or []

    def safe_count(value: object) -> int | None:
        return value if type(value) is int and value >= 0 else None

    return {
        "candidate_count": len(candidates),
        "candidate_finish_reason": _safe_gemini_reason(candidate.get("finishReason"), _GEMINI_FINISH_REASONS),
        "prompt_block_reason": _safe_gemini_reason(feedback.get("blockReason"), _GEMINI_BLOCK_REASONS),
        "has_prompt_feedback": bool(feedback),
        "part_count": len(parts),
        "text_part_count": sum(isinstance(part.get("text"), str) for part in parts),
        "thought_part_count": sum(part.get("thought") is True for part in parts),
        "inline_data_part_count": sum("inlineData" in part for part in parts),
        "function_call_part_count": sum("functionCall" in part for part in parts),
        "candidate_blocked_rating_count": sum(isinstance(r, dict) and r.get("blocked") is True for r in ratings),
        "prompt_blocked_rating_count": sum(isinstance(r, dict) and r.get("blocked") is True for r in prompt_ratings),
        "thoughts_token_count": safe_count(usage.get("thoughtsTokenCount")),
        "cached_content_token_count": safe_count(usage.get("cachedContentTokenCount")),
    }


class LLMUnavailableError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        transport_attempts: int = 1,
        http_status: int | None = None,
        error_type: str | None = None,
        provider_warning_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.transport_attempts = max(0, int(transport_attempts))
        self.http_status = http_status
        self.error_type = error_type or type(self).__name__
        self.provider_warning_count = provider_warning_count


class CircuitOpenError(LLMUnavailableError):
    pass


class LLMAttemptBudgetError(LLMTerminalError):
    """The task's network budget is spent, not a provider outage."""
    code = 'network_budget_exhausted'


@dataclass
class LLMAttemptBudget:
    limit: int = 3
    used: int = 0
    deadline: float | None = None
    started: float = field(default_factory=time.monotonic)


_TASK_BUDGET: ContextVar[LLMAttemptBudget | None] = ContextVar("llm_task_budget", default=None)


def bounded_llm_task(function):
    """Isolate concurrent tasks while sharing transport allowance across repair passes."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        if _TASK_BUDGET.get() is not None:
            return await function(*args, **kwargs)
        settings = getattr(args[0], 'settings', None) if args else None
        deadline = time.monotonic() + deadline_seconds(settings or get_settings())
        budget = LLMAttemptBudget()
        budget.deadline = deadline
        token = _TASK_BUDGET.set(budget)
        deadline_token = TASK_DEADLINE.set(deadline)
        requested_token = _TASK_REQUESTED_ROUTE.set(None)
        try:
            return await within_deadline(function(*args, **kwargs), deadline=deadline)
        except LLMTaskDeadlineError as exc:
            exc.transport_attempts = budget.used
            raise
        finally:
            _TASK_REQUESTED_ROUTE.reset(requested_token)
            TASK_DEADLINE.reset(deadline_token)
            _TASK_BUDGET.reset(token)
    return wrapped


def task_attempt_metadata() -> dict[str, int]:
    budget = _TASK_BUDGET.get()
    return {"network_attempts": budget.used, "network_attempt_limit": budget.limit} if budget else {}


_CIRCUIT_STATES: dict[str, dict] = {}
_CIRCUIT_LOCK = threading.Lock()
_TELEMETRY_WRITE_FAILURES = 0
_ATTEMPT_TRACE: ContextVar[list | None] = ContextVar('llm_attempt_trace', default=None)
_TASK_REQUESTED_ROUTE: ContextVar[dict | None] = ContextVar('llm_requested_route', default=None)


def telemetry_write_failure_count():
    return _TELEMETRY_WRITE_FAILURES


def _telemetry_write_failed():
    global _TELEMETRY_WRITE_FAILURES
    with _CIRCUIT_LOCK:
        _TELEMETRY_WRITE_FAILURES += 1
    logger.warning('LLM telemetry write failed')


class LLMResponse(BaseModel):
    content: str
    provider: str | None = None
    model: str | None = None
    requested_model: str | None = None
    reported_model_version: str | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    transport_diagnostics: dict[str, object] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None
    generation_attempts: int = 1
    estimated: bool = False
    usage_event_id: str | None = None
    provider_diagnostics: dict[str, int | str | bool | None] | None = None
    budget_diagnostics: dict[str, int | None] | None = None


async def chat_with_optional_conversation_id(
    client,
    messages: list[dict],
    *,
    response_format: dict | None = None,
    conversation_id: str | None = None,
) -> LLMResponse:
    try:
        return await client.chat(messages, response_format=response_format, conversation_id=conversation_id)
    except TypeError as exc:
        if "conversation_id" not in str(exc):
            raise
        return await client.chat(messages, response_format=response_format)


def _mock_visible_user_text(value: str) -> str:
    text = str(value or "")
    marker = "[User dialogue]"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = re.split(r"\n\n\[(?:System retry instruction|System instruction|Character action[^\]]*)\]", text, maxsplit=1)[0]
    return text.strip()


class LLMClient:
    def __init__(self, settings: Settings | None = None, *, profile: str = "chat", purpose: str | None = None, overrides: dict | None = None):
        self.settings = settings or get_settings()
        self.profile = profile
        self.purpose = purpose or ("compression" if profile == "compression" else "chat_generation")
        self.overrides = overrides or {}

    @classmethod
    def reset_circuit_breakers(cls) -> None:
        with _CIRCUIT_LOCK:
            _CIRCUIT_STATES.clear()

    def circuit_breaker_enabled(self) -> bool:
        return bool(self.overrides.get("circuit_breaker_enabled", True))

    def circuit_failure_threshold(self) -> int:
        return max(1, min(20, int(self.overrides.get("circuit_failure_threshold") or 2)))

    def circuit_cooldown_seconds(self) -> float:
        return max(1.0, min(600.0, float(self.overrides.get("circuit_cooldown_seconds") or 30.0)))

    def circuit_key(self) -> str:
        # Opaque, content-free route revision. Never include the credential or ref.
        try:
            endpoint = self.overrides.get('base_url') or self.base_url()
        except LLMTerminalError:
            endpoint = None
        identity = [self.provider(), self.overrides.get('provider_account_id'),
                    self.overrides.get('model_option_key'), self.model(),
                    endpoint,
                    self.overrides.get('provider_config_revision')]
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def ensure_circuit_available(self):
        if not self.circuit_breaker_enabled():
            return None
        key = self.circuit_key()
        with _CIRCUIT_LOCK:
            state = _CIRCUIT_STATES.setdefault(key, {'failures': 0, 'open_until': 0.0, 'epoch': uuid4().hex, 'probe': None})
            if state['open_until'] > time.monotonic() or state.get('probe'):
                raise CircuitOpenError('LLM circuit breaker is open', transport_attempts=0)
            probe = uuid4().hex if state['open_until'] else None
            state['probe'] = probe
            return (key, state['epoch'], probe, state['failures'])

    def record_circuit_failure(self, token=None) -> None:
        if not self.circuit_breaker_enabled():
            return
        key = self.circuit_key()
        with _CIRCUIT_LOCK:
            state = _CIRCUIT_STATES.setdefault(key, {'failures': 0, 'open_until': 0.0, 'epoch': uuid4().hex, 'probe': None})
            if token is not None and (state['epoch'] != token[1] or state.get('probe') != token[2]):
                return
            state['failures'] += 1
            if state['failures'] >= self.circuit_failure_threshold():
                state.update(open_until=time.monotonic() + self.circuit_cooldown_seconds(), epoch=uuid4().hex, probe=None)

    def record_circuit_success(self, token=None) -> None:
        if not self.circuit_breaker_enabled():
            return
        with _CIRCUIT_LOCK:
            key = self.circuit_key()
            state = _CIRCUIT_STATES.get(key)
            if state and token is not None and (state['epoch'] != token[1] or state.get('probe') != token[2] or state['failures'] != token[3] or (state['open_until'] and token[2] is None)):
                return
            _CIRCUIT_STATES.pop(key, None)

    def release_circuit_probe(self, token):
        if token is None or token[2] is None:
            return
        with _CIRCUIT_LOCK:
            state = _CIRCUIT_STATES.get(token[0])
            if state and state.get('probe') == token[2]:
                state.update(probe=None, open_until=time.monotonic() + self.circuit_cooldown_seconds())

    def normalize_response_format(self, response_format: dict | None) -> dict | None:
        if response_format and response_format.get("type") == "json_object":
            return {"type": "json_object"}
        return response_format

    def strict_structured_output(self) -> bool:
        return self.provider() == "xai"

    def transport_capabilities(self, *, model: str | None = None):
        gemini = self.provider() == "gemini" or (self.settings.llm_transport == "litellm" and self.provider() in {"google", "vertex_ai"})
        return capabilities(
            model=model or self.model(),
            endpoint="https://generativelanguage.googleapis.com/v1beta" if gemini else self.base_url(),
            transport="generate_content" if gemini else "chat_completions",
            reasoning_effort=self.overrides.get("reasoning_effort"),
        )

    def openai_compatible_temperature(self) -> float | None:
        return self.transport_capabilities().temperature

    def sampling_parameters(self, *, model: str | None = None) -> tuple[dict, list[str]]:
        policy = self.transport_capabilities(model=model)
        gemini = self.provider() == "gemini" or (self.settings.llm_transport == "litellm" and self.provider() in {"google", "vertex_ai"})
        params = {"temperature": self.overrides.get("temperature", policy.temperature)}
        if self.overrides.get("top_p", policy.top_p) is not None:
            params["topP" if gemini else "top_p"] = self.overrides.get("top_p", policy.top_p)
        if self.overrides.get("top_k") is not None:
            params["topK" if gemini else "top_k"] = self.overrides["top_k"]
        if policy.temperature is None:
            return {}, list(params)
        omitted = []
        if not gemini and "top_k" in params:
            # No generic Chat Completions top_k contract.
            params.pop("top_k")
            omitted.append("top_k")
        return {key: value for key, value in params.items() if value is not None}, omitted

    def thinking_parameters(self, *, model: str | None = None, wire_confirmed: bool = False) -> tuple[dict, dict]:
        """Preserve explicit transport controls; auto-OFF is chat-only and exact-gated."""
        gemini = self.provider() == "gemini" or (self.settings.llm_transport == "litellm" and self.provider() in {"google", "vertex_ai"})
        explicit = {key: self.overrides[key] for key in ("reasoning_effort", "thinking_level", "thinking_budget")
                    if self.overrides.get(key) is not None}
        names = ({"thinking_level": "thinkingLevel", "thinking_budget": "thinkingBudget"}
                 if gemini else {"reasoning_effort": "reasoning_effort"})
        selected = {names[key]: value for key, value in explicit.items() if key in names}
        params = {"thinkingConfig": selected} if gemini and selected else selected
        on_wire = [("thinkingConfig." if gemini else "") + key for key in selected]
        requested = self.settings.chat_thinking_policy
        parameter = None
        if requested == "provider_default":
            reason = "provider_default"
        elif self.profile != "chat" or self.purpose not in {
            "chat_generation", "chat_generation_fallback", "chat_command_postprocess",
        }:
            reason = "nonchat_scope"
        elif explicit:
            # Non-native controls still block automatic changes, but do not
            # pretend they were serialized by this transport.
            reason = ("explicit_override_preserved" if explicit.keys() <= names.keys()
                      else "explicit_override_not_serialized")
        else:
            params, reason = thinking_off_parameters(
                model=model or self.model(),
                endpoint="https://generativelanguage.googleapis.com/v1beta" if gemini else self.base_url(),
                transport="generate_content" if gemini else "chat_completions",
            )
            parameter = next(iter(params), None)
            if parameter and not wire_confirmed:
                reason = "transport_unconfirmed"
        return params, {
            "requested": requested,
            "applied_on_wire": bool(parameter and wire_confirmed),
            "parameter": parameter,
            "omission_reason": reason,
            "explicit_parameters_on_wire": on_wire if wire_confirmed else [],
        }

    def transport_diagnostics(self, *, model: str | None = None, warning_count: int = 0,
                              wire_confirmed: bool = False) -> dict:
        diagnostics = self.transport_capabilities(model=model).diagnostics()
        _, omitted = self.sampling_parameters(model=model)
        diagnostics["omitted_parameters"] = omitted
        diagnostics["warning_codes"] = ["sampling_omitted"] if omitted else []
        diagnostics["provider_warning_count"] = warning_count
        _, diagnostics["thinking_policy"] = self.thinking_parameters(model=model, wire_confirmed=wire_confirmed)
        if warning_count:
            diagnostics["warning_codes"].append("provider_warning_present")
        return diagnostics

    def usage_context(self) -> dict:
        trace = _ATTEMPT_TRACE.get() or []
        return {"stage": self.overrides.get("stage") or self.purpose,
                "provider_account_id": self.overrides.get('provider_account_id'),
                "model_option_key": self.overrides.get('model_option_key'),
                "route_revision": self.circuit_key(),
                "requested_route": _TASK_REQUESTED_ROUTE.get(),
                "effective_route": self.route_observation(),
                "attempt_trace": list(trace)}

    def route_observation(self):
        return {'provider': self.provider(), 'model': self.model(),
                'provider_account_id': self.overrides.get('provider_account_id'),
                'model_option_key': self.overrides.get('model_option_key'),
                'route_revision': self.circuit_key()}

    def gemini_response_schema(self, response_format: dict | None) -> dict | None:
        if not response_format or response_format.get("type") != "json_object":
            return None
        schema = response_format.get("schema")
        if not isinstance(schema, dict):
            return None

        def sanitize(value: object) -> object:
            if isinstance(value, dict):
                return {
                    key: sanitize(child)
                    for key, child in value.items()
                    if key != "additionalProperties"
                }
            if isinstance(value, list):
                return [sanitize(child) for child in value]
            return value

        sanitized = sanitize(schema)
        return sanitized if isinstance(sanitized, dict) else None

    def provider(self) -> str:
        if self.overrides.get("provider"):
            return str(self.overrides["provider"]).lower()
        if self.profile == "compression":
            return (self.settings.compression_llm_provider or "openai_compatible").lower()
        return (self.settings.chat_llm_provider or "openai_compatible").lower()

    def model(self) -> str:
        if self.overrides.get("model"):
            return str(self.overrides["model"])
        if self.profile == "compression":
            return self.settings.compression_llm_model or self.settings.llm_model
        return self.settings.chat_llm_model or self.settings.llm_model

    def base_url(self) -> str:
        if self.settings.llm_transport == "litellm":
            from app.engine.litellm_transport import resolve_route
            # The resolver calls this legacy helper only for explicit legacy
            # compatible configuration (handled below to avoid recursion).
            if self.provider() != "openai_compatible" or self.overrides.get("provider_account_id") is not None:
                return resolve_route(self).base_url
        if self.overrides.get("base_url"):
            return str(self.overrides["base_url"])
        if self.profile == "compression":
            return self.settings.compression_llm_base_url or self.settings.llm_base_url
        return self.settings.chat_llm_base_url or self.settings.llm_base_url

    def api_key(self) -> str:
        if self.provider() == "gemini":
            return self.gemini_api_key()
        if self.overrides.get("api_key"):
            return str(self.overrides["api_key"])
        if self.profile == "compression":
            return self.settings.compression_llm_api_key or self.settings.llm_api_key
        return self.settings.chat_llm_api_key or self.settings.llm_api_key

    def gemini_api_key(self) -> str:
        def usable_key(value: str | None) -> str | None:
            if not value:
                return None
            candidate = str(value).strip()
            if not candidate or candidate.lower() in {"not-needed-for-local", "not_needed_for_local", "not-needed", "none"}:
                return None
            return candidate

        key = usable_key(self.overrides.get("gemini_api_key") or self.overrides.get("api_key"))
        if not key and self.profile == "compression":
            key = usable_key(self.settings.compression_llm_api_key)
        if not key:
            key = usable_key(self.settings.chat_llm_api_key) or usable_key(self.settings.gemini_api_key) or usable_key(self.settings.google_api_key)
        if not key:
            raise LLMConfigurationError("Gemini API key is not configured")
        return key

    def should_fallback_to_gemini(self) -> bool:
        if not self.settings.local_llm_fallback_enabled:
            return False
        if (self.settings.local_llm_fallback_provider or "").lower() != "gemini":
            return False
        if self.provider() != "openai_compatible":
            return False
        return bool(self.settings.chat_llm_api_key or self.settings.gemini_api_key or self.settings.google_api_key)

    def timeout_seconds(self) -> float:
        if self.overrides.get("timeout_seconds"):
            return float(self.overrides["timeout_seconds"])
        if self.profile == "compression":
            return self.settings.compression_llm_timeout_seconds or self.settings.llm_timeout_seconds
        return self.settings.chat_llm_timeout_seconds or self.settings.llm_timeout_seconds

    def max_output_tokens(self) -> int | None:
        value = self.overrides.get("min_output_tokens")
        if value is None:
            return None
        # Gemini/OpenAI-compatible APIs do not support minOutputTokens. This
        # legacy value now selects response-length headroom; the prompt/schema
        # express length as a bubble range, while this only supplies a ceiling.
        return max(512, min(8192, int(value) * 2))

    @bounded_llm_task
    async def chat(self, messages: list[dict], *, response_format: dict | None = None, conversation_id: str | None = None) -> LLMResponse:
        trace_token = _ATTEMPT_TRACE.set([])
        try:
            if _TASK_REQUESTED_ROUTE.get() is None:
                _TASK_REQUESTED_ROUTE.set(self.route_observation())
            return await self._chat(messages, response_format=response_format, conversation_id=conversation_id)
        finally:
            _ATTEMPT_TRACE.reset(trace_token)

    async def _chat(self, messages: list[dict], *, response_format: dict | None = None, conversation_id: str | None = None) -> LLMResponse:
        if self.settings.llm_mock:
            user_text = _mock_visible_user_text(next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""))
            if (response_format or {}).get("name") == "free_body_replies":
                return LLMResponse(content=json.dumps({"replies": [{"who": "C1", "text": f"Mock reply to: {user_text[:80]}"}]}), provider="mock", model="mock")
            if response_format is None and ("Return only <m c=" in str(messages) or "Primary: <m c=" in str(messages)):
                return LLMResponse(content='<m c="C1">Mock reply.</m>', provider="mock", model="mock")
            if response_format and response_format.get("type") == "json_object":
                schema = response_format.get("schema") if isinstance(response_format, dict) else None
                replies_schema = (schema or {}).get("properties", {}).get("replies", {}) if isinstance(schema, dict) else {}
                if isinstance(replies_schema, dict) and replies_schema:
                    minimum = max(1, int(replies_schema.get("minItems") or 1))
                    return LLMResponse(content=json.dumps({
                        "replies": [
                            {
                                "reply_type": "character",
                                "character_id": "mock_character",
                                "speaker_name": "mock_character",
                                "dialogue": f"Mock reply {index + 1} to: {user_text[:80]}",
                                "emotion": "neutral",
                                "action": "",
                                "thought": "",
                            }
                            for index in range(minimum)
                        ],
                    }, ensure_ascii=False), provider="mock", model="mock")
                return LLMResponse(content=json.dumps({
                    "character_id": "mock_character",
                    "text": f"Mock reply to: {user_text[:80]}",
                    "emotion": "neutral",
                    "action": "",
                }, ensure_ascii=False), provider="mock", model="mock")
            return LLMResponse(content=f"Mock reply to: {user_text[:80]}", provider="mock", model="mock")

        circuit_token = None
        try:
            if self.provider() == 'vertex_ai' and self.settings.llm_transport != 'litellm':
                raise LLMConfigurationError('Vertex requires the LiteLLM transport')
            if response_format and self.overrides.get('supports_json') is False:
                raise LLMCapabilityError('Selected model does not support JSON output')
            sampling, omitted = self.sampling_parameters()
            if any(self.overrides.get(name) is not None for name in omitted):
                raise LLMCapabilityError('Explicit sampling control is unsupported')
            _, thinking_diagnostics = self.thinking_parameters()
            if thinking_diagnostics['omission_reason'] == 'explicit_override_not_serialized':
                raise LLMCapabilityError('Explicit thinking control is unsupported')
            if self.settings.llm_transport == "litellm":
                from app.engine.litellm_transport import resolve_route
                route = resolve_route(self)
            circuit_token = self.ensure_circuit_available()
            if self.settings.llm_transport == "litellm":
                response = await self._chat_litellm(route, messages, response_format=response_format, conversation_id=conversation_id)
            elif self.provider() == "gemini":
                response = await self._chat_gemini(messages, response_format=response_format)
            else:
                response = await self._chat_openai_compatible(messages, response_format=response_format, conversation_id=conversation_id)
            deadline = TASK_DEADLINE.get()
            if deadline is not None and time.monotonic() >= deadline:
                raise LLMTaskDeadlineError(transport_attempts=response.generation_attempts)
            if response.finish_reason in {'SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII', 'MODEL_ARMOR', 'IMAGE_SAFETY', 'content_filter', 'refusal'}:
                raise LLMPolicyBlockedError(transport_attempts=response.generation_attempts)
        except LLMUnavailableError as exc:
            if not isinstance(exc, (CircuitOpenError, LLMAttemptBudgetError)):
                self.record_circuit_failure(circuit_token)
            self._record_transport_failure(exc, conversation_id=conversation_id)
            raise
        except LLMTerminalError as exc:
            self.release_circuit_probe(circuit_token)
            self._record_transport_failure(exc, conversation_id=conversation_id)
            raise
        except asyncio.CancelledError:
            self.release_circuit_probe(circuit_token)
            trace = _ATTEMPT_TRACE.get() or []
            deadline = TASK_DEADLINE.get()
            expired = deadline is not None and time.monotonic() >= deadline
            exc = LLMTaskDeadlineError(error_type='task_deadline_exhausted' if expired else 'task_cancelled', transport_attempts=sum(t['dispatched'] for t in trace))
            self._record_transport_failure(exc, conversation_id=conversation_id)
            raise
        self.record_circuit_success(circuit_token)
        self._record_usage(response, conversation_id=conversation_id)
        if response.provider_diagnostics and self.profile == "compression":
            logger.warning(
                "Gemini empty compression response conversation=%s purpose=%s model=%s usage_event=%s diagnostics=%s",
                conversation_id, self.purpose, response.model, response.usage_event_id,
                response.provider_diagnostics,
            )
        return response

    async def _chat_litellm(self, route, messages, *, response_format=None, conversation_id=None) -> LLMResponse:
        from app.engine import litellm_transport as sdk

        sampling, _ = self.sampling_parameters()
        parameters = { {"topP": "top_p", "topK": "top_k"}.get(key, key): value
                       for key, value in sampling.items() }
        thinking, _ = self.thinking_parameters()
        parameters.update(thinking)
        if "reasoning" in parameters:
            parameters["extra_body"] = {"reasoning": parameters.pop("reasoning")}
        cap = self.max_output_tokens()
        if cap is not None:
            parameters["max_tokens"] = cap
        schema = sdk.response_format_copy(response_format, strict=self.strict_structured_output())
        if schema is not None:
            parameters["response_format"] = schema
        if route.sdk_provider in {"gemini", "vertex_ai"}:
            parameters["safety_settings"] = self.gemini_safety_settings()
        if self.transport_capabilities().cache_affinity and conversation_id:
            parameters["extra_headers"] = {"x-grok-conv-id": hashlib.sha256(
                ("lorechat:" + conversation_id).encode("utf-8")).hexdigest()}

        async def request(reserve):
            return await sdk.completion(route, messages=messages, parameters=parameters,
                                        timeout=min(self.timeout_seconds(), remaining_seconds()), reserve=reserve)

        response = await self._post_with_retries(request, endpoint_label="LLM endpoint", boundary_counted=True)
        observed, data = sdk.native_observation(response, route)
        budget = {"requested_max_output_tokens": cap}
        diagnostics = None
        if route.sdk_provider in {"gemini", "vertex_ai"}:
            budget.update(thoughts_token_count=observed["reasoning_tokens"],
                          cached_content_token_count=observed["cached_tokens"])
            if self.profile == "compression" and not observed["content"]:
                diagnostics = _gemini_empty_response_diagnostics(data)
        transport_diagnostics = self.transport_diagnostics(
            warning_count=len(response.headers.get_list("warning")), wire_confirmed=False)
        _, omitted = self.sampling_parameters()
        transport_diagnostics.update(sdk.control_diagnostics(response, parameters, self.overrides, omitted))
        wire_controls = transport_diagnostics["serialized_wire_controls"]
        thinking_controls = sdk.control_fields(thinking)
        confirmed = bool(thinking_controls) and all(wire_controls.get(k) == v for k, v in thinking_controls.items())
        _, transport_diagnostics["thinking_policy"] = self.thinking_parameters(wire_confirmed=confirmed)
        if not transport_diagnostics["omitted_parameters"]:
            transport_diagnostics["warning_codes"] = [c for c in transport_diagnostics["warning_codes"] if c != "sampling_omitted"]
        return LLMResponse(
            **observed, provider=self.provider(), model=route.model, requested_model=route.model,
            generation_attempts=response.extensions["lorechat_attempts"],
            transport_diagnostics=transport_diagnostics,
            budget_diagnostics=budget, provider_diagnostics=diagnostics,
        )

    async def _chat_openai_compatible(self, messages: list[dict], *, response_format: dict | None = None, conversation_id: str | None = None) -> LLMResponse:
        url = self.base_url().rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key()}"}
        policy = self.transport_capabilities()
        if policy.cache_affinity and conversation_id:
            headers["x-grok-conv-id"] = hashlib.sha256(("lorechat:" + conversation_id).encode("utf-8")).hexdigest()
        payload = {
            "model": self.model(),
            "messages": messages,
        }
        sampling, _ = self.sampling_parameters()
        payload.update(sampling)
        thinking, _ = self.thinking_parameters()
        payload.update(thinking)
        max_output_tokens = self.max_output_tokens()
        if max_output_tokens:
            payload[policy.output_cap_parameter] = max_output_tokens
        normalized_response_format = self.normalize_response_format(response_format)
        if normalized_response_format:
            schema = response_format.get("schema") if isinstance(response_format, dict) else None
            if normalized_response_format.get("type") == "json_object" and isinstance(schema, dict):
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": (response_format or {}).get("name") or "character_chat_replies",
                        "strict": (response_format or {}).get("strict", self.strict_structured_output()),
                        "schema": schema,
                    },
                }
            else:
                payload["response_format"] = normalized_response_format
        async def request():
            async with httpx.AsyncClient(timeout=self.timeout_seconds()) as client:
                return await client.post(url, headers=headers, json=payload)

        response = await self._post_with_retries(request, endpoint_label="LLM endpoint")
        data = response.json()
        usage = data.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details")
        completion_details = usage.get("completion_tokens_details")
        choice = data["choices"][0]
        return LLMResponse(
            content=choice["message"]["content"],
            provider=self.provider(),
            model=self.model(),
            requested_model=self.model(),
            reported_model_version=safe_model_version(data.get("model")),
            cached_tokens=safe_count(prompt_details.get("cached_tokens")) if isinstance(prompt_details, dict) else None,
            reasoning_tokens=safe_count(completion_details.get("reasoning_tokens")) if isinstance(completion_details, dict) else None,
            transport_diagnostics=self.transport_diagnostics(warning_count=len(response.headers.get_list("warning")), wire_confirmed=True),
            prompt_tokens=safe_count(usage.get("prompt_tokens")),
            completion_tokens=safe_count(usage.get("completion_tokens")),
            total_tokens=safe_count(usage.get("total_tokens")),
            finish_reason=choice.get("finish_reason"),
            generation_attempts=int(response.extensions.get("lorechat_attempts", 1)),
            budget_diagnostics={"requested_max_output_tokens": max_output_tokens},
        )

    def gemini_safety_settings(self) -> list[dict]:
        threshold = self.settings.gemini_safety_threshold or "BLOCK_NONE"
        return [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": threshold},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": threshold},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": threshold},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": threshold},
            {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": threshold},
        ]

    def gemini_fallback_model(self, model: str) -> str | None:
        return None

    def transport_retry_attempts(self) -> int:
        return max(1, min(5, int(self.overrides.get("retry_attempts") or 2)))

    def gemini_retry_attempts(self) -> int:
        return self.transport_retry_attempts()

    def is_retryable_http_status(self, status_code: int) -> bool:
        return status_code in {408, 429, 500, 502, 503, 504}

    def is_retryable_transport_exception(self, exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return self.is_retryable_http_status(exc.response.status_code)
        return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError))

    def retry_after_seconds(self, exc: Exception) -> float | None:
        if not isinstance(exc, httpx.HTTPStatusError):
            return None
        value = (exc.response.headers.get("Retry-After") or "").strip()
        if not value:
            return None
        try:
            return max(0.0, min(30.0, float(value)))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, min(30.0, (parsed - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                return None

    def retry_delay_seconds(self, exc: Exception, *, attempt: int) -> float:
        retry_after = self.retry_after_seconds(exc)
        if retry_after is not None:
            return retry_after
        base = min(8.0, 0.8 * (2 ** max(0, attempt - 1)))
        return base + random.uniform(0.0, min(0.5, base * 0.2))

    def transport_error_message(self, exc: Exception, *, endpoint_label: str) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"{endpoint_label} unavailable for {self.provider()}:{self.model()} (HTTP {exc.response.status_code})"
        return f"{endpoint_label} unavailable for {self.provider()}:{self.model()} ({type(exc).__name__})"

    def _http_error_has_code(self, exc, allowed):
        if not isinstance(exc, httpx.HTTPStatusError) or len(exc.response.content) > 8 * 1024 * 1024:
            return False
        try:
            data = exc.response.json()
            error = data.get('error') if isinstance(data, dict) else None
            if not isinstance(error, dict):
                return False
            inner = error.get('innererror')
            codes = [error.get('code'), error.get('type'), inner.get('code') if isinstance(inner, dict) else None]
            return any(isinstance(code, str) and code in allowed for code in codes)
        except (ValueError, UnicodeError, RecursionError):
            return False

    def explicit_http_policy_block(self, exc):
        return self._http_error_has_code(exc, {
            'content_policy_violation', 'content_filter', 'safety',
            'prohibited_content', 'moderation_blocked', 'ResponsibleAIPolicyViolation',
        })

    def explicit_http_billing_failure(self, exc):
        return self._http_error_has_code(exc, {
            'insufficient_quota', 'billing_hard_limit_reached', 'billing_not_active',
            'credit_balance_too_low',
        })

    async def _post_with_retries(self, request, *, endpoint_label: str, boundary_counted: bool = False) -> httpx.Response:
        attempts = self.transport_retry_attempts()
        budget = _TASK_BUDGET.get()
        if budget is not None:
            attempts = min(attempts, budget.limit - budget.used)
        if attempts <= 0:
            error = LLMAttemptBudgetError("LLM task network attempt budget exhausted", error_type="network_budget_exhausted")
            error.transport_attempts = 0
            raise error
        last_exc: Exception | None = None
        dispatched = 0

        def reserve():
            nonlocal dispatched
            remaining_seconds()
            if budget is not None and budget.deadline is not None and time.monotonic() >= budget.deadline:
                raise LLMTaskDeadlineError()
            if budget is not None:
                if budget.used >= budget.limit:
                    raise LLMAttemptBudgetError("LLM task network attempt budget exhausted", transport_attempts=0,
                                               error_type="network_budget_exhausted")
                budget.used += 1
            dispatched += 1

        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            before = dispatched
            trace = _ATTEMPT_TRACE.get()
            record = {'attempt': attempt, 'dispatched': 0, 'outcome': 'pending',
                      'http_status': None, 'transport_elapsed_seconds': 0.0, 'backoff_elapsed_seconds': 0.0}
            if trace is not None:
                trace.append(record)
            try:
                deadline = budget.deadline if budget is not None else TASK_DEADLINE.get()
                if boundary_counted:
                    response = await within_deadline(request(reserve), deadline=deadline)
                else:
                    reserve()
                    response = await within_deadline(request(), deadline=deadline)
                response.raise_for_status()
                record.update(outcome='success', http_status=response.status_code)
                response.extensions["lorechat_attempts"] = dispatched
                return response
            except asyncio.CancelledError:
                record['outcome'] = 'cancelled'
                raise
            except Exception as exc:
                if isinstance(exc, (LLMTerminalError, LLMAttemptBudgetError)):
                    exc.transport_attempts = dispatched
                    record.update(outcome=exc.error_type, http_status=exc.http_status)
                    raise
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                warning_count = len(exc.response.headers.get_list("warning")) if isinstance(exc, httpx.HTTPStatusError) else 0
                if self.explicit_http_billing_failure(exc):
                    record.update(outcome='account_billing_unavailable', http_status=status)
                    raise LLMConfigurationError(error_type='account_billing_unavailable', http_status=status, transport_attempts=dispatched, provider_warning_count=warning_count) from None
                if self.explicit_http_policy_block(exc):
                    record.update(outcome='provider_policy_block', http_status=status)
                    raise LLMPolicyBlockedError(http_status=status, transport_attempts=dispatched, provider_warning_count=warning_count) from None
                record.update(outcome='availability_failure' if self.is_retryable_transport_exception(exc) else 'terminal_failure', http_status=status)
                last_exc = exc
                if not self.is_retryable_transport_exception(exc):
                    kind = (LLMAuthenticationError if status in {401, 403} else
                            LLMConfigurationError if status == 404 else LLMRequestError)
                    raise kind(http_status=status, transport_attempts=dispatched, provider_warning_count=warning_count) from None
                if not self.is_retryable_transport_exception(exc) or attempt >= attempts:
                    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    raise LLMUnavailableError(
                        self.transport_error_message(exc, endpoint_label=endpoint_label),
                        transport_attempts=dispatched,
                        http_status=status,
                        error_type=type(exc).__name__,
                        provider_warning_count=len(exc.response.headers.get_list("warning")) if isinstance(exc, httpx.HTTPStatusError) else 0,
                    ) from None
                record['transport_elapsed_seconds'] = time.monotonic() - started
                sleep_started = time.monotonic()
                try:
                    await within_deadline(asyncio.sleep(self.retry_delay_seconds(exc, attempt=attempt)), deadline=deadline)
                except LLMTaskDeadlineError as deadline_error:
                    deadline_error.transport_attempts = dispatched
                    record['outcome'] = deadline_error.error_type
                    raise
                finally:
                    record['backoff_elapsed_seconds'] = time.monotonic() - sleep_started
            finally:
                record['dispatched'] = dispatched - before
                if record['transport_elapsed_seconds'] == 0.0:
                    record['transport_elapsed_seconds'] = time.monotonic() - started
        assert last_exc is not None
        raise LLMUnavailableError(
            self.transport_error_message(last_exc, endpoint_label=endpoint_label),
            transport_attempts=attempts,
            error_type=type(last_exc).__name__,
        ) from last_exc

    def gemini_error_message(self, exc: Exception, *, model: str) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"Gemini endpoint unavailable: {model} (HTTP {exc.response.status_code})"
        return f"Gemini endpoint unavailable: {model} ({type(exc).__name__})"

    def is_retryable_gemini_exception(self, exc: Exception) -> bool:
        return self.is_retryable_transport_exception(exc)

    async def _post_gemini_with_retries(self, url: str, *, model: str, payload: dict) -> httpx.Response:
        async def request():
            async with httpx.AsyncClient(timeout=self.timeout_seconds()) as client:
                return await client.post(
                    url,
                    headers={"x-goog-api-key": self.gemini_api_key()},
                    json=payload,
                )

        return await self._post_with_retries(request, endpoint_label="Gemini endpoint")

    async def _chat_gemini(self, messages: list[dict], *, response_format: dict | None = None, model: str | None = None) -> LLMResponse:
        model = model or self.model()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        system_parts: list[dict] = []
        contents: list[dict] = []
        for message in messages:
            role = message.get("role") or "user"
            text = message.get("content") or ""
            if role == "system":
                system_parts.append({"text": text})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": text}]})
            else:
                contents.append({"role": "user", "parts": [{"text": text}]})
        payload: dict = {
            "contents": contents,
            "generationConfig": {},
            "safetySettings": self.gemini_safety_settings(),
        }
        sampling, _ = self.sampling_parameters(model=model)
        payload["generationConfig"].update(sampling)
        thinking, _ = self.thinking_parameters(model=model)
        payload["generationConfig"].update(thinking)
        max_output_tokens = self.max_output_tokens()
        if max_output_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_output_tokens
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}
        if response_format and response_format.get("type") == "json_object":
            payload["generationConfig"]["responseMimeType"] = "application/json"
            response_schema = self.gemini_response_schema(response_format)
            if response_schema:
                payload["generationConfig"]["responseSchema"] = response_schema
        response = await self._post_gemini_with_retries(url, model=model, payload=payload)
        data = response.json()
        candidates = data.get("candidates") or []
        if (data.get('promptFeedback') or {}).get('blockReason'):
            raise LLMPolicyBlockedError(transport_attempts=response.extensions.get('lorechat_attempts', 1))
        candidate = candidates[0] if candidates else {}
        parts = ((candidate.get("content") or {}).get("parts") or []) if candidate else []
        content = "".join(part.get("text", "") for part in parts if not part.get("thought")).strip()
        finish_reason = candidate.get("finishReason") if candidate else None
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            content=content,
            provider="gemini",
            model=model,
            requested_model=model,
            reported_model_version=safe_model_version(data.get("modelVersion")),
            cached_tokens=safe_count(usage.get("cachedContentTokenCount")),
            reasoning_tokens=safe_count(usage.get("thoughtsTokenCount")),
            transport_diagnostics=self.transport_diagnostics(model=model, warning_count=len(response.headers.get_list("warning")), wire_confirmed=True),
            prompt_tokens=safe_count(usage.get("promptTokenCount")),
            completion_tokens=safe_count(usage.get("candidatesTokenCount")),
            total_tokens=safe_count(usage.get("totalTokenCount")),
            finish_reason=finish_reason,
            generation_attempts=int(response.extensions.get("lorechat_attempts") or 1),
            budget_diagnostics={
                "requested_max_output_tokens": max_output_tokens,
                "thoughts_token_count": safe_count(usage.get("thoughtsTokenCount")),
                "cached_content_token_count": safe_count(usage.get("cachedContentTokenCount")),
            },
            provider_diagnostics=(
                _gemini_empty_response_diagnostics(data)
                if self.profile == "compression" and not content else None
            ),
        )

    def _record_usage(self, response: LLMResponse, *, conversation_id: str | None = None) -> None:
        if response.provider == "mock":
            return
        event_id = f"usage_{uuid4().hex[:12]}"
        try:
            with Session(engine) as session:
                session.add(LLMUsageEvent(
                    id=event_id,
                    provider=response.provider or self.provider(),
                    model=response.model or self.model(),
                    purpose=self.purpose,
                    conversation_id=conversation_id,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    total_tokens=response.total_tokens,
                    estimated=response.estimated,
                    metadata_={
                        "status": "transport_success",
                        **self.usage_context(),
                        "requested_model": response.requested_model or response.model or self.model(),
                        "reported_model_version": response.reported_model_version,
                        "token_details": {"cached_tokens": response.cached_tokens, "reasoning_tokens": response.reasoning_tokens},
                        **({"transport": response.transport_diagnostics} if response.transport_diagnostics is not None else {}),
                        "finish_reason": response.finish_reason,
                        "response_length": len(response.content or ""),
                        "response_sha256": hashlib.sha256((response.content or "").encode("utf-8")).hexdigest(),
                        "transport_attempts": response.generation_attempts,
                        "generation_attempts": response.generation_attempts,
                        "parse_code": None,
                        "validation_code": None,
                        **({"output_budget": response.budget_diagnostics} if response.budget_diagnostics is not None else {}),
                        **task_attempt_metadata(),
                        **({"gemini_empty_response": response.provider_diagnostics} if response.provider_diagnostics and self.profile == "compression" else {}),
                    },
                ))
                session.commit()
            response.usage_event_id = event_id
        except Exception:
            # Attempt accounting must never break chat generation.
            _telemetry_write_failed()
            return

    def failure_transport_diagnostics(self, warning_count=0):
        try:
            return self.transport_diagnostics(warning_count=warning_count)
        except Exception:
            # A failing route preflight must not prevent its zero-send ledger.
            return {'diagnostic_code': 'route_preflight_unavailable'}

    def _record_transport_failure(
        self,
        exc: LLMUnavailableError | LLMTerminalError,
        *,
        conversation_id: str | None = None,
    ) -> None:
        try:
            with Session(engine) as session:
                session.add(LLMUsageEvent(
                    id=f"usage_{uuid4().hex[:12]}",
                    provider=self.provider(),
                    model=self.model(),
                    purpose=self.purpose,
                    conversation_id=conversation_id,
                    prompt_tokens=None,
                    completion_tokens=None,
                    total_tokens=None,
                    estimated=False,
                    metadata_={
                        "status": "transport_failed",
                        **self.usage_context(),
                        "requested_model": self.model(),
                        "reported_model_version": None,
                        "token_details": {"cached_tokens": None, "reasoning_tokens": None},
                        "transport": self.failure_transport_diagnostics(warning_count=exc.provider_warning_count),
                        "output_budget": {"requested_max_output_tokens": self.max_output_tokens()},
                        **task_attempt_metadata(),
                        "transport_attempts": exc.transport_attempts,
                        "http_status": exc.http_status,
                        "error_type": exc.error_type,
                        "response_length": 0,
                        "response_sha256": None,
                        "parse_code": None,
                        "validation_code": None,
                    },
                ))
                session.commit()
        except Exception:
            _telemetry_write_failed()
            return

    def record_response_outcome(
        self,
        response: LLMResponse | None,
        *,
        status: str,
        parse_code: str | None,
        validation_code: str | None,
        metadata_updates: dict[str, object] | None = None,
    ) -> None:
        event_id = getattr(response, "usage_event_id", None)
        if not event_id:
            return
        try:
            with Session(engine) as session:
                event = session.get(LLMUsageEvent, event_id)
                if event is None:
                    return
                metadata = dict(event.metadata_ or {})
                metadata.update({
                    "status": status,
                    "parse_code": parse_code,
                    "validation_code": validation_code,
                })
                metadata.update(metadata_updates or {})
                event.metadata_ = metadata
                session.add(event)
                session.commit()
        except Exception:
            _telemetry_write_failed()
            return
