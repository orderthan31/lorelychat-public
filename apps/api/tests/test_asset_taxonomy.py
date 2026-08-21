from datetime import datetime, timezone

from app.db.models import CharacterAsset
from app.services.asset_taxonomy import AUDIT_METADATA_KEY, SELECTION_METADATA_KEY, apply_visual_audit, selection_policy


def make_asset() -> CharacterAsset:
    return CharacterAsset(
        id="asset_test",
        character_id="char_test",
        label="test",
        image_url="/uploads/test.png",
        mood_tags=["romantic", "daily"],
        scene_tags=["beach", "summer"],
        outfit_tags=["swimsuit"],
        pose_tags=["portrait"],
        expression_tags=["natural"],
        metadata_={"provider": "kept"},
    )


def audit_row() -> dict:
    return {
        "asset_id": "asset_test",
        "mood_tags": ["Happy", "confident", "happy"],
        "scene_tags": ["Beach"],
        "outfit_tags": ["swimsuit"],
        "pose_tags": ["upper body"],
        "expression_tags": ["smile"],
        "confidence": "high",
        "issues": ["missing_tag"],
        "visual_note": "해변 수영복 이미지",
    }


def test_selection_policy_requires_matching_scene_for_swimwear():
    policy = selection_policy(["beach", "outdoor"], ["swimsuit"])
    assert policy == {
        "scope": "scene_required",
        "required_scene_tags": ["beach", "pool"],
        "preferred_scene_tags": [],
    }


def test_apply_visual_audit_preserves_previous_tags_and_is_idempotent():
    asset = make_asset()
    first_time = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    assert apply_visual_audit(asset, audit_row(), source_sha256="abc", applied_at=first_time) is True
    assert asset.mood_tags == ["happy", "confident"]
    assert asset.pose_tags == ["upper_body"]
    assert asset.metadata_["provider"] == "kept"
    assert asset.metadata_[AUDIT_METADATA_KEY]["previous_tags"]["mood_tags"] == ["romantic", "daily"]
    assert asset.metadata_[SELECTION_METADATA_KEY]["scope"] == "scene_required"

    second_time = datetime(2026, 7, 22, 13, 0, tzinfo=timezone.utc)
    assert apply_visual_audit(asset, audit_row(), source_sha256="abc", applied_at=second_time) is False
    assert asset.metadata_[AUDIT_METADATA_KEY]["last_applied_at"] == first_time.isoformat()
