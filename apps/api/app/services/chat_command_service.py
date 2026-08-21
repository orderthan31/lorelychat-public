from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from sqlalchemy import asc, desc
from sqlmodel import Session, select

from app.db.models import ChatCommand, Character, CharacterMemory, Conversation, ConversationParticipant, ConversationRelationshipState, Message, SceneState, WorldSetting
from app.engine import prompts
from app.engine.llm_client import LLMClient, chat_with_optional_conversation_id
from app.engine.output_guard import internal_control_token_reason
from app.schemas.chat_commands import ChatCommandCreate, ChatCommandUpdate, DEFAULT_POSTPROCESS_CONTEXT_OPTIONS, normalize_command_name

DEFAULT_CHAT_COMMANDS = [
    ChatCommandCreate(
        name="스케줄",
        display_name="스케줄",
        description="일정, 동선, 준비물, 컨디션을 버블 UI로 정리하는 스케줄 연출 커맨드.",
        generation_prompt=(
            "스케줄 커맨드가 활성화되어 있다. 캐릭터의 원래 말투와 관계성은 유지하되, 장면 안에서 일정/동선/준비 상태를 더 의식해 반응해라. "
            "사용자에게 처리해주겠다고 설명하지 말고, 캐릭터가 일정표·스태프 콜·컨디션을 자연스럽게 언급하게 해라."
        ),
        postprocess_prompt=(
            "원본 캐릭터 응답은 보존하고, 스케줄 오버레이 버블 UI를 만들어라. "
            "blocks에는 timeline으로 시간표/동선, checklist로 준비물/확인사항, vote로 컨디션·지각위험·여유도 같은 비율을 반드시 넣어라. "
            "필요하면 note/table/cards도 추가하되 !커맨드 문자열은 절대 언급하지 마라."
        ),
        postprocess_target="last_bubble",
        enabled=True,
        priority=100,
    ),
    ChatCommandCreate(
        name="방송",
        display_name="방송",
        description="라이브 방송 오버레이를 마지막에 별도 버블로 붙이는 기본 방송 커맨드.",
        generation_prompt=(
            "방송 커맨드가 활성화되어 있다. 캐릭터의 대사는 평소 말투와 관계성을 유지하되, 이미 카메라/라이브/무대/송출이 켜진 장면처럼 반응해라. "
            "사용자에게 방송을 보여주겠다고 설명하지 말고, 화면 안 사건처럼 자연스럽게 이어가라."
        ),
        postprocess_prompt=(
            "라이브 방송 오버레이 전용 버블 UI를 만들어라. 기존 캐릭터 대사 버블을 반복하지 말고, 방송 화면 자막/상태/댓글창 레이어만 구성해라. "
            "blocks에는 comments를 반드시 포함하고, 댓글은 댓글작성자/댓글내용이 보이게 만들어라. "
            "댓글에는 장면 반응·팬덤 반응·놀림·응원·오해·채팅창 드립을 섞어라. "
            "필요하면 note 블록으로 방송 상태, table 블록으로 자막/미션/투표/순위 같은 화면 정보를 붙여라. "
            "사용자에게 말을 걸거나 요청을 접수했다는 식의 문장, !커맨드 문자열은 절대 언급하지 마라."
        ),
        postprocess_target="last_bubble",
        enabled=True,
        priority=90,
    ),
    ChatCommandCreate(
        name="방송전체",
        display_name="방송 전체",
        description="각 생성 버블에 확률적으로 라이브 댓글/자막 blocks를 끼워 넣는다.",
        generation_prompt=(
            "방송 전체 커맨드가 활성화되어 있다. 모든 응답 버블이 라이브 송출 중인 장면처럼 자연스럽게 이어지게 하라. "
            "캐릭터의 말투와 관계성은 유지하고, 사용자에게 기능 설명을 하지 마라."
        ),
        postprocess_prompt=(
            "각 대상 캐릭터 버블에 라이브 방송 UI blocks를 추가해라. content는 원본 말투/사건을 유지하고, "
            "blocks에는 comments를 반드시 포함해 시청자 반응·팬덤 드립·오해·응원을 붙여라. 필요하면 note/table도 추가해라."
        ),
        postprocess_target="all_bubbles",
        postprocess_probability=60,
        enabled=True,
        priority=89,
    ),
    ChatCommandCreate(
        name="방송처음",
        display_name="방송 처음",
        description="라이브 방송 오버레이 전용 버블을 생성 버블들 앞에 삽입한다.",
        generation_prompt=(
            "방송 처음 커맨드가 활성화되어 있다. 캐릭터 대사는 이미 라이브 송출이 진행 중인 장면처럼 자연스럽게 이어가라. "
            "오프닝 인사, 시작 멘트, 방송 시작 선언을 새로 만들지 말고 지금 장면의 흐름을 유지해라."
        ),
        postprocess_prompt=(
            "방금 생성된 대화 앞에 놓일 라이브 방송 진행 중 오버레이 전용 버블을 만들어라. "
            "content는 짧은 방송 화면/자막 묘사로 두고, 방송 시작·오프닝·엔딩·종료·마무리·Live Stream Started/Ended 같은 상태 전환은 만들지 마라. "
            "blocks에는 comments와 note를 포함해라."
        ),
        postprocess_target="first_bubble",
        enabled=True,
        priority=88,
    ),
    ChatCommandCreate(
        name="방송마지막",
        display_name="방송 마지막",
        description="라이브 방송 오버레이 전용 버블을 생성 버블들 뒤에 삽입한다.",
        generation_prompt=(
            "방송 마지막 커맨드가 활성화되어 있다. '마지막'은 후처리 오버레이의 삽입 위치만 뜻한다. "
            "캐릭터 대사는 라이브 송출이 계속 진행 중인 장면처럼 자연스럽게 이어가고, 방송을 끝내거나 마무리하지 마라."
        ),
        postprocess_prompt=(
            "방금 생성된 대화 뒤에 놓일 라이브 방송 진행 중 댓글창/상태 오버레이 전용 버블을 만들어라. "
            "'마지막'은 삽입 위치만 뜻하므로 방송 엔딩, 종료 멘트, 마무리 인사, 다음 방송 예고, Live Stream Ended, stream ended 같은 표현은 절대 만들지 마라. "
            "기존 대사를 반복하지 말고, content와 blocks 모두 지금 장면에 대한 실시간 반응·댓글·자막·상태 패널로만 구성해라. "
            "장면에 없는 캐릭터가 실시간 발화하는 것처럼 쓰지 말고, 새 사건·관계 결론·사용자의 다음 행동을 확정하지 마라. "
            "blocks에는 comments를 반드시 포함하고 note/table을 필요에 따라 포함해라."
        ),
        postprocess_target="last_bubble",
        enabled=True,
        priority=87,
    ),
]


