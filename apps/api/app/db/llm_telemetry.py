"""Independent telemetry transactions; never reuse a caller's chat transaction."""
from typing import Protocol

from sqlmodel import Session

from app.db.models import LLMUsageEvent


class TelemetryWriter(Protocol):
    def insert(self, **values) -> None: ...

    def update_outcome(self, event_id: str, metadata: dict) -> None: ...


class SQLTelemetryWriter:
    def __init__(self, engine):
        self.engine = engine

    def insert(self, **values) -> None:
        with Session(self.engine) as session:
            session.add(LLMUsageEvent(**values))
            session.commit()

    def update_outcome(self, event_id: str, metadata: dict) -> None:
        with Session(self.engine) as session:
            event = session.get(LLMUsageEvent, event_id)
            if event is None:
                return
            event.metadata_ = {**(event.metadata_ or {}), **metadata}
            session.add(event)
            session.commit()


def default_telemetry_writer() -> TelemetryWriter:
    # Resolve only at the persistence boundary, not during transport import.
    from app.db.session import engine

    return SQLTelemetryWriter(engine)
