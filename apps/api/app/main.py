from contextlib import AsyncExitStack, asynccontextmanager
import os
import time
import logging
import asyncio

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.assets import router as assets_router
from app.api.images import router as images_router
from app.api.lore_agent import router as lore_agent_router
from app.api.characters import router as characters_router
from app.api.chat_commands import router as chat_commands_router
from app.api.conversations import _run_message_generation_job_sync, _run_post_commit_task_sync, _run_mock_generation_queue, router as conversations_router
from app.api.data_cleanup import router as data_cleanup_router

from app.api.model_providers import router as model_providers_router
from app.api.presets import router as presets_router
from app.api.relationship_map import router as relationship_map_router
from app.api.runtime_settings import router as runtime_settings_router

from app.api.tts import router as tts_router
from app.api.uploads import router as uploads_router
from app.api.world_settings import router as world_settings_router
from app.core.config import get_settings
from app.core.logging_config import configure_file_logging
from app.core.upload_files import UploadStaticFiles
from app.db.session import get_session, init_db, engine
from app.ops.sqlite_ops import acquire_runtime_lock, release_runtime_lock
from app.ops.request_limits import RequestBodyLimitMiddleware
from app.services import asset_service, conversation_service
from app.services.generation_dispatcher import generation_dispatcher
from app.services.post_commit_dispatcher import post_commit_dispatcher
from app.services.chat_command_service import seed_default_chat_commands
from app.agent.schema import initialize_agent_schema
from app.agent.service import AgentService
from app.mcp import build_mcp_gateway

from sqlalchemy import text
from sqlmodel import Session


async def _supervise_mock_generation(app: FastAPI, target_engine):
    while True:
        try:
            # The mock runner filters durable execution_mode=mock rows; imported
            # live-intended queues are neither recovered nor claimed here.
            await _run_mock_generation_queue(target_engine)
            app.state.mock_executor_healthy = True
        except asyncio.CancelledError:
            raise
        except Exception:
            app.state.mock_executor_healthy = False
            logging.getLogger(__name__).exception("Mock execution iteration failed; retrying")
        await asyncio.sleep(0.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    runtime_lock = None
    dispatcher_started = False
    mock_task = None
    agent_stack = AsyncExitStack()
    mcp_gateway = None
    app.state.mock_generation_task = None
    app.state.ready = False
    app.state.dispatchers_started = False
    target_engine = getattr(app.state, "engine", engine)
    app.state.engine = target_engine
    if target_engine is not engine and not settings.llm_mock:
        raise RuntimeError("Injected lifespan engines require mock mode; production workers are never rebound")
    try:
        if not settings.llm_mock:
            runtime_lock = acquire_runtime_lock(
                os.getenv("LORECHAT_RUNTIME_LOCK_PATH", "/tmp/lorechat-back.lock")
            )
        init_db(target_engine)
        initialize_agent_schema(target_engine)
        app.state.agent_service = AgentService(target_engine)
        mcp_gateway = build_mcp_gateway(lambda: app.state.agent_service)
        app.state.lore_mcp_gateway = mcp_gateway
        await agent_stack.enter_async_context(mcp_gateway.lifespan())
        from app.services.image_cleanup_journal import replay_cleanup
        await asyncio.to_thread(replay_cleanup, target_engine,
                                upload_root=Path(settings.upload_root),
                                shared_root=settings.shared_upload_root, limit=16)
        with Session(target_engine) as session:
            seed_default_chat_commands(session)
            asset_service.migrate_existing_avatars(session)
            # Null is an intentional character-only room, not a migration marker.
            # Legacy world conversion must never run implicitly on startup.
            conversation_service.requeue_expired_message_generation_jobs(session, execution_mode="mock" if settings.llm_mock else None)
            if not settings.llm_mock:
                conversation_service.requeue_expired_post_commit_tasks(session)
        dispatcher_started = not settings.llm_mock
        if dispatcher_started:
            generation_dispatcher.start(_run_message_generation_job_sync)
            post_commit_dispatcher.start(_run_post_commit_task_sync)
        if settings.llm_mock and getattr(app.state, "mock_execution", True):
            app.state.mock_executor_healthy = False
            mock_task = asyncio.create_task(_supervise_mock_generation(app, target_engine))
            app.state.mock_generation_task = mock_task
        app.state.dispatchers_started = dispatcher_started
        app.state.ready = True
        yield
    finally:
        app.state.ready = False
        if mcp_gateway is not None:
            mcp_gateway.stop_admission()
        if mock_task is not None:
            mock_task.cancel()
            try:
                await mock_task
            except asyncio.CancelledError:
                pass
            app.state.mock_generation_task = None
        drained = True
        shutdown_timeout = getattr(settings, "dispatcher_shutdown_timeout_seconds", 30)
        deadline = time.monotonic() + max(0.0, float(30 if shutdown_timeout is None else shutdown_timeout))
        if dispatcher_started:
            # Stop admission in BOTH lanes before spending the shared drain budget.
            post_commit_dispatcher.request_stop()
            generation_dispatcher.request_stop()
            drained_post = post_commit_dispatcher.stop(timeout_seconds=max(0.0, deadline - time.monotonic()))
            drained_generation = generation_dispatcher.stop(timeout_seconds=max(0.0, deadline - time.monotonic()))
            drained = drained_post and drained_generation
        if mcp_gateway is not None:
            while mcp_gateway.active_calls and time.monotonic() < deadline:
                await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            drained = drained and mcp_gateway.active_calls == 0
        try:
            # The SDK manager must leave in this same lifespan task, after tools
            # have drained, not from an unrelated request/cancellation task.
            await agent_stack.aclose()
        except BaseException:
            app.state.undrained_runtime_lock = runtime_lock
            logging.getLogger(__name__).error("Agent transport shutdown incomplete; runtime ownership retained")
            raise
        if drained:
            release_runtime_lock(runtime_lock)
        else:
            # A Python thread cannot be killed safely. Retain flock until process
            # exit rather than admit a replacement while old effects can finalize.
            app.state.undrained_runtime_lock = runtime_lock
            logging.getLogger(__name__).critical("Shutdown drain deadline exceeded; runtime ownership retained until process exit")


settings = get_settings()
configure_file_logging(settings.log_dir)


app = FastAPI(title="Lorechat API", lifespan=lifespan)
UPLOAD_ROOT = Path(settings.upload_root).expanduser().resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestBodyLimitMiddleware)