@dataclass(frozen=True)
class ParsedChatCommand:
    command: ChatCommand
    args: str
    raw_content: str


def list_chat_commands(session: Session, *, include_disabled: bool = False) -> list[ChatCommand]:
    stmt = select(ChatCommand)
    if not include_disabled:
        stmt = stmt.where(ChatCommand.enabled == True)  # noqa: E712
    stmt = stmt.order_by(desc(ChatCommand.priority), asc(ChatCommand.name))
    return list(session.exec(stmt).all())


def get_chat_command(session: Session, command_id: str) -> ChatCommand | None:
    return session.get(ChatCommand, command_id)


def get_chat_command_by_name(session: Session, name: str, *, enabled_only: bool = False) -> ChatCommand | None:
    normalized = normalize_command_name(name)
    stmt = select(ChatCommand).where(ChatCommand.name == normalized)
    if enabled_only:
        stmt = stmt.where(ChatCommand.enabled == True)  # noqa: E712
    return session.exec(stmt).first()


def create_chat_command(session: Session, payload: ChatCommandCreate) -> ChatCommand:
    name = normalize_command_name(payload.name)
    if get_chat_command_by_name(session, name):
        raise ValueError(f"이미 존재하는 커맨드입니다: !{name}")
    prompt = payload.prompt or payload.postprocess_prompt or payload.generation_prompt
    entry = ChatCommand(
        id=f"cmd_{uuid4().hex[:12]}",
        name=name,
        display_name=payload.display_name or name,
        description=payload.description,
        prompt=prompt,
        generation_prompt=payload.generation_prompt or prompt,
        postprocess_prompt=payload.postprocess_prompt or prompt,
        postprocess_target=payload.postprocess_target,
        postprocess_probability=payload.postprocess_probability,
        postprocess_context_options=list(payload.postprocess_context_options or DEFAULT_POSTPROCESS_CONTEXT_OPTIONS),
        enabled=payload.enabled,
        priority=payload.priority,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def update_chat_command(session: Session, command: ChatCommand, payload: ChatCommandUpdate) -> ChatCommand:
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        next_name = normalize_command_name(data["name"])
        existing = get_chat_command_by_name(session, next_name)
        if existing and existing.id != command.id:
            raise ValueError(f"이미 존재하는 커맨드입니다: !{next_name}")
        command.name = next_name
        if not data.get("display_name"):
            command.display_name = next_name
    for key in ("display_name", "description", "prompt", "generation_prompt", "postprocess_prompt", "postprocess_target", "postprocess_probability", "postprocess_context_options", "enabled", "priority"):
        if key in data and data[key] is not None:
            setattr(command, key, data[key])
    if not command.prompt:
        command.prompt = command.postprocess_prompt or command.generation_prompt
    if not command.generation_prompt and command.prompt:
        command.generation_prompt = command.prompt
    if not command.postprocess_prompt and command.prompt:
        command.postprocess_prompt = command.prompt
    if not command.display_name:
        command.display_name = command.name
    command.updated_at = datetime.now(timezone.utc)
    session.add(command)
    session.commit()
    session.refresh(command)
    return command


def delete_chat_command(session: Session, command: ChatCommand) -> None:
    default_names = {normalize_command_name(payload.name) for payload in DEFAULT_CHAT_COMMANDS}
    if command.name in default_names:
        command.enabled = False
        command.updated_at = datetime.now(timezone.utc)
        session.add(command)
        session.commit()
        return
    session.delete(command)
    session.commit()


def seed_default_chat_commands(session: Session) -> None:
    for payload in DEFAULT_CHAT_COMMANDS:
        existing = get_chat_command_by_name(session, payload.name)
        if existing:
            # Keep user-created non-default commands untouched, but migrate bundled
            # defaults to the split prompt/target schema when the app upgrades.
            existing.display_name = payload.display_name or existing.display_name or existing.name
            existing.description = payload.description or existing.description
            existing.prompt = payload.prompt or payload.postprocess_prompt or payload.generation_prompt or existing.prompt
            existing.generation_prompt = payload.generation_prompt or existing.generation_prompt or existing.prompt
            existing.postprocess_prompt = payload.postprocess_prompt or existing.postprocess_prompt or existing.prompt
            existing.postprocess_target = payload.postprocess_target or existing.postprocess_target or "all_bubbles"
            existing.postprocess_probability = payload.postprocess_probability
            if not existing.postprocess_context_options:
                existing.postprocess_context_options = list(payload.postprocess_context_options or DEFAULT_POSTPROCESS_CONTEXT_OPTIONS)
            existing.priority = payload.priority
            # Preserve user-hidden bundled commands. Otherwise deleting a default
            # command would immediately resurrect it on the next list request.
            existing.updated_at = datetime.now(timezone.utc)
            session.add(existing)
            continue
        create_chat_command(session, payload)
    session.commit()


ALLOWED_COMMAND_BLOCK_TYPES = {"text", "note", "table", "comments", "commentary", "checklist", "timeline", "vote", "cards"}


def compact_block_text(value: object, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def sanitize_command_block(block: object) -> dict | None:
    if not isinstance(block, dict):
        return None
    block_type = compact_block_text(block.get("type"), 40).lower() or "text"
    if block_type == "comment":
        block_type = "comments"
    if block_type not in ALLOWED_COMMAND_BLOCK_TYPES:
        block_type = "text"
    title = compact_block_text(block.get("title"), 80)
    if block_type == "table":
        headers = [compact_block_text(item, 40) for item in (block.get("headers") or []) if compact_block_text(item, 40)][:6]
        rows = []
        for row in block.get("rows") or []:
            if not isinstance(row, list):
                continue
            cells = [compact_block_text(cell, 80) for cell in row[:6]]
            if any(cells):
                rows.append(cells)
            if len(rows) >= 8:
                break
        if not headers and rows:
            headers = [f"항목 {idx + 1}" for idx in range(max(len(row) for row in rows))]
        return {"type": "table", "title": title, "headers": headers, "rows": rows} if rows else None
    if block_type in {"comments", "commentary"}:
        comments = []
        for item in block.get("comments") or block.get("items") or []:
            if isinstance(item, dict):
                author = compact_block_text(item.get("author") or item.get("name") or item.get("user"), 40)
                text = compact_block_text(item.get("text") or item.get("content") or item.get("comment"), 160)
            else:
                author = "시청자"
                text = compact_block_text(item, 160)
            if text:
                comments.append({"author": author or "시청자", "text": text})
            if len(comments) >= 8:
                break
        return {"type": "comments", "title": title or "댓글/반응", "comments": comments} if comments else None
    if block_type == "checklist":
        items = []
        for item in block.get("items") or []:
            if isinstance(item, dict):
                text = compact_block_text(item.get("text") or item.get("content") or item.get("label"), 140)
                checked = bool(item.get("checked") or item.get("done"))
            else:
                text = compact_block_text(item, 140)
                checked = False
            if text:
                items.append({"text": text, "checked": checked})
            if len(items) >= 10:
                break
        return {"type": "checklist", "title": title, "items": items} if items else None
    if block_type == "timeline":
        items = []
        for item in block.get("items") or block.get("events") or []:
            if isinstance(item, dict):
                time = compact_block_text(item.get("time") or item.get("label") or item.get("at"), 40)
                text = compact_block_text(item.get("text") or item.get("content") or item.get("event"), 160)
            else:
                time = ""
                text = compact_block_text(item, 160)
            if text:
                items.append({"time": time, "text": text})
            if len(items) >= 10:
                break
        return {"type": "timeline", "title": title, "items": items} if items else None
    if block_type == "vote":
        options = []
        for item in block.get("options") or block.get("items") or []:
            if isinstance(item, dict):
                label = compact_block_text(item.get("label") or item.get("text") or item.get("name"), 80)
                value = item.get("value") if item.get("value") is not None else item.get("percent")
            else:
                label = compact_block_text(item, 80)
                value = None
            if label:
                options.append({"label": label, "value": value})
            if len(options) >= 8:
                break
        return {"type": "vote", "title": title, "options": options} if options else None
    if block_type == "cards":
        cards = []
        for item in block.get("cards") or block.get("items") or []:
            if isinstance(item, dict):
                card_title = compact_block_text(item.get("title") or item.get("label") or item.get("name"), 80)
                text = compact_block_text(item.get("text") or item.get("content") or item.get("description"), 220)
            else:
                card_title = ""
                text = compact_block_text(item, 220)
            if card_title or text:
                cards.append({"title": card_title, "text": text})
            if len(cards) >= 6:
                break
        return {"type": "cards", "title": title, "cards": cards} if cards else None
    text = compact_block_text(block.get("text") or block.get("content"), 700)
    return {"type": block_type, "title": title, "text": text} if text else None


def extract_json_object(text: str) -> dict | None:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text or ""):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def parse_command_postprocess_response(raw: str, expected_count: int) -> list[dict] | None:
    data = extract_json_object(raw)
    if not data:
        return None
    messages = data.get("messages")
    if not isinstance(messages, list) or len(messages) != expected_count:
        return None
    parsed = []
    for item in messages:
        if not isinstance(item, dict) or internal_control_token_reason(item):
            return None
        content = compact_block_text(item.get("content") or item.get("dialogue"), 2000)
        raw_blocks = item.get("blocks") or []
        if not isinstance(raw_blocks, list) or any(not isinstance(raw_block, dict) for raw_block in raw_blocks):
            return None
        blocks = []
        for raw_block in raw_blocks:
            block = sanitize_command_block(raw_block)
            if block is None:
                return None
            blocks.append(block)
        if not content and not blocks:
            return None
        parsed.append({"content": content, "blocks": blocks})
    return parsed


def command_response_instruction(target_count: int, *, overlay_mode: bool = False) -> str:
    mode_rule = (
        "messages 배열은 후처리 전용 오버레이 버블이다. 기존 캐릭터 대사 전체를 반복하지 말고, postprocess_prompt가 요구한 UI/연출 정보만 담아라."
        if overlay_mode else
        "각 메시지의 content는 원본 응답의 캐릭터 말투/관계/사건을 유지하되, 커맨드 프롬프트가 요구한 연출/형식만 반영해라."
    )
    return f"""반환 규칙: JSON 객체만 반환해라. markdown/code fence 금지.
형식:
{{"messages":[{{"content":"최종 본문","blocks":[{{"type":"note","title":"짧은 제목","text":"보조 정보"}},{{"type":"table","title":"표 제목","headers":["항목","내용"],"rows":[["키","값"]]}},{{"type":"comments","title":"댓글/반응","comments":[{{"author":"이름","text":"내용"}}]}},{{"type":"checklist","title":"체크리스트","items":[{{"text":"할 일","checked":false}}]}},{{"type":"timeline","title":"타임라인","items":[{{"time":"09:00","text":"일정"}}]}},{{"type":"vote","title":"투표","options":[{{"label":"선택지","value":72}}]}},{{"type":"cards","title":"카드 묶음","cards":[{{"title":"카드 제목","text":"카드 내용"}}]}}]}}]}}
messages 배열 길이는 반드시 {target_count}개다.
{mode_rule}
커맨드는 대화 상대에게 설명할 기능명이 아니라 후처리 연출 지시다. 사용자에게 요청을 접수했다거나 처리해주겠다고 직접 말하지 마라.
postprocess_prompt가 comments/timeline/checklist/vote/table/cards/note 같은 blocks를 요구하면 blocks는 필수다. 특히 comments를 요구하면 comments block을 반드시 포함해라.
허용 block type: text, note, table, comments, commentary, checklist, timeline, vote, cards. 알 수 없는 표현은 가장 가까운 허용 타입으로 바꿔라."""


def active_command_ids_for_conversation(conversation) -> list[str]:
    raw_ids = getattr(conversation, "active_command_ids", None) or []
    ids = [str(item) for item in raw_ids if str(item or "").strip()]
    legacy_id = getattr(conversation, "active_command_id", None)
    if legacy_id and legacy_id not in ids:
        ids.insert(0, legacy_id)
    return ids


def set_active_command_ids(session: Session, conversation, command_ids: list[str]) -> None:
    deduped: list[str] = []
    for command_id in command_ids:
        if command_id and command_id not in deduped:
            deduped.append(command_id)
    conversation.active_command_ids = deduped
    conversation.active_command_id = deduped[0] if deduped else None
    session.add(conversation)
    session.commit()


def add_active_command(session: Session, conversation, command: ChatCommand) -> list[str]:
    ids = active_command_ids_for_conversation(conversation)
    if command.id not in ids:
        ids.append(command.id)
    set_active_command_ids(session, conversation, ids)
    return ids


def active_commands_metadata(commands: list[ChatCommand]) -> dict:
    return {
        "active_commands": [command.name for command in commands],
        "active_command_ids": [command.id for command in commands],
        "active_command": commands[0].name if commands else None,
        "active_command_id": commands[0].id if commands else None,
        "command_postprocess_status": "pending",
    }


def active_command_metadata(command: ChatCommand) -> dict:
    return active_commands_metadata([command])


def parse_chat_command(session: Session, content: str) -> ParsedChatCommand | None:
    raw = content or ""
    stripped = raw.strip()
    if not stripped.startswith("!"):
        return None
    body = stripped[1:].lstrip()
    if not body:
        return None
    parts = body.split(maxsplit=1)
    name = normalize_command_name(parts[0])
    args = parts[1].strip() if len(parts) > 1 else ""
    command = get_chat_command_by_name(session, name, enabled_only=True)
    if not command:
        return None
    return ParsedChatCommand(command=command, args=args, raw_content=raw)


def command_metadata(parsed: ParsedChatCommand) -> dict:
    return {
        "command": parsed.command.name,
        "command_id": parsed.command.id,
        "raw_content": parsed.raw_content,
        "command_args": parsed.args,
        "command_postprocess_status": "pending",
    }


def command_probability_allows(command: ChatCommand, message: Message) -> bool:
    probability = max(0, min(100, int(getattr(command, "postprocess_probability", 100) or 0)))
    if probability >= 100:
        return True
    if probability <= 0:
        return False
    seed = f"{command.id}:{message.id}".encode("utf-8")
    value = int(hashlib.sha256(seed).hexdigest()[:8], 16) % 100
    return value < probability


def _command_context_options(command: ChatCommand) -> set[str]:
    raw = getattr(command, "postprocess_context_options", None)
    if not raw:
        raw = DEFAULT_POSTPROCESS_CONTEXT_OPTIONS
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    return {str(item) for item in (raw or []) if str(item).strip()}


def _format_message_line(message: Message, *, character_names: dict[str, str] | None = None) -> str:
    speaker = message.speaker_id or message.speaker_type
    if message.speaker_type == "character" and message.speaker_id and character_names:
        speaker = f"{character_names.get(message.speaker_id, message.speaker_id)}({message.speaker_id})"
    visible = compact_block_text(" ".join(part for part in [message.action, message.content] if part), 320)
    return f"- {message.speaker_type}:{speaker}: {visible}" if visible else ""


def _room_characters_and_roles(session: Session, conversation_id: str) -> tuple[list[Character], dict[str, str]]:
    participants = session.exec(
        select(ConversationParticipant)
        .where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.participant_type == "character",
        )
    ).all()
    characters: list[Character] = []
    roles: dict[str, str] = {}
    for participant in participants:
        character = session.get(Character, participant.participant_id)
        if character:
            characters.append(character)
            if participant.role:
                roles[character.id] = participant.role
    return characters, roles


