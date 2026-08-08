#!/usr/bin/env bash
set -euo pipefail

# API 是当前 Compose 环境唯一允许执行迁移的应用进程。
# 先等依赖，再显式升级业务 schema 和 LangGraph checkpoint schema，最后启动 Uvicorn。
cd "$(dirname "$0")/.."

python scripts/wait_for_dependencies.py \
  --services postgres elasticsearch rabbitmq redis \
  --timeout-seconds "${DEPENDENCY_WAIT_TIMEOUT_SECONDS:-120}"

alembic upgrade head
python scripts/setup_langgraph_checkpoints.py

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}"
