from __future__ import annotations

import inspect
import json
import re
from collections import Counter
from copy import deepcopy
from importlib import import_module
from typing import Any, Awaitable, Callable
from pydantic import ValidationError

try:
    repair_json = import_module("json_repair").repair_json
except Exception:  # pragma: no cover - optional runtime dependency fallback
    repair_json = None

from app.db.models import Character, Message, SceneState
from app.engine.llm_client import LLMClient, LLMUnavailableError, chat_with_optional_conversation_id
from app.engine.output_contract import SEMANTIC_FIELD_MARKDOWN_DESCRIPTION, THOUGHT_MAX_CHARS
from app.engine.output_guard import internal_control_token_reason
from app.engine.prompt_harness import PromptHarness, approx_tokens
from app.engine.prompt_graph import build_prompt_generation_graph, run_prompt_generation_pipeline
from app.engine.prompts import build_character_messages, build_multi_character_prompt_harness, is_silent_cast_role
from app.schemas.dialogue import CharacterReply, MultiCharacterReply


class CharacterRuntimeError(RuntimeError):
    pass


CHARACTER_TURN_CONTINUATION_INSTRUCTION = """[Current turn control]
Continue naturally from the preceding character-authored turn.
The preceding assistant/model-role message was spoken or acted by the named room character, not by the human user.
The human user did not speak or act in this turn. Do not attribute that character's dialogue, action, intention, or proposal to the human user.
Generate the next natural room replies under the system prompt and structured output contract."""


def character_retry_instruction(min_bubbles: int) -> str:
    return (
        "[System retry instruction]\n"
        "The previous model response was invalid, incomplete JSON, contained a degenerate repetition loop, "
        "used an oversized thought, or returned too few bubbles. "
        "Return one complete JSON object matching the replies schema only. "
        f"Return exactly {min_bubbles} reply bubbles. Correct only the JSON structure, required IDs, field placement, bubble count, and invalid repetition. "
        "Preserve the intended roleplay wording and emotional specificity; do not compress or summarize valid prose merely to make it easier to validate. "
        f"Each thought must be at most {THOUGHT_MAX_CHARS} characters and add a distinct private beat rather than repeating dialogue or action. "
        "Write each dialogue and action once without repeating phrases to fill space. "
        "Every character reply must include the exact character_id and speaker_name for the actual speaking room character. "
        "Every storytelling reply must include character_id and speaker_name as empty strings. "
        "Every character reply must include a non-empty dialogue string. "
        "Do not put the character's spoken response only in thought or action. "
        "Do not include markdown fences, explanations, or partial objects."
    )


def format_runtime_source_message(
    *,
    speaker_type: str,
    speaker_id: str | None,
    content: str,
    action: str | None = None,
    speaker_name: str | None = None,
) -> str:
    if speaker_type == "system":
        return f"[Scene direction / system message]\n{content}"
    speaker_label = speaker_name or speaker_id or speaker_type
    if speaker_type == "character":
        if speaker_id and speaker_name:
            speaker_label = f"{speaker_name}({speaker_id})"
        parts = []
        if action:
            parts.append(f"[Character action: {speaker_label}]\n{action}")
        if content:
            parts.append(f"[Character dialogue: {speaker_label}]\n{content}")
        return "\n\n".join(parts) or content
    parts = []
    if action:
        parts.append(f"[User situation/action]\n{action}")
    if content:
        parts.append(f"[User dialogue]\n{content}")
    return "\n\n".join(parts) or content


def provider_name_for_client(client: Any) -> str:
    provider = getattr(client, "provider", None)
    if callable(provider):
        return str(provider() or "")
    return str(provider or getattr(client, "provider_name", "") or "")


SUCCESSFUL_FINISH_REASONS = {"STOP", "END_TURN", "COMPLETED", "SUCCESS"}
VISIBLE_REPLY_TYPES = frozenset({"character", "storytelling"})
INTERNAL_EVENT_FIELDS = frozenset({"metadata", "event", "events", "tool_call", "tool_calls", "function_call", "function_calls"})


