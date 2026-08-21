from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from app.db.models import CharacterAsset


AUDIT_METADATA_KEY = "visual_tag_audit_v1"
SELECTION_METADATA_KEY = "asset_selection_v2"
TAG_FIELDS = ("mood_tags", "scene_tags", "outfit_tags", "pose_tags", "expression_tags")


def normalize_tags(values: Iterable[Any] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        tag = str(value).strip().lower().replace(" ", "_")
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized


def selection_policy(scene_tags: Iterable[Any] | None, outfit_tags: Iterable[Any] | None) -> dict[str, Any]:
    scenes = set(normalize_tags(scene_tags))
    outfits = set(normalize_tags(outfit_tags))

    required: set[str] = set()
    preferred: set[str] = set()

    if scenes & {"beach", "pool"} or outfits & {"swimsuit", "bikini"}:
        required.update({"beach", "pool"})
    if "school_uniform" in outfits:
        required.add("school")
    if "sleepwear" in outfits:
        required.update({"bedroom", "home"})
    if "sportswear" in outfits and "gym" in scenes:
        required.add("gym")

    if outfits & {"officewear", "suit", "blazer"}:
        preferred.add("office")
    if outfits & {"evening_dress", "satin_dress", "velvet_dress"}:
        preferred.update({"event", "city_night"})
    if "sportswear" in outfits:
        preferred.update({"gym", "outdoor"})
    preferred.update(scenes & {"office", "event", "city_night", "rooftop", "cafe", "park", "street"})

    scope = "scene_required" if required else "scene_preferred" if preferred else "generic"
    return {
        "scope": scope,
        "required_scene_tags": sorted(required),
        "preferred_scene_tags": sorted(preferred - required),
    }


def apply_visual_audit(
    asset: CharacterAsset,
    audit_row: dict[str, Any],
    *,
    source_sha256: str,
    applied_at: datetime,
) -> bool:
    before = {field: normalize_tags(getattr(asset, field, [])) for field in TAG_FIELDS}
    after = {field: normalize_tags(audit_row.get(field, [])) for field in TAG_FIELDS}
    metadata = dict(asset.metadata_ or {})
    existing_audit = metadata.get(AUDIT_METADATA_KEY)

    if not isinstance(existing_audit, dict):
        existing_audit = {
            "previous_tags": before,
            "first_applied_at": applied_at.isoformat(),
        }
    else:
        existing_audit = dict(existing_audit)

    next_policy = selection_policy(after["scene_tags"], after["outfit_tags"])
    tags_changed = any(before[field] != after[field] for field in TAG_FIELDS)
    audit_is_current = (
        isinstance(metadata.get(AUDIT_METADATA_KEY), dict)
        and metadata[AUDIT_METADATA_KEY].get("source_sha256") == source_sha256
    )
    policy_is_current = metadata.get(SELECTION_METADATA_KEY) == next_policy
    if not tags_changed and audit_is_current and policy_is_current:
        return False

    audit_metadata = {
        **existing_audit,
        "source_sha256": source_sha256,
        "confidence": str(audit_row.get("confidence") or ""),
        "issues": normalize_tags(audit_row.get("issues", [])),
        "visual_note": str(audit_row.get("visual_note") or "").strip(),
        "last_applied_at": applied_at.isoformat(),
    }
    next_metadata = {
        **metadata,
        AUDIT_METADATA_KEY: audit_metadata,
        SELECTION_METADATA_KEY: next_policy,
    }

    for field, values in after.items():
        setattr(asset, field, values)
    asset.metadata_ = next_metadata
    asset.updated_at = applied_at
    return True