def _build_scene_context(scene: SceneState | None) -> list[str]:
    if not scene:
        return []
    return [
        "[현재 장면]",
        f"- 장소: {compact_block_text(scene.location, 120)}",
        f"- 분위기: {compact_block_text(scene.mood, 160)}",
        f"- 현재 갈등/주제: {compact_block_text(scene.current_conflict, 220)}",
        f"- 마지막 사건: {compact_block_text(scene.last_event, 260)}",
        f"- 장기 장면 메모리: {compact_block_text(scene.summary, 900)}",
    ]


def _build_world_context(world: WorldSetting | None, scene: SceneState | None) -> list[str]:
    world_seed = compact_block_text((world.world_seed if world else None) or (scene.world_seed if scene else None), 900)
    if not world and not world_seed:
        return []
    lines = ["[세계관]"]
    if world:
        lines.append(f"- 세계관 제목: {compact_block_text(world.title, 120)}")
        if world.description:
            lines.append(f"- 세계관 설명: {compact_block_text(world.description, 360)}")
    if world_seed:
        lines.append(f"- 세계관 핵심 전제 / world_seed: {world_seed}")
    return lines


def _build_characters_context(characters: list[Character], room_cast_roles: dict[str, str]) -> list[str]:
    if not characters:
        return []
    lines = ["[캐릭터]"]
    cards = prompts.format_multi_character_cards(characters, room_cast_roles=room_cast_roles)
    if cards:
        lines.append(cards)
    roles = prompts.format_cast_role_contract(room_cast_roles, characters=characters)
    if roles:
        lines.extend(["역할/말하기 우선순위:", roles])
    return lines


