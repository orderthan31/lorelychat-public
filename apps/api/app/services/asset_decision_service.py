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
import time
from urllib.parse import urlsplit

from sqlalchemy import or_, and_
from sqlmodel import Session, select, col

from app.core.config import get_settings
from app.db.models import (
    CharacterAsset, Conversation, Message, MessageAsset, ModelOption,
    ModelProviderAccount, RuntimeSetting, SceneState,
)
from app.services import asset_selector, model_provider_service, provider_secret_service
from app.services.hardening_core_transactions import isolated_mock_execution

MAX_QUESTIONS = 32
MAX_CANDIDATES = 254
PRIOR_ATTACHMENTS_PER_CHARACTER = 8
POLICY_VERSION = "multi_bubble_v1"
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
    """Eligibility is per turn; actual attachments, not cadence, prevent repeats."""
    return True


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _row(row):
    # Avoid Pydantic warnings that can expose malformed private JSON.
    return {name: getattr(row, name) for name in type(row).model_fields} if row is not None else None


def _body(message: Message) -> dict:
    """One complete ordered representation, never raw + aggregate duplication."""
    metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
    parts = metadata.get("render_parts")
    if (isinstance(parts, list) and parts and all(isinstance(p, dict)
            and p.get("type") in {"dialogue", "action", "thought"}
            and isinstance(p.get("text"), str) for p in parts)):
        return {"render_parts": [{"type": p["type"], "text": p["text"]} for p in parts]}
    raw = metadata.get("raw_body")
    if isinstance(raw, str) and raw.strip():
        return {"raw_body": raw}
    return {"content": message.content, "action": message.action or "",
            "emotion": message.emotion or "", "thought": message.thought or ""}


def _scene_text(context_messages) -> str:
    return " ".join(json.dumps(_body(m), ensure_ascii=False) for m in context_messages)


def _eligible(asset: CharacterAsset, message: Message, scene: SceneState | None,
              turn_text: str = "") -> bool:
    from app.services.asset_decision_metadata import decision_policy, detected_scene_tags
    if not asset.enabled or asset.asset_type != "image" or asset.character_id != message.speaker_id:
        return False
    if any(not isinstance(getattr(asset, field), list)
           or any(not isinstance(tag, str) for tag in getattr(asset, field)) for field in _TAG_FIELDS):
        return False
    policy = decision_policy(asset)
    if policy is None:
        return False
    if policy.get("scope") == "scene_required":
        scenes = detected_scene_tags(scene.location if scene else "", turn_text,
                                     message.action or "", message.content)
        required = policy.get("required_scene_tags") or []
        required_tags = asset_selector.tagset(required) | detected_scene_tags(*required)
        if not scenes & required_tags:
            return False
    return True


def _safe_text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"(?:https?://|data:|/uploads/)\S+", "", value[:limit * 2])
    return "".join(c for c in value if c.isprintable() or c in "\n\t")[:limit]


def bounded_diagnostics(values: dict) -> dict:
    """Persist only fixed outcomes, bounded counters and requested route identity."""
    result: dict = {"policy_version": POLICY_VERSION}
    for field in ("target_count", "question_count", "candidate_count", "request_bytes",
                  "image_count", "attached_count", "elapsed_ms"):
        value = values.get(field, 0)
        result[field] = min(2**31 - 1, max(0, value)) if type(value) is int else 0
    outcomes = {"success", "none", "fallback", "request_budget_exceeded", "stale", "send_recovery_abstention"}
    outcome = values.get("outcome")
    result["outcome"] = outcome if isinstance(outcome, str) and outcome in outcomes else "none"
    prep = values.get("preparation_status")
    result["preparation_status"] = prep if isinstance(prep, str) and prep in {"not_needed", "images", "text_only"} else "not_needed"
    provider = values.get("requested_provider")
    if isinstance(provider, str) and provider in _PROVIDERS:
        result["requested_provider"] = provider
    model = values.get("requested_model")
    if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,159}", model) and "://" not in model:
        result["requested_model"] = model
    return result


