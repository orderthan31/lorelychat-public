"""Read-only, Decision-only views of authored asset hints.

These functions do not inspect pixels or upgrade historical audit notes into
visual evidence. Nothing is persisted: the legacy selector and shared taxonomy
retain their existing behavior. Descriptive output is an allowlist, never a
serialization of the asset or its metadata.
"""
from __future__ import annotations

from copy import deepcopy
import re

from app.services.asset_taxonomy import SELECTION_METADATA_KEY, TAG_FIELDS, selection_policy


_SCENE_EXTENSIONS = {
    "beach": ("seashore", "shoreline", "seafront", "coast", "coastal", "coastline",
              "oceanfront", "해안", "바다", "백사장"),
    "pool": ("poolside", "swimming pool", "infinity pool", "waterpark", "water park",
             "풀사이드", "풀장", "인피니티풀", "인피니티 풀", "워터파크",
             "풀빌라", "실내풀", "야외풀", "호텔풀", "리조트풀", "온수풀",
             "plunge pool", "플런지풀", "플런지 풀"),
    "waterside": ("waterside", "waterfront", "lakeside", "lake shore", "riverside",
                  "riverbank", "yacht deck", "수변", "물가", "호숫가", "호수변",
                  "강가", "강변", "계곡", "요트", "yacht", "marina", "harbor"),
    "home": ("집안", "거실", "자택"),
    "gym": ("헬스", "피트니스", "체육관"),
}
_OUTFIT_ALIASES = {
    "bikini": ("bikini", "비키니"),
    "swimsuit": ("swimsuit", "swimwear", "bathing suit", "수영복"),
    "school_uniform": ("school uniform", "교복"),
    "sleepwear": ("sleepwear", "nightwear", "pajamas", "pyjamas", "잠옷", "파자마"),
    "sportswear": ("sportswear", "activewear", "workout clothes", "tracksuit", "운동복", "트레이닝복"),
    "officewear": ("officewear", "office wear", "business attire", "오피스룩", "정장"),
    "suit": ("suit",),
    "blazer": ("blazer", "블레이저"),
    "evening_dress": ("evening dress", "이브닝 드레스"),
    "satin_dress": ("satin dress", "새틴 드레스"),
    "velvet_dress": ("velvet dress", "벨벳 드레스"),
}
# Only entire production/aesthetic tags are discarded; captions and useful
# semantic clauses are not broadly removed just because they look prompt-like.
_NOISE_TAGS = frozenset({
    "masterpiece", "best_quality", "high_quality", "ultra_quality", "8k", "4k",
    "generated", "ai_generated", "reference_image", "image_generation",
    "xai", "fal", "openai", "openrouter", "grok_imagine_image", "grok_imagine_image_quality",
})
_NOISE_PREFIXES = (
    "batch", "provider", "model", "seed", "source", "generation", "negative_prompt",
    "prompt", "api_key", "credential", "authored_concept", "visual_tag_audit",
)
_LOCATION = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|data:|www\.)\S+"
    r"|(?<!\w)(?:/|\./|\.\./|~/|[a-z]:[\\/])\S+",
    re.IGNORECASE,
)


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    # Strip media locations before truncation, so partial URLs cannot survive.
    cleaned = _LOCATION.sub("", value[:limit * 4])
    return " ".join("".join(c for c in cleaned if c.isprintable() or c.isspace()).split())[:limit]


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def _contains(text: str, alias: str) -> bool:
    text = text.casefold().replace("_", " ").replace("-", " ")
    alias = alias.casefold().replace("_", " ").replace("-", " ")
    if alias.isascii():
        # English tokens must not turn carpool/parking/homeostasis into scenes.
        return re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text) is not None
    # Korean authored tags commonly join a scene/outfit to modifiers or particles.
    return alias in text


