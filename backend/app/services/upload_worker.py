import logging
import os
import socket
import threading
from dataclasses import dataclass
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from uuid import uuid4

from sqlalchemy import and_, or_
from sqlmodel import Session, select

from app.config import Settings
from app.db.database import engine
from app.db.models import UploadProcessingJob
from app.services.upload_postprocess_service import (
    JOB_STATUS_PENDING,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_RETRY_SCHEDULED,
    is_celery_processing_backend,
    run_processing_job,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedUploadProcessingJob:
    """一次成功 claim 的上传后处理 job。"""

    job_id: int
    claim_token: str


class UploadProcessingWorkerManager:
    """应用内上传后处理 worker。

    当前先用数据库 + 应用内线程池实现异步执行。
    后面如果接外部 MQ，只需要替换这里的调度层。
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._stop_event = threading.Event()
        self._futures: Dict[int, Future] = {}
        self._lock = threading.Lock()
        self._worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"

    def start(self, settings: Settings) -> None:
        # Celery 模式由外部 worker 消费，不能让应用内线程池重复抢同一批 job。
        if is_celery_processing_backend(settings):
            return
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, settings.upload_job_max_workers),
            thread_name_prefix="upload-job-worker",
        )
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(settings,),
            daemon=True,
            name="upload-job-dispatcher",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=False)
        self._thread = None
        self._executor = None
        with self._lock:
            self._futures.clear()

    def _run_loop(self, settings: Settings) -> None:
        while not self._stop_event.is_set():
            try:
                self.dispatch_once(settings)
            except Exception:
                logger.exception("Upload worker dispatch failed")
            self._stop_event.wait(max(1, settings.upload_job_poll_interval_seconds))

    def dispatch_once(self, settings: Settings) -> List[int]:
        """拉取一批 due jobs 并提交到线程池。"""

        if is_celery_processing_backend(settings):
            return []

        if self._executor is None:
            return []

        self._cleanup_finished_futures()
        free_slots = self._calculate_free_worker_slots(settings)
        if free_slots <= 0:
            return []

        claimed_jobs = claim_due_upload_processing_jobs(
            settings=settings,
            limit=free_slots,
            worker_id=self._worker_id,
        )
        if not claimed_jobs:
            return []

        submitted_job_ids: List[int] = []
        with self._lock:
            for claimed_job in claimed_jobs:
                job_id = claimed_job.job_id
                if job_id in self._futures:
                    continue
                future = self._executor.submit(
                    run_processing_job,
                    job_id=job_id,
                    settings=settings,
                    claim_token=claimed_job.claim_token,
                )
                self._futures[job_id] = future
                submitted_job_ids.append(job_id)
        self._cleanup_finished_futures()
        return submitted_job_ids

    def _calculate_free_worker_slots(self, settings: Settings) -> int:
        """计算线程池当前还能接收多少个新 job。"""

        with self._lock:
            active_count = sum(1 for future in self._futures.values() if not future.done())
        return max(0, max(1, settings.upload_job_max_workers) - active_count)

    def _cleanup_finished_futures(self) -> None:
        with self._lock:
            finished_job_ids = [
                job_id
                for job_id, future in self._futures.items()
                if future.done()
            ]
            finished_futures = [
                (job_id, self._futures.pop(job_id))
                for job_id in finished_job_ids
            ]

        for job_id, future in finished_futures:
            try:
                future.result()
            except Exception:
                logger.exception("Upload processing job failed outside retry handler", extra={"job_id": job_id})


def claim_due_upload_processing_jobs(
    *,
    settings: Settings,
    limit: int,
    worker_id: str = "",
) -> List[ClaimedUploadProcessingJob]:
    """把到期可执行的 job 标记为 queued，并返回 claim 信息。

    PostgreSQL 下会生成 SELECT ... FOR UPDATE SKIP LOCKED。
    这能保证多个 worker 同时抢任务时，同一行只会被一个事务拿到。
    """

    if limit <= 0:
        return []

    now = datetime.utcnow()
    effective_worker_id = worker_id or f"manual:{os.getpid()}:{uuid4().hex[:8]}"
    lease_expires_at = now + timedelta(seconds=settings.upload_job_lease_seconds)
    with Session(engine) as session:
        statement = (
            select(UploadProcessingJob)
            .where(
                or_(
                    and_(
                        UploadProcessingJob.status.in_(
                            [JOB_STATUS_PENDING, JOB_STATUS_RETRY_SCHEDULED]
                        ),
                        UploadProcessingJob.next_run_at <= now,
                    ),
                    and_(
                        UploadProcessingJob.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING]),
                        UploadProcessingJob.lease_expires_at.is_not(None),
                        UploadProcessingJob.lease_expires_at <= now,
                    ),
                )
            )
            .order_by(UploadProcessingJob.next_run_at.asc(), UploadProcessingJob.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list(session.exec(statement).all())

        claimed_jobs: List[ClaimedUploadProcessingJob] = []
        for job in jobs:
            claim_token = uuid4().hex
            job.status = JOB_STATUS_QUEUED
            job.claim_token = claim_token
            job.locked_by = effective_worker_id
            job.claimed_at = now
            job.lease_expires_at = lease_expires_at
            job.updated_at = now
            session.add(job)
            claimed_jobs.append(
                ClaimedUploadProcessingJob(
                    job_id=job.id,
                    claim_token=claim_token,
                )
            )
        if claimed_jobs:
            session.commit()
        return claimed_jobs


_worker_manager = UploadProcessingWorkerManager()


def get_upload_processing_worker_manager() -> UploadProcessingWorkerManager:
    return _worker_manager
