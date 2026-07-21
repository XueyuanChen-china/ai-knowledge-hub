#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${BACKEND_DIR}"
exec ./.venv/bin/celery -A app.celery_app.celery_app worker \
  --loglevel=info \
  --queues="${CELERY_TASK_DEFAULT_QUEUE:-ai_knowledge_hub}"
