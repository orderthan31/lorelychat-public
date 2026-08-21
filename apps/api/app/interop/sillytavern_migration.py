"""One-way Lorechat -> SillyTavern migration.

The two products keep completely separate stores.  This module writes an
independent bundle from Lorechat's SQLModel session, then applies that bundle
through SillyTavern's official HTTP endpoints plus an explicit copy of image
assets into SillyTavern's own user-images directory.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import http.cookiejar
import json
import mimetypes
from pathlib import Path
import re
import shutil
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from uuid import uuid4

from sqlmodel import Session, select

from app.db.models import (
    BattleMatchRecord,
    BattleStanding,
    Character,
    CharacterAsset,
    CharacterMemory,
    Conversation,
    ConversationParticipant,
    ConversationRelationshipState,
    Message,
    MessageAsset,
    SceneState,
    WorldSetting,
)

BUNDLE_VERSION = 1
SECRET_KEY_RE = re.compile(r"(?:api[_-]?key|authorization|cookie|password|secret|token)", re.IGNORECASE)
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(api[_ -]?key|authorization|password|secret|token)\s*[:=]\s*[^\s,;&]+"
)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _model_data(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _sanitize_export(value: Any) -> Any:
    """Recursively redact credentials and credential-like error fragments."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SECRET_KEY_RE.search(str(key)) else _sanitize_export(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_export(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    return value


def _join_sections(*sections: tuple[str, str | None]) -> str:
    blocks = []
    for title, body in sections:
        cleaned = (body or "").strip()
        if cleaned:
            blocks.append(f"## {title}\n{cleaned}")
    return "\n\n".join(blocks)


def character_to_card(character: Character) -> dict[str, Any]:
    emotional_rules = "\n".join(f"- {item}" for item in character.emotional_rules or [])
    forbidden_rules = "\n".join(f"- {item}" for item in character.forbidden_rules or [])
    traits = ", ".join(f"{key}={value}" for key, value in sorted((character.trait_scores or {}).items()))
    description = _join_sections(
        ("설명", character.description),
        ("외형", character.appearance),
    )
    personality = _join_sections(
        ("인물 설정", character.persona),
        ("행동 방식", character.behavior_style),
        ("성향 수치", traits),
    )
    system_prompt = _join_sections(
        ("말투", character.speech_style),
        ("감정 규칙", emotional_rules),
        ("금지 규칙", forbidden_rules),
    )
    source = _sanitize_export(_model_data(character))
    source["source_id"] = character.id
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": character.name,
            "description": description,
            "personality": personality,
            "scenario": "",
            "first_mes": "",
            "mes_example": "",
            "creator_notes": "Lorechat에서 단방향 이관됨",
            "system_prompt": system_prompt,
            "post_history_instructions": "",
            "alternate_greetings": [],
            "tags": ["Lorechat migrated"],
            "creator": "Lorechat migration",
            "character_version": "1",
            "extensions": {"lorechat": source},
        },
    }


def _world_text(world: WorldSetting | None) -> str:
    if not world:
        return ""
    tags = ", ".join(world.tags or [])
    return _join_sections(
        ("세계관", world.description),
        ("장르", world.genre_mode),
        ("장소", world.location),
        ("분위기", world.mood),
        ("핵심 설정", world.world_seed),
        ("첫 장면", world.opening_scene),
        ("첫 대사", world.opening_line),
        ("톤", world.tone_preset),
        ("관계 구도", world.relationship_archetype),
        ("기억 압축 기준", world.compression_focus),
        ("태그", tags),
    )


