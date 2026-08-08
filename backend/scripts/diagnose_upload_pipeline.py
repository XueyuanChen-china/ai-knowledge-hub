#!/usr/bin/env python3
"""只读输出上传任务、阶段 job 与审计记录，供运维排障使用。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import engine
from app.db.models import UploadAuditLog, UploadProcessingJob, UploadTask
from app.observability.logging import redact_value


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only upload pipeline diagnostics")
    parser.add_argument("--upload-id", help="Inspect one upload task by upload_id")
    parser.add_argument(
        "--stuck-leases",
        action="store_true",
        help="List jobs whose running lease has expired. Does not modify them.",
    )
    return parser.parse_args()


def serialize_job(job: UploadProcessingJob) -> dict[str, object]:
    return {
        "job_id": job.id,
        "stage": job.stage,
        "status": job.status,
        "current_step": job.current_step,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "celery_task_id": job.celery_task_id,
        "locked_by": job.locked_by,
        "lease_expires_at": job.lease_expires_at.isoformat() if job.lease_expires_at else None,
        "error_message": job.error_message,
    }


def main() -> int:
    args = parse_arguments()
    if not args.upload_id and not args.stuck_leases:
        raise SystemExit("Provide --upload-id or --stuck-leases")

    with Session(engine) as session:
        payload: dict[str, object] = {}
        if args.upload_id:
            upload_task = session.exec(
                select(UploadTask).where(UploadTask.upload_id == args.upload_id)
            ).first()
            if upload_task is None:
                raise SystemExit(f"Upload task not found: {args.upload_id}")
            jobs = list(
                session.exec(
                    select(UploadProcessingJob)
                    .where(UploadProcessingJob.upload_task_id == upload_task.id)
                    .order_by(UploadProcessingJob.id)
                ).all()
            )
            audit_logs = list(
                session.exec(
                    select(UploadAuditLog)
                    .where(UploadAuditLog.upload_task_id == upload_task.id)
                    .order_by(UploadAuditLog.id)
                ).all()
            )
            payload["upload_task"] = {
                "upload_id": upload_task.upload_id,
                "status": upload_task.status,
                "processing_status": upload_task.processing_status,
                "document_id": upload_task.document_id,
                "processing_error_message": upload_task.processing_error_message,
            }
            payload["jobs"] = [serialize_job(job) for job in jobs]
            payload["audit_events"] = [
                {
                    "event_type": item.event_type,
                    "level": item.level,
                    "created_at": item.created_at.isoformat(),
                    "detail": redact_value(json.loads(item.detail_json or "{}")),
                }
                for item in audit_logs
            ]

        if args.stuck_leases:
            now = datetime.utcnow()
            stuck_jobs = list(
                session.exec(
                    select(UploadProcessingJob)
                    .where(
                        UploadProcessingJob.status == "running",
                        UploadProcessingJob.lease_expires_at.is_not(None),
                        UploadProcessingJob.lease_expires_at < now,
                    )
                    .order_by(UploadProcessingJob.lease_expires_at)
                ).all()
            )
            payload["expired_running_leases"] = [serialize_job(job) for job in stuck_jobs]

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
