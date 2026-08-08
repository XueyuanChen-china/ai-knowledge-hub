# Incident Runbook

## Scope

This runbook covers the API, Celery upload pipeline, PostgreSQL, Elasticsearch and RabbitMQ checks that are available in the current project. It is intentionally read-only: diagnosis must not silently modify jobs or replay messages.

## Correlation IDs

Every HTTP response contains `X-Request-ID` and `X-Trace-ID`. `request_id` identifies one HTTP request; `trace_id` identifies the end-to-end flow. If the caller does not provide a trace ID, the application starts one from the request ID.

For upload processing, the API passes the trace ID in Celery message headers. JSON logs and `upload_audit_logs.detail_json` therefore share `trace_id`, `upload_id`, `processing_job_id` and, on workers, `celery_task_id`.

Start troubleshooting with the response headers, then search API and worker logs by `trace_id`. Do not paste a JWT, password, OSS secret, LLM key, or complete presigned URL into tickets.

## Health Endpoints

| Endpoint | Meaning | Failure behavior |
| --- | --- | --- |
| `GET /health/live` | FastAPI process can answer HTTP. | Does not check dependencies; stays `200` during a database outage. |
| `GET /health/ready` | Normal PostgreSQL-backed APIs can serve traffic. | `503` when PostgreSQL is unavailable. |
| `GET /health/ready/search` | Search/index paths can serve traffic. | `503` and `elasticsearch=degraded` when ES is unavailable. |
| `GET /health/ready/uploads` | Upload post-processing can be dispatched. | `503` and `rabbitmq=degraded` when RabbitMQ is unavailable. |
| `GET /health/metrics` | In-process Prometheus text metrics. | Values reset when this process restarts. |

Use the endpoint matching the caller's responsibility. A RabbitMQ outage must not make a read-only knowledge-base endpoint appear unavailable.

## Diagnose One Upload

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend
./.venv/bin/python scripts/diagnose_upload_pipeline.py --upload-id upl_xxx
```

The command reads `upload_tasks`, stage jobs and audit events only. Check the last stage whose status is not `completed`, its error message, attempt count, Celery task ID, and the shared trace context in audit details.

## Diagnose Stuck Leases

```bash
cd /Users/xueyuanchen.x/Desktop/ai-knowledge-hub/backend
./.venv/bin/python scripts/diagnose_upload_pipeline.py --stuck-leases
```

An expired `running` lease indicates a worker crash, long-running operation, or timeout mismatch. Record the job ID and trace ID first. This U7 command does not repair it automatically; recovery policy remains an explicit operator decision.

## First Response Checklist

1. Call the matching readiness endpoint and save its component statuses.
2. Record `X-Trace-ID` from the failing request.
3. Search API and Celery worker JSON logs by that trace ID.
4. For uploads, run the diagnostic command and compare job state with RabbitMQ/DLQ counts.
5. Confirm the external dependency before retrying: PostgreSQL, ES, RabbitMQ, OSS, or Qwen.
6. Retry or replay only through a documented, authorized follow-up tool. U7 deliberately provides no write/replay command.
