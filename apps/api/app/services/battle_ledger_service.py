from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Session, select

from app.db.models import BattleMatchRecord, BattleStanding, Character, Conversation, ConversationParticipant
from app.services import external_memory_service

BATTLE_GENRE_MODE = "battle"
DEFAULT_WIN_POINTS = 3


def is_battle_conversation(conversation: Conversation | None) -> bool:
    return bool(conversation and (conversation.genre_mode or "").strip().lower() == BATTLE_GENRE_MODE)


def normalize_matchup_key(value: str | None, participant_a_id: str, participant_b_id: str) -> str:
    cleaned = "_".join((value or "").strip().lower().replace("-", "_").split())
    if cleaned:
        return cleaned[:160]
    ordered = sorted([participant_a_id, participant_b_id])
    return "_vs_".join(ordered)[:160]


def _compact(value: str | None, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def _character_name(session: Session, character_id: str | None) -> str:
    if not character_id:
        return "미정"
    character = session.get(Character, character_id)
    return character.name if character else character_id


def _standing(session: Session, conversation_id: str, character_id: str) -> BattleStanding:
    standing = session.get(BattleStanding, (conversation_id, character_id))
    if not standing:
        standing = BattleStanding(conversation_id=conversation_id, character_id=character_id)
        session.add(standing)
    return standing


def _rebuild_standings(session: Session, conversation_id: str) -> None:
    records = list(session.exec(
        select(BattleMatchRecord).where(
            BattleMatchRecord.conversation_id == conversation_id,
            BattleMatchRecord.genre_mode == BATTLE_GENRE_MODE,
            BattleMatchRecord.result_status == "completed",
        )
    ).all())
    character_ids: set[str] = set()
    for record in records:
        character_ids.update([record.participant_a_id, record.participant_b_id])
    for character_id in character_ids:
        standing = _standing(session, conversation_id, character_id)
        standing.wins = sum(1 for record in records if record.winner_id == character_id)
        standing.losses = sum(1 for record in records if record.loser_id == character_id)
        standing.points = standing.wins * DEFAULT_WIN_POINTS
        standing.updated_at = datetime.now(timezone.utc)
        session.add(standing)
    ordered = list(session.exec(
        select(BattleStanding).where(BattleStanding.conversation_id == conversation_id)
    ).all())
    ordered.sort(key=lambda item: (-item.points, -item.wins, item.losses, item.character_id))
    for index, standing in enumerate(ordered, start=1):
        standing.rank = index
        standing.updated_at = datetime.now(timezone.utc)
        session.add(standing)


def _record_match_order(record: BattleMatchRecord) -> int:
    metadata = record.metadata_ if isinstance(record.metadata_, dict) else {}
    try:
        return int(metadata.get("match_order") or 9999)
    except (TypeError, ValueError):
        return 9999


def build_battle_memory_card(session: Session, record: BattleMatchRecord) -> dict:
    winner_name = _character_name(session, record.winner_id)
    loser_name = _character_name(session, record.loser_id)
    participant_a = _character_name(session, record.participant_a_id)
    participant_b = _character_name(session, record.participant_b_id)
    summary = _compact(record.process_summary, 420)
    decisive = _compact(record.decisive_moment, 160)
    match_order = _record_match_order(record)
    content_lines = [
        "[Battle match memory]",
        f"match_order={match_order if match_order != 9999 else '?'}",
        f"matchup_key={record.matchup_key}",
        f"participants={participant_a} vs {participant_b}",
        f"winner={winner_name}",
        f"loser={loser_name}",
    ]
    if summary:
        content_lines.append(f"process_summary={summary}")
    if decisive:
        content_lines.append(f"decisive_moment={decisive}")
    return {
        "content": "\n".join(content_lines),
        "metadata": {
            "conversation_id": record.conversation_id,
            "genre_mode": BATTLE_GENRE_MODE,
            "scope": "battle_match",
            "source": "battle_ledger",
            "match_order": match_order if match_order != 9999 else None,
            "match_record_id": record.id,
            "matchup_key": record.matchup_key,
            "participant_ids": [record.participant_a_id, record.participant_b_id],
            "character_id": "__room__",
            "winner_id": record.winner_id,
            "loser_id": record.loser_id,
            "result_status": record.result_status,
            "is_authoritative_domain_state": False,
            "memory_type": "battle_match_memory",
            "importance": 5,
        },
    }


def upsert_match_result(
    session: Session,
    conversation: Conversation | None,
    event: dict,
    *,
    sync_external: bool = True,
) -> BattleMatchRecord | None:
    if not is_battle_conversation(conversation):
        return None
    participant_a_id = str(event.get("participant_a_id") or "").strip()
    participant_b_id = str(event.get("participant_b_id") or "").strip()
    if not participant_a_id or not participant_b_id:
        return None
    matchup_key = normalize_matchup_key(event.get("matchup_key"), participant_a_id, participant_b_id)
    record = session.exec(
        select(BattleMatchRecord).where(
            BattleMatchRecord.conversation_id == conversation.id,
            BattleMatchRecord.matchup_key == matchup_key,
        )
    ).first()
    now = datetime.now(timezone.utc)
    if not record:
        record = BattleMatchRecord(
            id=f"match_{uuid4().hex[:12]}",
            conversation_id=conversation.id,
            matchup_key=matchup_key,
            participant_a_id=participant_a_id,
            participant_b_id=participant_b_id,
        )
    record.genre_mode = BATTLE_GENRE_MODE
    record.participant_a_id = participant_a_id
    record.participant_b_id = participant_b_id
    record.winner_id = str(event.get("winner_id") or "").strip() or None
    record.loser_id = str(event.get("loser_id") or "").strip() or None
    record.result_status = str(event.get("result_status") or "completed").strip() or "completed"
    record.process_summary = _compact(event.get("process_summary"), 900) or None
    record.decisive_moment = _compact(event.get("decisive_moment"), 240) or None
    record.source_message_start_id = str(event.get("source_message_start_id") or "").strip() or None
    record.source_message_end_id = str(event.get("source_message_end_id") or "").strip() or None
    record.metadata_ = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    record.updated_at = now
    session.add(record)
    session.flush()
    _rebuild_standings(session, conversation.id)
    session.commit()
    session.refresh(record)
    if sync_external and record.result_status == "completed":
        external_memory_service.sync_battle_memory_card_to_external(build_battle_memory_card(session, record), genre_mode=BATTLE_GENRE_MODE)
    return record


def apply_battle_events(session: Session, conversation: Conversation | None, battle_events: list[dict] | None) -> list[BattleMatchRecord]:
    if not is_battle_conversation(conversation):
        return []
    records: list[BattleMatchRecord] = []
    for event in battle_events or []:
        if not isinstance(event, dict):
            continue
        if (event.get("event_type") or "match_result") != "match_result":
            continue
        record = upsert_match_result(session, conversation, event)
        if record:
            records.append(record)
    return records


def list_match_records(session: Session, conversation_id: str) -> list[BattleMatchRecord]:
    records = list(session.exec(
        select(BattleMatchRecord).where(BattleMatchRecord.conversation_id == conversation_id, BattleMatchRecord.genre_mode == BATTLE_GENRE_MODE)
    ).all())
    return sorted(records, key=lambda record: (_record_match_order(record), record.created_at, record.id))


def list_standings(session: Session, conversation_id: str) -> list[BattleStanding]:
    standings = list(session.exec(
        select(BattleStanding).where(BattleStanding.conversation_id == conversation_id)
    ).all())
    return sorted(standings, key=lambda item: (item.rank or 9999, -item.points, item.character_id))


def _record_match_order_value(record: BattleMatchRecord) -> int | None:
    order = _record_match_order(record)
    return None if order == 9999 else order


def _serialize_match(session: Session, record: BattleMatchRecord) -> dict:
    return {
        "id": record.id,
        "match_order": _record_match_order_value(record),
        "round_number": (record.metadata_ or {}).get("round_number") if isinstance(record.metadata_, dict) else None,
        "matchup_key": record.matchup_key,
        "participant_a_id": record.participant_a_id,
        "participant_b_id": record.participant_b_id,
        "participant_a_name": _character_name(session, record.participant_a_id),
        "participant_b_name": _character_name(session, record.participant_b_id),
        "winner_id": record.winner_id,
        "loser_id": record.loser_id,
        "winner_name": _character_name(session, record.winner_id) if record.winner_id else None,
        "loser_name": _character_name(session, record.loser_id) if record.loser_id else None,
        "result_status": record.result_status,
        "process_summary": record.process_summary,
        "decisive_moment": record.decisive_moment,
        "source_message_start_id": record.source_message_start_id,
        "source_message_end_id": record.source_message_end_id,
        "metadata": record.metadata_ or {},
        "created_at": record.created_at.isoformat() if hasattr(record.created_at, "isoformat") else str(record.created_at),
        "updated_at": record.updated_at.isoformat() if hasattr(record.updated_at, "isoformat") else str(record.updated_at),
    }


def _battle_character_participant_ids(session: Session, conversation_id: str) -> set[str]:
    return {
        participant.participant_id
        for participant in session.exec(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.participant_type == "character",
            )
        ).all()
    }