def _safe_fs_segment(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", value).strip().strip(".")
    return cleaned or "Lorechat"


def _lore_entry(
    uid: int,
    comment: str,
    content: str,
    *,
    keys: list[str] | None = None,
    constant: bool = True,
    selective: bool = False,
) -> dict[str, Any]:
    return {
        "uid": uid,
        "key": keys or [],
        "keysecondary": [],
        "comment": comment[:100],
        "content": content,
        "constant": constant,
        "selective": selective,
        "order": 100,
        "position": 0,
        "disable": False,
        "displayIndex": uid,
        "addMemo": True,
        "group": "",
        "groupOverride": False,
        "groupWeight": 100,
        "sticky": 0,
        "cooldown": 0,
        "delay": 0,
        "probability": 100,
        "depth": 4,
        "useProbability": True,
        "role": None,
        "vectorized": False,
        "excludeRecursion": False,
        "preventRecursion": False,
        "delayUntilRecursion": False,
        "scanDepth": None,
        "caseSensitive": None,
        "matchWholeWords": None,
        "useGroupScoring": None,
        "automationId": "",
    }


def world_to_lorebook(world: WorldSetting) -> dict[str, Any]:
    content = _world_text(world)
    keys = [world.title, *(world.tags or [])]
    return {
        "name": f"Lorechat · {world.title}",
        "entries": {"0": _lore_entry(0, world.title, content, keys=keys)},
        "extensions": {"lorechat": _sanitize_export(_model_data(world))},
    }


def _named_character(character_by_id: dict[str, Character], character_id: str) -> str:
    character = character_by_id.get(character_id)
    return character.name if character else character_id


def conversation_lorebook(
    conversation: Conversation,
    world: WorldSetting | None,
    scene: SceneState | None,
    memories: list[CharacterMemory],
    relationships: list[ConversationRelationshipState],
    matches: list[BattleMatchRecord],
    standings: list[BattleStanding],
    character_by_id: dict[str, Character],
) -> tuple[str, dict[str, Any]]:
    name = f"Lorechat · {conversation.title or conversation.id} · {conversation.id[-6:]}"
    entries: dict[str, dict[str, Any]] = {}
    uid = 0

    def add(
        comment: str,
        content: str,
        *,
        keys: list[str] | None = None,
        constant: bool = True,
        selective: bool = False,
    ) -> None:
        nonlocal uid
        if not content.strip():
            return
        entries[str(uid)] = _lore_entry(
            uid,
            comment,
            content,
            keys=keys,
            constant=constant,
            selective=selective,
        )
        uid += 1

    add("기본 세계관", _world_text(world))
    if scene:
        add("현재 장면", _join_sections(*[(key, str(value) if value is not None else None) for key, value in _model_data(scene).items() if key not in {"conversation_id", "updated_at", "last_compression_error"}]))
    if memories:
        lines = [
            f"- [{_named_character(character_by_id, item.character_id)} / 중요도 {item.importance}] {item.content}"
            for item in memories
        ]
        add("기억", "\n".join(lines))
    if relationships:
        lines = []
        for item in relationships:
            data = _model_data(item)
            lines.append(
                "- "
                + f"{_named_character(character_by_id, item.character_id)} → {item.counterpart_type}:{item.counterpart_id} | "
                + f"trust={item.trust_level}, affinity={item.affinity_level}, tension={item.tension_level}, "
                + f"conflict={item.conflict_level}, cooperation={item.cooperation_level} | "
                + f"mood={item.current_mood or '-'} | dynamic={item.current_dynamic or '-'} | "
                + f"hooks={', '.join(data.get('unresolved_hooks') or []) or '-'}"
            )
        add("관계 상태", "\n".join(lines))
    if matches or standings:
        lines = []
        for item in matches:
            lines.append(
                f"- match={item.matchup_key} | {_named_character(character_by_id, item.participant_a_id)} vs "
                f"{_named_character(character_by_id, item.participant_b_id)} | status={item.result_status} | "
                f"winner={_named_character(character_by_id, item.winner_id) if item.winner_id else '-'} | "
                f"summary={item.process_summary or '-'} | decisive={item.decisive_moment or '-'}"
            )
        for item in standings:
            lines.append(
                f"- standing={_named_character(character_by_id, item.character_id)} | wins={item.wins}, "
                f"losses={item.losses}, points={item.points}, rank={item.rank or '-'}"
            )
        add("배틀 장부", "\n".join(lines))

    return name, {
        "name": name,
        "entries": entries,
        "extensions": {
            "lorechat": {
                "source_conversation_id": conversation.id,
                "source_world_id": conversation.world_setting_id,
            }
        },
    }


def _escape_markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", "<br>")


def _render_command_blocks(metadata: dict[str, Any] | None) -> str:
    blocks = (metadata or {}).get("command_blocks")
    if not isinstance(blocks, list):
        return ""
    rendered: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "unknown")
        title = str(block.get("title") or "").strip()
        lines = [f"### {title}"] if title else []
        if block_type == "table":
            headers = [str(item) for item in block.get("headers") or []]
            rows = block.get("rows") or []
            if headers:
                lines.append("| " + " | ".join(_escape_markdown_cell(item) for item in headers) + " |")
                lines.append("| " + " | ".join("---" for _ in headers) + " |")
            for row in rows:
                if isinstance(row, list):
                    lines.append("| " + " | ".join(_escape_markdown_cell(item) for item in row) + " |")
        elif block_type == "comments":
            for item in block.get("comments") or []:
                if isinstance(item, dict):
                    lines.append(f"- **{item.get('author') or '익명'}**: {item.get('text') or ''}")
        elif block_type == "note":
            lines.append(str(block.get("text") or ""))
        elif block_type == "vote":
            for item in block.get("options") or []:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('label') or '-'}: **{item.get('value') if item.get('value') is not None else '-'}**")
        elif block_type == "timeline":
            for item in block.get("items") or []:
                if isinstance(item, dict):
                    lines.append(f"- **{item.get('time') or '-'}** — {item.get('text') or ''}")
        elif block_type == "checklist":
            for item in block.get("items") or []:
                if isinstance(item, dict):
                    marker = "x" if item.get("checked") else " "
                    lines.append(f"- [{marker}] {item.get('text') or ''}")
        else:
            lines.append("```json")
            lines.append(json.dumps(_sanitize_export(block), ensure_ascii=False, indent=2, default=_json_default))
            lines.append("```")
        text = "\n".join(line for line in lines if line.strip()).strip()
        if text:
            rendered.append(text)
    return "\n\n".join(rendered)