def _prior_attachments(session, conversation_id, context_messages, characters):
    """Bound actual image links BEFORE this turn, independently of bubble count.

    The newest attached image for each character is a hard exact-ID exclusion.
    Older facts are model evidence only; no perceptual-similarity claim is made.
    """
    from app.services.asset_decision_metadata import recommendation_facts
    anchor = min(context_messages, key=lambda m: (m.created_at, m.id))
    prior = []
    for character in characters:
        rows = session.exec(select(MessageAsset, Message, CharacterAsset).join(
            Message, Message.id == MessageAsset.message_id).join(
            CharacterAsset, CharacterAsset.id == MessageAsset.asset_id).where(
            Message.conversation_id == conversation_id, Message.speaker_id == character,
            CharacterAsset.character_id == character, CharacterAsset.asset_type == "image",
            or_(Message.created_at < anchor.created_at,
                and_(Message.created_at == anchor.created_at, Message.id < anchor.id)),
            col(Message.id).not_in([m.id for m in context_messages]),
        ).order_by(Message.created_at.desc(), Message.id.desc(), MessageAsset.id.desc())
          .limit(PRIOR_ATTACHMENTS_PER_CHARACTER)).all()
        for link, message, asset in rows:
            prior.append({"link": _row(link), "message_id": message.id,
                          "speaker_id": message.speaker_id, "created_at": str(message.created_at),
                          "asset": recommendation_facts(asset)})
    return prior


def _snapshot(session: Session, conversation_id: str, ids: list[str], key: str):
    conversation = session.get(Conversation, conversation_id)
    messages = [session.get(Message, mid) for mid in ids]
    if not conversation or any(m is None or m.conversation_id != conversation_id for m in messages):
        return None
    if any(m.speaker_type != "character" or not m.speaker_id for m in messages):
        return None
    jobs = {m.generation_job_id for m in messages}
    if len(jobs) != 1:
        return None
    job = next(iter(jobs))
    context_messages = (list(session.exec(select(Message).where(
        Message.conversation_id == conversation_id, Message.generation_job_id == job,
    ).order_by(Message.reply_index, Message.created_at, Message.id)).all()) if job else messages)
    scene = session.get(SceneState, conversation_id)
    characters = sorted({m.speaker_id for m in messages})
    assets = list(session.exec(select(CharacterAsset).where(col(CharacterAsset.character_id).in_(characters))
                               .order_by(CharacterAsset.id)).all())
    links = list(session.exec(select(MessageAsset).where(col(MessageAsset.message_id).in_([m.id for m in context_messages]))
                              .order_by(MessageAsset.id)).all())
    prior = _prior_attachments(session, conversation_id, context_messages, characters)
    option = session.exec(select(ModelOption).where(ModelOption.key == key)).first()
    account = session.get(ModelProviderAccount, option.provider_account_id) if option else None
    route = _route(session, key) if not _mock() else None
    active_key = decision_model_key_for_turn(session, conversation_id)
    fingerprint = _digest({
        "conversation_id": conversation.id, "messages": [_row(m) for m in context_messages],
        # Appended turns and Arc-only edits are not consumed evidence.
        "scene": {"location": scene.location, "mood": scene.mood} if scene else None,
        "assets": [_row(a) for a in assets], "links": [_row(l) for l in links], "prior": prior,
        "settings": [_row(session.get(RuntimeSetting, i)) for i in ("global", conversation_id)],
        "option": _row(option), "account": _row(account),
        "route": vars(route) if route else None, "mock": _mock(),
    })
    return fingerprint, messages, scene, assets, links, route if active_key == key else None, context_messages, prior


def _repeat_ids(prior):
    latest = {}
    for item in prior:
        if item["asset"] is not None:
            latest.setdefault(item["speaker_id"], item["link"]["asset_id"])
    return set(latest.values())


def _legacy(session, conversation_id, messages, scene, assets, links, decisions=None,
            context_messages=None, prior=()):
    """Preserve seed; enforce current manual, exact-repeat and policy exclusions."""
    attached = {link.message_id for link in links}
    reserved = {link.asset_id for link in links} | _repeat_ids(prior)
    result = []
    turn_text = _scene_text(context_messages or messages)
    for message in messages:
        if message.id in attached:
            continue
        eligible = {a.id for a in assets if _eligible(a, message, scene, turn_text)}
        if decisions is not None:
            selected_id = decisions.get(message.id)  # unasked/ineligible is none, never a hidden tail
        else:
            selected = asset_selector.select_asset_for_message(
                session, conversation_id=conversation_id, character_id=message.speaker_id,
                emotion=message.emotion, action=message.action, content=message.content,
                scene_state=scene, selection_seed=message.id,
                excluded_asset_ids=reserved | {a.id for a in assets if a.id not in eligible},
            )
            selected_id = selected.id if selected else None
        if selected_id in eligible and selected_id not in reserved:
            result.append((message.id, selected_id))
            reserved.add(selected_id)
    return result