def _active_match(session: Session, conversation_id: str) -> BattleMatchRecord | None:
    active = [record for record in list_match_records(session, conversation_id) if record.result_status == "in_progress"]
    active.sort(key=lambda record: (_record_match_order(record), record.created_at, record.id), reverse=True)
    return active[0] if active else None


def _next_match_order(session: Session, conversation_id: str) -> int:
    orders = [_record_match_order(record) for record in list_match_records(session, conversation_id)]
    concrete_orders = [order for order in orders if order != 9999]
    return (max(concrete_orders) if concrete_orders else 0) + 1


def _control_value(control: object, key: str, default=None):
    if isinstance(control, dict):
        return control.get(key, default)
    return getattr(control, key, default)


def _validate_battle_participant_pair(session: Session, conversation_id: str, participant_a_id: str, participant_b_id: str) -> None:
    if not participant_a_id or not participant_b_id:
        raise ValueError("Battle participants are required")
    if participant_a_id == participant_b_id:
        raise ValueError("Battle participants must be different characters")
    allowed = _battle_character_participant_ids(session, conversation_id)
    if participant_a_id not in allowed or participant_b_id not in allowed:
        raise ValueError("Battle participants must be character participants in this conversation")


def _ensure_fighter(record: BattleMatchRecord, character_id: str | None, *, field: str) -> None:
    if not character_id:
        return
    if character_id not in {record.participant_a_id, record.participant_b_id}:
        raise ValueError(f"{field} must be one of the active battle participants")


