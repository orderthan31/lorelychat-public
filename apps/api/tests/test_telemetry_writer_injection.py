"""Telemetry stays on the injected database, independent of chat transactions."""
import json

from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import Settings
from app.db.llm_telemetry import SQLTelemetryWriter
from app.db.models import LLMUsageEvent
from app.engine.llm_client import LLMClient, LLMResponse, LLMUnavailableError, telemetry_write_failure_count


def test_two_injected_databases_keep_identity_and_outcomes_isolated(tmp_path, monkeypatch):
    def forbidden_default():
        raise AssertionError('injected clients must not resolve application DB')
    monkeypatch.setattr('app.engine.llm_client.default_telemetry_writer', forbidden_default)
    engines = [create_engine(f'sqlite:///{tmp_path / name}') for name in ('a.sqlite', 'b.sqlite')]
    responses = []
    try:
        for index, engine in enumerate(engines):
            SQLModel.metadata.create_all(engine)
            client = LLMClient(Settings(llm_mock=True), telemetry_writer=SQLTelemetryWriter(engine))
            response = LLMResponse(content='PRIVATE RESPONSE', provider='openai', model='test',
                                   generation_attempts=2, prompt_tokens=4, completion_tokens=5,
                                   total_tokens=9, budget_diagnostics={'requested_max_output_tokens': 100})
            # Writer uses its own transaction; rolling back caller state cannot erase it.
            with Session(engine) as caller:
                client._record_usage(response, conversation_id=f'room-{index}')
                caller.rollback()
            assert response.usage_event_id
            client.record_response_outcome(response, status='validated', parse_code='ok',
                                           validation_code='ok', metadata_updates={'repair_attempts': 1})
            client._record_transport_failure(LLMUnavailableError('PRIVATE ERROR', transport_attempts=2))
            responses.append(response)
        for index, engine in enumerate(engines):
            with Session(engine) as session:
                rows = session.exec(select(LLMUsageEvent)).all()
                assert len(rows) == 2
                event = session.get(LLMUsageEvent, responses[index].usage_event_id)
                assert event.conversation_id == f'room-{index}'
                assert session.get(LLMUsageEvent, responses[1-index].usage_event_id) is None
                assert event.total_tokens == 9
                assert event.metadata_['status'] == 'validated'
                assert event.metadata_['transport_attempts'] == 2
                assert event.metadata_['repair_attempts'] == 1
                assert event.metadata_['output_budget']['requested_max_output_tokens'] == 100
                assert 'PRIVATE' not in json.dumps([r.model_dump(mode='json') for r in rows])
    finally:
        for engine in engines:
            engine.dispose()


def test_all_writer_failures_are_best_effort(caplog):
    class BrokenWriter:
        def insert(self, **values):
            raise RuntimeError('PRIVATE DATABASE ERROR')
        def update_outcome(self, *args):
            raise RuntimeError('PRIVATE DATABASE ERROR')
    client = LLMClient(Settings(llm_mock=True), telemetry_writer=BrokenWriter())
    response = LLMResponse(content='answer', provider='openai', model='test')
    before = telemetry_write_failure_count()
    client._record_usage(response)
    assert response.usage_event_id is None
    client._record_transport_failure(LLMUnavailableError('failed'))
    response.usage_event_id = 'existing'
    client.record_response_outcome(response, status='valid', parse_code=None, validation_code=None)
    assert telemetry_write_failure_count() == before + 3
    assert 'PRIVATE' not in caplog.text