@app.get("/")
def home() -> dict:
    return {
        "name": "Lorechat API",
        "frontend": "Use the bundled web app or run apps/web locally",
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/ready")
def ready(session: Session = Depends(get_session)) -> dict:
    if not getattr(app.state, "ready", False):
        raise HTTPException(status_code=503, detail="service startup is incomplete")
    mock_task = getattr(app.state, "mock_generation_task", None)
    if mock_task is not None and (mock_task.done() or not getattr(app.state, "mock_executor_healthy", False)):
        raise HTTPException(status_code=503, detail="mock execution supervision is unhealthy")
    if getattr(app.state, "dispatchers_started", False) and not (
        generation_dispatcher.is_running and post_commit_dispatcher.is_running
        and generation_dispatcher.is_healthy and post_commit_dispatcher.is_healthy
    ):
        raise HTTPException(status_code=503, detail="required dispatcher supervision is unhealthy")
    try:
        session.execute(text("SELECT 1")).scalar_one()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database probe failed") from exc
    return {
        "ready": True,
        "database": "ok",
        "dispatchers_started": bool(getattr(app.state, "dispatchers_started", False)),
    }


app.include_router(characters_router)
app.include_router(chat_commands_router)
app.include_router(assets_router)
app.include_router(images_router)
app.include_router(lore_agent_router)
app.include_router(conversations_router)

app.include_router(model_providers_router)
app.include_router(presets_router)
app.include_router(relationship_map_router)
app.include_router(data_cleanup_router)
app.include_router(runtime_settings_router)

app.include_router(tts_router)
app.include_router(uploads_router)
app.include_router(world_settings_router)
app.mount(
    "/uploads",
    UploadStaticFiles(UPLOAD_ROOT, settings.shared_upload_root),
    name="uploads",
)


async def _mcp_endpoint(scope, receive, send):
    # One stable mount, one fresh SDK manager per host lifespan. Repeated
    # TestClient lifespans must not reuse an exhausted AnyIO task group.
    gateway = getattr(app.state, "lore_mcp_gateway", None)
    if gateway is not None:
        await gateway.asgi_app(scope, receive, send)
        return
    from starlette.responses import JSONResponse
    status = 503 if scope.get("path") == "/mcp" else 404
    await JSONResponse({"error": "mcp_unavailable" if status == 503 else "not_found"}, status_code=status)(scope, receive, send)


# LAST: preserve existing API and upload routing; no /mcp/mcp submount.
app.mount("/", _mcp_endpoint, name="lore-mcp")
