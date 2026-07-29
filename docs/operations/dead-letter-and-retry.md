# Upload Retry and Dead-Letter Operations

## Current Retry Contract

The PostgreSQL `upload_processing_jobs` row is the business state source of truth. Each stage has `attempt_count`, `max_attempts`, status and error fields. A retryable failure becomes `retry_scheduled`; Celery schedules the next attempt using the configured backoff.

When attempts are exhausted, the stage job becomes `failed`. The Celery task raises `Reject(requeue=False)`. With the configured RabbitMQ queue dead-letter exchange, RabbitMQ moves the original delivery to `ai_knowledge_hub.dead` instead of returning it to the main queue.

This gives two linked facts:

- PostgreSQL explains which business job failed and why.
- RabbitMQ DLQ retains the rejected delivery for broker-level inspection.

Neither is an automatic replay mechanism in U7.

## Inspect Queue Counts

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub
docker compose exec rabbitmq rabbitmqctl list_queues name messages messages_ready messages_unacknowledged
```

Inspect the failed business job without modifying it:

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend
./.venv/bin/python scripts/diagnose_upload_pipeline.py --upload-id upl_xxx
```

## Safe Recovery Procedure

1. Identify the job, stage, error message, attempt count, Celery task ID and trace ID.
2. Fix the dependency or input cause first. Replaying an invalid PDF, wrong OSS permission, or unavailable model only creates duplicate failures.
3. Verify `/health/ready/uploads` and, when needed, `/health/ready/search`.
4. Have an authorized operator use a future explicit replay tool. Do not manually publish an unknown old payload to the main queue.
5. Confirm the new attempt has an audit trail and does not duplicate a completed document/index.

## Boundary

U7 exposes diagnostics and documents recovery. It does not yet include a permissioned DLQ replay API, automatic lease repair, alert routing, or a dashboard. Those write controls need idempotency safeguards and operator authorization first.
