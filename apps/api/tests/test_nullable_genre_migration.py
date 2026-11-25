"""Migrate physical legacy schemas without rewriting conversation data."""
import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import SQLModel, Session
from app.db.models import Conversation
from app.db.session import make_engine, migrate_sqlite_columns
from app.schemas.conversations import ConversationCreate
from app.services.conversation_service import create_conversation


@pytest.mark.parametrize("legacy", ["missing", "required", "required_default"])
def test_nullable_migration_and_real_create(tmp_path, legacy):
    engine = make_engine(f"sqlite:///{tmp_path / 'synthetic.db'}")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as db:
        db.exec_driver_sql("ALTER TABLE conversations DROP COLUMN genre_mode")
        if legacy != "missing":
            default = " DEFAULT 'battle'" if legacy == "required_default" else ""
            db.exec_driver_sql("ALTER TABLE conversations ADD COLUMN genre_mode VARCHAR NOT NULL" + default)
    with Session(engine) as session:
        if legacy != "missing":
            session.add(Conversation(id="existing", mode="user_character", genre_mode="mystery"))
            session.commit()
    migrate_sqlite_columns(engine)
    with Session(engine) as session:
        new_id = create_conversation(session, ConversationCreate(mode="user_character")).id
    migrate_sqlite_columns(engine)
    with engine.connect() as db:
        assert db.exec_driver_sql("SELECT genre_mode IS NULL FROM conversations WHERE id=?", (new_id,)).scalar_one()
        genre = next(row for row in db.exec_driver_sql("PRAGMA table_info(conversations)") if row[1] == "genre_mode")
        assert genre[3:5] == (0, None)
        if legacy != "missing":
            assert db.exec_driver_sql("SELECT genre_mode FROM conversations WHERE id='existing'").scalar_one() == "mystery"
        assert db.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        assert not db.exec_driver_sql("PRAGMA foreign_key_check").all()
    engine.dispose()


def test_nullable_migration_rolls_back_if_custom_index_blocks_drop(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'custom.db'}")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as db:
        db.exec_driver_sql("ALTER TABLE conversations DROP COLUMN genre_mode")
        db.exec_driver_sql("ALTER TABLE conversations ADD COLUMN genre_mode VARCHAR NOT NULL DEFAULT 'battle'")
        db.exec_driver_sql("CREATE INDEX custom_genre_index ON conversations(genre_mode)")
    with pytest.raises(OperationalError, match="custom_genre_index"):
        migrate_sqlite_columns(engine)
    with engine.connect() as db:
        columns = {row[1]: row for row in db.exec_driver_sql("PRAGMA table_info(conversations)")}
        assert 'genre_mode_nullable' not in columns
        assert columns['genre_mode'][3:5] == (1, "'battle'")
    engine.dispose()
