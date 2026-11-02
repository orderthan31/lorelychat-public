"""One monotonic wallclock allowance for an entire logical provider task."""
import asyncio
from contextvars import ContextVar
import math
import time
from app.engine.llm_error_types import LLMTaskDeadlineError

TASK_DEADLINE: ContextVar[float | None] = ContextVar('llm_absolute_task_deadline', default=None)

def deadline_seconds(settings):
    try:
        value = float(getattr(settings, 'llm_task_deadline_seconds', 300.0))
    except (ValueError, TypeError, OverflowError):
        from app.engine.llm_error_types import LLMConfigurationError
        raise LLMConfigurationError('Invalid LLM task deadline') from None
    if not math.isfinite(value) or value <= 0:
        from app.engine.llm_error_types import LLMConfigurationError
        raise LLMConfigurationError('Invalid LLM task deadline')
    return value

def remaining_seconds(default=300.0):
    deadline = TASK_DEADLINE.get()
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LLMTaskDeadlineError()
    return remaining

async def within_deadline(awaitable, *, deadline=None):
    remaining = (deadline - time.monotonic()) if deadline is not None else remaining_seconds()
    if remaining <= 0:
        if hasattr(awaitable, 'close'):
            awaitable.close()
        raise LLMTaskDeadlineError()
    timeout = asyncio.timeout(remaining)
    try:
        async with timeout:
            return await awaitable
    except TimeoutError:
        if timeout.expired():
            raise LLMTaskDeadlineError() from None
        raise

async def bounded_close(close):
    """Bound cooperative cleanup; cancel stalled closure without waiting forever.

    At expiry/cancellation allow at most 100ms of local resource cleanup. A
    cancellation-resistant third-party coroutine can outlive this wait; it never
    authorizes another dispatch. Observe completion without logging its exception.
    """
    deadline = TASK_DEADLINE.get()
    allowance = min(0.1, max(0.0, deadline-time.monotonic())) if deadline else 0.1
    task = asyncio.create_task(close())
    def observe(done):
        if not done.cancelled():
            done.exception()
    task.add_done_callback(observe)
    try:
        done, _ = await asyncio.wait({task}, timeout=allowance)
        if not done:
            task.cancel()
    except asyncio.CancelledError:
        task.cancel()
        raise
