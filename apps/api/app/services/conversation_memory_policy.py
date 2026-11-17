"""Pure conversation memory text normalization, validation and ledger guardrails."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models import CharacterMemory, Message


MAX_SCENE_MEMORY_CHARS = 2200


def compact_text(value: str | None, limit: int = 180, *, preserve_linebreaks: bool = False) -> str:
    if preserve_linebreaks:
        lines = [" ".join(line.split()) for line in (value or "").splitlines()]
        compacted_lines = []
        previous_blank = False
        for line in lines:
            is_blank = not line
            if is_blank and previous_blank:
                continue
            compacted_lines.append(line)
            previous_blank = is_blank
        text = "\n".join(compacted_lines).strip()
    else:
        text = " ".join((value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def compact_block_text(value: str | None, limit: int = 900) -> str:
    """Compact a structured block while preserving line breaks and bullets.

    Scene memory is intentionally a small structured block. Collapsing it with
    `compact_text()` turns headings and bullets into one dense sentence, which
    makes the next prompt harder to read and encourages raw-log leakage.
    """
    lines = []
    for line in (value or "").splitlines():
        stripped = " ".join(line.strip().split())
        if not stripped:
            continue
        indent = "  " if line[:1].isspace() and stripped.startswith("-") else ""
        lines.append(f"{indent}{stripped}")
    text = "\n".join(lines).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def clamp_state_value(value: int, *, low: int = -5, high: int = 5) -> int:
    return max(low, min(high, value))


def parse_json_object_from_text(text: str) -> dict | None:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


DURABLE_MEMORY_POSITIVE_TOKENS = [
    "세계관", "설정", "규칙", "장소", "공간", "상황", "사건", "단서", "비밀", "약속",
    "목표", "계획", "임무", "금지", "경계", "선호", "사실", "오픈 훅", "앞으로",
    "다음", "관계가", "바뀜", "변화", "고정", "확정", "기억해야",
    "world", "worldbuilding", "setting", "rule", "location", "place", "situation", "event", "clue",
    "secret", "promise", "goal", "plan", "mission", "boundary", "preference", "fact", "hook",
    "changed", "established", "confirmed", "entered", "arrived", "residence", "lodging", "identity",
]


DURABLE_MEMORY_NOISE_TOKENS = [
    "말했다", "대답했다", "웃었다", "바라봤", "고개", "표정", "기분", "감정", "반응했다",
    "생각했다", "느꼈", "한마디", "대사", "잠깐", "살짝", "미소", "침묵",
    "said", "replied", "answered", "smiled", "looked", "glanced", "nodded", "emotion", "mood",
    "reacted", "thought", "felt", "line", "briefly", "slightly", "silence",
]


BATTLE_RANKING_TOKENS = ["서열", "랭킹", "순위", "위", "승리", "패배", "격파", "굴복", "도전 금지", "락", "결투", "대전", "히스토리", "스토리"]


BATTLE_FLOW_NOISE_TOKENS = ["직전", "대기", "핵심 변수", "공세", "공격 강도", "패턴", "버티", "이성을 잃", "주도권을 역전"]


BATTLE_FINAL_TOKENS = ["확정", "공식", "현재", "유지", "승리", "패배", "격파", "굴복", "도전 금지 락", "락 해제"]


BATTLE_LEDGER_KEYS = {"ranking_snapshot", "battle_history", "battle_rule"}


LEAGUE_TOKENS = ["리그", "승점", "승무패", "정규리그", "순위표", "승점표", "경기 결과", "전적", "무승부", "league", "standings", "points", "match result", "record"]


LEAGUE_STANDINGS_TOKENS = ["순위표", "승점표", "standings", "points table"]


LEAGUE_HISTORY_TOKENS = ["경기 결과", "경기 히스토리", "매치 히스토리", "match history", "match result"]


def normalize_memory_fingerprint(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum() or "가" <= ch <= "힣")[:180]


MEMORY_DEDUPE_STOPWORDS = {
    "그리고", "그러나", "하지만", "현재", "이번", "모든", "오직", "통해", "으로", "하며",
    "한다", "된다", "이루어진다", "이루어짐", "간주됨", "상태", "상황", "사실", "공식",
    "the", "and", "or", "to", "of", "a", "an", "is", "are", "was", "were", "only",
}


def memory_similarity_tokens(text: str) -> set[str]:
    normalized = re.sub(r"char_[0-9a-f]+", " ", text.lower())
    raw_tokens = re.findall(r"[가-힣a-z0-9]{2,}", normalized)
    return {token for token in raw_tokens if token not in MEMORY_DEDUPE_STOPWORDS and not token.isdigit()}


def memories_are_similar(left: str, right: str) -> bool:
    left_tokens = memory_similarity_tokens(left)
    right_tokens = memory_similarity_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = left_tokens & right_tokens
    smaller = min(len(left_tokens), len(right_tokens))
    larger = max(len(left_tokens), len(right_tokens))
    if smaller <= 4:
        return len(overlap) >= max(3, smaller)
    containment = len(overlap) / smaller
    jaccard = len(overlap) / larger
    return containment >= 0.62 or jaccard >= 0.45


def scene_summary_copies_recent_raw_text(summary: str, recent_messages: list[Message]) -> bool:
    """Reject compression that merely strips field labels but keeps recent turns verbatim."""
    normalized_summary = compact_text(summary, MAX_SCENE_MEMORY_CHARS)
    if not normalized_summary:
        return False
    for message in recent_messages[-12:]:
        for raw in [message.content, message.action, message.thought]:
            text = compact_text(raw, 260)
            if len(text) < 36:
                continue
            if text[:70] in normalized_summary or text[-70:] in normalized_summary:
                return True
            for start in range(0, max(1, len(text) - 55), 45):
                chunk = text[start:start + 55]
                if len(chunk) >= 36 and chunk in normalized_summary:
                    return True
    return False


def battle_ranking_memory_key(content: str) -> str | None:
    """Return a stable ledger key for battle/ranking memories that should be updated, not appended."""
    text = compact_text(content, 260)
    if not any(token in text for token in BATTLE_RANKING_TOKENS):
        return None
    lowered = text.lower()
    char_ids = sorted(set(re.findall(r"char_[0-9a-f]+", lowered)))
    names = sorted(set(re.findall(r"[가-힣]{2,4}(?=(?:\(|는|은|가|이|에게|를|을|의|와|과))", text)))
    if "현재까지 대전" in text or "대전 스토리" in text or "대전 히스토리" in text:
        return "battle_history"
    if "승리 조건" in text or "서열 결정" in text or "서열 결정 방식" in text or "규칙" in text:
        return "battle_rule"
    if "도전 금지" in text or "락" in text:
        subject = char_ids[0] if char_ids else (names[0] if names else "room")
        return f"challenge_lock:{subject}"
    if "현재 확정된 주요 서열" in text or "랭킹" in text or "순위" in text or ("서열" in text and re.search(r"\d+위", text)):
        return "ranking_snapshot"
    if any(token in text for token in ["승리", "패배", "격파", "굴복"]):
        if len(char_ids) >= 2:
            return "battle_result:" + ":".join(char_ids[:2])
        if len(names) >= 2:
            return "battle_result:" + ":".join(names[:2])
        return "battle_result:room"
    return None


def league_memory_key(content: str) -> str | None:
    """Return a stable ledger key for league standings/match memories."""
    text = compact_text(content, 320)
    lowered = text.lower()
    if not any(token in text or token in lowered for token in LEAGUE_TOKENS):
        return None
    char_ids = sorted(set(re.findall(r"char_[0-9a-f]+", lowered)))
    names = sorted(set(re.findall(r"[가-힣]{2,4}(?=(?:\(|는|은|가|이|에게|를|을|의|와|과|:))", text)))
    if any(token in text or token in lowered for token in LEAGUE_STANDINGS_TOKENS):
        return "league_standings"
    if any(token in text or token in lowered for token in LEAGUE_HISTORY_TOKENS):
        return "league_match_history"
    if "승점" in text and (len(char_ids) + len(names)) >= 2:
        return "league_standings"
    if any(token in text for token in ["승", "패", "무"]) and (char_ids or names):
        subject = char_ids[0] if char_ids else names[0]
        return f"league_record:{subject}"
    return None


def memory_ledger_key(content: str) -> str | None:
    return battle_ranking_memory_key(content) or league_memory_key(content)


def is_league_ledger_key(ledger_key: str | None) -> bool:
    return bool(ledger_key and (ledger_key in {"league_standings", "league_match_history"} or ledger_key.startswith("league_record:")))


def is_battle_flow_noise(content: str) -> bool:
    text = compact_text(content, 260)
    if not any(token in text for token in BATTLE_RANKING_TOKENS):
        return False
    if any(token in text for token in ["직전", "대기", "핵심 변수", "다음 전투"]):
        return True
    if any(token in text for token in BATTLE_FLOW_NOISE_TOKENS) and not any(token in text for token in BATTLE_FINAL_TOKENS):
        return True
    return False


def is_battle_ledger_key(ledger_key: str | None) -> bool:
    if not ledger_key:
        return False
    return ledger_key in BATTLE_LEDGER_KEYS or ledger_key.startswith("battle_result:")


def contains_rank_ledger_claim(text: str) -> bool:
    has_rank_number = bool(re.search(r"\d+\s*위", text or ""))
    has_official_rank_phrase = any(phrase in (text or "") for phrase in [
        "현재 공개된 공식 서열", "현재 확정된 주요 서열", "공식 서열", "서열표", "랭킹표",
        "ranking snapshot", "official ranking",
    ])
    return has_official_rank_phrase or (has_rank_number and any(token in (text or "") for token in ["서열", "랭킹", "순위", "위"]))


def official_battle_memory_noise(content: str) -> bool:
    text = content or ""
    if not text:
        return False
    has_official_result = any(token in text for token in ["공식 승자", "공식 패자", "winner", "loser", "승자", "패자"])
    has_battle_result = any(token in text for token in ["승리", "패배", "격파", "굴복", "결과", "result"])
    return contains_rank_ledger_claim(text) or (has_official_result and has_battle_result)


def strip_rank_claims_from_scene_summary(summary: str) -> str:
    """Keep scene anchors, but remove model-invented official rank/ledger claims.

    Official battle/ranking canon lives in CharacterMemory ledger rows. Local compression models
    can drift while rewriting summaries, so scene summaries must not become a second source of truth.
    """
    cleaned_lines: list[str] = []
    for line in (summary or "").splitlines():
        text = line.strip()
        if not text:
            cleaned_lines.append(line)
            continue
        if contains_rank_ledger_claim(text):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned or compact_text(summary, MAX_SCENE_MEMORY_CHARS)


def extract_ledger_name_id_pairs(content: str) -> list[tuple[str, str]]:
    """Extract stable Korean name + character id anchors from ledger text."""
    return re.findall(r"([가-힣]{2,4})\((char_[0-9a-f]+)\)", content or "")


def has_ledger_name_id_mismatch(existing_memories: list[CharacterMemory], candidate_content: str) -> bool:
    """Reject compression ledger updates that pair an existing name/id anchor differently.

    Official battle ledgers often store rows like `김민지(char_...): 12위`. A local
    compression model can accidentally swap names and ids while rewriting a table;
    if a name or id already has a known pairing, the compression output must not
    introduce a conflicting pairing.
    """
    name_to_id: dict[str, str] = {}
    id_to_name: dict[str, str] = {}
    for memory in existing_memories:
        for name, character_id in extract_ledger_name_id_pairs(memory.content):
            name_to_id.setdefault(name, character_id)
            id_to_name.setdefault(character_id, name)
    if not name_to_id and not id_to_name:
        return False
    for name, character_id in extract_ledger_name_id_pairs(candidate_content):
        if name_to_id.get(name) not in {None, character_id}:
            return True
        if id_to_name.get(character_id) not in {None, name}:
            return True
    return False


def is_durable_room_memory(content: str, memory_type: str, importance: int = 3) -> bool:
    """Keep every non-empty manual user note; filter only auto-derived memory noise."""
    text = compact_text(content, 260)
    if memory_type == "user_note":
        return bool(text)
    if len(text) < 12 or importance < 3:
        return False
    if any(marker in text for marker in ["dialogue=", "action=", "emotion=", "speaker_id=", "character reply:", "counterpart message:"]):
        return False
    if is_battle_flow_noise(text):
        return False
    if memory_ledger_key(text):
        return True
    text_lower = text.lower()
    if any(token in text or token in text_lower for token in DURABLE_MEMORY_NOISE_TOKENS) and not any(token in text_lower or token in text for token in ["사건", "단서", "약속", "설정", "규칙", "상황", "비밀", "목표", "경계", "event", "clue", "promise", "setting", "rule", "situation", "secret", "goal", "boundary"]):
        return False
    has_positive = any(token in text or token in text_lower for token in DURABLE_MEMORY_POSITIVE_TOKENS)
    if memory_type in {"preference", "boundary"}:
        return has_positive or importance >= 4
    if memory_type == "open_hook":
        return any(token in text or token in text_lower for token in ["다음", "앞으로", "미해결", "오픈 훅", "단서", "목표", "계획", "next", "future", "unresolved", "hook", "clue", "goal", "plan"])
    return has_positive


def memory_already_exists(existing: list[CharacterMemory], content: str) -> bool:
    candidate = normalize_memory_fingerprint(content)
    if not candidate:
        return True
    for memory in existing:
        current = normalize_memory_fingerprint(memory.content)
        if not current:
            continue
        if candidate == current or candidate in current or current in candidate or memories_are_similar(memory.content, content):
            return True
    return False