def apply_battle_control(session: Session, conversation: Conversation | None, control: object | None, *, message_id: str) -> BattleMatchRecord | None:
    if not control or not is_battle_conversation(conversation):
        return None
    assert conversation is not None
    action = str(_control_value(control, "action") or "").strip().lower()
    now = datetime.now(timezone.utc)
    if action == "start":
        if _active_match(session, conversation.id):
            raise ValueError("An active battle match already exists")
        participant_a_id = str(_control_value(control, "participant_a_id") or "").strip()
        participant_b_id = str(_control_value(control, "participant_b_id") or "").strip()
        _validate_battle_participant_pair(session, conversation.id, participant_a_id, participant_b_id)
        metadata = {
            "match_order": _next_match_order(session, conversation.id),
            "current_phase": _control_value(control, "current_phase") or "opening",
        }
        if _control_value(control, "advantage") is not None:
            metadata["advantage"] = {"favored_character_id": _control_value(control, "favored_character_id"), "value": float(_control_value(control, "advantage"))}
        record = BattleMatchRecord(
            id=f"match_{uuid4().hex[:12]}",
            conversation_id=conversation.id,
            genre_mode=BATTLE_GENRE_MODE,
            matchup_key=normalize_matchup_key(_control_value(control, "matchup_key"), participant_a_id, participant_b_id),
            participant_a_id=participant_a_id,
            participant_b_id=participant_b_id,
            result_status="in_progress",
            process_summary=_compact(_control_value(control, "process_summary"), 900) or None,
            source_message_start_id=message_id,
            source_message_end_id=message_id,
            metadata_=metadata,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record

    match_id = str(_control_value(control, "match_id") or "").strip()
    record = session.get(BattleMatchRecord, match_id) if match_id else _active_match(session, conversation.id)
    if not record or record.conversation_id != conversation.id:
        raise ValueError("Active battle match not found")

    if action == "progress":
        if record.result_status != "in_progress":
            raise ValueError("Battle progress requires an in-progress match")
        favored = str(_control_value(control, "favored_character_id") or "").strip() or None
        _ensure_fighter(record, favored, field="favored_character_id")
        metadata = dict(record.metadata_ or {})
        if _control_value(control, "current_phase"):
            metadata["current_phase"] = _control_value(control, "current_phase")
        if _control_value(control, "advantage") is not None:
            metadata["advantage"] = {"favored_character_id": favored, "value": float(_control_value(control, "advantage"))}
        record.metadata_ = metadata
        if _control_value(control, "process_summary"):
            record.process_summary = _compact(_control_value(control, "process_summary"), 900)
        record.source_message_end_id = message_id
        record.updated_at = now
        session.add(record)
        session.commit()
        session.refresh(record)
        return record

    if action == "end":
        if record.result_status != "in_progress":
            raise ValueError("Battle end requires an in-progress match")
        winner_id = str(_control_value(control, "winner_id") or "").strip()
        _ensure_fighter(record, winner_id, field="winner_id")
        if not winner_id:
            raise ValueError("winner_id is required to end a battle")
        loser_id = record.participant_b_id if winner_id == record.participant_a_id else record.participant_a_id
        record.winner_id = winner_id
        record.loser_id = loser_id
        record.result_status = "completed"
        record.source_message_end_id = message_id
        record.process_summary = _compact(_control_value(control, "process_summary"), 900) or record.process_summary
        record.decisive_moment = _compact(_control_value(control, "decisive_moment"), 240) or record.decisive_moment
        record.updated_at = now
        session.add(record)
        session.flush()
        _rebuild_standings(session, conversation.id)
        session.commit()
        session.refresh(record)
        external_memory_service.sync_battle_memory_card_to_external(build_battle_memory_card(session, record), genre_mode=BATTLE_GENRE_MODE)
        return record

    if action == "cancel":
        if record.result_status != "in_progress":
            raise ValueError("Battle cancel requires an in-progress match")
        record.result_status = "cancelled"
        record.source_message_end_id = message_id
        record.updated_at = now
        session.add(record)
        session.commit()
        session.refresh(record)
        return record

    raise ValueError("Unsupported battle control action")


def get_battle_state(session: Session, conversation: Conversation) -> dict:
    participants = list(session.exec(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation.id,
            ConversationParticipant.participant_type == "character",
        )
    ).all())
    participants.sort(key=lambda item: (item.order_index is None, item.order_index or 0, item.participant_id))
    participant_payload = []
    for participant in participants:
        character = session.get(Character, participant.participant_id)
        participant_payload.append({
            "id": participant.participant_id,
            "type": participant.participant_type,
            "name": character.name if character else participant.participant_id,
            "avatar_url": character.avatar_url if character else None,
            "role": participant.role,
            "order_index": participant.order_index,
        })

    records = list_match_records(session, conversation.id)
    active_candidates = [record for record in records if record.result_status == "in_progress"]
    active_candidates.sort(key=lambda record: (_record_match_order(record), record.created_at, record.id), reverse=True)
    recent_records = sorted(records, key=lambda record: (_record_match_order(record), record.created_at, record.id), reverse=True)
    standings_payload = []
    for standing in list_standings(session, conversation.id):
        character = session.get(Character, standing.character_id)
        standings_payload.append({
            "character_id": standing.character_id,
            "name": character.name if character else standing.character_id,
            "avatar_url": character.avatar_url if character else None,
            "wins": standing.wins,
            "losses": standing.losses,
            "points": standing.points,
            "rank": standing.rank,
            "updated_at": standing.updated_at.isoformat() if hasattr(standing.updated_at, "isoformat") else str(standing.updated_at),
        })
    return {
        "conversation_id": conversation.id,
        "genre_mode": conversation.genre_mode,
        "participants": participant_payload,
        "active_match": _serialize_match(session, active_candidates[0]) if active_candidates else None,
        "recent_matches": [_serialize_match(session, record) for record in recent_records],
        "standings": standings_payload,
    }