def _build_relationship_memory_context(session: Session, conversation_id: str, characters: list[Character]) -> list[str]:
    if not characters:
        return []
    lines = ["[관계/기억]"]
    added = False
    for character in characters:
        relationship_states = session.exec(
            select(ConversationRelationshipState)
            .where(
                ConversationRelationshipState.conversation_id == conversation_id,
                ConversationRelationshipState.character_id == character.id,
                ConversationRelationshipState.counterpart_type != "system",
            )
            .order_by(ConversationRelationshipState.updated_at.desc())
            .limit(4)
        ).all()
        memories = session.exec(
            select(CharacterMemory)
            .where(
                CharacterMemory.conversation_id == conversation_id,
                CharacterMemory.character_id.in_([character.id, "__room__"]),
                CharacterMemory.memory_type == "user_note",
            )
            .order_by(CharacterMemory.importance.desc(), CharacterMemory.updated_at.desc())
            .limit(4)
        ).all()
        if relationship_states or memories:
            added = True
            lines.append(f"- {character.name}({character.id})")
        for state in relationship_states:
            lines.append(
                "  관계: "
                f"counterpart={state.counterpart_type}:{state.counterpart_id}; "
                f"trust={state.trust_level}; affinity={state.affinity_level}; tension={state.tension_level}; "
                f"conflict={state.conflict_level}; cooperation={state.cooperation_level}; "
                f"mood={compact_block_text(state.current_mood, 80)}; dynamic={compact_block_text(state.current_dynamic, 160)}"
            )
        for memory in memories:
            lines.append(f"  유저노트/{memory.importance}: {compact_block_text(memory.content, 220)}")
    return lines if added else []


