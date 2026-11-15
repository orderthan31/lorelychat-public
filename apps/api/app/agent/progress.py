"""Small public execution markers from real LangChain lifecycle callbacks.

Only a closed stage name and monotonic counters cross this boundary. Model
messages, tool arguments/results, credentials and private reasoning are never
stored or returned as progress. This does not dispatch or retry any operation.
"""
from __future__ import annotations

from copy import deepcopy

from langchain_core.callbacks import AsyncCallbackHandler
from sqlalchemy import select

from app.agent import models

_TOOL_STAGES = {
    'lore_search': 'search',
    'lore_get_character': 'read',
    'lore_get_world': 'read',
    'lore_capabilities': 'read',
    'lore_stage_character': 'draft',
    'lore_stage_world': 'draft',
    'lore_revise_draft': 'draft',
    'lore_preview_draft': 'review',
    'lore_request_approval': 'review',
    'lore_image_context': 'image_context',
    'lore_plan_image': 'image_plan',
    'lore_navigation_target': 'navigation',
    'lore_get_operation': 'read',
    'lore_get_image_job': 'read',
    'lore_commit_draft': 'draft',
}


class AgentProgressCallback(AsyncCallbackHandler):
    """Run-local observer; no external tracing or model-authored progress text."""

    run_inline = True
    raise_error = False

    def __init__(self, service, principal, session_id, request_id):
        self.service = service
        self.principal = principal
        self.session_id = session_id
        self.request_id = request_id
        self._round = 0
        self._disabled = False

    def _publish(self, stage):
        if self._disabled:
            return
        try:
            with self.service._write() as connection:
                row = connection.execute(select(models.operations).where(
                    models.operations.c.id == self.request_id,
                    models.operations.c.owner_id == self.principal.id,
                )).mappings().first()
                if row is None:
                    return
                operation = deepcopy(row['data'])
                if (operation.get('kind') != 'agent_turn'
                        or operation.get('status') != 'running'
                        or operation.get('result', {}).get('session_id') != self.session_id):
                    return
                previous = operation.get('progress')
                sequence = previous.get('sequence', 0) if isinstance(previous, dict) else 0
                if type(sequence) is not int or sequence < 0:
                    sequence = 0
                progress = {'stage': stage, 'sequence': sequence + 1}
                if self._round:
                    progress['round'] = self._round
                operation['progress'] = progress
                self.service._put(connection, models.operations, self.request_id, operation)
        except Exception:
            # An optional status write must not abort/retry inference or leak a
            # SQL/provider exception through LangChain's callback error logger.
            self._disabled = True

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        if not self._disabled:
            self._round += 1
            self._publish('model')

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        name = serialized.get('name') if isinstance(serialized, dict) else None
        stage = _TOOL_STAGES.get(name) if isinstance(name, str) else None
        if stage is not None:
            self._publish(stage)