def _validated_result(session, conversation_id, ids, key, fingerprint, decisions, apply_fence, diagnostics=None):
    # Acquire a physical write/ownership boundary BEFORE the authoritative read.
    if apply_fence is not None and not apply_fence():
        raise ValueError("asset_decision_application_fence_lost")
    session.expire_all()
    current = _snapshot(session, conversation_id, ids, key)
    if current is None or current[0] != fingerprint:
        if diagnostics is not None:
            diagnostics["outcome"] = "stale"
        return []
    _, messages, scene, assets, links, _, context_messages, prior = current
    selected = _legacy(session, conversation_id, messages, scene, assets, links, decisions, context_messages, prior)
    # A later logical turn can finish its task first. Under the same write fence,
    # also reject an exact match to the nearest following actual image. This is
    # a commit-only adjacency guard, never future prose/model input; unrelated
    # appended turns and different images do not invalidate this source.
    end = max(context_messages, key=lambda m: (m.created_at, m.id))
    owners = {m.id: m.speaker_id for m in messages}
    following = set()
    for character in {owners[mid] for mid, _ in selected}:
        next_asset = session.exec(select(MessageAsset.asset_id).join(
            Message, Message.id == MessageAsset.message_id).join(
            CharacterAsset, CharacterAsset.id == MessageAsset.asset_id).where(
            Message.conversation_id == conversation_id, Message.speaker_id == character,
            CharacterAsset.character_id == character, CharacterAsset.asset_type == "image",
            or_(Message.created_at > end.created_at,
                and_(Message.created_at == end.created_at, Message.id > end.id)),
        ).order_by(Message.created_at, Message.id, MessageAsset.id).limit(1)).first()
        if next_asset:
            following.add(next_asset)
    selected = [(mid, aid) for mid, aid in selected if aid not in following]
    if not selected and diagnostics is not None and diagnostics.get("outcome") == "success":
        diagnostics["outcome"] = "none"
    return selected


