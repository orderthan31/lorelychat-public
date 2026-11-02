from __future__ import annotations

import logging
import os
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Callable
from uuid import uuid4

from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.db.models import PostCommitTask
from app.db.session import engine
from app.services import conversation_service

logger = logging.getLogger(__name__)
PostCommitRunner = Callable[[str, str], None]


class PostCommitDispatcher:
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
        self.heartbeat_interval_seconds = max(0.05, float(heartbeat_interval_seconds))
        self.lease_owner = f"{socket.gethostname()}:{os.getpid()}:post:{uuid4().hex[:8]}"
        self._runner: PostCommitRunner | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._futures: set[Future] = set()
        self._lock = threading.Lock()
        self._last_progress = time.monotonic()
        self._supervisor_error = False
        self._heartbeat_errors: set[str] = set()

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def is_healthy(self) -> bool:
        return (self.is_running and not self._supervisor_error and not self._heartbeat_errors
                and time.monotonic() - self._last_progress < max(15.0, self.poll_interval_seconds * 4))

    def request_stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def start(self, runner: PostCommitRunner) -> None:
        if self.is_running:
            return
        with self._lock:
            if any(not future.done() for future in self._futures):
                raise RuntimeError("Cannot restart dispatcher before previous workers drain")
        self._runner = runner
        self._stop.clear()
        self._wake.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="lorechat-post-commit",
        )
        # Recovery runs continuously inside the exception-resilient supervisor.
        self._supervisor_error = True
        self._thread = threading.Thread(
            target=self._dispatch_loop,
            name="lorechat-post-commit-dispatcher",
            daemon=True,
        )
        self._thread.start()
        self.wake()

    def stop(self, *, timeout_seconds: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        self.request_stop()
        if self._thread:
            self._thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            futures = set(self._futures)
        if futures:
            wait(futures, timeout=max(0.0, deadline - time.monotonic()))
        drained = not (self._thread and self._thread.is_alive()) and all(f.done() for f in futures)
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=False)
        if drained:
            self._thread = None
            self._executor = None
            self._cleanup_futures()
        return drained

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
                logger.exception("Post-commit worker future exited unexpectedly")

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                with Session(self.engine) as session:
                    conversation_service.requeue_expired_post_commit_tasks(session)
                self._cleanup_futures()
                with self._lock:
                    available_slots = self.max_workers - len(self._futures)
                for _ in range(max(0, available_slots)):
                    if self._stop.is_set():
                        break
                    with Session(self.engine) as session:
                        claimed = conversation_service.claim_next_post_commit_task(
                            session,
                            lease_owner=f"{self.lease_owner}:{uuid4().hex}",
                            lease_seconds=self.lease_seconds,
                        )
                    if claimed is None:
                        break
                    executor = self._executor
                    if executor is None:
                        break
                    future = executor.submit(self._run_claimed_task, claimed.id, claimed.lease_owner)
                    with self._lock:
                        self._futures.add(future)
                self._supervisor_error = False
                self._last_progress = time.monotonic()
            except Exception:
                self._supervisor_error = True
                logger.exception("Dispatcher iteration failed; retrying after bounded poll")
            self._wake.wait(self.poll_interval_seconds)
            self._wake.clear()

    def _heartbeat_loop(self, task_id: str, stop_heartbeat: threading.Event, lease_owner: str) -> None:
        while not stop_heartbeat.wait(self.heartbeat_interval_seconds):
            try:
                with Session(self.engine) as session:
                    renewed = conversation_service.heartbeat_post_commit_task(
                        session, task_id, lease_owner=lease_owner,
                        lease_seconds=self.lease_seconds,
                    )
                if not renewed:
                    self._heartbeat_errors.add(task_id)
                    return
                self._heartbeat_errors.discard(task_id)
            except Exception:
                self._heartbeat_errors.add(task_id)
                logger.exception("Lease renewal failed; readiness degraded until recovery")

    def _run_claimed_task(self, task_id: str, lease_owner: str) -> None:
        runner = self._runner
        if runner is None:
            return
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(task_id, stop_heartbeat, lease_owner),
            name=f"lorechat-post-commit-heartbeat-{task_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            runner(task_id, lease_owner)
        except Exception as exc:
            logger.exception("Post-commit runner failed for task=%s", task_id)
            with Session(self.engine) as session:
                task = session.get(PostCommitTask, task_id)
                if task and task.status == "processing" and task.lease_owner == lease_owner:
                    conversation_service.mark_post_commit_task_failed(
                        session,
                        task,
                        exc,
                        lease_owner=lease_owner,
                    )
        finally:
            stop_heartbeat.set()
            heartbeat.join()  # A worker is not drained while its heartbeat can still write.
            with Session(self.engine) as session:
                task = session.get(PostCommitTask, task_id)
                if task and task.status == "processing" and task.lease_owner == lease_owner:
                    conversation_service.mark_post_commit_task_failed(
                        session,
                        task,
                        RuntimeError("Post-commit runner exited without a terminal task state"),
                        lease_owner=lease_owner,
                    )
            self._heartbeat_errors.discard(task_id)
            self.wake()


post_commit_dispatcher = PostCommitDispatcher(engine)