CHAT_REPLIES_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "replies": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "reply_type": {
                        "type": "string",
                        "enum": ["character", "storytelling"],
                        "description": "Visible character dialogue or visible storytelling only.",
                    },
                    "character_id": {
                        "type": "string",
                        "description": "Required exact Character id for character replies; use an empty string for storytelling bubbles.",
                    },
                    "speaker_name": {
                        "type": "string",
                        "description": "Required exact visible character name for character replies; use an empty string for storytelling bubbles.",
                    },
                    "dialogue": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Required visible spoken line for every character reply, written as natural Korean conversation. "
                            "Preserve the character's specific rhythm, honorific level, vocabulary, hesitation, interruption, and subtext; "
                            "never summarize intended speech or put the main response only in thought or action. "
                            f"{SEMANTIC_FIELD_MARKDOWN_DESCRIPTION}"
                        ),
                    },
                    "emotion": {
                        "type": "string",
                        "description": "Precise current emotional state, including useful valence, intensity, or degree of control when relevant.",
                    },
                    "action": {
                        "type": "string",
                        "description": (
                            "Natural roleplay prose describing a concrete visible movement, reaction, spatial change, or relevant sensory beat. "
                            "Write a specific beat rather than a generic action label; it may be empty only when dialogue fully carries the moment. "
                            "It is not a substitute for dialogue. "
                            f"{SEMANTIC_FIELD_MARKDOWN_DESCRIPTION}"
                        ),
                    },
                    "thought": {
                        "type": "string",
                        "maxLength": THOUGHT_MAX_CHARS,
                        "description": (
                            f"Private in-character inner voice, at most {THOUGHT_MAX_CHARS} characters, that adds an unspoken impulse, "
                            "contradiction, suspicion, or desire. Do not repeat or summarize dialogue, action, or scene context; "
                            "it may be empty when no distinct private beat exists and never replaces the main spoken response. "
                            f"{SEMANTIC_FIELD_MARKDOWN_DESCRIPTION}"
                        ),
                    },
                },
                "required": [
                    "reply_type",
                    "character_id",
                    "speaker_name",
                    "dialogue",
                    "emotion",
                    "action",
                    "thought",
                ],
            },
        },
    },
    "required": ["replies"],
}


def build_chat_replies_response_schema(*, min_bubbles: int = 1, max_bubbles: int = 8) -> dict:
    maximum = max(1, min(8, int(max_bubbles)))
    minimum = max(1, min(maximum, int(min_bubbles)))
    schema = deepcopy(CHAT_REPLIES_RESPONSE_SCHEMA)
    replies_schema = schema["properties"]["replies"]
    replies_schema["minItems"] = minimum
    replies_schema["maxItems"] = maximum
    return schema


def build_chat_replies_response_format(
    *,
    min_bubbles: int = 1,
    max_bubbles: int = 8,
    retry: bool = False,
) -> dict:
    return {
        "type": "json_object",
        "name": "character_chat_replies_retry" if retry else "character_chat_replies",
        "schema": build_chat_replies_response_schema(
            min_bubbles=min_bubbles,
            max_bubbles=min_bubbles if retry else max_bubbles,
        ),
    }


def generation_control_reserve_tokens(
    *,
    speaker_type: str,
    min_bubbles: int,
    max_bubbles: int,
) -> int:
    """Reserve the larger of the initial and retry request-control envelopes."""

    retry_instruction = character_retry_instruction(min_bubbles)
    initial_response_tokens = approx_tokens(json.dumps(
        build_chat_replies_response_format(
            min_bubbles=min_bubbles,
            max_bubbles=max_bubbles,
        ),
        ensure_ascii=False,
        sort_keys=True,
    ))
    retry_response_tokens = approx_tokens(json.dumps(
        build_chat_replies_response_format(
            min_bubbles=min_bubbles,
            max_bubbles=max_bubbles,
            retry=True,
        ),
        ensure_ascii=False,
        sort_keys=True,
    ))
    if speaker_type == "character":
        initial_control_tokens = approx_tokens(CHARACTER_TURN_CONTINUATION_INSTRUCTION)
        retry_control_tokens = approx_tokens(
            f"{CHARACTER_TURN_CONTINUATION_INSTRUCTION}\n\n{retry_instruction}"
        )
    else:
        initial_control_tokens = 0
        retry_control_tokens = approx_tokens(f"\n\n{retry_instruction}") + 1
    return max(
        initial_control_tokens + initial_response_tokens,
        retry_control_tokens + retry_response_tokens,
    )


CHARACTER_REPLY_RESPONSE_SCHEMA = CHAT_REPLIES_RESPONSE_SCHEMA["properties"]["replies"]["items"]
CANONICAL_REPLY_FIELDS = frozenset({
    "reply_type",
    "character_id",
    "speaker_name",
    "dialogue",
    "emotion",
    "action",
    "thought",
})