def detected_scene_tags(*texts: str | None) -> set[str]:
    """Canonical scene aliases for Decision context and stored-policy matching.

    Normalize each field independently (``coffee`` + ``shop`` is not a cafe).
    Non-string inputs are ignored rather than serialized into evidence.
    """
    # Safe lazy reuse: asset_selector imports models/pure taxonomy, not startup.
    from app.services.asset_selector import SCENE_ALIASES

    result: set[str] = set()
    for text in texts:
        if not isinstance(text, str):
            continue
        for canonical, aliases in SCENE_ALIASES.items():
            if any(_contains(text, alias) for alias in aliases):
                result.add(canonical)
        for canonical, aliases in _SCENE_EXTENSIONS.items():
            if any(_contains(text, alias) for alias in aliases):
                result.add(canonical)
    return result


def _valid_policy(policy: object) -> bool:
    if not isinstance(policy, dict):
        return False
    scope = policy.get("scope")
    if not isinstance(scope, str) or scope not in {"generic", "scene_preferred", "scene_required"}:
        return False
    for field in ("required_scene_tags", "preferred_scene_tags"):
        values = policy.get(field)
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            return False
    required, preferred = policy["required_scene_tags"], policy["preferred_scene_tags"]
    if scope == "scene_required":
        return bool(required)
    if scope == "scene_preferred":
        return not required and bool(preferred)
    return not required and not preferred


def decision_policy(asset) -> dict | None:
    """Preserve valid explicit policy; reject malformed rather than bypass it.

    Only absent policies are derived. Korean/compound normalization is confined
    to this view; no source tag, common taxonomy or OFF behavior is changed.
    """
    metadata = getattr(asset, "metadata_", None)
    if metadata is not None and not isinstance(metadata, dict):
        return None
    if isinstance(metadata, dict) and SELECTION_METADATA_KEY in metadata:
        stored = metadata[SELECTION_METADATA_KEY]
        return deepcopy(stored) if _valid_policy(stored) else None

    raw_scenes = _strings(getattr(asset, "scene_tags", None))
    raw_outfits = _strings(getattr(asset, "outfit_tags", None))
    # Some authored packs name the garment category in a structured label while
    # typed outfit tags describe only its color/cut. Use that explicit category,
    # never free-caption/generation instructions or arbitrary substring matches.
    label = getattr(asset, "label", None)
    category = re.split(r"[·:|]", label[:120], maxsplit=1)[0].strip().casefold() if isinstance(label, str) else ""
    if category in {"비키니", "수영복", "bikini", "swimsuit", "swimwear"}:
        raw_outfits = [*raw_outfits, category]
    scenes = detected_scene_tags(*raw_scenes)
    outfits = {canonical for canonical, aliases in _OUTFIT_ALIASES.items()
               if any(_contains(text, alias)
                      and (canonical != "suit" or not _contains(text, "bathing suit"))
                      for text in raw_outfits for alias in aliases)}
    policy = selection_policy(scenes, outfits)
    # Swimwear is also legitimate at a lake/river/yacht, not only beach/pool.
    # Bare waterside scenery without swimwear adds no new hard requirement.
    if outfits & {"bikini", "swimsuit"}:
        policy["required_scene_tags"] = sorted(set(policy["required_scene_tags"]) | {"waterside"})
    return policy


def recommendation_facts(asset) -> dict:
    """Bounded authored caption and typed hints, explicitly not pixel findings."""
    facts = {
        "label": _text(getattr(asset, "label", None), 80),
        "caption": _text(getattr(asset, "description", None), 320),
        "evidence_source": "authored_metadata",
        "pixel_verified": False,
    }
    for field in (*TAG_FIELDS, "tags"):
        tags: list[str] = []
        for value in _strings(getattr(asset, field, None)):
            if field == "tags":
                normalized = value.strip().casefold().replace(" ", "_").replace("-", "_")
                if normalized in _NOISE_TAGS or re.fullmatch(r"[a-f0-9]{32,64}", normalized) or any(
                    normalized == prefix or normalized.startswith((prefix + ":", prefix + "_", prefix + "="))
                    for prefix in _NOISE_PREFIXES
                ):
                    continue
            cleaned = _text(value, 64)
            if cleaned and cleaned not in tags:
                tags.append(cleaned)
            if len(tags) == 12:
                break
        facts[field] = tags
    return facts
