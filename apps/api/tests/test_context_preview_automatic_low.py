from app.core.config import get_settings
from app.db.models import Message
from app.engine.character_runtime import format_runtime_source_message, generation_control_reserve_tokens
from app.engine.prompt_harness import approx_tokens
from app.engine.prompts import format_message_for_context
from app.services import context_preview_service, conversation_service, runtime_settings_service


def test_automatic_compression_preview_selects_nothing_at_low_pressure(
    client,
    session,
    monkeypatch,
):
    character = client.post(
        "/characters",
        json={"name": "세아", "persona": "침착하다."},
    ).json()
    room = client.post(
        "/conversations",
        json={
            "mode": "user_character",
            "participants": [
                {"type": "user", "id": "user_001"},
                {"type": "character", "id": character["id"]},
            ],
        },
    ).json()
    for index in range(4):
        session.add(Message(
            id=f"msg_low_preview_{index}",
            conversation_id=room["id"],
            speaker_type="user" if index % 2 == 0 else "character",
            speaker_id="user_001" if index % 2 == 0 else character["id"],
            content=f"짧은 대화 {index}",
        ))
    session.commit()

    monkeypatch.setenv("CONTEXT_MANAGEMENT_MODE", "automatic")
    get_settings.cache_clear()
    try:
        response = client.get(f"/conversations/{room['id']}/compression-preview")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["selected_recent_message_count"] == 0


def test_automatic_compression_preview_accounts_for_controls_and_character_names(
    client,
    session,
    monkeypatch,
):
    character = client.post(
        "/characters",
        json={"name": "아주 긴 캐릭터 표시 이름", "persona": "침착하다."},
    ).json()
    room = client.post(
        "/conversations",
        json={
            "mode": "user_character",
            "participants": [
                {"type": "user", "id": "user_001"},
                {"type": "character", "id": character["id"]},
            ],
        },
    ).json()
    character_message = Message(
        id="msg_named_preview_character",
        conversation_id=room["id"],
        speaker_type="character",
        speaker_id=character["id"],
        content="이름이 포함된 렌더링",
    )
    session.add(character_message)
    session.add(Message(
        id="msg_named_preview_user",
        conversation_id=room["id"],
        speaker_type="user",
        speaker_id="user_001",
        content="다음 대화를 이어줘",
        action="문을 열고 들어간다",
    ))
    session.commit()

    captured = {}
    original_plan = conversation_service.automatic_context_plan

    def capture_plan(*args, **kwargs):
        captured.update(kwargs)
        return original_plan(*args, **kwargs)

    monkeypatch.setattr(conversation_service, "automatic_context_plan", capture_plan)
    monkeypatch.setenv("CONTEXT_MANAGEMENT_MODE", "automatic")
    get_settings.cache_clear()
    try:
        response = client.get(f"/conversations/{room['id']}/compression-preview")
        user_context_preview = context_preview_service.build_context_preview(session, room["id"])
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    preset = runtime_settings_service.DEFAULT_RESPONSE_LENGTH_PRESET
    control_tokens = generation_control_reserve_tokens(
        speaker_type="user",
        min_bubbles=runtime_settings_service.min_bubbles_for_preset(preset, is_multi_room=False),
        max_bubbles=runtime_settings_service.max_bubbles_for_preset(preset, is_multi_room=False),
    )
    assert captured["mandatory_prompt_tokens"] >= control_tokens
    current_section = next(
        section for section in user_context_preview.sections
        if section.key == "current_user_input"
    )
    assert current_section.content == format_runtime_source_message(
        speaker_type="user",
        speaker_id="user_001",
        content="다음 대화를 이어줘",
        action="문을 열고 들어간다",
    )
    estimator = captured["token_estimator"]
    assert estimator(character_message) == approx_tokens(
        format_message_for_context(
            character_message,
            include_thought=False,
            character_names={character["id"]: character["name"]},
        )
    ) + 1

    continuation = Message(
        id="msg_named_preview_continuation",
        conversation_id=room["id"],
        speaker_type="character",
        speaker_id=character["id"],
        content="내가 먼저 이어 말할게",
        action="고개를 돌린다",
    )
    session.add(continuation)
    session.commit()
    character_context_preview = context_preview_service.build_context_preview(session, room["id"])
    character_section = next(
        section for section in character_context_preview.sections
        if section.key == "current_user_input"
    )
    assert "role=assistant" in character_section.title
    assert character_section.content == format_runtime_source_message(
        speaker_type="character",
        speaker_id=character["id"],
        content=continuation.content,
        action=continuation.action,
        speaker_name=character["name"],
    )
