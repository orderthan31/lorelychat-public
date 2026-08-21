from app.db.models import Character, CharacterAsset, Message, MessageAsset, SceneState
from app.services.asset_selector import MOOD_ALIASES, detect_tags, select_asset_for_message


def add_character(session):
    session.add(Character(id="char_selector", name="선택기", persona="테스트 캐릭터"))
    session.commit()


def add_asset(
    session,
    asset_id,
    *,
    mood_tags=None,
    scene_tags=None,
    outfit_tags=None,
    pose_tags=None,
    expression_tags=None,
    metadata=None,
    priority=50,
):
    asset = CharacterAsset(
        id=asset_id,
        character_id="char_selector",
        label=asset_id,
        image_url=f"/uploads/{asset_id}.png",
        mood_tags=mood_tags or [],
        scene_tags=scene_tags or [],
        outfit_tags=outfit_tags or [],
        pose_tags=pose_tags or [],
        expression_tags=expression_tags or [],
        metadata_=metadata or {},
        priority=priority,
        enabled=True,
    )
    session.add(asset)
    session.commit()
    return asset


def choose(session, *, emotion=None, action=None, content=None, location="작업방", seed="message-1"):
    return select_asset_for_message(
        session,
        conversation_id="conv_selector",
        character_id="char_selector",
        emotion=emotion,
        action=action,
        content=content,
        scene_state=SceneState(conversation_id="conv_selector", location=location),
        selection_seed=seed,
    )


def test_short_korean_substrings_do_not_misclassify_dialogue_as_anger():
    detected = detect_tags("상대방과 영화 이야기를 대화로 이어간다", MOOD_ALIASES)
    assert "angry" not in detected


def test_scene_required_swimwear_is_blocked_without_beach_and_selected_at_beach(session):
    add_character(session)
    add_asset(session, "asset_casual", mood_tags=["happy"], scene_tags=["indoor"], outfit_tags=["casual"])
    add_asset(session, "asset_swim", mood_tags=["happy"], scene_tags=["beach"], outfit_tags=["swimsuit"])

    assert choose(session, emotion="happy", location="작업방").id == "asset_casual"
    assert choose(session, emotion="happy", location="해변", seed="message-2").id == "asset_swim"


def test_bulk_visual_mood_can_match_without_user_outfit_input(session):
    add_character(session)
    add_asset(
        session,
        "asset_bulk",
        mood_tags=["relaxed", "happy"],
        scene_tags=["cafe"],
        outfit_tags=["knitwear", "skirt"],
        pose_tags=["sitting"],
    )

    selected = choose(session, emotion="relaxed", content="오늘은 좀 여유롭네", location="", seed="bulk-message")
    assert selected.id == "asset_bulk"


def test_pose_only_signal_can_select_matching_asset(session):
    add_character(session)
    add_asset(session, "asset_sitting", mood_tags=["relaxed"], pose_tags=["sitting"])
    add_asset(session, "asset_standing", mood_tags=["relaxed"], pose_tags=["standing"])

    selected = choose(session, action="의자에 앉아 사용자를 바라본다", content="응", location="", seed="pose-message")
    assert selected.id == "asset_sitting"


def test_usage_balancing_prefers_less_exposed_relevant_asset(session):
    add_character(session)
    add_asset(session, "asset_popular", mood_tags=["happy"], expression_tags=["smile"])
    add_asset(session, "asset_fresh", mood_tags=["happy"], expression_tags=["smile"])
    for index in range(5):
        session.add(MessageAsset(id=f"link_{index}", message_id=f"old_{index}", asset_id="asset_popular"))
    session.commit()

    selected = choose(session, emotion="happy", location="", seed="balance-message")
    assert selected.id == "asset_fresh"


def test_recent_asset_is_avoided_when_equally_exposed_alternative_exists(session):
    add_character(session)
    add_asset(session, "asset_recent", mood_tags=["happy"])
    add_asset(session, "asset_other", mood_tags=["happy"])
    session.add(Message(
        id="recent_message",
        conversation_id="conv_selector",
        speaker_type="character",
        speaker_id="char_selector",
        content="방금 이미지",
    ))
    session.add(MessageAsset(id="recent_link", message_id="recent_message", asset_id="asset_recent"))
    session.add(MessageAsset(id="other_old_link", message_id="other_old_message", asset_id="asset_other"))
    session.commit()

    selected = choose(session, emotion="happy", location="", seed="recent-balance-message")
    assert selected.id == "asset_other"