def _build_recent_messages_context(session: Session, conversation_id: str, character_names: dict[str, str]) -> list[str]:
    messages = list(reversed(session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(desc(Message.created_at))
        .limit(10)
    ).all()))
    lines = [_format_message_line(message, character_names=character_names) for message in messages]
    lines = [line for line in lines if line]
    return ["[최근 메시지]", *lines] if lines else []


def _build_turn_messages_context(source_message: Message, generated_messages: list[Message], character_names: dict[str, str]) -> list[str]:
    lines = ["[현재 턴 메시지]"]
    source_line = _format_message_line(source_message, character_names=character_names)
    if source_line:
        lines.extend(["유저/source 메시지:", source_line])
    generated_lines = [_format_message_line(message, character_names=character_names) for message in generated_messages]
    generated_lines = [line for line in generated_lines if line]
    if generated_lines:
        lines.append("1차 응답 메시지:")
        lines.extend(generated_lines[:8])
    return lines if len(lines) > 1 else []


def build_command_postprocess_context(session: Session, command: ChatCommand, conversation_id: str, source_message: Message, generated_messages: list[Message]) -> str:
    options = _command_context_options(command)
    conversation = session.get(Conversation, conversation_id)
    scene = session.get(SceneState, conversation_id)
    world = session.get(WorldSetting, conversation.world_setting_id) if conversation and conversation.world_setting_id else None
    characters, room_cast_roles = _room_characters_and_roles(session, conversation_id)
    character_names = {character.id: character.name for character in characters}
    blocks: list[str] = []
    if "scene" in options:
        blocks.extend(_build_scene_context(scene))
    if "world" in options:
        blocks.extend(_build_world_context(world, scene))
    if "characters" in options:
        blocks.extend(_build_characters_context(characters, room_cast_roles))
    if "relationship_memory" in options:
        blocks.extend(_build_relationship_memory_context(session, conversation_id, characters))
    if "recent_messages" in options:
        blocks.extend(_build_recent_messages_context(session, conversation_id, character_names))
    if "turn_messages" in options:
        blocks.extend(_build_turn_messages_context(source_message, generated_messages, character_names))
    return "후속 생성 참고 컨텍스트:\n" + "\n".join(blocks) if blocks else "후속 생성 참고 컨텍스트: 선택된 추가 컨텍스트 없음"


