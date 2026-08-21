from __future__ import annotations

import hashlib
import random
import re

from sqlalchemy import func
from sqlmodel import Session, select

from app.db.models import CharacterAsset, Message, MessageAsset, SceneState
from app.services.asset_taxonomy import SELECTION_METADATA_KEY, selection_policy


MOOD_ALIASES = {
    "happy": ["happy", "joy", "joyful", "smile", "기쁨", "행복", "웃음", "미소", "즐거움", "반가움"],
    "relaxed": ["relaxed", "calm", "peaceful", "편안", "여유로운", "느긋", "차분", "평온"],
    "confident": ["confident", "confidence", "proud", "자신감", "당당", "도발적"],
    "serious": ["serious", "stern", "cold_gaze", "진지", "심각", "냉정", "차가운 표정"],
    "angry": ["angry", "furious", "rage", "화남", "화난", "화가 난", "짜증", "분노", "노려봄"],
    "sad": ["sad", "teary", "crying", "슬픔", "슬퍼", "울음", "눈물", "서운", "우울"],
    "shy": ["shy", "embarrassed", "blush", "수줍", "당황", "부끄", "머뭇"],
    "surprised": ["surprised", "shocked", "놀람", "놀란", "깜짝"],
    "flirty": ["flirty", "teasing", "playful", "유혹", "능글", "장난스러운"],
    "neutral": ["neutral", "무표정", "담담", "평범한 표정"],
}
SCENE_ALIASES = {
    "indoor": ["indoor", "실내", "방 안", "작업방", "라운지"],
    "outdoor": ["outdoor", "야외", "바깥", "산책", "공원", "거리", "옥상", "해변"],
    "city_night": ["city_night", "city night", "야경", "밤거리", "네온"],
    "event": ["event", "party", "concert", "행사", "파티", "공연", "콘서트"],
    "park": ["park", "공원"],
    "street": ["street", "거리", "골목"],
    "cafe": ["cafe", "coffee shop", "카페", "커피숍"],
    "beach": ["beach", "seaside", "해변", "바닷가"],
    "pool": ["pool", "수영장"],
    "rooftop": ["rooftop", "옥상", "루프탑"],
    "bedroom": ["bedroom", "침실", "침대"],
    "home": ["home", "at home", "집에서", "집 안"],
    "kitchen": ["kitchen", "주방", "부엌", "요리"],
    "gym": ["gym", "fitness", "헬스장", "체육관", "운동 중"],
    "office": ["office", "workplace", "사무실", "회사"],
    "school": ["school", "campus", "classroom", "학교", "캠퍼스", "교실", "강의실"],
    "gallery": ["gallery", "museum", "미술관", "박물관"],
}
POSE_ALIASES = {
    "standing": ["standing", "stands", "서 있다", "일어선", "선 채"],
    "sitting": ["sitting", "sits", "seated", "앉아", "앉는다"],
    "walking": ["walking", "walks", "걷고", "걷는다", "산책"],
    "leaning": ["leaning", "leans", "기대어", "기댄"],
    "lying": ["lying", "lies down", "누워", "눕는다"],
    "arms_crossed": ["arms crossed", "crosses her arms", "팔짱", "팔을 교차"],
    "hand_on_hip": ["hand on hip", "손을 허리에", "허리에 손"],
    "looking_back": ["looking back", "looks back", "뒤돌아", "돌아본다"],
    "holding_object": ["holding", "holds", "들고", "쥐고", "잔을 든", "컵을 든"],
    "kneeling": ["kneeling", "무릎을 꿇"],
    "peace_sign": ["peace sign", "브이 포즈"],
    "selfie": ["selfie", "셀카"],
}
MOOD_EQUIVALENTS = {
    "smile": "happy",
    "soft_smile": "happy",
    "bright_smile": "happy",
    "joy": "happy",
    "calm": "relaxed",
    "peaceful": "relaxed",
    "confidence": "confident",
    "proud": "confident",
    "stern": "serious",
    "cold_gaze": "serious",
    "furious": "angry",
    "rage": "angry",
    "teary": "sad",
    "crying": "sad",
    "embarrassed": "shy",
    "blush": "shy",
    "shocked": "surprised",
    "playful": "flirty",
    "teasing": "flirty",
}
SAFE_FALLBACK_MOODS = {"happy", "relaxed", "neutral"}


def _text_blob(*values: str | None) -> str:
    return " ".join(value or "" for value in values).lower()


def _contains_alias(text: str, needle: str) -> bool:
    normalized = needle.strip().lower().replace("_", " ")
    if not normalized:
        return False
    searchable = text.replace("_", " ")
    if normalized.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", searchable) is not None
    return len(normalized) >= 2 and normalized in searchable


def detect_tags(text: str, aliases: dict[str, list[str]]) -> set[str]:
    return {tag for tag, needles in aliases.items() if any(_contains_alias(text, needle) for needle in needles)}


def tagset(values: list[str] | None) -> set[str]:
    return {str(value).strip().lower().replace(" ", "_") for value in (values or []) if str(value).strip()}


def canonical_moods(values: list[str] | None) -> set[str]:
    return {MOOD_EQUIVALENTS.get(tag, tag) for tag in tagset(values)}


