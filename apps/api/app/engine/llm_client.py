from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import hashlib
import json
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


class LLMUnavailableError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        transport_attempts: int = 1,
        http_status: int | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.transport_attempts = max(1, int(transport_attempts))
        self.http_status = http_status
        self.error_type = error_type or type(self).__name__


class CircuitOpenError(LLMUnavailableError):
    pass


_CIRCUIT_STATES: dict[str, dict[str, float | int]] = {}
_CIRCUIT_LOCK = threading.Lock()


class LLMResponse(BaseModel):
    content: str
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None
    generation_attempts: int = 1
    estimated: bool = False
    usage_event_id: str | None = None


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
        provider_identity = self.overrides.get("provider_account_id") or self.base_url()
        return f"{self.provider()}|{provider_identity}|{self.model()}"

    def ensure_circuit_available(self) -> None:
        if not self.circuit_breaker_enabled():
            return
        now = time.monotonic()
        key = self.circuit_key()
        with _CIRCUIT_LOCK:
            state = _CIRCUIT_STATES.get(key)
            if not state:
                return
            open_until = float(state.get("open_until") or 0.0)
            if open_until > now:
                raise CircuitOpenError(
                    f"LLM circuit breaker is open for {self.provider()}:{self.model()}"
                )
            if open_until:
                _CIRCUIT_STATES.pop(key, None)

    def record_circuit_failure(self) -> None:
        if not self.circuit_breaker_enabled():
            return
        key = self.circuit_key()
        with _CIRCUIT_LOCK:
            state = _CIRCUIT_STATES.setdefault(key, {"failures": 0, "open_until": 0.0})
            failures = int(state.get("failures") or 0) + 1
            state["failures"] = failures
            if failures >= self.circuit_failure_threshold():
                state["open_until"] = time.monotonic() + self.circuit_cooldown_seconds()

    def record_circuit_success(self) -> None:
        if not self.circuit_breaker_enabled():
            return
        with _CIRCUIT_LOCK:
            _CIRCUIT_STATES.pop(self.circuit_key(), None)

    def normalize_response_format(self, response_format: dict | None) -> dict | None:
        if response_format and response_format.get("type") == "json_object":
            return {"type": "json_object"}
        return response_format

    def strict_structured_output(self) -> bool:
        return self.provider() == "xai"

    def openai_compatible_temperature(self) -> float:
        # Grok's structured-output path otherwise collapses into unusually terse,
        # literal replies. Keep the higher sampling temperature isolated to xAI;
        # other OpenAI-compatible providers retain the established default.
        return 1.1 if self.provider() == "xai" else 0.7

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
            raise LLMUnavailableError("Gemini API key is not configured")
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

    async def chat(self, messages: list[dict], *, response_format: dict | None = None, conversation_id: str | None = None) -> LLMResponse:
        if self.settings.llm_mock:
            user_text = _mock_visible_user_text(next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""))
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

        try:
            self.ensure_circuit_available()
            if self.provider() == "gemini":
                response = await self._chat_gemini(messages, response_format=response_format)
            else:
                response = await self._chat_openai_compatible(messages, response_format=response_format)
        except LLMUnavailableError as exc:
            if not isinstance(exc, CircuitOpenError):
                self.record_circuit_failure()
            self._record_transport_failure(exc, conversation_id=conversation_id)
            raise
        self.record_circuit_success()
        self._record_usage(response, conversation_id=conversation_id)
        return response

    async def _chat_openai_compatible(self, messages: list[dict], *, response_format: dict | None = None) -> LLMResponse:
        url = self.base_url().rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key()}"}
        payload = {
            "model": self.model(),
            "messages": messages,
            "temperature": self.openai_compatible_temperature(),
        }
        max_output_tokens = self.max_output_tokens()
        if max_output_tokens:
            payload["max_tokens"] = max_output_tokens
        normalized_response_format = self.normalize_response_format(response_format)
        if normalized_response_format:
            schema = response_format.get("schema") if isinstance(response_format, dict) else None
            if normalized_response_format.get("type") == "json_object" and isinstance(schema, dict):
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": (response_format or {}).get("name") or "character_chat_replies",
                        "strict": self.strict_structured_output(),
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
        choice = data["choices"][0]
        return LLMResponse(
            content=choice["message"]["content"],
            provider=self.provider(),
            model=self.model(),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            finish_reason=choice.get("finish_reason"),
            generation_attempts=int(response.extensions.get("lorechat_attempts", 1)),
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

    async def _post_with_retries(self, request, *, endpoint_label: str) -> httpx.Response:
        attempts = self.transport_retry_attempts()
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await request()
                response.raise_for_status()
                response.extensions["lorechat_attempts"] = attempt
                return response
            except Exception as exc:
                last_exc = exc
                if not self.is_retryable_transport_exception(exc) or attempt >= attempts:
                    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    raise LLMUnavailableError(
                        self.transport_error_message(exc, endpoint_label=endpoint_label),
                        transport_attempts=attempt,
                        http_status=status,
                        error_type=type(exc).__name__,
                    ) from exc
                await asyncio.sleep(self.retry_delay_seconds(exc, attempt=attempt))
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
            "generationConfig": {
                "temperature": 0.7,
            },
            "safetySettings": self.gemini_safety_settings(),
        }
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
        candidate = candidates[0] if candidates else {}
        parts = ((candidate.get("content") or {}).get("parts") or []) if candidate else []
        content = "".join(part.get("text", "") for part in parts).strip()
        finish_reason = candidate.get("finishReason") if candidate else None
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            content=content,
            provider="gemini",
            model=model,
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
            total_tokens=usage.get("totalTokenCount"),
            finish_reason=finish_reason,
            generation_attempts=int(response.extensions.get("lorechat_attempts") or 1),
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
                        "finish_reason": response.finish_reason,
                        "response_length": len(response.content or ""),
                        "response_sha256": hashlib.sha256((response.content or "").encode("utf-8")).hexdigest(),
                        "transport_attempts": response.generation_attempts,
                        "generation_attempts": response.generation_attempts,
                        "parse_code": None,
                        "validation_code": None,
                    },
                ))
                session.commit()
            response.usage_event_id = event_id
        except Exception:
            # Attempt accounting must never break chat generation.
            return

    def _record_transport_failure(
        self,
        exc: LLMUnavailableError,
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
            return
