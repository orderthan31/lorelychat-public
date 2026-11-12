"""Provider-free service contracts: memory DB only; never application lifespan."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (CharacterAsset, Conversation, Message, MessageAsset,
                           ModelOption, ModelProviderAccount, RuntimeSetting, SceneState)
from app.services import asset_decision_service as service
from app.services import decision_transport as transport
from app.services.hardening_core_transactions import isolated_mock_execution


@pytest.fixture
def fixture(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(llm_mock=False))
    monkeypatch.setattr(service.provider_secret_service, "get_secret", lambda ref: "synthetic-key")
    calls = []

    async def decide(route, *, context, questions):
        calls.append((route, context, questions))
        return {q.id: next(k for k in q.candidates if k != "none") for q in questions}

    monkeypatch.setattr(transport, "decide_assets", decide)
    with Session(engine) as session:
        session.add_all([
            Conversation(id="room", mode="single"),
            Message(id="m1", conversation_id="room", speaker_type="character", speaker_id="char", content="A distant bell tolls."),
            SceneState(conversation_id="room", location="", mood=""),
            RuntimeSetting(id="global", decision_enabled=True, decision_model_option_key="decision"),
            ModelProviderAccount(id="account", provider_type="openai", alias="test", configured=True, api_key_secret_ref="fake"),
            ModelOption(id="option", key="decision", provider_account_id="account", provider_type="openai", model="test", label="test", supports_decision=True),
            CharacterAsset(id="a1", character_id="char", label="Reflective", description="A thoughtful pause", image_url="/uploads/private.png", mood_tags=["happy"]),
        ])
        session.commit()
        yield session, engine, calls
    engine.dispose()


def run(fixture, ids=None):
    return asyncio.run(service.select_turn_assets(fixture[0], conversation_id="room", message_ids=ids or ["m1"], model_option_key="decision"))


def test_on_semantic_choice_has_no_lexical_requirement(fixture):
    session, _, calls = fixture
    asset = session.get(CharacterAsset, "a1")
    asset.mood_tags = ["sad"]
    session.commit()
    assert run(fixture) == [("m1", "a1")]
    assert len(calls) == 1
    payload = json.dumps(calls[0][1]) + json.dumps(calls[0][2][0].candidates)
    assert "/uploads/" not in payload
    assert "A distant bell tolls." in payload
    assert session.exec(select(MessageAsset)).all() == []


@pytest.mark.parametrize("mode", ["off", "llm_mock", "isolated", "missing_setting"])
def test_off_and_mock_paths_use_legacy_without_request(fixture, monkeypatch, mode):
    session, _, calls = fixture
    token = None
    if mode == "off":
        session.get(RuntimeSetting, "global").decision_enabled = False
    elif mode == "missing_setting":
        session.delete(session.get(RuntimeSetting, "global"))
    elif mode == "llm_mock":
        monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(llm_mock=True))
    else:
        token = isolated_mock_execution.set(True)
    session.commit()
    try:
        assert service.decision_model_key_for_turn(session, "room") is None
        assert run(fixture) == [("m1", "a1")]
        assert not calls
        if mode == "missing_setting":
            assert session.get(RuntimeSetting, "global") is None
    finally:
        if token is not None:
            isolated_mock_execution.reset(token)


def test_explicit_none_does_not_fall_back(fixture, monkeypatch):
    async def decide(*args, **kwargs):
        return {"m1": None}
    monkeypatch.setattr(transport, "decide_assets", decide)
    assert run(fixture) == []


@pytest.mark.parametrize("answer", [{}, {"unknown": "a1"}, {"m1": "unknown"}, {"m1": "none"}, {"m1": []}, None])
def test_malformed_whole_answer_falls_back(fixture, monkeypatch, answer):
    async def decide(*args, **kwargs):
        return answer
    monkeypatch.setattr(transport, "decide_assets", decide)
    assert run(fixture) == [("m1", "a1")]


def test_error_falls_back_once(fixture, monkeypatch):
    calls = []
    async def decide(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("private provider error must not escape")
    monkeypatch.setattr(transport, "decide_assets", decide)
    assert run(fixture) == [("m1", "a1")]
    assert calls == [1]


@pytest.mark.parametrize("invalid", ["disabled", "wrong_owner", "bad_tags", "bad_metadata", "video", "scene_required"])
def test_invalid_assets_cannot_be_candidates_or_fallback(fixture, invalid):
    session, _, calls = fixture
    asset = session.get(CharacterAsset, "a1")
    if invalid == "disabled":
        asset.enabled = False
    elif invalid == "wrong_owner":
        asset.character_id = "other"
    elif invalid == "bad_tags":
        asset.mood_tags = {"bad": "value"}
    elif invalid == "bad_metadata":
        asset.metadata_ = ["bad"]
    elif invalid == "video":
        asset.asset_type = "video"
    else:
        asset.scene_tags = ["beach"]
        asset.outfit_tags = ["swimsuit"]
    session.commit()
    assert run(fixture) == []
    assert calls == []


def test_duplicate_model_choices_not_attached_twice(fixture):
    session, _, calls = fixture
    session.add(Message(id="m2", conversation_id="room", speaker_type="character", speaker_id="char", content="Echo."))
    session.commit()
    assert run(fixture, ["m1", "m2"]) == [("m1", "a1")]
    assert len(calls[0][2]) == 2


def test_bounded_candidates_and_legacy_tail(fixture):
    session, _, calls = fixture
    for index in range(35):
        session.add(CharacterAsset(id=f"extra{index:02d}", character_id="char", label="happy", image_url="x", mood_tags=["happy"]))
    ids = ["m1"]
    for index in range(1, 30):
        mid = f"m{index + 1}"
        ids.append(mid)
        session.add(Message(id=mid, conversation_id="room", speaker_type="character", speaker_id="char", content="Hello."))
    session.commit()
    result = run(fixture, ids)
    assert len(calls) == 1
    assert 1 <= len(calls[0][2]) <= 24
    assert all(len(q.candidates) == 24 and "none" in q.candidates for q in calls[0][2])
    decided_ids = {q.id for q in calls[0][2]}
    assert {mid for mid, _ in result}.issuperset(set(ids) - decided_ids)
    _, _, _, _, payload = transport._request(calls[0][0], calls[0][1], calls[0][2])
    assert len(payload.decode("utf-8")) <= service.MAX_REQUEST_CHARS
    assert len({aid for _, aid in result}) == len(result)


@pytest.mark.parametrize("change", ["disabled", "deleted", "unsupported", "capability", "missing_secret", "missing_base", "model_provider_mismatch", "configured"])
def test_inactive_route_no_request(fixture, monkeypatch, change):
    session, _, calls = fixture
    account = session.get(ModelProviderAccount, "account")
    option = session.get(ModelOption, "option")
    if change == "disabled":
        account.enabled = False
    elif change == "deleted":
        session.delete(account)
    elif change == "unsupported":
        account.provider_type = option.provider_type = "future-provider"
    elif change == "capability":
        option.supports_decision = False
    elif change == "missing_secret":
        monkeypatch.setattr(service.provider_secret_service, "get_secret", lambda ref: None)
    elif change == "missing_base":
        account.provider_type = option.provider_type = "openai_compatible"
    elif change == "configured":
        account.configured = False
    else:
        account.provider_type = "google"
    session.commit()
    assert service.decision_model_key_for_turn(session, "room") is None
    assert run(fixture) == [("m1", "a1")]
    assert calls == []


@pytest.mark.parametrize("change", ["content", "settings", "scene", "asset", "model", "account", "delete", "manual"])
def test_network_has_no_transaction_and_stale_turn_is_discarded(fixture, monkeypatch, change):
    session, engine, _ = fixture
    async def decide(*args, **kwargs):
        assert not session.in_transaction()
        with Session(engine) as writer:
            if change == "content":
                writer.get(Message, "m1").content = "Edited"
            elif change == "settings":
                writer.get(RuntimeSetting, "global").decision_enabled = False
            elif change == "scene":
                writer.get(SceneState, "room").location = "beach"
            elif change == "asset":
                writer.get(CharacterAsset, "a1").enabled = False
            elif change == "model":
                writer.get(ModelOption, "option").model = "changed"
            elif change == "account":
                writer.get(ModelProviderAccount, "account").api_key_secret_ref = "changed"
            elif change == "delete":
                writer.delete(writer.get(Message, "m1"))
            else:
                writer.add(MessageAsset(id="manual", message_id="m1", asset_id="a1"))
            writer.commit()
        return {"m1": "a1"}
    monkeypatch.setattr(transport, "decide_assets", decide)
    assert run(fixture) == []
    if change == "manual":
        assert session.get(MessageAsset, "manual").asset_id == "a1"


def test_newer_turn_and_arc_only_update_do_not_invalidate_committed_source(fixture, monkeypatch):
    from datetime import datetime, timedelta, timezone
    session, engine, _ = fixture
    async def decide(*args, **kwargs):
        assert not session.in_transaction()
        with Session(engine) as writer:
            writer.add(Message(id="newer", conversation_id="room", speaker_type="user", speaker_id="u", content="Newer turn"))
            writer.get(Conversation, "room").updated_at = datetime.now(timezone.utc) + timedelta(seconds=1)
            writer.get(SceneState, "room").summary = "Updated Arc, same visible scene"
            writer.get(SceneState, "room").compression_revision = 1
            writer.commit()
        return {"m1": "a1"}
    monkeypatch.setattr(transport, "decide_assets", decide)
    assert run(fixture) == [("m1", "a1")]
    assert session.get(Message, "newer").content == "Newer turn"


def test_entire_candidate_payload_is_bounded_not_only_shared_input(fixture):
    session, _, calls = fixture
    ids = ["m1"]
    for index in range(30):
        session.add(CharacterAsset(id=f"large{index:02d}", character_id="char", label="A" * 80,
            description="D" * 320, image_url="x", mood_tags=["happy"], scene_tags=["indoor"],
            pose_tags=["standing"], outfit_tags=["casual"], expression_tags=["happy"]))
        mid = f"large-message-{index}"
        ids.append(mid)
        session.add(Message(id=mid, conversation_id="room", speaker_type="character", speaker_id="char", content="Hello"))
    session.commit()
    run(fixture, ids)
    assert len(calls) == 1
    route, context, questions = calls[0]
    assert len(questions) < service.MAX_QUESTIONS
    _, _, _, _, payload = transport._request(route, context, questions)
    assert len(payload.decode("utf-8")) <= service.MAX_REQUEST_CHARS


def test_manual_link_untouched_without_request(fixture):
    session, _, calls = fixture
    session.add(MessageAsset(id="manual", message_id="m1", asset_id="a1"))
    session.commit()
    assert run(fixture) == []
    assert calls == []
    assert session.get(MessageAsset, "manual").asset_id == "a1"


@pytest.mark.parametrize("ids", [["missing"], ["m1", "missing"]])
def test_missing_messages_invalidate_whole_turn(fixture, ids):
    assert run(fixture, ids) == []
    assert fixture[2] == []


def test_clean_session_required_but_key_lookup_never_flushes(fixture):
    session, _, _ = fixture
    message = Message(id="pending", conversation_id="room", speaker_type="character", speaker_id="char", content="Pending")
    session.add(message)
    assert service.decision_model_key_for_turn(session, "room") == "decision"
    assert message in session.new
    with pytest.raises(ValueError, match="requires_clean_session"):
        run(fixture)
    assert message in session.new


def test_no_source_writes_or_commit(fixture, monkeypatch):
    session, engine, _ = fixture
    def forbidden_commit():
        raise AssertionError("service must not commit")
    monkeypatch.setattr(session, "commit", forbidden_commit)
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert run(fixture) == [("m1", "a1")]
        assert set(statements) <= {"SELECT"}
        assert not session.new and not session.dirty and not session.deleted
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_context_bounds_and_sensitive_fields_omitted(fixture):
    session, _, calls = fixture
    message = session.get(Message, "m1")
    message.content = "c" * 20_000
    message.action = "a" * 20_000
    message.emotion = "e" * 20_000
    asset = session.get(CharacterAsset, "a1")
    asset.generation_prompt = "PRIVATE_PROMPT_SENTINEL"
    asset.description = "https://private.invalid/image " + "d" * 10_000
    session.commit()
    assert run(fixture) == [("m1", "a1")]
    context = calls[0][1]
    assert len(json.dumps(context, ensure_ascii=False)) <= 12_000
    assert all(len(context["messages"][0][field]) <= 3000 for field in ("content", "action", "emotion"))
    serialized = json.dumps(context) + json.dumps(calls[0][2][0].candidates)
    assert "PRIVATE_PROMPT_SENTINEL" not in serialized and "https://private" not in serialized


def test_timeout_uses_legacy_and_cancels(fixture, monkeypatch):
    cancelled = []
    async def decide(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
    monkeypatch.setattr(service, "TOTAL_TIMEOUT", 0.001)
    monkeypatch.setattr(transport, "decide_assets", decide)
    assert run(fixture) == [("m1", "a1")]
    assert cancelled == [True]


def test_room_off_overrides_global_on_without_creating_settings(fixture):
    session, _, calls = fixture
    session.add(RuntimeSetting(id="room", conversation_id="room", decision_enabled=False))
    session.commit()
    assert service.decision_model_key_for_turn(session, "room") is None
    assert run(fixture) == [("m1", "a1")]
    assert not calls


@pytest.mark.parametrize("base", ["http://remote.invalid/v1", "https://host/v1?secret=x", "https://user:pass@host/v1", "https://[broken", " https://host/v1"])
def test_invalid_route_base_never_calls_transport(fixture, base):
    session, _, calls = fixture
    session.get(ModelProviderAccount, "account").base_url = base
    session.commit()
    assert service.decision_model_key_for_turn(session, "room") is None
    assert run(fixture) == [("m1", "a1")]
    assert not calls


def test_keyless_loopback_route_uses_empty_string_secret(fixture, monkeypatch):
    session, _, calls = fixture
    account = session.get(ModelProviderAccount, "account")
    account.provider_type = "openai_compatible"
    account.base_url = "http://127.0.0.1:1234/v1"
    account.preset = "lm_studio"
    session.get(ModelOption, "option").provider_type = "openai_compatible"
    session.commit()
    monkeypatch.setattr(service.provider_secret_service, "get_secret", lambda ref: None)
    assert run(fixture) == [("m1", "a1")]
    assert calls[0][0].api_key == "" and calls[0][0].keyless_local


def test_fallback_is_original_selector_seed_and_exclusions(fixture, monkeypatch):
    session, _, _ = fixture
    session.add(CharacterAsset(id="a2", character_id="char", label="Second", image_url="x", mood_tags=["happy"]))
    session.add(Message(id="m2", conversation_id="room", speaker_type="character", speaker_id="char", content="Echo."))
    session.commit()
    scene = session.get(SceneState, "room")
    expected = []
    excluded = set()
    for mid in ["m1", "m2"]:
        message = session.get(Message, mid)
        selected = service.asset_selector.select_asset_for_message(
            session, conversation_id="room", character_id="char", emotion=message.emotion,
            action=message.action, content=message.content, scene_state=scene,
            selection_seed=message.id, excluded_asset_ids=excluded,
        )
        if selected:
            expected.append((mid, selected.id))
            excluded.add(selected.id)
    async def unavailable(*args, **kwargs):
        raise transport.DecisionUnavailable()
    monkeypatch.setattr(transport, "decide_assets", unavailable)
    assert run(fixture, ["m1", "m2"]) == expected


def test_malformed_one_question_falls_back_entire_turn(fixture, monkeypatch):
    session, _, _ = fixture
    session.add(CharacterAsset(id="a2", character_id="char", label="Second", image_url="x", mood_tags=["happy"]))
    session.add(Message(id="m2", conversation_id="room", speaker_type="character", speaker_id="char", content="Echo."))
    session.commit()
    async def partial(*args, **kwargs):
        return {"m1": None, "m2": "not-in-list"}
    monkeypatch.setattr(transport, "decide_assets", partial)
    result = run(fixture, ["m1", "m2"])
    assert len(result) == 2 and {mid for mid, _ in result} == {"m1", "m2"}


def test_error_after_source_mutation_discards_instead_of_legacy(fixture, monkeypatch):
    session, engine, _ = fixture
    async def unavailable(*args, **kwargs):
        with Session(engine) as writer:
            writer.get(Message, "m1").content = "Edited while provider failed"
            writer.commit()
        raise transport.DecisionUnavailable()
    monkeypatch.setattr(transport, "decide_assets", unavailable)
    assert run(fixture) == []