def format_official_battle_state(session: Session, conversation: Conversation | None, *, limit: int = 12) -> str:
    if not is_battle_conversation(conversation):
        return ""
    assert conversation is not None
    conversation_id = conversation.id
    records = list_match_records(session, conversation_id)[:limit]
    # Standings are compact and must not be truncated before all participants are shown;
    # otherwise tied league rooms look like players disappeared from long-term state.
    standings = list_standings(session, conversation_id)
    if not records and not standings:
        return ""
    lines = [
        "[Official battle state]",
        "Use this as source of truth for battle results, scores, and standings. If semantic memory conflicts, this section wins.",
    ]
    if standings:
        lines.append("Standings:")
        for standing in standings:
            name = _character_name(session, standing.character_id)
            rank = standing.rank or "?"
            lines.append(f"- {rank}위 {name}({standing.character_id}): 승={standing.wins}, 패={standing.losses}, 승점={standing.points}")
    if records:
        lines.append("Match records:")
        for record in records:
            winner = _character_name(session, record.winner_id)
            loser = _character_name(session, record.loser_id)
            summary = _compact(record.process_summary, 140)
            lines.append(f"- {record.matchup_key}: {winner} 승 vs {loser} 패" + (f" | {summary}" if summary else ""))
    return "\n".join(lines)
