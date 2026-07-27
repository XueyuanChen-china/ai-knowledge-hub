#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker compose -f "${BACKEND_DIR}/docker-compose.redis.yml" up -d
docker compose -f "${BACKEND_DIR}/docker-compose.redis.yml" ps
