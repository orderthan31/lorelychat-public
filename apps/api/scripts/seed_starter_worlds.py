from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import Session, select

from app.db.session import engine, init_db
from app.db.models import WorldSetting
from app.schemas.world_settings import WorldSettingCreate, WorldSettingUpdate
from app.services import world_setting_service


SEED_PATH = Path(__file__).resolve().parents[1] / "seeds" / "starter_worlds.json"


def main() -> None:
    worlds = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    created = 0
    updated = 0
    init_db()
    with Session(engine) as session:
        for payload in worlds:
            title = str(payload.get("title") or "").strip()
            if not title:
                continue
            existing = session.exec(select(WorldSetting).where(WorldSetting.title == title)).first()
            if existing:
                world_setting_service.update_world_setting(session, existing, WorldSettingUpdate(**payload))
                updated += 1
            else:
                world_setting_service.create_world_setting(session, WorldSettingCreate(**payload))
                created += 1
    print(f"starter worlds seeded: created={created}, updated={updated}, total={len(worlds)}")


if __name__ == "__main__":
    main()
