"""Additive SQLite migration, explicitly called after init_db by startup owner."""
from sqlalchemy import inspect
from sqlmodel import SQLModel
from app.agent.models import TABLES


def initialize_agent_schema(engine):
    if engine.dialect.name != 'sqlite':
        raise RuntimeError('Lore Agent currently requires SQLite')
    SQLModel.metadata.create_all(engine, tables=TABLES)
    with engine.begin() as connection:
        for table in ('characters', 'world_settings'):
            columns = {c['name'] for c in inspect(connection).get_columns(table)}
            if 'agent_revision' not in columns:
                connection.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN agent_revision INTEGER NOT NULL DEFAULT 0')
            # The guard avoids recursive firing while covering unknown/future
            # authored columns as well as every existing ORM and SQL writer.
            connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS {table}_agent_revision')
            connection.exec_driver_sql(f'''CREATE TRIGGER {table}_agent_revision
                AFTER UPDATE ON {table}
                WHEN NEW.agent_revision = OLD.agent_revision
                BEGIN UPDATE {table} SET agent_revision=OLD.agent_revision+1 WHERE id=NEW.id; END''')
