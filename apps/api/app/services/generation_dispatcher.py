from __future__ import annotations

import logging
import os
import socket
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable
from uuid import uuid4

from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.db.models import MessageGenerationJob
from app.db.session import engine
from app.services import conversation_service

logger = logging.getLogger(__name__)

GenerationRunner = Callable[[str, str], None]


class GenerationDispatcher:
    def __init__(
        self,
        target_engine: Engine,
        *,
        max_workers: int = 2,
        poll_interval_seconds: float = 0.5,
        lease_seconds: int = 180,
        heartbeat_interval_seconds: float = 15.0,
    ) -> None:
        self.engine = target_engine
        self.max_workers = max(1, int(max_workers))
        self.poll_interval_seconds = max(0.05, float(poll_interval_seconds))
        self.lease_seconds = max(10, int(lease_seconds))
        self.heartbeat_interval_seconds = max(0.1, float(heartbeat_interval_seconds))
        self.lease_owner = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        self._runner: GenerationRunner | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._futures: set[Future] = set()
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, runner: GenerationRunner) -> None:
        if self.is_running:
            return
        self._runner = runner
        self._stop.clear()
        self._wake.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="lorechat-generation",
        )
        with Session(self.engine) as session:
            recovered = conversation_service.requeue_expired_message_generation_jobs(session)
        if recovered:
            logger.warning("Requeued %s generation jobs with expired leases", recovered)
        self._thread = threading.Thread(
            target=self._dispatch_loop,
            name="lorechat-generation-dispatcher",
            daemon=True,
        )
        self._thread.start()
        self.wake()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=max(0.1, timeout_seconds))
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=False)
        self._thread = None
        self._executor = None
        with self._lock:
            self._futures.clear()

    def wake(self) -> None:
        self._wake.set()

    def _cleanup_futures(self) -> None:
        with self._lock:
            done = {future for future in self._futures if future.done()}
            self._futures.difference_update(done)
        for future in done:
            try:
                future.result()
            except Exception:
                logger.exception("Generation worker future exited unexpectedly")

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            self._cleanup_futures()
            with self._lock:
                available_slots = self.max_workers - len(self._futures)
            for _ in range(max(0, available_slots)):
                if self._stop.is_set():
                    break
                with Session(self.engine) as session:
                    claimed = conversation_service.claim_next_message_generation_job(
                        session,
                        lease_owner=self.lease_owner,
                        lease_seconds=self.lease_seconds,
                    )
                if claimed is None:
                    break
                executor = self._executor
                if executor is None:
                    break
                future = executor.submit(self._run_claimed_job, claimed.id)
                with self._lock:
                    self._futures.add(future)
            self._wake.wait(self.poll_interval_seconds)
            self._wake.clear()

    def _heartbeat_loop(self, job_id: str, stop_heartbeat: threading.Event) -> None:
        while not stop_heartbeat.wait(self.heartbeat_interval_seconds):
            with Session(self.engine) as session:
                renewed = conversation_service.heartbeat_message_generation_job(
                    session,
                    job_id,
                    lease_owner=self.lease_owner,
                    lease_seconds=self.lease_seconds,
                )
            if not renewed:
                return

    def _run_claimed_job(self, job_id: str) -> None:
        runner = self._runner
        if runner is None:
            return
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(job_id, stop_heartbeat),
            name=f"lorechat-generation-heartbeat-{job_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            runner(job_id, self.lease_owner)
        except Exception as exc:
            logger.exception("Generation runner failed for job=%s", job_id)
            with Session(self.engine) as session:
                job = session.get(MessageGenerationJob, job_id)
                if job and job.status == "running" and job.lease_owner == self.lease_owner:
                    conversation_service.mark_message_generation_job_failed(session, job, exc)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=1.0)
            with Session(self.engine) as session:
                job = session.get(MessageGenerationJob, job_id)
                if job and job.status == "running" and job.lease_owner == self.lease_owner:
                    if job.cancel_requested_at is not None:
                        conversation_service.mark_message_generation_job_cancelled(session, job)
                    else:
                        conversation_service.mark_message_generation_job_failed(
                            session,
                            job,
                            RuntimeError("Generation runner exited without a terminal job state"),
                        )
            self.wake()


generation_dispatcher = GenerationDispatcher(engine)