def _visible_message(message: Message) -> str:
    parts = []
    if (message.action or "").strip():
        parts.append(f"*{message.action.strip()}*")
    if (message.content or "").strip():
        parts.append(message.content.strip())
    command_blocks = _render_command_blocks(message.metadata_)
    if command_blocks:
        parts.append(command_blocks)
    return "\n\n".join(parts)


def _message_to_st(
    message: Message,
    character_by_id: dict[str, Character],
    avatar_by_character: dict[str, str],
    asset_path_by_id: dict[str, str],
    message_asset_ids: dict[str, list[str]],
    user_name: str,
) -> dict[str, Any]:
    source = {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "speaker_type": message.speaker_type,
        "speaker_id": message.speaker_id,
        "content": message.content,
        "action": message.action,
        "thought": message.thought,
        "emotion": message.emotion,
        "metadata": message.metadata_,
        "created_at": message.created_at,
        "asset_ids": message_asset_ids.get(message.id, []),
    }
    speaker_type = message.speaker_type
    is_user = speaker_type == "user"
    is_system = speaker_type == "system"
    if is_user:
        name = user_name
    elif speaker_type == "character":
        name = _named_character(character_by_id, message.speaker_id)
    elif speaker_type == "storytelling":
        name = "Narrator"
    else:
        name = message.speaker_id or "System"

    extra: dict[str, Any] = {
        "api": "manual",
        "lorechat": _sanitize_export(source),
    }
    if speaker_type == "storytelling":
        extra["type"] = "narrator"
    elif is_system:
        extra["type"] = "comment"
        extra["isSmallSys"] = True

    linked_assets = message_asset_ids.get(message.id, [])
    if linked_assets and linked_assets[0] in asset_path_by_id:
        extra["image"] = asset_path_by_id[linked_assets[0]]
        extra["title"] = Path(asset_path_by_id[linked_assets[0]]).name

    result: dict[str, Any] = {
        "name": name,
        "is_user": is_user,
        "is_system": is_system,
        "send_date": message.created_at.isoformat(),
        "mes": _visible_message(message),
        "extra": extra,
    }
    if speaker_type == "character":
        avatar = avatar_by_character.get(message.speaker_id)
        if avatar:
            result["original_avatar"] = avatar
            result["force_avatar"] = f"/thumbnail?type=avatar&file={quote(avatar)}"
        result["swipe_id"] = 0
        result["swipes"] = [result["mes"]]
        result["swipe_info"] = [{"send_date": result["send_date"], "extra": {}}]
    return result