async def apply_chat_command_postprocess(
    *,
    session: Session,
    command: ChatCommand,
    conversation_id: str,
    source_message: Message,
    generated_messages: list[Message],
    llm_client: LLMClient | None = None,
    prompt_settings: dict[str, str] | None = None,
    commit: bool = True,
) -> list[Message]:
    """Apply command postprocess to generated chat bubbles.

    all_bubbles mutates selected generated bubbles. first_bubble/last_bubble keep
    generated bubbles intact and insert one storytelling overlay bubble before/after them.
    """

    def track_changes(*objects: Message) -> None:
        if commit:
            for item in objects:
                session.add(item)

    def persist_changes(*objects: Message) -> None:
        if commit:
            session.commit()
            for item in objects:
                session.refresh(item)

    character_bubbles = [message for message in generated_messages if message.speaker_type == "character" and message.content.strip()]
    if not character_bubbles:
        source_message.metadata_ = {**(source_message.metadata_ or {}), "command_postprocess_status": "skipped_no_character_reply"}
        track_changes(source_message)
        persist_changes(source_message)
        return generated_messages

    target_mode = getattr(command, "postprocess_target", "all_bubbles") or "all_bubbles"
    overlay_mode = target_mode in {"first_bubble", "last_bubble"}
    targets = character_bubbles if overlay_mode else [message for message in character_bubbles if command_probability_allows(command, message)]
    if not targets:
        source_message.metadata_ = {**(source_message.metadata_ or {}), "command_postprocess_status": "skipped_probability"}
        track_changes(source_message)
        persist_changes(source_message)
        return generated_messages

    client = llm_client or LLMClient(profile="chat", purpose="chat_command_postprocess")
    original_block = "\n\n".join(f"[{message.speaker_id}]\n{message.content}" for message in targets)
    expected_count = 1 if overlay_mode else len(targets)
    response_instruction = command_response_instruction(expected_count, overlay_mode=overlay_mode)
    postprocess_prompt = getattr(command, "postprocess_prompt", None) or command.prompt
    postprocess_context = build_command_postprocess_context(session, command, conversation_id, source_message, generated_messages)
    settings = prompt_settings or {}
    system_prompt = "\n\n".join(
        part.strip()
        for part in [
            settings.get("chat_command_postprocess_base", ""),
            settings.get("chat_command_postprocess_source_priority", ""),
        ]
        if part and part.strip()
    )
    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": (
                f"커맨드: !{command.name}\n"
                f"설명: {command.description}\n"
                f"후처리 대상: {target_mode}\n"
                f"postprocess_prompt:\n{postprocess_prompt}\n\n"
                f"{postprocess_context}\n\n"
                f"커맨드 요청/연출 지시:\n{source_message.content}\n\n"
                "대상 생성 버블:\n"
                f"{original_block}\n\n"
                f"{response_instruction}"
            ),
        },
    ]
    try:
        response = await chat_with_optional_conversation_id(messages=messages, client=client, conversation_id=conversation_id)
        command_messages = parse_command_postprocess_response(response.content, expected_count)
        if command_messages is None:
            raise ValueError("command postprocess response did not match the structured messages schema")

        if overlay_mode:
            item = command_messages[0]
            if not item["blocks"]:
                raise ValueError("command overlay response requires at least one renderable block")
            if target_mode == "first_bubble":
                anchor = targets[0]
                overlay_created_at = anchor.created_at - timedelta(milliseconds=1)
            else:
                anchor = generated_messages[-1] if generated_messages else targets[-1]
                overlay_created_at = anchor.created_at + timedelta(milliseconds=1)
            overlay = Message(
                id=f"msg_{uuid4().hex[:12]}",
                conversation_id=conversation_id,
                speaker_type="storytelling",
                speaker_id="command_overlay",
                content="",
                metadata_={
                    "command_applied": command.name,
                    "commands_applied": [command.name],
                    "command_id": command.id,
                    "command_postprocess_status": "applied",
                    "command_blocks": item["blocks"],
                    "command_block_schema": "chat_overlay_v1",
                    "command_overlay": True,
                    "command_overlay_target": target_mode,
                },
                created_at=overlay_created_at,
            )
            track_changes(overlay)
            source_message.metadata_ = {**(source_message.metadata_ or {}), "command_postprocess_status": "applied"}
            track_changes(source_message)
            persist_changes(overlay, source_message)
            return [overlay, *generated_messages] if target_mode == "first_bubble" else [*generated_messages, overlay]

        for message, item in zip(targets, command_messages, strict=True):
            metadata = dict(message.metadata_ or {})
            applied = list(metadata.get("commands_applied") or [])
            if command.name not in applied:
                applied.append(command.name)
            metadata.update({
                "command_applied": command.name,
                "commands_applied": applied,
                "command_id": command.id,
                "pre_command_content": metadata.get("pre_command_content", message.content),
                "command_postprocess_status": "applied",
                "command_blocks": item["blocks"],
                "command_block_schema": "chat_overlay_v1",
                "command_postprocess_target": target_mode,
                "command_postprocess_probability": getattr(command, "postprocess_probability", 100),
            })
            message.content = item["content"] or message.content
            message.metadata_ = metadata
            track_changes(message)
        source_message.metadata_ = {**(source_message.metadata_ or {}), "command_postprocess_status": "applied"}
        track_changes(source_message)
        persist_changes(*targets, source_message)
    except Exception as exc:
        for message in targets:
            metadata = dict(message.metadata_ or {})
            metadata.update({
                "command_applied": command.name,
                "command_id": command.id,
                "command_postprocess_status": "failed",
                "command_postprocess_error": f"{type(exc).__name__}: {str(exc)[:500]}",
            })
            message.metadata_ = metadata
            track_changes(message)
        source_message.metadata_ = {**(source_message.metadata_ or {}), "command_postprocess_status": "failed"}
        track_changes(source_message)
        persist_changes(*targets, source_message)
    return generated_messages
