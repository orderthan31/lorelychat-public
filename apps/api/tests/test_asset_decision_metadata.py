"""Provider-free tests for the Decision-only authored metadata projection."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.services.asset_decision_metadata import (
    decision_policy, detected_scene_tags, recommendation_facts,
)


def asset(**overrides):
    fields = dict(label="Sea breeze", description="A red bikini, shy smile, hand on hip.",
                  tags=[], mood_tags=["shy"], scene_tags=["beach"],
                  outfit_tags=["bikini"], pose_tags=["hand_on_hip"],
                  expression_tags=["shy_smile"], metadata_={})
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.mark.parametrize("outfit,scene", [
    ("비키니", "해변"), ("체리색_홀터넥_비키니", "리조트_인피니티풀"),
    ("리본 비키니 세트", "바닷가 테라스"), ("수영복", "호숫가"),
    ("red halter bikini", "poolside cabana"), ("one-piece swimsuit", "coastal deck"),
])
def test_missing_policy_normalizes_swim_outfits_and_scenes(outfit, scene):
    policy = decision_policy(asset(outfit_tags=[outfit], scene_tags=[scene]))
    assert policy["scope"] == "scene_required"
    assert detected_scene_tags(scene) & set(policy["required_scene_tags"])


@pytest.mark.parametrize("text,expected", [
    ("리조트_인피니티풀_가장자리", "pool"), ("루프탑 풀사이드", "pool"),
    ("해안 산책로", "beach"), ("바다 전망 테라스", "beach"),
    ("수변 데크", "waterside"), ("강가 피크닉", "waterside"),
    ("poolside", "pool"), ("infinity-pool", "pool"),
    ("seashore", "beach"), ("waterfront deck", "waterside"),
    ("학교_교실", "school"), ("침실에서", "bedroom"),
    ("홈트레이닝 체육관", "gym"), ("사무실에서", "office"),
    ("coffee_shop", "cafe"), ("rooftop garden", "rooftop"),
])
def test_scene_aliases_support_compounds(text, expected):
    assert expected in detected_scene_tags(None, text)


@pytest.mark.parametrize('scene,outfits,expected_scene', [
    ('호숫가 산책로', ['살구색 홀터', '청록색 중간 높이 하의'], 'waterside'),
    ('정박한 요트', ['아이보리 홀터', '미드나이트 블루 하의'], 'waterside'),
    ('정원 플런지풀', ['불투명 투피스', '접힌 허리선'], 'pool'),
])
def test_missing_swim_policy_uses_explicit_category_label_without_rewriting(scene, outfits, expected_scene):
    item = asset(label='비키니 · Synthetic character · Rest', outfit_tags=outfits, scene_tags=[scene])
    before = deepcopy(vars(item))
    policy = decision_policy(item)
    assert policy is not None
    assert policy['scope'] == 'scene_required'
    assert expected_scene in detected_scene_tags(scene)
    assert detected_scene_tags(scene) & set(policy['required_scene_tags'])
    assert vars(item) == before


def test_category_inference_is_not_arbitrary_caption_or_theme_search():
    for label in ('Bikini-themed poster', 'Not a bikini', 'portrait'):
        item = asset(label=label, description='Generate a bikini, but it may not be depicted.',
                     outfit_tags=['casual'], scene_tags=['indoor'])
        policy = decision_policy(item)
        assert policy is not None and policy['scope'] == 'generic'
    explicit = {'scope': 'generic', 'required_scene_tags': [], 'preferred_scene_tags': []}
    assert decision_policy(asset(label='비키니 · Synthetic', metadata_={'asset_selection_v2': explicit})) == explicit


def test_english_aliases_do_not_match_arbitrary_substrings_or_across_fields():
    assert not detected_scene_tags("carpool schooling homeostasis officeholder beachhead parking")
    assert "cafe" not in detected_scene_tags("coffee", "shop")
    assert detected_scene_tags(None, "") == set()


def test_valid_explicit_policy_is_preserved_and_detached():
    stored = {"scope": "scene_required", "required_scene_tags": ["해변"],
              "preferred_scene_tags": ["rooftop"]}
    item = asset(metadata_={"asset_selection_v2": stored})
    policy = decision_policy(item)
    assert policy == stored
    assert detected_scene_tags(*policy["required_scene_tags"]) & detected_scene_tags("해안가")
    policy["required_scene_tags"].append("pool")
    assert stored["required_scene_tags"] == ["해변"]


@pytest.mark.parametrize("stored", [None, [], "generic", {},
    {"scope": "unknown", "required_scene_tags": [], "preferred_scene_tags": []},
    {"scope": "scene_required", "required_scene_tags": "beach", "preferred_scene_tags": []},
    {"scope": "scene_required", "required_scene_tags": [1], "preferred_scene_tags": []},
    {"scope": "scene_required", "required_scene_tags": [""], "preferred_scene_tags": []},
    {"scope": "scene_required", "required_scene_tags": [], "preferred_scene_tags": []},
    {"scope": "generic", "required_scene_tags": ["pool"], "preferred_scene_tags": []},
    {"scope": "scene_preferred", "required_scene_tags": [], "preferred_scene_tags": "office"},
])
def test_explicit_malformed_policy_is_rejected_not_rederived(stored):
    assert decision_policy(asset(metadata_={"asset_selection_v2": stored})) is None


@pytest.mark.parametrize("outfit,scene,scope,tag", [
    ("교복 재킷", "교실", "scene_required", "school"),
    ("포근한 잠옷", "집 안", "scene_required", "home"),
    ("silk pajamas", "bedroom", "scene_required", "bedroom"),
    ("트레이닝복", "헬스장", "scene_required", "gym"),
    ("운동복", "공원", "scene_preferred", "outdoor"),
    ("오피스룩 블레이저", "회사", "scene_preferred", "office"),
    ("tailored suit", "office", "scene_preferred", "office"),
    ("satin_dress", "event", "scene_preferred", "event"),
])
def test_non_swim_policy_positives(outfit, scene, scope, tag):
    policy = decision_policy(asset(outfit_tags=[outfit], scene_tags=[scene]))
    assert policy["scope"] == scope
    assert tag in policy["required_scene_tags"] + policy["preferred_scene_tags"]


def test_casual_outfits_remain_generic_and_legacy_taxonomy_is_unchanged():
    from app.services.asset_taxonomy import selection_policy
    assert decision_policy(asset(outfit_tags=["red cardigan"], scene_tags=["실내"]))["scope"] == "generic"
    assert selection_policy(["바닷가"], ["비키니"])["scope"] == "generic"


def test_projection_keeps_specific_authored_semantics_but_not_production_records():
    item = asset(
        label="Render 01", description="masterpiece, red halter bikini with gold rings, sly half-smile, satin ribbon, hand on hip",
        tags=["gold rings", "batch:SECRET_BATCH", "provider:SECRET_PROVIDER", "seed:123", "https://invalid/SECRET_TAG_URL"],
        outfit_tags=["red_halter_bikini"], expression_tags=["sly_half_smile"],
        generation_prompt="SECRET_GENERATION", negative_prompt="SECRET_NEGATIVE",
        url="https://invalid/SECRET_URL", file_path="/private/SECRET_FILE",
        metadata_={"authored_concept": {"full_prompt": "SECRET_CONCEPT"},
                   "api_key": "SECRET_KEY", "batch": "SECRET_BATCH",
                   "visual_tag_audit_v1": {"visual_note": "SECRET_HISTORICAL_NOTE", "source_sha256": "a" * 64}})
    before = deepcopy(vars(item))
    facts = recommendation_facts(item)
    serialized = json.dumps(facts)
    assert "SECRET_" not in serialized
    assert facts["evidence_source"] == "authored_metadata"
    assert facts["pixel_verified"] is False
    for text in ("red halter bikini", "gold rings", "sly half-smile", "satin ribbon", "hand on hip"):
        assert text in facts["caption"]
    assert facts["outfit_tags"] == ["red_halter_bikini"]
    assert facts["expression_tags"] == ["sly_half_smile"]
    assert facts["tags"] == ["gold rings"]
    assert vars(item) == before
    facts["outfit_tags"].append("new")
    assert vars(item) == before


def test_projection_is_bounded_and_removes_embedded_media_locations():
    item = asset(label="L" * 1000, description="A smile https://invalid/SECRET_CAPTION /uploads/SECRET_PATH then a wave " + "x" * 2000,
                 mood_tags=["x" * 1000] * 100)
    facts = recommendation_facts(item)
    assert len(facts["label"]) <= 80
    assert len(facts["caption"]) <= 320
    assert len(facts["mood_tags"]) <= 12
    assert all(len(tag) <= 64 for tag in facts["mood_tags"])
    assert "SECRET_" not in json.dumps(facts)
    assert "then a wave" in facts["caption"]


def test_malformed_optional_facts_are_not_stringified():
    facts = recommendation_facts(asset(description={"secret": "SECRET_DICT"}, tags="SECRET_TAGS",
                                       expression_tags=[{"secret": "SECRET_TAG_DICT"}, "smile"]))
    assert facts["caption"] == ""
    assert facts["tags"] == []
    assert facts["expression_tags"] == ["smile"]
    assert "SECRET_" not in json.dumps(facts)


@pytest.mark.parametrize("scene", ["호텔_야외풀", "풀빌라 데크", "온수풀 가장자리", "리조트풀"])
def test_korean_compound_pool_contexts_match_stored_policy(scene):
    assert "pool" in detected_scene_tags(scene)


def test_bathing_suit_is_not_officewear():
    policy = decision_policy(asset(outfit_tags=["blue bathing suit"], scene_tags=["lake shore"]))
    assert policy["scope"] == "scene_required"
    assert "office" not in policy["preferred_scene_tags"]


def test_general_tags_drop_production_identifiers_without_deleting_visual_words():
    facts = recommendation_facts(asset(tags=[
        "grok-imagine-image", "xai", "fal", "source_sha256:abcdef", "batch_20261101",
        "0123456789abcdef" * 4, "gold rings", "red satin", "smiling",
    ]))
    assert facts["tags"] == ["gold rings", "red satin", "smiling"]


def test_derivation_and_projection_leave_entire_input_unchanged():
    item = asset(scene_tags=["리조트 인피니티풀"], outfit_tags=["청록색 비키니"],
                 metadata_={"generation_prompt": "untouched", "nested": {"tags": ["untouched"]}})
    before = deepcopy(vars(item))
    recommendation_facts(item)
    decision_policy(item)
    assert vars(item) == before
