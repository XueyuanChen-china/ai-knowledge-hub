#!/usr/bin/env bash
set -euo pipefail

# Worker 只等待依赖和已完成的 migration，绝不自行执行 Alembic upgrade，
# 避免多个 Worker 副本在启动时并发改表。
cd "$(dirname "$0")/.."

python scripts/wait_for_dependencies.py \
  --services postgres elasticsearch rabbitmq redis \
  --timeout-seconds "${DEPENDENCY_WAIT_TIMEOUT_SECONDS:-120}"

python -c "from app.db.database import check_database_ready; check_database_ready()"

exec celery -A app.celery_app.celery_app worker \
  --loglevel="${CELERY_LOG_LEVEL:-info}" \
  --queues="${CELERY_TASK_DEFAULT_QUEUE:-ai_knowledge_hub}"
