"""Safe failure evidence for Agent receipts, UI and server logs."""
from dataclasses import dataclass
import logging
import re

import httpx

from app.engine.llm_error_types import LLMResponseEvidenceError, LLMTerminalError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Failure:
    code: str
    exception: str
    http_status: int | None = None

    @property
    def text(self):
        return self.code + (f' · HTTP {self.http_status}' if self.http_status is not None else '')

    def wrap(self, error_type):
        error = error_type(self.text)
        error.agent_failure = self
        return error


def failure(stage, exc, guard=None):
    """Prefer observed response headers over SDK-normalized status guesses."""
    known = getattr(exc, 'agent_failure', None)
    if isinstance(known, Failure):
        return known
    cause = guard.error if guard is not None and guard.error is not None else exc
    known = getattr(cause, 'agent_failure', None)
    if isinstance(known, Failure):
        return known
    response = guard.response if guard is not None else None
    status = response.status_code if response is not None else None
    if status is None and isinstance(cause, LLMTerminalError):
        status = cause.http_status
    if status is None and isinstance(cause, httpx.HTTPStatusError):
        status = cause.response.status_code
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    if isinstance(cause, (httpx.TimeoutException, TimeoutError)):
        code = f'{stage}_TIMEOUT'
    elif status is not None and not 200 <= status < 300:
        code = f'{stage}_HTTP_ERROR'
    elif isinstance(cause, LLMResponseEvidenceError):
        code = f'{stage}_INVALID_RESPONSE'
    elif isinstance(cause, httpx.RequestError):
        code = f'{stage}_NETWORK_ERROR'
    else:
        code = f'{stage}_FAILED'
    return Failure(code, exception_name(cause), status)


def exception_name(exc):
    name = type(exc).__name__
    return name if re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]{0,79}', name) else 'Exception'


def record(detail, *, request_id, job_id=None, session_id=None, model=None, provider=None):
    def label(value):
        return value if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_.:-]{1,128}', value) else '-'
    # No exception messages, request/response bodies, headers or tracebacks.
    logger.warning('lore_agent_failure request_id=%s job_id=%s session_id=%s provider=%s model=%s code=%s http_status=%s exception=%s',
                   label(request_id), label(job_id), label(session_id), label(provider), label(model),
                   detail.code, detail.http_status if detail.http_status is not None else '-', detail.exception)
    return detail.text
