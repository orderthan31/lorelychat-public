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

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, runner: PostCommitRunner) -> None:
        if self.is_running:
            return
        self._runner = runner
        self._stop.clear()
        self._wake.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="lorechat-post-commit",
        )
        with Session(self.engine) as session:
            recovered = conversation_service.requeue_expired_post_commit_tasks(session)
        if recovered:
            logger.warning("Requeued %s post-commit tasks with expired leases", recovered)
        self._thread = threading.Thread(
            target=self._dispatch_loop,
            name="lorechat-post-commit-dispatcher",
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
                logger.exception("Post-commit worker future exited unexpectedly")

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            self._cleanup_futures()
            with self._lock:
                available_slots = self.max_workers - len(self._futures)
            for _ in range(max(0, available_slots)):
                if self._stop.is_set():
                    break
                with Session(self.engine) as session:
                    claimed = conversation_service.claim_next_post_commit_task(
                        session,
                        lease_owner=self.lease_owner,
                        lease_seconds=self.lease_seconds,
                    )
                if claimed is None:
                    break
                executor = self._executor
                if executor is None:
                    break
                future = executor.submit(self._run_claimed_task, claimed.id)
                with self._lock:
                    self._futures.add(future)
            self._wake.wait(self.poll_interval_seconds)
            self._wake.clear()

    def _heartbeat_loop(self, task_id: str, stop_heartbeat: threading.Event) -> None:
        while not stop_heartbeat.wait(self.heartbeat_interval_seconds):
            with Session(self.engine) as session:
                renewed = conversation_service.heartbeat_post_commit_task(
                    session,
                    task_id,
                    lease_owner=self.lease_owner,
                    lease_seconds=self.lease_seconds,
                )
            if not renewed:
                return

    def _run_claimed_task(self, task_id: str) -> None:
        runner = self._runner
        if runner is None:
            return
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(task_id, stop_heartbeat),
            name=f"lorechat-post-commit-heartbeat-{task_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            runner(task_id, self.lease_owner)
        except Exception as exc:
            logger.exception("Post-commit runner failed for task=%s", task_id)
            with Session(self.engine) as session:
                task = session.get(PostCommitTask, task_id)
                if task and task.status == "processing" and task.lease_owner == self.lease_owner:
                    conversation_service.mark_post_commit_task_failed(
                        session,
                        task,
                        exc,
                        lease_owner=self.lease_owner,
                    )
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=1.0)
            with Session(self.engine) as session:
                task = session.get(PostCommitTask, task_id)
                if task and task.status == "processing" and task.lease_owner == self.lease_owner:
                    conversation_service.mark_post_commit_task_failed(
                        session,
                        task,
                        RuntimeError("Post-commit runner exited without a terminal task state"),
                        lease_owner=self.lease_owner,
                    )
            self.wake()


post_commit_dispatcher = PostCommitDispatcher(engine)
