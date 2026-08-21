from contextlib import asynccontextmanager
import os

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.assets import router as assets_router
from app.api.characters import router as characters_router
from app.api.chat_commands import router as chat_commands_router
from app.api.conversations import _run_message_generation_job_sync, _run_post_commit_task_sync, router as conversations_router
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
from app.db.session import get_session, init_db, engine
from app.ops.sqlite_ops import acquire_runtime_lock, release_runtime_lock
from app.services import asset_service, conversation_service, prompt_snapshot_service, world_migration_service
from app.services.generation_dispatcher import generation_dispatcher
from app.services.post_commit_dispatcher import post_commit_dispatcher
from app.services.chat_command_service import seed_default_chat_commands

from sqlalchemy import text
from sqlmodel import Session


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    runtime_lock = None
    dispatcher_started = False
    try:
        if not settings.llm_mock:
            runtime_lock = acquire_runtime_lock(
                os.getenv("LORECHAT_RUNTIME_LOCK_PATH", "/tmp/lorechat-back.lock")
            )
        init_db()
        with Session(engine) as session:
            seed_default_chat_commands(session)
            asset_service.migrate_existing_avatars(session)
            world_migration_service.clone_missing_conversation_world_settings(session)
            prompt_snapshot_service.prune_prompt_snapshots(session)
            conversation_service.requeue_expired_message_generation_jobs(session)
            conversation_service.requeue_expired_post_commit_tasks(session)
        dispatcher_started = not settings.llm_mock
        if dispatcher_started:
            generation_dispatcher.start(_run_message_generation_job_sync)
            post_commit_dispatcher.start(_run_post_commit_task_sync)
        app.state.dispatchers_started = dispatcher_started
        app.state.ready = True
        yield
    finally:
        app.state.ready = False
        if dispatcher_started:
            post_commit_dispatcher.stop()
            generation_dispatcher.stop()
        release_runtime_lock(runtime_lock)


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
app.include_router(conversations_router)

app.include_router(model_providers_router)
app.include_router(presets_router)
app.include_router(relationship_map_router)
app.include_router(data_cleanup_router)
app.include_router(runtime_settings_router)

app.include_router(tts_router)
app.include_router(uploads_router)
app.include_router(world_settings_router)
app.mount("/uploads", StaticFiles(directory=UPLOAD_ROOT), name="uploads")
