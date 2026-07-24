#!/usr/bin/env bash
set -euo pipefail

# 统一从 backend 目录执行 Alembic，避免不同当前目录导致脚本找不到 app/alembic.ini。
cd "$(dirname "$0")/.."

command_name="${1:-upgrade}"

case "$command_name" in
  upgrade)
    ./.venv/bin/alembic upgrade head
    ;;
  current)
    ./.venv/bin/alembic current
    ;;
  stamp-existing)
    if [[ "${CONFIRM_EXISTING_SCHEMA:-}" != "yes" ]]; then
      echo "Refusing to stamp without CONFIRM_EXISTING_SCHEMA=yes" >&2
      exit 1
    fi
    echo "Stamping the existing schema at the baseline. Back up PostgreSQL first." >&2
    ./.venv/bin/alembic stamp c544b5601674
    ;;
  downgrade)
    ./.venv/bin/alembic downgrade "${2:-base}"
    ;;
  *)
    echo "Usage: $0 {upgrade|current|stamp-existing|downgrade [revision]}" >&2
    exit 2
    ;;
esac