def _conversation_sidecar(
    conversation: Conversation,
    participants: list[ConversationParticipant],
    scene: SceneState | None,
    memories: list[CharacterMemory],
    relationships: list[ConversationRelationshipState],
    matches: list[BattleMatchRecord],
    standings: list[BattleStanding],
) -> dict[str, Any]:
    return _sanitize_export({
        "conversation": _model_data(conversation),
        "participants": [_model_data(item) for item in participants],
        "scene": _model_data(scene) if scene else None,
        "memories": [_model_data(item) for item in memories],
        "relationships": [_model_data(item) for item in relationships],
        "battle_matches": [_model_data(item) for item in matches],
        "battle_standings": [_model_data(item) for item in standings],
    })


def _asset_extension(url: str) -> str:
    extension = Path(urlparse(url).path).suffix.lower()
    return extension if extension in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else ".png"


def export_bundle(
    session: Session,
    output_dir: str | Path,
    *,
    conversation_ids: list[str] | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for subdir in ("characters", "worlds", "chats", "conversations", "assets/files"):
        (output / subdir).mkdir(parents=True, exist_ok=True)

    characters = list(session.exec(select(Character).order_by(Character.name)).all())
    worlds = list(session.exec(select(WorldSetting).order_by(WorldSetting.title)).all())
    conversations_stmt = select(Conversation).order_by(Conversation.created_at, Conversation.id)
    conversations = list(session.exec(conversations_stmt).all())
    if conversation_ids is not None:
        wanted = set(conversation_ids)
        conversations = [item for item in conversations if item.id in wanted]
    conversation_id_set = {item.id for item in conversations}
    messages = list(session.exec(select(Message).order_by(Message.created_at, Message.id)).all())
    messages = [item for item in messages if item.conversation_id in conversation_id_set]
    participants = list(session.exec(select(ConversationParticipant)).all())
    participants = [item for item in participants if item.conversation_id in conversation_id_set]
    scenes = list(session.exec(select(SceneState)).all())
    scenes = [item for item in scenes if item.conversation_id in conversation_id_set]
    memories = list(session.exec(select(CharacterMemory).order_by(CharacterMemory.created_at)).all())
    memories = [item for item in memories if item.conversation_id in conversation_id_set]
    relationships = list(session.exec(select(ConversationRelationshipState)).all())
    relationships = [item for item in relationships if item.conversation_id in conversation_id_set]
    matches = list(session.exec(select(BattleMatchRecord).order_by(BattleMatchRecord.created_at)).all())
    matches = [item for item in matches if item.conversation_id in conversation_id_set]
    standings = list(session.exec(select(BattleStanding)).all())
    standings = [item for item in standings if item.conversation_id in conversation_id_set]
    assets = list(session.exec(select(CharacterAsset).order_by(CharacterAsset.id)).all())
    all_message_assets = list(session.exec(select(MessageAsset).order_by(MessageAsset.display_order)).all())
    message_ids = {item.id for item in messages}
    message_assets = [item for item in all_message_assets if item.message_id in message_ids]

    character_by_id = {item.id: item for item in characters}
    world_by_id = {item.id: item for item in worlds}
    asset_by_id = {item.id: item for item in assets}
    default_asset_by_character = {
        item.character_id: item.id for item in assets if item.is_default
    }
    asset_path_by_id = {
        item.id: (
            f"/user/images/{quote(_safe_fs_segment(_named_character(character_by_id, item.character_id)))}/"
            f"{item.id}{_asset_extension(item.image_url)}"
        )
        for item in assets
    }
    avatar_by_character = {
        item.id: f"lorechat_{item.id}.png" for item in characters
    }

    messages_by_conversation: dict[str, list[Message]] = defaultdict(list)
    participants_by_conversation: dict[str, list[ConversationParticipant]] = defaultdict(list)
    memories_by_conversation: dict[str, list[CharacterMemory]] = defaultdict(list)
    relationships_by_conversation: dict[str, list[ConversationRelationshipState]] = defaultdict(list)
    matches_by_conversation: dict[str, list[BattleMatchRecord]] = defaultdict(list)
    standings_by_conversation: dict[str, list[BattleStanding]] = defaultdict(list)
    scene_by_conversation = {item.conversation_id: item for item in scenes}
    message_asset_ids: dict[str, list[str]] = defaultdict(list)
    for item in messages:
        messages_by_conversation[item.conversation_id].append(item)
    for item in participants:
        participants_by_conversation[item.conversation_id].append(item)
    for values in participants_by_conversation.values():
        values.sort(key=lambda item: (item.order_index if item.order_index is not None else 999, item.participant_id))
    for item in memories:
        memories_by_conversation[item.conversation_id].append(item)
    for item in relationships:
        relationships_by_conversation[item.conversation_id].append(item)
    for item in matches:
        matches_by_conversation[item.conversation_id].append(item)
    for item in standings:
        standings_by_conversation[item.conversation_id].append(item)
    for item in message_assets:
        if item.asset_id in asset_by_id:
            message_asset_ids[item.message_id].append(item.asset_id)

    manifest: dict[str, Any] = {
        "bundle_version": BUNDLE_VERSION,
        "direction": "lorechat_to_sillytavern",
        "storage_policy": "separate_stores_no_shared_database",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "characters": {},
        "worlds": {},
        "conversations": {},
        "counts": {
            "characters": len(characters),
            "worlds": len(worlds),
            "conversations": len(conversations),
            "messages": len(messages),
            "assets": len(assets),
        },
    }

    for character in characters:
        card_path = output / "characters" / f"{character.id}.json"
        _write_json(card_path, character_to_card(character))
        manifest["characters"][character.id] = {
            "name": character.name,
            "card": str(card_path.relative_to(output)),
            "preserved_name": f"lorechat_{character.id}",
            "avatar_asset_id": default_asset_by_character.get(character.id),
        }

    for world in worlds:
        world_name = f"Lorechat · {world.title} · {world.id[-6:]}"
        world_payload = world_to_lorebook(world)
        world_payload["name"] = world_name
        path = output / "worlds" / f"world_{world.id}.json"
        _write_json(path, world_payload)
        manifest["worlds"][world.id] = {
            "name": world_name,
            "file": str(path.relative_to(output)),
        }

    for conversation in conversations:
        convo_participants = participants_by_conversation[conversation.id]
        convo_memories = memories_by_conversation[conversation.id]
        convo_relationships = relationships_by_conversation[conversation.id]
        convo_matches = matches_by_conversation[conversation.id]
        convo_standings = standings_by_conversation[conversation.id]
        scene = scene_by_conversation.get(conversation.id)
        char_ids = [
            item.participant_id
            for item in convo_participants
            if item.participant_type == "character" and item.participant_id in character_by_id
        ]
        lorebook_name, lorebook = conversation_lorebook(
            conversation,
            world_by_id.get(conversation.world_setting_id or ""),
            scene,
            convo_memories,
            convo_relationships,
            convo_matches,
            convo_standings,
            character_by_id,
        )
        lorebook_path = output / "worlds" / f"conversation_{conversation.id}.json"
        _write_json(lorebook_path, lorebook)
        sidecar = _conversation_sidecar(
            conversation,
            convo_participants,
            scene,
            convo_memories,
            convo_relationships,
            convo_matches,
            convo_standings,
        )
        sidecar_path = output / "conversations" / f"{conversation.id}.json"
        _write_json(sidecar_path, sidecar)
        user_ids = [item.participant_id for item in convo_participants if item.participant_type == "user"]
        user_name = conversation.created_by or (user_ids[0] if user_ids else "User")
        primary_name = _named_character(character_by_id, char_ids[0]) if char_ids else (conversation.title or "Lorechat")
        header = {
            "user_name": user_name,
            "character_name": primary_name,
            "create_date": conversation.created_at.isoformat(),
            "chat_metadata": {
                "world_info": lorebook_name,
                "lorechat": sidecar,
            },
        }
        chat = [header]
        chat.extend(
            _message_to_st(
                item,
                character_by_id,
                avatar_by_character,
                asset_path_by_id,
                message_asset_ids,
                user_name,
            )
            for item in messages_by_conversation[conversation.id]
        )
        chat_path = output / "chats" / f"{conversation.id}.jsonl"
        chat_path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False, default=_json_default) for item in chat),
            encoding="utf-8",
        )
        manifest["conversations"][conversation.id] = {
            "title": conversation.title or conversation.id,
            "chat": str(chat_path.relative_to(output)),
            "sidecar": str(sidecar_path.relative_to(output)),
            "lorebook": str(lorebook_path.relative_to(output)),
            "lorebook_name": lorebook_name,
            "character_ids": char_ids,
            "user_name": user_name,
            "kind": "group" if len(char_ids) > 1 else "character",
            "stable_chat_id": f"lorechat_{conversation.id}",
        }

    asset_index = {
        item.id: {
            "character_id": item.character_id,
            "source_url": item.image_url,
            "bundle_file": f"assets/files/{item.id}{_asset_extension(item.image_url)}",
            "st_relative": (
                f"user/images/{_safe_fs_segment(_named_character(character_by_id, item.character_id))}/"
                f"{item.id}{_asset_extension(item.image_url)}"
            ),
            "st_path": asset_path_by_id[item.id],
            "is_default": item.is_default,
            "lorechat": _sanitize_export(_model_data(item)),
        }
        for item in assets
    }
    _write_json(output / "assets" / "index.json", asset_index)
    _write_json(output / "manifest.json", manifest)
    return manifest


class SillyTavernClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.csrf_token = self._get_json("/csrf-token")["token"]

    def _request(self, path: str, *, data: bytes | None = None, content_type: str | None = None) -> bytes:
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["X-CSRF-Token"] = self.csrf_token
            if content_type:
                headers["Content-Type"] = content_type
        request = Request(self.base_url + path, data=data, headers=headers, method="POST" if data is not None else "GET")
        with self.opener.open(request, timeout=180) as response:
            return response.read()

    def _get_json(self, path: str) -> Any:
        raw = self._request(path)
        return json.loads(raw.decode("utf-8"))

    def post_json(self, path: str, payload: Any) -> Any:
        raw = self._request(
            path,
            data=json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8"),
            content_type="application/json",
        )
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def post_multipart(self, path: str, fields: dict[str, str], file_path: Path) -> Any:
        boundary = f"----LorechatMigration{uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ])
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="avatar"; filename="{file_path.name}"\r\n'.encode("utf-8"),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        raw = self._request(
            path,
            data=b"".join(chunks),
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        if not raw:
            return None
        text = raw.decode("utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def import_character(self, card_path: Path, preserved_name: str) -> str:
        result = self.post_multipart(
            "/api/characters/import",
            {"file_type": "json", "preserved_name": preserved_name},
            card_path,
        )
        if not isinstance(result, dict) or not result.get("file_name") or result.get("error"):
            raise RuntimeError(f"SillyTavern character import failed: {card_path.name}: {result}")
        return f"{result['file_name']}.png"

    def edit_character_avatar(self, avatar_file: str, image_path: Path) -> None:
        result = self.post_multipart(
            "/api/characters/edit-avatar",
            {"avatar_url": avatar_file},
            image_path,
        )
        if result not in (None, {}) and isinstance(result, dict) and result.get("error"):
            raise RuntimeError(f"SillyTavern avatar update failed: {avatar_file}: {result}")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _resolve_source_url(source_url: str, source_base_url: str) -> str:
    parsed = urlparse(source_url)
    path = parsed.path if parsed.scheme else source_url
    if path.startswith("/"):
        return source_base_url.rstrip("/") + quote(path, safe="/:%")
    return source_url


def materialize_assets(bundle_dir: str | Path, *, source_base_url: str) -> dict[str, int]:
    bundle = Path(bundle_dir)
    index = json.loads((bundle / "assets" / "index.json").read_text(encoding="utf-8"))
    downloaded = 0
    reused = 0
    failed = 0
    for item in index.values():
        target = bundle / item["bundle_file"]
        if target.exists() and target.stat().st_size > 0:
            reused += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urlopen(_resolve_source_url(item["source_url"], source_base_url), timeout=60) as response:
                target.write_bytes(response.read())
            downloaded += 1
        except Exception as exc:
            item["download_error"] = f"{type(exc).__name__}: {exc}"
            failed += 1
    _write_json(bundle / "assets" / "index.json", index)
    return {"downloaded": downloaded, "reused": reused, "failed": failed}


def apply_bundle(
    bundle_dir: str | Path,
    *,
    st_url: str,
    st_data_root: str | Path,
    source_base_url: str,
) -> dict[str, Any]:
    bundle = Path(bundle_dir)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("direction") != "lorechat_to_sillytavern":
        raise ValueError("Not a Lorechat -> SillyTavern migration bundle")
    asset_result = materialize_assets(bundle, source_base_url=source_base_url)
    asset_index = json.loads((bundle / "assets" / "index.json").read_text(encoding="utf-8"))

    st_root = Path(st_data_root)
    copied_assets = 0
    for item in asset_index.values():
        source = bundle / item["bundle_file"]
        if not source.exists():
            continue
        target = st_root / item["st_relative"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied_assets += 1

    client = SillyTavernClient(st_url)
    avatar_by_character: dict[str, str] = {}
    for character_id, item in manifest["characters"].items():
        avatar_file = client.import_character(bundle / item["card"], item["preserved_name"])
        avatar_by_character[character_id] = avatar_file
        avatar_asset_id = item.get("avatar_asset_id")
        if avatar_asset_id and avatar_asset_id in asset_index:
            image_path = bundle / asset_index[avatar_asset_id]["bundle_file"]
            if image_path.exists():
                client.edit_character_avatar(avatar_file, image_path)

    for item in manifest["worlds"].values():
        client.post_json("/api/worldinfo/edit", {
            "name": item["name"],
            "data": json.loads((bundle / item["file"]).read_text(encoding="utf-8")),
        })
    for item in manifest["conversations"].values():
        client.post_json("/api/worldinfo/edit", {
            "name": item["lorebook_name"],
            "data": json.loads((bundle / item["lorebook"]).read_text(encoding="utf-8")),
        })

    groups = client.post_json("/api/groups/all", {}) or []
    groups_by_chat_id = {str(item.get("chat_id")): item for item in groups if item.get("chat_id")}
    character_chats = 0
    group_chats = 0
    for conversation_id, item in manifest["conversations"].items():
        chat = _read_jsonl(bundle / item["chat"])
        for message in chat[1:]:
            source_id = message.get("extra", {}).get("lorechat", {}).get("speaker_id")
            if source_id in avatar_by_character:
                avatar_file = avatar_by_character[source_id]
                message["original_avatar"] = avatar_file
                message["force_avatar"] = f"/thumbnail?type=avatar&file={quote(avatar_file)}"
        if item["kind"] == "character" and item["character_ids"]:
            avatar_file = avatar_by_character[item["character_ids"][0]]
            client.post_json("/api/chats/save", {
                "avatar_url": avatar_file,
                "file_name": item["stable_chat_id"],
                "chat": chat,
                "force": True,
            })
            character_chats += 1
            continue

        members = [avatar_by_character[char_id] for char_id in item["character_ids"] if char_id in avatar_by_character]
        existing = groups_by_chat_id.get(item["stable_chat_id"])
        group_payload = {
            "name": item["title"],
            "members": members,
            "allow_self_responses": False,
            "activation_strategy": 0,
            "generation_mode": 0,
            "disabled_members": [],
            "fav": False,
            "chat_id": item["stable_chat_id"],
            "chats": [item["stable_chat_id"]],
            "auto_mode_delay": 5,
            "generation_mode_join_prefix": "",
            "generation_mode_join_suffix": "",
        }
        if existing:
            group_payload["id"] = existing["id"]
            client.post_json("/api/groups/edit", group_payload)
        else:
            created = client.post_json("/api/groups/create", group_payload)
            if not isinstance(created, dict) or not created.get("id"):
                raise RuntimeError(f"SillyTavern group creation failed: {conversation_id}: {created}")
            groups_by_chat_id[item["stable_chat_id"]] = created
        client.post_json("/api/chats/group/save", {
            "id": item["stable_chat_id"],
            "chat": chat,
            "force": True,
        })
        group_chats += 1

    return {
        "characters": len(avatar_by_character),
        "worlds": len(manifest["worlds"]),
        "conversation_lorebooks": len(manifest["conversations"]),
        "character_chats": character_chats,
        "group_chats": group_chats,
        "messages": manifest["counts"]["messages"],
        "assets_copied": copied_assets,
        "asset_materialization": asset_result,
        "avatar_by_character": avatar_by_character,
    }


def verify_bundle(
    bundle_dir: str | Path,
    *,
    st_url: str,
    st_data_root: str | Path,
) -> dict[str, Any]:
    """Read migrated records back through ST APIs and compare them with the bundle."""
    bundle = Path(bundle_dir)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    asset_index = json.loads((bundle / "assets" / "index.json").read_text(encoding="utf-8"))
    client = SillyTavernClient(st_url)
    characters = client.post_json("/api/characters/all", {}) or []
    worlds = client.post_json("/api/worldinfo/list", {}) or []
    groups = client.post_json("/api/groups/all", {}) or []

    avatar_names = {item.get("avatar") for item in characters}
    world_names = {item.get("name") for item in worlds}
    group_chat_ids = {str(item.get("chat_id")) for item in groups}
    missing_characters = []
    missing_worlds = []
    missing_groups = []
    chat_mismatches = []
    verified_messages = 0

    for character_id, item in manifest["characters"].items():
        avatar = f"{item['preserved_name']}.png"
        if avatar not in avatar_names:
            missing_characters.append({"source_id": character_id, "avatar": avatar})
    expected_world_names = [item["name"] for item in manifest["worlds"].values()]
    expected_world_names.extend(item["lorebook_name"] for item in manifest["conversations"].values())
    missing_worlds = sorted(name for name in expected_world_names if name not in world_names)

    for conversation_id, item in manifest["conversations"].items():
        expected = _read_jsonl(bundle / item["chat"])
        if item["kind"] == "character" and item["character_ids"]:
            character = manifest["characters"][item["character_ids"][0]]
            actual = client.post_json("/api/chats/get", {
                "avatar_url": f"{character['preserved_name']}.png",
                "file_name": item["stable_chat_id"],
            }) or []
        else:
            if item["stable_chat_id"] not in group_chat_ids:
                missing_groups.append(item["stable_chat_id"])
            actual = client.post_json("/api/chats/group/get", {"id": item["stable_chat_id"]}) or []
        expected_ids = [
            message.get("extra", {}).get("lorechat", {}).get("id")
            for message in expected[1:]
        ]
        actual_ids = [
            message.get("extra", {}).get("lorechat", {}).get("id")
            for message in actual[1:]
        ]
        actual_source = (
            actual[0].get("chat_metadata", {}).get("lorechat", {}).get("conversation", {}).get("id")
            if actual else None
        )
        if len(actual) != len(expected) or actual_ids != expected_ids or actual_source != conversation_id:
            chat_mismatches.append({
                "conversation_id": conversation_id,
                "expected_items": len(expected),
                "actual_items": len(actual),
                "source_id": actual_source,
                "ordered_message_ids_match": actual_ids == expected_ids,
            })
        else:
            verified_messages += len(actual) - 1

    st_root = Path(st_data_root)
    missing_assets = []
    size_mismatches = []
    for asset_id, item in asset_index.items():
        source = bundle / item["bundle_file"]
        target = st_root / item["st_relative"]
        if not target.exists():
            missing_assets.append(asset_id)
        elif source.exists() and source.stat().st_size != target.stat().st_size:
            size_mismatches.append(asset_id)

    embedded_databases = [str(path.relative_to(bundle)) for path in bundle.rglob("*.db")]
    ok = not any((
        missing_characters,
        missing_worlds,
        missing_groups,
        chat_mismatches,
        missing_assets,
        size_mismatches,
        embedded_databases,
    )) and verified_messages == manifest["counts"]["messages"]
    report = {
        "ok": ok,
        "characters_expected": len(manifest["characters"]),
        "characters_found": len(manifest["characters"]) - len(missing_characters),
        "worlds_expected": len(expected_world_names),
        "worlds_found": len(expected_world_names) - len(missing_worlds),
        "conversations_expected": len(manifest["conversations"]),
        "conversations_verified": len(manifest["conversations"]) - len(chat_mismatches),
        "messages_expected": manifest["counts"]["messages"],
        "messages_verified": verified_messages,
        "assets_expected": len(asset_index),
        "assets_verified": len(asset_index) - len(missing_assets) - len(size_mismatches),
        "missing_characters": missing_characters,
        "missing_worlds": missing_worlds,
        "missing_groups": missing_groups,
        "chat_mismatches": chat_mismatches,
        "missing_assets": missing_assets,
        "asset_size_mismatches": size_mismatches,
        "embedded_database_files": embedded_databases,
    }
    if not ok:
        raise RuntimeError(f"SillyTavern migration verification failed: {json.dumps(report, ensure_ascii=False)}")
    return report
