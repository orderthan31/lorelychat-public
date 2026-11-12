"""Optional, read-only asset decisions with a detached optimistic snapshot.

The caller owns link insertion and the outbox lease CAS in the same transaction.
No provider result is authoritative until the complete source snapshot is re-read.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit

from sqlalchemy import func
from sqlmodel import Session, select, col

from app.core.config import get_settings
from app.db.models import (
    CharacterAsset, Conversation, Message, MessageAsset, ModelOption,
    ModelProviderAccount, PostCommitTask, RuntimeSetting, SceneState,
)
from app.services import asset_selector, model_provider_service, provider_secret_service
from app.services.hardening_core_transactions import isolated_mock_execution

MAX_QUESTIONS = 24
MAX_CANDIDATES = 23
MAX_CONTEXT = 12_000
MAX_REQUEST_CHARS = 48_000
TOTAL_TIMEOUT = 8.0
_TAG_FIELDS = ("tags", "mood_tags", "expression_tags", "scene_tags", "pose_tags", "outfit_tags")
_PROVIDERS = {"openai", "google", "openrouter", "openai_compatible"}


def _mock() -> bool:
    return isolated_mock_execution.get() or get_settings().llm_mock


def _route(session: Session, key: str):
    # Lazy import keeps the read-only key resolver independent of transport setup.
    from app.services.decision_transport import DecisionRoute

    option = session.exec(select(ModelOption).where(ModelOption.key == key)).first()
    if not option or option.deleted_at or not option.enabled or not option.supports_decision:
        return None
    account = session.get(ModelProviderAccount, option.provider_account_id)
    if (not account or account.deleted_at or not account.enabled or not account.configured
            or account.provider_type not in _PROVIDERS
            or account.provider_type != option.provider_type or not option.model.strip()):
        return None
    base = account.base_url
    if account.provider_type == "openai_compatible" and not base:
        return None
    local = False
    if base:
        try:
            parsed = urlsplit(base)
            local = parsed.hostname == "localhost"
            try:
                local = local or ipaddress.ip_address(parsed.hostname or "").is_loopback
            except ValueError:
                pass
            if (base != base.strip() or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment
                    or not (parsed.scheme == "https" or (parsed.scheme == "http" and local))
                    or any(ord(c) < 32 or ord(c) == 127 for c in base)):
                return None
        except ValueError:
            return None
    try:
        secret = provider_secret_service.get_secret(account.api_key_secret_ref)
    except Exception:
        return None  # Optional credential store failure; never expose its exception.
    keyless = local and model_provider_service.keyless_local_allowed(account)
    if (not isinstance(secret, (str, type(None)))
            or (secret and any(ord(c) < 32 or ord(c) == 127 for c in secret))
            or (not (secret or "").strip() and not keyless)):
        return None
    modalities = getattr(option, "input_modalities", None)
    mime_types = getattr(option, "image_mime_types", None)
    modalities = tuple(modalities) if isinstance(modalities, list) and all(isinstance(v, str) for v in modalities) else ()
    mime_types = tuple(mime_types) if isinstance(mime_types, list) and all(isinstance(v, str) for v in mime_types) else ()
    return DecisionRoute(provider_type=account.provider_type, model=option.model,
                         api_key=secret or "", base_url=base, keyless_local=keyless,
                         input_modalities=modalities, image_mime_types=mime_types)


def decision_model_key_for_turn(session: Session, conversation_id: str) -> str | None:
    """Read existing settings only; safe inside a finalizer with pending replies."""
    if _mock():
        return None
    with session.no_autoflush:
        setting = session.get(RuntimeSetting, conversation_id) or session.get(RuntimeSetting, "global")
        key = setting.decision_model_option_key if setting and setting.decision_enabled else None
        return key if key and _route(session, key) is not None else None


def decision_turn_allowed(session: Session, conversation_id: str) -> bool:
    """Two completed generated turns between optional checks, even after none.

    No prose is read and no state is written. Existing/manual images also start
    the quiet window. OFF never calls this policy or changes the old selector.
    """
    with session.no_autoflush:
        recent_turns = list(session.exec(select(Message.generation_job_id).where(
            Message.conversation_id == conversation_id,
            col(Message.generation_job_id).is_not(None),
        ).group_by(Message.generation_job_id).order_by(
            func.max(Message.created_at).desc(), func.max(Message.id).desc(),
        ).limit(2)).all())
        if not recent_turns:
            return True
        image = session.exec(select(MessageAsset.id).join(Message, Message.id == MessageAsset.message_id).where(
            Message.conversation_id == conversation_id,
            col(Message.generation_job_id).in_(recent_turns),
        ).limit(1)).first()
        if image is not None:
            return False
        last_task = session.exec(select(PostCommitTask).where(
            PostCommitTask.task_type == "asset_decision",
            PostCommitTask.payload_["conversation_id"].as_string() == conversation_id,
        ).order_by(PostCommitTask.created_at.desc(), PostCommitTask.id.desc()).limit(1)).first()
        if last_task:
            payload = last_task.payload_ if isinstance(last_task.payload_, dict) else {}
            checked_turn = payload.get("turn_id")
            if not checked_turn:
                ids = payload.get("message_ids")
                prior = session.get(Message, ids[0]) if isinstance(ids, list) and ids and isinstance(ids[0], str) else None
                checked_turn = prior.generation_job_id if prior and prior.conversation_id == conversation_id else None
            if checked_turn in recent_turns:
                return False
    return True


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _row(row):
    # ORM fields, not Pydantic serialization: malformed optional JSON must not
    # emit serializer warnings containing private raw values.
    return {name: getattr(row, name) for name in type(row).model_fields} if row is not None else None


def _eligible(asset: CharacterAsset, message: Message, scene: SceneState | None) -> bool:
    if not asset.enabled or asset.asset_type != "image" or asset.character_id != message.speaker_id:
        return False
    if any(not isinstance(getattr(asset, field), list)
           or any(not isinstance(tag, str) for tag in getattr(asset, field)) for field in _TAG_FIELDS):
        return False
    policy = asset_selector.asset_selection_policy(asset)
    if policy is None:
        return False
    if policy.get("scope") == "scene_required":
        scenes = asset_selector.detect_tags(
            " ".join(v or "" for v in (scene.location if scene else None, message.action, message.content)).lower(),
            asset_selector.SCENE_ALIASES,
        )
        if not scenes & asset_selector.tagset(policy.get("required_scene_tags") or []):
            return False
    return True


def _safe_text(value, limit: int) -> str:
    # Descriptive fields only: never image paths, prompts, metadata or cards.
    if not isinstance(value, str):
        return ""
    value = re.sub(r"(?:https?://|data:|/uploads/)\S+", "", value[:limit * 2])
    return "".join(c for c in value if c.isprintable() or c in "\n\t")[:limit]


def _description(asset: CharacterAsset) -> str:
    parts = [_safe_text(asset.label, 80), _safe_text(asset.description, 320)]
    for field in _TAG_FIELDS:
        parts.append(field + ": " + ", ".join(_safe_text(t, 40) for t in getattr(asset, field)[:12]))
    return " | ".join(parts)[:1100]


def _snapshot(session: Session, conversation_id: str, ids: list[str], key: str):
    conversation = session.get(Conversation, conversation_id)
    messages = [session.get(Message, mid) for mid in ids]
    if not conversation or any(m is None or m.conversation_id != conversation_id for m in messages):
        return None
    if any(m.speaker_type != "character" or not m.speaker_id for m in messages):
        return None
    scene = session.get(SceneState, conversation_id)
    characters = sorted({m.speaker_id for m in messages})
    assets = list(session.exec(select(CharacterAsset).where(CharacterAsset.character_id.in_(characters))
                               .order_by(CharacterAsset.id)).all())
    links = list(session.exec(select(MessageAsset).where(MessageAsset.message_id.in_(ids))
                              .order_by(MessageAsset.id)).all())
    option = session.exec(select(ModelOption).where(ModelOption.key == key)).first()
    account = session.get(ModelProviderAccount, option.provider_account_id) if option else None
    route = _route(session, key) if not _mock() else None
    active_key = decision_model_key_for_turn(session, conversation_id)
    fingerprint = _digest({
        "conversation_id": conversation.id,
        "messages": [_row(m) for m in messages],
        # Newer appended turns and Arc-only compression do not change the
        # evidence consumed by this decision for an already committed bubble.
        "scene": {"location": scene.location, "mood": scene.mood} if scene else None,
        "assets": [_row(a) for a in assets], "links": [_row(l) for l in links],
        "settings": [_row(session.get(RuntimeSetting, i)) for i in ("global", conversation_id)],
        "option": _row(option), "account": _row(account),
        "route": vars(route) if route else None, "mock": _mock(),
    })
    return fingerprint, messages, scene, assets, links, route if active_key == key else None


def _legacy(session, conversation_id, messages, scene, assets, links, decisions=None):
    """Use the original seed and per-character reservations, including manual links."""
    decisions = decisions or {}
    attached = {link.message_id for link in links}
    reserved: dict[str, set[str]] = {}
    owners = {m.id: m.speaker_id for m in messages}
    for link in links:
        reserved.setdefault(owners[link.message_id], set()).add(link.asset_id)
    result = []
    for message in messages:
        excluded = reserved.setdefault(message.speaker_id, set())
        if message.id in attached:
            continue
        eligible = {a.id for a in assets if _eligible(a, message, scene)}
        if message.id in decisions:
            selected_id = decisions[message.id]
        else:
            selected = asset_selector.select_asset_for_message(
                session, conversation_id=conversation_id, character_id=message.speaker_id,
                emotion=message.emotion, action=message.action, content=message.content,
                scene_state=scene, selection_seed=message.id,
                excluded_asset_ids=excluded | {a.id for a in assets if a.id not in eligible},
            )
            selected_id = selected.id if selected else None
        if selected_id in eligible and selected_id not in excluded:
            result.append((message.id, selected_id))
            excluded.add(selected_id)
    return result


def _validated_result(session, conversation_id, ids, key, fingerprint, decisions, apply_fence):
    # SQLite's deferred BEGIN does not make SELECT revalidation atomic with a
    # later attachment. The production caller must reserve its real write/lease
    # boundary BEFORE this authoritative read, and retain it through link+ack.
    if apply_fence is not None and not apply_fence():
        raise ValueError("asset_decision_application_fence_lost")
    session.expire_all()
    current = _snapshot(session, conversation_id, ids, key)
    if current is None or current[0] != fingerprint:
        return []
    _, messages, scene, assets, links, _ = current
    return _legacy(session, conversation_id, messages, scene, assets, links, decisions)


async def select_turn_assets(session: Session, *, conversation_id: str,
                             message_ids: list[str], model_option_key: str,
                             apply_fence=None) -> list[tuple[str, str]]:
    """Return proposed links only; caller must attach and fence its lease atomically.

    Requires a clean session. Releases the read transaction before the single
    bounded await. Any changed source invalidates the *whole* turn, even fallback.
    Messages beyond the bounded question set retain legacy selection behavior.
    """
    if session.new or session.dirty or session.deleted:
        raise ValueError("asset_decision_requires_clean_session")
    ids = list(dict.fromkeys(message_ids))
    if not ids:
        return []
    # Expire identity-map cache: this must read current rows, not caller snapshots.
    session.expire_all()
    snapshot = _snapshot(session, conversation_id, ids, model_option_key)
    if snapshot is None:
        return []
    fingerprint, messages, scene, assets, links, route = snapshot
    if route is None:
        return _validated_result(session, conversation_id, ids, model_option_key, fingerprint, None, apply_fence)

    from app.services.decision_transport import AssetDecisionQuestion, decide_assets

    attached = {link.message_id for link in links}
    reserved = {link.asset_id for link in links}
    counts = {c: asset_selector.usage_counts(session, c) for c in {m.speaker_id for m in messages}}
    recent = {c: asset_selector.recent_asset_ranks(session, conversation_id, c) for c in counts}
    context = {"scene": {"location": (scene.location or "")[:3000] if scene else "",
                         "mood": (scene.mood or "")[:3000] if scene else ""}, "messages": []}
    questions = []
    for message in messages:
        if len(questions) >= MAX_QUESTIONS:
            break
        if message.id in attached:
            continue
        candidates = [a for a in assets if a.id not in reserved and _eligible(a, message, scene)]
        candidates.sort(key=lambda a: (a.id in recent[a.character_id], counts[a.character_id].get(a.id, 0),
                                       -max(0, min(100, a.priority or 0)), not a.is_default, a.id))
        if not candidates:
            continue
        bubble = {"id": message.id, "content": message.content[:3000],
                  "action": (message.action or "")[:3000], "emotion": (message.emotion or "")[:3000]}
        # Exact source prefixes, no history or summaries. Stop at a full-bubble
        # boundary rather than fabricating context for questions with no text.
        context["messages"].append(bubble)
        if len(json.dumps(context, ensure_ascii=False)) > MAX_CONTEXT:
            context["messages"].pop()
            break
        vision_capable = 'image' in route.input_modalities and bool(route.image_mime_types)
        candidate_limit = 8 if vision_capable else MAX_CANDIDATES
        choices = {a.id: _description(a) for a in candidates[:candidate_limit]}
        choices["none"] = "No image is needed for routine dialogue or an unchanged scene, or no candidate fits."
        question = AssetDecisionQuestion(id=message.id, candidates=choices)
        proposed = questions + [question]
        # Include repeated candidate descriptions, not only the shared input.
        # 1 KiB/question conservatively reserves provider envelope/instructions.
        payload_chars = len(json.dumps(context, ensure_ascii=False)) + len(json.dumps(
            [{"id": q.id, "candidates": q.candidates} for q in proposed], ensure_ascii=False,
        )) + 1024 * len(proposed)
        if payload_chars > MAX_REQUEST_CHARS:
            context["messages"].pop()
            break
        questions.append(question)
    if not questions:
        return _validated_result(session, conversation_id, ids, model_option_key, fingerprint, None, apply_fence)
    wanted = {alias for q in questions for alias in q.candidates if alias != 'none'}
    media_candidates = [
        {'id': asset.id, 'image_url': asset.image_url, 'thumbnail_url': asset.thumbnail_url}
        for asset in assets if asset.id in wanted
    ]
    # Only primitive context, frozen route/questions and digest survive awaits.
    del snapshot, messages, scene, assets, links
    message = candidates = None
    session.rollback()
    decisions = None
    try:
        from app.services.decision_asset_media import prepare_images
        images = await prepare_images(route, media_candidates)
        request = {'context': context, 'questions': questions}
        if images:
            request['images'] = images
        answer = await asyncio.wait_for(decide_assets(route, **request), timeout=TOTAL_TIMEOUT)
        expected = {q.id: q.candidates for q in questions}
        if (isinstance(answer, dict) and set(answer) == set(expected)
                and all(value is None or (isinstance(value, str) and value != "none" and value in expected[mid])
                        for mid, value in answer.items())):
            decisions = answer
    except Exception:
        # Provider exceptions and malformed results are optional; no raw logging.
        pass
    if session.new or session.dirty or session.deleted:
        raise ValueError("asset_decision_requires_clean_session")
    return _validated_result(session, conversation_id, ids, model_option_key, fingerprint, decisions, apply_fence)