class CanonicalParserError(CharacterRuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_canonical_multi_reply_content(content: str) -> list[dict]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise CanonicalParserError("invalid_json", "Canonical reply envelope was not valid JSON") from exc
    if not isinstance(data, dict):
        raise CanonicalParserError("top_level_not_object", "Canonical reply envelope must be an object")
    if set(data) != {"replies"}:
        raise CanonicalParserError("top_level_fields", "Canonical reply envelope must contain only replies")
    replies = data.get("replies")
    if not isinstance(replies, list) or not replies:
        raise CanonicalParserError("replies_not_list", "Canonical replies must be a non-empty array")
    parsed: list[dict] = []
    for item in replies:
        if not isinstance(item, dict):
            raise CanonicalParserError("reply_not_object", "Canonical reply members must be objects")
        if set(item) != CANONICAL_REPLY_FIELDS:
            raise CanonicalParserError(
                "canonical_reply_fields",
                "Canonical reply fields must match the closed contract",
            )
        if any(not isinstance(item[field], str) for field in CANONICAL_REPLY_FIELDS):
            raise CanonicalParserError("reply_field_type", "Canonical reply fields must all be strings")
        if item["reply_type"] not in VISIBLE_REPLY_TYPES:
            raise CanonicalParserError("reply_type", "Canonical reply_type must be visible")
        parsed.append(dict(item))
    return parsed


def build_parser_shadow_metrics(
    content: str,
    *,
    compatibility_replies: list[dict] | None,
) -> dict[str, object]:
    canonical_replies: list[dict] | None = None
    try:
        canonical_replies = parse_canonical_multi_reply_content(content)
        canonical_code = "accepted"
    except CanonicalParserError as exc:
        canonical_code = exc.code
    compatibility_accepted = bool(compatibility_replies)
    canonical_accepted = canonical_replies is not None
    return {
        "canonical_parse_code": canonical_code,
        "compatibility_parse_code": "accepted" if compatibility_accepted else "rejected",
        "compatibility_used": compatibility_accepted and not canonical_accepted,
        "canonical_match": bool(
            canonical_accepted
            and compatibility_accepted
            and canonical_replies == compatibility_replies
        ),
    }


def normalize_speaker_alias(value: str) -> str:
    return re.sub(r"[\s\[\](){}'\"`·.,:：/\\_-]+", "", (value or "").strip().lower())


def resolve_character_id_from_alias(alias: str, character_id_by_name: dict[str, str]) -> str | None:
    normalized_alias = normalize_speaker_alias(alias)
    if not normalized_alias:
        return None
    normalized_name_map = {
        normalize_speaker_alias(name): character_id
        for name, character_id in character_id_by_name.items()
    }
    if normalized_alias in normalized_name_map:
        return normalized_name_map[normalized_alias]
    if len(normalized_alias) < 2:
        return None
    matches = [
        character_id
        for normalized_name, character_id in normalized_name_map.items()
        if normalized_alias in normalized_name or normalized_name in normalized_alias
    ]
    return matches[0] if len(set(matches)) == 1 else None


def normalize_reply_data(
    data: dict,
    *,
    default_character_id: str | None = None,
    character_id_by_name: dict[str, str] | None = None,
) -> dict:
    normalized = dict(data)
    if not normalized.get("text") and isinstance(normalized.get("dialogue"), str):
        normalized["text"] = normalized["dialogue"]
    for alias_key in ("character_id", "character"):
        alias = str(normalized.get(alias_key) or "").strip()
        if alias and default_character_id and default_character_id in alias:
            normalized["character_id"] = default_character_id
            break
    if character_id_by_name:
        for alias_key in ("speaker_name", "speaker", "character_name", "character", "name"):
            alias = str(normalized.get(alias_key) or "").strip()
            character_id = resolve_character_id_from_alias(alias, character_id_by_name) if alias else None
            if character_id:
                # Treat an explicit speaker/name field as stronger than character_id.
                # Some local/preview models copy the first allowed character_id for
                # every bubble while still emitting the correct speaker name or a
                # short visible alias such as "민지" for "김민지".
                normalized["character_id"] = character_id
                break
    if default_character_id and not normalized.get("character_id"):
        normalized["character_id"] = default_character_id
    for key in ("text", "emotion", "action", "thought"):
        if normalized.get(key) is None:
            normalized[key] = ""
    normalized["reply_type"] = (normalized.get("reply_type") or normalized.get("type") or "character").strip().lower()
    return normalized


def parse_loose_json_string_field(content: str, key: str) -> str | None:
    closed = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', content, flags=re.DOTALL)
    if closed:
        try:
            return json.loads(f'"{closed.group(1)}"')
        except json.JSONDecodeError:
            return closed.group(1)
    # Gemini can stop mid-object/mid-string. Salvage the last unterminated
    # string field instead of saving the whole raw JSON envelope as dialogue.
    open_matches = list(re.finditer(rf'"{key}"\s*:\s*"', content, flags=re.DOTALL))
    if not open_matches:
        return None
    start = open_matches[-1].end()
    raw = content[start:]
    raw = raw.rsplit("\n}", 1)[0].rsplit("\n]", 1)[0]
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw.replace('\\"', '"').replace('\\n', '\n')


def parse_loose_json_fields(content: str) -> dict | None:
    fields: dict[str, str] = {}
    for key in ("character_id", "character_name", "speaker", "speaker_name", "name", "dialogue", "text", "emotion", "action", "thought", "reply_type", "type"):
        value = parse_loose_json_string_field(content, key)
        if value is not None:
            fields[key] = value
    if fields.get("dialogue") or fields.get("text"):
        return fields
    return None


def json_structure_is_complete(content: str) -> bool:
    stack: list[str] = []
    in_string = False
    escaped = False
    saw_structure = False
    matching = {"}": "{", "]": "["}
    for char in content:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
            saw_structure = True
        elif char in "}]":
            saw_structure = True
            if not stack or stack[-1] != matching[char]:
                return False
            stack.pop()
    return not saw_structure or (not stack and not in_string)


def iter_json_candidates(content: str) -> list[dict | list]:
    decoder = json.JSONDecoder()
    candidates: list[dict | list] = []
    try:
        data = json.loads(content)
        if isinstance(data, (dict, list)):
            candidates.append(data)
    except json.JSONDecodeError:
        pass

    if repair_json is not None and content.strip().startswith(("{", "[")):
        try:
            repaired = repair_json(content, return_objects=True)
            if isinstance(repaired, (dict, list)):
                candidates.append(repaired)
        except Exception:
            pass

    for index, char in enumerate(content):
        if char not in "[{":
            continue
        try:
            data, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            if repair_json is None:
                continue
            try:
                data = repair_json(content[index:], return_objects=True)
            except Exception:
                continue
        if isinstance(data, (dict, list)):
            candidates.append(data)
    return candidates


def parse_character_reply_content(content: str) -> dict:
    if not json_structure_is_complete(content):
        raise json.JSONDecodeError("Incomplete JSON structure", content, len(content))
    candidates = [candidate for candidate in iter_json_candidates(content) if isinstance(candidate, dict)]
    for data in candidates:
        if isinstance(data.get("dialogue"), str) and data["dialogue"].strip():
            return data
    for data in candidates:
        if isinstance(data.get("text"), str) and data["text"].strip():
            return data
    if candidates:
        return candidates[0]
    loose = parse_loose_json_fields(content)
    if loose:
        return loose
    raise json.JSONDecodeError("No JSON object found", content, 0)


def parse_multi_reply_content(content: str, *, default_character_id: str | None = None, allowed_character_ids: set[str] | None = None) -> list[dict]:
    if not json_structure_is_complete(content):
        raise CharacterRuntimeError("Structured JSON response was truncated or incomplete")
    candidates = iter_json_candidates(content)
    for data in candidates:
        if isinstance(data, dict) and isinstance(data.get("replies"), list):
            if any(not isinstance(item, dict) for item in data["replies"]):
                raise CharacterRuntimeError("Structured JSON contained a non-object reply item")
            replies = list(data["replies"])
            if replies:
                return replies
        if isinstance(data, list):
            replies = []
            for item in data:
                if not isinstance(item, dict):
                    raise CharacterRuntimeError("Structured JSON contained a non-object reply item")
                nested = item.get("replies")
                if isinstance(nested, list):
                    if any(not isinstance(nested_item, dict) for nested_item in nested):
                        raise CharacterRuntimeError("Structured JSON contained a non-object reply item")
                    for nested_item in nested:
                        reply = dict(nested_item)
                        for key in ("character", "character_id", "character_name", "speaker", "speaker_name", "name"):
                            if item.get(key) and not reply.get(key):
                                reply[key] = item[key]
                        replies.append(reply)
                else:
                    replies.append(item)
            if replies:
                return replies
    try:
        return [parse_character_reply_content(content)]
    except json.JSONDecodeError:
        # The chat contract is structured-only. Treat raw prose as a failed
        # generation so the shared retry/failure path can preserve evidence
        # without ever rendering provider text as a user-visible bubble.
        return []


COMMAND_LITERAL_RE = re.compile(r"(?:^|\s)![^\s.!?。！？,，]+")


def strip_command_literals(value: Any) -> Any:
    if not isinstance(value, str) or "!" not in value:
        return value
    return COMMAND_LITERAL_RE.sub("", value).strip()


def raw_reply_identity(raw: dict, *, character_id_by_name: dict[str, str] | None = None) -> str | None:
    for key in ("character_id", "character"):
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    if character_id_by_name:
        for key in ("speaker_name", "speaker", "character_name", "name"):
            alias = str(raw.get(key) or "").strip()
            character_id = resolve_character_id_from_alias(alias, character_id_by_name) if alias else None
            if character_id:
                return character_id
    return None


def degenerate_repetition_reason(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if re.search(r"(\S)\1{31,}", text):
        return "single-character run"
    if len(text) < 120:
        return None

    sentences = [
        " ".join(part.split()).strip()
        for part in re.split(r"[.!?。！？]+\s*|\n+", value)
        if len(" ".join(part.split()).strip()) >= 4
    ]
    if len(sentences) >= 8:
        repeated_sentence_count = Counter(sentences).most_common(1)[0][1]
        if repeated_sentence_count >= 6 and repeated_sentence_count / len(sentences) >= 0.5:
            return "repeated sentence coverage"

    words = re.findall(r"[가-힣A-Za-z0-9]+", text.lower())
    if len(words) >= 40:
        for width in range(1, min(8, len(words)) + 1):
            counts = Counter(
                tuple(words[index:index + width])
                for index in range(len(words) - width + 1)
            )
            repeated_count = counts.most_common(1)[0][1]
            coverage = repeated_count * width / len(words)
            if repeated_count >= 8 and coverage >= 0.65:
                return f"repeated {width}-word phrase coverage"
    return None


def validate_replies(
    raw_replies: list[dict],
    *,
    default_character_id: str | None = None,
    allowed_character_ids: set[str] | None = None,
    character_id_by_name: dict[str, str] | None = None,
    min_bubbles: int = 1,
    max_bubbles: int = 4,
) -> list[CharacterReply]:
    required_bubbles = max(1, min(max_bubbles, int(min_bubbles)))
    raw_count = len(raw_replies)
    if raw_count < required_bubbles or raw_count > max_bubbles:
        raise CharacterRuntimeError(
            f"Structured JSON returned {raw_count} reply bubbles; required range is {required_bubbles}-{max_bubbles}"
        )

    replies: list[CharacterReply] = []
    multi_character_strict = bool(allowed_character_ids and len(allowed_character_ids) > 1)
    for index, raw in enumerate(raw_replies, start=1):
        raw_type = (raw.get("reply_type") or raw.get("type") or "character").strip().lower()
        if raw_type not in VISIBLE_REPLY_TYPES:
            raise CharacterRuntimeError(
                f"Structured JSON reply {index} used non-visible reply_type={raw_type}"
            )
        internal_fields = sorted(field for field in INTERNAL_EVENT_FIELDS if field in raw)
        if internal_fields:
            raise CharacterRuntimeError(
                f"Structured JSON reply {index} mixed internal event fields into visible reply: {', '.join(internal_fields)}"
            )
        is_storytelling = raw_type == "storytelling"
        explicit_identity = None if is_storytelling else raw_reply_identity(raw, character_id_by_name=character_id_by_name)
        if multi_character_strict and not is_storytelling and not explicit_identity:
            raise CharacterRuntimeError(
                f"Structured JSON reply {index} omitted speaker identity in a multi-character room"
            )
        normalized = normalize_reply_data(
            raw,
            default_character_id=None if multi_character_strict else default_character_id,
            character_id_by_name=character_id_by_name,
        )
        for key in ("text", "dialogue", "action", "thought"):
            if key in normalized:
                normalized[key] = strip_command_literals(normalized[key])
        for field_name in ("dialogue", "text", "emotion", "action", "thought"):
            if field_name not in normalized:
                continue
            control_token_reason = internal_control_token_reason(normalized[field_name])
            if control_token_reason:
                raise CharacterRuntimeError(
                    f"Structured JSON reply {index} {field_name} exposed internal control token: {control_token_reason}"
                )
            repetition_reason = degenerate_repetition_reason(normalized[field_name])
            if repetition_reason:
                raise CharacterRuntimeError(
                    f"Structured JSON reply {index} {field_name} repetition loop detected: {repetition_reason}"
                )
        thought = normalized.get("thought")
        if isinstance(thought, str) and len(thought) > THOUGHT_MAX_CHARS:
            raise CharacterRuntimeError(
                f"Structured JSON reply {index} thought exceeded {THOUGHT_MAX_CHARS} characters; regenerate instead of truncating"
            )
        character_id = normalized.get("character_id") or (None if multi_character_strict else default_character_id)
        normalized["character_id"] = character_id
        if normalized.get("reply_type") == "storytelling":
            normalized["character_id"] = "storyteller"
        elif allowed_character_ids and character_id not in allowed_character_ids:
            if character_id == "mock_character" and default_character_id:
                normalized["character_id"] = default_character_id
            elif not multi_character_strict and default_character_id:
                normalized["character_id"] = default_character_id
            else:
                raise CharacterRuntimeError(
                    f"Structured JSON reply {index} used invalid speaker identity"
                )
        try:
            reply = CharacterReply.model_validate(normalized)
        except ValidationError as exc:
            raise CharacterRuntimeError(
                f"Structured JSON reply {index} failed schema validation"
            ) from exc
        if not reply.text.strip():
            raise CharacterRuntimeError(
                f"Structured JSON reply {index} had empty dialogue"
            )
        replies.append(reply)

    if not any(reply.reply_type != "storytelling" for reply in replies):
        raise CharacterRuntimeError("Structured JSON requires at least one character dialogue reply")
    return replies


def has_character_reply_candidate(raw_replies: list[dict]) -> bool:
    return any((raw.get("reply_type") or raw.get("type") or "character").strip().lower() != "storytelling" for raw in raw_replies)


class CharacterRuntime:
    def __init__(self, llm_client: LLMClient | None = None, fallback_llm_client: LLMClient | None = None):
        self.llm_client = llm_client or LLMClient(profile="chat", purpose="chat_generation")
        self.fallback_llm_client = fallback_llm_client
        self.prompt_generation_graph = build_prompt_generation_graph()

    async def generate_replies(
        self,
        *,
        characters: list[Character],
        recent_messages: list[Message],
        user_message: str,
        scene_state: SceneState | None = None,
        directive: str | None = None,
        conversation_mode: str | None = None,
        genre_mode: str | None = None,
        continuity_context_by_character: dict[str, str] | None = None,
        relationship_context_by_character: dict[str, str] | None = None,
        external_memory_context_by_character: dict[str, str] | None = None,
        offstage_actor_context: str | None = None,
        min_bubbles: int = 1,
        max_bubbles: int = 4,
        prompt_settings: dict[str, str] | None = None,
        min_output_tokens: int | None = None,
        conversation_id: str | None = None,
        battle_control_context: dict[str, object] | None = None,
        official_domain_context: str | None = None,
        room_cast_roles: dict[str, str] | None = None,
        persist_replies: Callable[[list[CharacterReply]], Any | Awaitable[Any]] | None = None,
        source_message_id: str | None = None,
        source_speaker_type: str = "user",
        prompt_snapshot_callback: Callable[[dict], Any | Awaitable[Any]] | None = None,
        prepared_harness: PromptHarness | None = None,
    ) -> list[CharacterReply] | Any:
        if not characters:
            return []
        max_bubbles = max(1, min(8, int(max_bubbles)))
        min_bubbles = max(1, min(max_bubbles, int(min_bubbles)))
        harness = prepared_harness or build_multi_character_prompt_harness(
            characters=characters,
            recent_messages=recent_messages,
            user_message=user_message,
            scene_state=scene_state,
            directive=directive,
            conversation_mode=conversation_mode,
            genre_mode=genre_mode,
            continuity_context_by_character=continuity_context_by_character,
            relationship_context_by_character=relationship_context_by_character,
            external_memory_context_by_character=external_memory_context_by_character,
            offstage_actor_context=offstage_actor_context,
            min_bubbles=min_bubbles,
            max_bubbles=max_bubbles,
            min_output_tokens=min_output_tokens,
            prompt_settings=prompt_settings,
            battle_control_context=battle_control_context,
            official_domain_context=official_domain_context,
            room_cast_roles=room_cast_roles,
            provider_type=provider_name_for_client(self.llm_client),
            context_management_mode=getattr(getattr(self.llm_client, "settings", None), "context_management_mode", "shadow"),
        )
        speaking_characters = [character for character in characters if not is_silent_cast_role((room_cast_roles or {}).get(character.id))]
        output_characters = speaking_characters or characters
        allowed = {character.id for character in output_characters}
        default_character_id = output_characters[0].id
        character_id_by_name = {character.name: character.id for character in output_characters}

        last_llm_response = None
        last_parser_shadow: dict[str, object] = {}
        active_llm_client = self.llm_client
        fallback_used = False
        last_request_messages: list[dict] = []

        async def generate(*, messages, response_format=None, conversation_id=None):
            nonlocal last_llm_response, active_llm_client, fallback_used, last_request_messages
            last_request_messages = [dict(message) for message in messages]
            try:
                last_llm_response = await chat_with_optional_conversation_id(
                    messages=messages,
                    client=active_llm_client,
                    response_format=response_format,
                    conversation_id=conversation_id,
                )
            except LLMUnavailableError:
                if active_llm_client is not self.llm_client or self.fallback_llm_client is None:
                    raise
                active_llm_client = self.fallback_llm_client
                fallback_used = True
                last_llm_response = await chat_with_optional_conversation_id(
                    messages=messages,
                    client=active_llm_client,
                    response_format=response_format,
                    conversation_id=conversation_id,
                )
            return last_llm_response

        def parse_and_validate(content: str) -> list[CharacterReply]:
            nonlocal last_response_content, last_parser_shadow
            last_response_content = content or ""
            last_parser_shadow = build_parser_shadow_metrics(
                last_response_content,
                compatibility_replies=None,
            )
            finish_reason = str(getattr(last_llm_response, "finish_reason", "") or "").strip().upper()
            if not last_response_content.strip():
                raise CharacterRuntimeError("Structured JSON response was empty")
            raw_replies = parse_multi_reply_content(content, default_character_id=default_character_id, allowed_character_ids=allowed)
            last_parser_shadow = build_parser_shadow_metrics(
                last_response_content,
                compatibility_replies=raw_replies,
            )
            replies = validate_replies(
                raw_replies,
                default_character_id=default_character_id,
                allowed_character_ids=allowed,
                character_id_by_name=character_id_by_name,
                min_bubbles=min_bubbles,
                max_bubbles=max_bubbles,
            )
            has_valid_character_reply = any(reply.reply_type != "storytelling" for reply in replies)
            if has_character_reply_candidate(raw_replies) and not has_valid_character_reply:
                raise CharacterRuntimeError("Structured JSON contained character replies without usable dialogue")
            if finish_reason and finish_reason not in SUCCESSFUL_FINISH_REASONS and finish_reason != "MAX_TOKENS":
                raise CharacterRuntimeError(
                    f"Structured JSON generation ended with non-success finish reason {finish_reason}"
                )
            if finish_reason == "MAX_TOKENS":
                last_parser_shadow["accepted_complete_max_tokens"] = True
            if hasattr(active_llm_client, "record_response_outcome"):
                active_llm_client.record_response_outcome(
                    last_llm_response,
                    status="validated",
                    parse_code="json_ok",
                    validation_code="schema_ok_max_tokens" if finish_reason == "MAX_TOKENS" else "schema_ok",
                    metadata_updates=last_parser_shadow,
                )
            return replies

        response_format = build_chat_replies_response_format(
            min_bubbles=min_bubbles,
            max_bubbles=max_bubbles,
        )
        last_error: Exception | None = None
        last_response_content = ""
        result = None
        parse_attempts = 0
        for attempt in range(2):
            parse_attempts = attempt + 1
            attempt_response_format = response_format if attempt == 0 else build_chat_replies_response_format(
                min_bubbles=min_bubbles,
                max_bubbles=max_bubbles,
                retry=True,
            )
            retry_instruction = "" if attempt == 0 else character_retry_instruction(min_bubbles)
            current_turn_role = "assistant" if source_speaker_type == "character" else "user"
            attempt_user_message = user_message
            continuation_instruction = ""
            if current_turn_role == "assistant":
                continuation_instruction = CHARACTER_TURN_CONTINUATION_INSTRUCTION
                if retry_instruction:
                    continuation_instruction = f"{continuation_instruction}\n\n{retry_instruction}"
            elif retry_instruction:
                attempt_user_message = f"{user_message}\n\n{retry_instruction}"
            try:
                result = await run_prompt_generation_pipeline(
                    self.prompt_generation_graph,
                    sections=harness.sections,
                    total_budget_tokens=harness.total_budget_tokens,
                    user_message=attempt_user_message,
                    current_turn_role=current_turn_role,
                    continuation_instruction=continuation_instruction,
                    generate=generate,
                    parse=parse_and_validate,
                    persist=persist_replies,
                    response_format=attempt_response_format,
                    conversation_id=conversation_id,
                )
                break
            except (json.JSONDecodeError, ValidationError, CharacterRuntimeError) as exc:
                last_error = exc
                if hasattr(active_llm_client, "record_response_outcome"):
                    parse_code = "json_decode_error" if isinstance(exc, json.JSONDecodeError) else "structured_parse_error"
                    validation_code = "schema_validation_error" if isinstance(exc, ValidationError) else type(exc).__name__
                    active_llm_client.record_response_outcome(
                        last_llm_response,
                        status="parse_failed",
                        parse_code=parse_code,
                        validation_code=validation_code,
                        metadata_updates=last_parser_shadow,
                    )
                if attempt >= 1:
                    if prompt_snapshot_callback is not None:
                        failure_snapshot = {
                            "conversation_id": conversation_id,
                            "source_message_id": source_message_id,
                            "provider": active_llm_client.provider() if hasattr(active_llm_client, "provider") else getattr(active_llm_client, "provider_name", None),
                            "model": active_llm_client.model() if hasattr(active_llm_client, "model") else getattr(active_llm_client, "model_name", None),
                            "purpose": getattr(active_llm_client, "purpose", "chat_generation"),
                            "compiled_text": harness.compiled_text,
                            "messages": last_request_messages,
                            "ledger": [entry.__dict__ for entry in harness.ledger],
                            "pipeline_steps": ["route_sections", "budget_context", "compile_prompt", "prepare_messages", "generate", "parse_failed"],
                            "used_tokens": harness.used_tokens,
                            "prompt_tokens": getattr(last_llm_response, "prompt_tokens", None),
                            "completion_tokens": getattr(last_llm_response, "completion_tokens", None),
                            "total_tokens": getattr(last_llm_response, "total_tokens", None),
                            "response_preview": last_response_content or f"[parse failed before response capture: {type(exc).__name__}: {exc}]",
                            "metadata": {
                                "finish_reason": getattr(last_llm_response, "finish_reason", None),
                                "response_length": len(last_response_content),
                                "generation_attempts": getattr(last_llm_response, "generation_attempts", 1),
                                "parse_attempts": parse_attempts,
                                "parse_status": "failed",
                                "fallback_used": fallback_used,
                            },
                        }
                        callback_result = prompt_snapshot_callback(failure_snapshot)
                        if inspect.isawaitable(callback_result):
                            await callback_result
                    raise
        if result is None:
            raise CharacterRuntimeError(str(last_error or "generation failed"))
        if prompt_snapshot_callback is not None:
            response = result.get("llm_response")
            snapshot = {
                "conversation_id": conversation_id,
                "source_message_id": source_message_id,
                "provider": getattr(response, "provider", None) or getattr(active_llm_client, "provider_name", None),
                "model": getattr(response, "model", None) or getattr(active_llm_client, "model_name", None),
                "purpose": getattr(active_llm_client, "purpose", "chat_generation"),
                "compiled_text": result.get("compiled_text") or "",
                "messages": result.get("messages") or [],
                "ledger": [entry.__dict__ for entry in (result.get("ledger") or [])],
                "pipeline_steps": result.get("pipeline_steps") or [],
                "used_tokens": result.get("used_tokens"),
                "prompt_tokens": getattr(response, "prompt_tokens", None),
                "completion_tokens": getattr(response, "completion_tokens", None),
                "total_tokens": getattr(response, "total_tokens", None),
                "response_preview": result.get("response_content") or "",
                "metadata": {
                    "finish_reason": getattr(response, "finish_reason", None),
                    "response_length": len(result.get("response_content") or ""),
                    "generation_attempts": getattr(response, "generation_attempts", 1),
                    "parse_attempts": parse_attempts,
                    "parse_status": "success" if parse_attempts == 1 else "retry_success",
                    "fallback_used": fallback_used,
                },
            }
            callback_result = prompt_snapshot_callback(snapshot)
            if inspect.isawaitable(callback_result):
                await callback_result
        if persist_replies is not None and "persisted_messages" in result:
            return result.get("persisted_messages")
        return result.get("parsed_replies") or []

    async def generate_reply(
        self,
        *,
        character: Character,
        recent_messages: list[Message],
        user_message: str,
        scene_state: SceneState | None = None,
        directive: str | None = None,
        conversation_mode: str | None = None,
        genre_mode: str | None = None,
        room_characters: list[Character] | None = None,
        continuity_context: str | None = None,
        prompt_settings: dict[str, str] | None = None,
        min_output_tokens: int | None = None,
        conversation_id: str | None = None,
    ) -> CharacterReply:
        # Backward-compatible wrapper for older call sites/tests. Keep it on
        # the same structured-only parser/validator boundary as room replies
        # so raw provider prose cannot bypass the output firewall.
        base_messages = build_character_messages(
            character=character,
            recent_messages=recent_messages,
            user_message=user_message,
            scene_state=scene_state,
            directive=directive,
            conversation_mode=conversation_mode,
            genre_mode=genre_mode,
            room_characters=room_characters,
            continuity_context=continuity_context,
            prompt_settings=prompt_settings,
            min_output_tokens=min_output_tokens,
            provider_type=provider_name_for_client(self.llm_client),
            context_management_mode=getattr(getattr(self.llm_client, "settings", None), "context_management_mode", "shadow"),
        )
        last_error: Exception | None = None
        active_llm_client = self.llm_client
        for attempt in range(2):
            messages = deepcopy(base_messages)
            if attempt:
                messages[-1] = {
                    **messages[-1],
                    "content": (
                        f"{messages[-1].get('content') or ''}\n\n[System retry instruction]\n"
                        "The previous response was invalid. Return one complete JSON object matching the schema only. "
                        "Do not include markdown fences, explanations, tool calls, internal functions, or raw prose."
                    ),
                }
            try:
                response = await chat_with_optional_conversation_id(
                    messages=messages,
                    client=active_llm_client,
                    response_format={"type": "json_object", "name": "character_chat_reply", "schema": CHARACTER_REPLY_RESPONSE_SCHEMA},
                    conversation_id=conversation_id,
                )
            except LLMUnavailableError:
                if active_llm_client is not self.llm_client or self.fallback_llm_client is None:
                    raise
                active_llm_client = self.fallback_llm_client
                response = await chat_with_optional_conversation_id(
                    messages=messages,
                    client=active_llm_client,
                    response_format={"type": "json_object", "name": "character_chat_reply", "schema": CHARACTER_REPLY_RESPONSE_SCHEMA},
                    conversation_id=conversation_id,
                )
            try:
                finish_reason = str(response.finish_reason or "").strip().upper()
                raw_replies = parse_multi_reply_content(
                    response.content,
                    default_character_id=character.id,
                    allowed_character_ids={character.id},
                )
                replies = validate_replies(
                    raw_replies,
                    default_character_id=character.id,
                    allowed_character_ids={character.id},
                    min_bubbles=1,
                    max_bubbles=1,
                )
                if finish_reason and finish_reason not in SUCCESSFUL_FINISH_REASONS and finish_reason != "MAX_TOKENS":
                    raise CharacterRuntimeError(
                        f"Structured JSON generation ended with non-success finish reason {finish_reason}"
                    )
                if hasattr(active_llm_client, "record_response_outcome"):
                    active_llm_client.record_response_outcome(
                        response,
                        status="validated",
                        parse_code="json_ok",
                        validation_code="schema_ok_max_tokens" if finish_reason == "MAX_TOKENS" else "schema_ok",
                    )
                return replies[0]
            except (json.JSONDecodeError, ValidationError, CharacterRuntimeError) as exc:
                last_error = exc
                if hasattr(active_llm_client, "record_response_outcome"):
                    parse_code = "json_decode_error" if isinstance(exc, json.JSONDecodeError) else "structured_parse_error"
                    validation_code = "schema_validation_error" if isinstance(exc, ValidationError) else type(exc).__name__
                    active_llm_client.record_response_outcome(
                        response,
                        status="parse_failed",
                        parse_code=parse_code,
                        validation_code=validation_code,
                    )
        raise CharacterRuntimeError(str(last_error or "structured generation failed"))
