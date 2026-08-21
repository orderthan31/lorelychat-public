from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import Session, select

from app.db.models import Character, WorldSetting
from app.db.session import engine, init_db
from app.schemas.characters import CharacterCreate
from app.schemas.world_settings import WorldSettingCreate
from app.services import character_service, world_setting_service

SEED_ROOT = Path(__file__).resolve().parents[1] / "seeds"


def load_json(name: str) -> list[dict]:
    return json.loads((SEED_ROOT / name).read_text(encoding="utf-8"))


def main() -> None:
    init_db()
    created_characters = 0
    created_worlds = 0
    with Session(engine) as session:
        for payload in load_json("demo_characters.json"):
            existing = session.exec(select(Character).where(Character.name == payload["name"])).first()
            if not existing:
                character_service.create_character(session, CharacterCreate(**payload))
                created_characters += 1
        for payload in load_json("starter_worlds.json"):
            existing = session.exec(select(WorldSetting).where(WorldSetting.title == payload["title"])).first()
            if not existing:
                world_setting_service.create_world_setting(session, WorldSettingCreate(**payload))
                created_worlds += 1
    print(f"demo seed complete: characters={created_characters}, worlds={created_worlds}")


if __name__ == "__main__":
    main()