def asset_selection_policy(asset: CharacterAsset) -> dict:
    stored = (asset.metadata_ or {}).get(SELECTION_METADATA_KEY)
    if isinstance(stored, dict) and stored.get("scope") in {"generic", "scene_preferred", "scene_required"}:
        return stored
    return selection_policy(asset.scene_tags, asset.outfit_tags)


def recent_asset_ranks(
    session: Session,
    conversation_id: str,
    character_id: str,
    *,
    limit: int = 8,
) -> dict[str, int]:
    messages = list(session.exec(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.speaker_type == "character",
            Message.speaker_id == character_id,
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    ).all())
    ranks: dict[str, int] = {}
    for rank, message in enumerate(messages, start=1):
        links = session.exec(select(MessageAsset).where(MessageAsset.message_id == message.id)).all()
        for link in links:
            ranks.setdefault(link.asset_id, rank)
    return ranks


def usage_counts(session: Session, character_id: str) -> dict[str, int]:
    rows = session.exec(
        select(MessageAsset.asset_id, func.count(MessageAsset.id))
        .join(CharacterAsset, CharacterAsset.id == MessageAsset.asset_id)
        .where(CharacterAsset.character_id == character_id)
        .group_by(MessageAsset.asset_id)
    ).all()
    return {str(asset_id): int(count) for asset_id, count in rows}


def weighted_choice(candidates: list[tuple[int, CharacterAsset]], seed_material: str) -> CharacterAsset:
    ordered = sorted(candidates, key=lambda item: item[1].id)
    weights = [
        max(1.0, float(score + max(0, min(100, int(asset.priority or 50))) / 20))
        for score, asset in ordered
    ]
    seed = int.from_bytes(hashlib.sha256(seed_material.encode("utf-8")).digest()[:8], "big")
    return random.Random(seed).choices([asset for _, asset in ordered], weights=weights, k=1)[0]


def select_asset_for_message(
    session: Session,
    *,
    conversation_id: str,
    character_id: str,
    emotion: str | None,
    action: str | None,
    content: str | None,
    scene_state: SceneState | None,
    min_score: int = 25,
    excluded_asset_ids: set[str] | None = None,
    selection_seed: str | None = None,
) -> CharacterAsset | None:
    assets = list(session.exec(
        select(CharacterAsset)
        .where(CharacterAsset.character_id == character_id, CharacterAsset.enabled == True)  # noqa: E712
        .order_by(CharacterAsset.is_default.desc(), CharacterAsset.priority.desc(), CharacterAsset.created_at.desc())
    ).all())
    if not assets:
        return None

    detected_moods = detect_tags(_text_blob(emotion), MOOD_ALIASES)
    if not detected_moods:
        detected_moods = detect_tags(_text_blob(action, content, scene_state.mood if scene_state else None), MOOD_ALIASES)
    detected_scenes = detect_tags(
        _text_blob(scene_state.location if scene_state else None, action, content),
        SCENE_ALIASES,
    )
    detected_poses = detect_tags(_text_blob(action), POSE_ALIASES)
    has_signal = bool(detected_moods or detected_scenes or detected_poses)
    excluded = set(excluded_asset_ids or set())

    scored: list[tuple[int, CharacterAsset]] = []
    for asset in assets:
        if asset.id in excluded:
            continue
        policy = asset_selection_policy(asset)
        required_scenes = tagset(policy.get("required_scene_tags") or [])
        if policy.get("scope") == "scene_required" and not (detected_scenes & required_scenes):
            continue

        asset_moods = canonical_moods(asset.mood_tags)
        asset_expressions = canonical_moods(asset.expression_tags)
        mood_match = bool(detected_moods & asset_moods)
        expression_match = bool(detected_moods & asset_expressions)
        scene_match = bool(detected_scenes & tagset(asset.scene_tags))
        pose_match = bool(detected_poses & tagset(asset.pose_tags))

        score = 0
        if mood_match:
            score += 35
        if expression_match:
            score += 25
        if scene_match:
            score += 30
        if pose_match:
            score += 25
        if policy.get("scope") == "scene_preferred" and scene_match:
            score += 10
        if detected_moods and not (mood_match or expression_match):
            score -= 10
        if not has_signal:
            if policy.get("scope") == "generic" and (asset_moods | asset_expressions) & SAFE_FALLBACK_MOODS:
                score = 25
            elif asset.is_default:
                score = 20
        if score >= min_score:
            scored.append((score, asset))

    if not scored:
        return None

    best_semantic = max(score for score, _ in scored)
    relevant = [(score, asset) for score, asset in scored if score >= best_semantic - 15]
    counts = usage_counts(session, character_id)
    minimum_usage = min(counts.get(asset.id, 0) for _, asset in relevant)
    balanced = [(score, asset) for score, asset in relevant if counts.get(asset.id, 0) <= minimum_usage + 1]

    recent = recent_asset_ranks(session, conversation_id, character_id)
    not_recent = [(score, asset) for score, asset in balanced if asset.id not in recent]
    final_candidates = not_recent or balanced
    seed_material = selection_seed or _text_blob(
        conversation_id,
        character_id,
        emotion,
        action,
        content,
        scene_state.location if scene_state else None,
    )
    return weighted_choice(final_candidates, seed_material)