async def select_turn_assets(session: Session, *, conversation_id: str,
                             message_ids: list[str], model_option_key: str,
                             apply_fence=None, reserve_send=None,
                             diagnostics: dict | None = None) -> list[tuple[str, str]]:
    """One complete admitted judgment or an explicit no-call outcome.

    reserve_send is a synchronous durable owner/attempt CAS after preparation and
    admission. It must commit before returning, leaving no write transaction over
    provider I/O. A previous ambiguous send abstains rather than sending again.
    """
    from app.services.asset_decision_metadata import recommendation_facts
    from app.services.decision_transport import (
        AssetDecisionQuestion, DecisionRequestBudgetExceeded, decide_assets, _request,
    )
    started = time.monotonic()
    diag = diagnostics if diagnostics is not None else {}
    diag.update(policy_version=POLICY_VERSION, target_count=len(message_ids), question_count=0,
                candidate_count=0, request_bytes=0, image_count=0, preparation_status="not_needed",
                outcome="none", attached_count=0)
    if session.new or session.dirty or session.deleted:
        raise ValueError("asset_decision_requires_clean_session")
    ids = list(dict.fromkeys(message_ids))
    if not ids:
        return []
    session.expire_all()
    snapshot = _snapshot(session, conversation_id, ids, model_option_key)
    if snapshot is None:
        diag["outcome"] = "stale"
        return []
    fingerprint, messages, scene, assets, links, route, context_messages, prior = snapshot

    def finish(decisions):
        result = _validated_result(session, conversation_id, ids, model_option_key, fingerprint,
                                   decisions, apply_fence, diag)
        diag["attached_count"] = len(result)
        diag["elapsed_ms"] = min(120000, int((time.monotonic() - started) * 1000))
        return result

    if route is None:
        diag["outcome"] = "fallback"
        return finish(None)
    diag.update(requested_provider=route.provider_type, requested_model=_safe_text(route.model, 160))
    attached = {link.message_id for link in links}
    reserved = {link.asset_id for link in links} | _repeat_ids(prior)
    context = {
        "scene": {"location": scene.location or "", "mood": scene.mood or ""} if scene else {},
        "messages": [{"id": m.id, "speaker_id": m.speaker_id, "speaker_type": m.speaker_type,
                      "reply_index": m.reply_index, **_body(m)} for m in context_messages],
        "current_attachments": [{"message_id": l.message_id, "asset_id": l.asset_id} for l in links],
        "prior_images": [{"message_id": p["message_id"], "speaker_id": p["speaker_id"],
                          "asset_id": p["link"]["asset_id"]} for p in prior],
        "asset_catalog": {}, "visual_asset_ids": [],
    }
    questions = []
    turn_text = _scene_text(context_messages)
    for message in messages:
        if message.id in attached:
            continue
        candidates = [a for a in assets if a.id not in reserved and _eligible(a, message, scene, turn_text)]
        if not candidates:
            continue
        if len(candidates) > MAX_CANDIDATES or len(messages) > MAX_QUESTIONS:
            diag["outcome"] = "request_budget_exceeded"
            return finish({})
        choices = {}
        for asset in candidates:
            context["asset_catalog"][asset.id] = {"character_id": asset.character_id, **recommendation_facts(asset)}
            choices[asset.id] = f"Catalog {asset.id}; owner {asset.character_id}."
        choices["none"] = "No image: no fit or redundant moment."
        questions.append(AssetDecisionQuestion(id=message.id, candidates=choices))
    diag.update(question_count=len(questions), candidate_count=len(context["asset_catalog"]))
    if not questions:
        return finish({})
    candidate_ids = set(context["asset_catalog"])
    for item in prior:
        context["asset_catalog"].setdefault(item["link"]["asset_id"],
            {"character_id": item["speaker_id"], **item["asset"]})
    # Actual native UTF-8 text envelope, not an estimated character budget.
    try:
        diag["request_bytes"] = len(_request(route, context, questions)[4])
    except DecisionRequestBudgetExceeded as exc:
        diag.update(outcome="request_budget_exceeded", request_bytes=exc.request_bytes)
        return finish({})
    except (ValueError, TypeError):
        diag["outcome"] = "request_budget_exceeded"
        return finish({})
    # Round-robin characters: visual supplement never truncates text catalogs.
    by_character = {c: [a for a in assets if a.character_id == c and a.id in candidate_ids]
                    for c in dict.fromkeys(m.speaker_id for m in messages)}
    media_candidates = []
    while any(by_character.values()) and len(media_candidates) < 8:
        for bucket in by_character.values():
            if bucket and len(media_candidates) < 8:
                asset = bucket.pop(0)
                media_candidates.append({"id": asset.id, "image_url": asset.image_url,
                                         "thumbnail_url": asset.thumbnail_url})
    del snapshot, messages, scene, assets, links, context_messages, prior, by_character
    message = candidates = asset = bucket = None
    session.rollback()
    from app.services.decision_asset_media import prepare_images
    try:
        images = await prepare_images(route, media_candidates)
    except Exception:
        images = []
    context["visual_asset_ids"] = [image.id for image in images]
    diag.update(image_count=len(images), preparation_status="images" if images else "text_only")
    request = {"context": context, "questions": questions}
    if images:
        request["images"] = images
    try:
        diag["request_bytes"] = len(_request(route, **request)[4])
    except DecisionRequestBudgetExceeded as exc:
        diag.update(outcome="request_budget_exceeded", request_bytes=exc.request_bytes)
        return finish({})
    except (ValueError, TypeError):
        diag["outcome"] = "request_budget_exceeded"
        return finish({})
    # Outside the provider exception handler: failed ownership must not fallback.
    if reserve_send is not None and not reserve_send():
        diag["outcome"] = "send_recovery_abstention"
        return finish({})
    decisions = None
    diag["outcome"] = "fallback"
    try:
        answer = await asyncio.wait_for(decide_assets(route, **request), timeout=TOTAL_TIMEOUT)
        expected = {q.id: q.candidates for q in questions}
        if (isinstance(answer, dict) and set(answer) == set(expected)
                and all(value is None or (isinstance(value, str) and value != "none" and value in expected[mid])
                        for mid, value in answer.items())):
            decisions = answer
            diag["outcome"] = "success" if any(answer.values()) else "none"
    except Exception:
        pass  # Fixed aggregate outcome only; never raw provider text/errors.
    if session.new or session.dirty or session.deleted:
        raise ValueError("asset_decision_requires_clean_session")
    return finish(decisions)
