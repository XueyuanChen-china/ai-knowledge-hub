#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

docker compose -f "${BACKEND_DIR}/docker-compose.postgres.yml" up -d
docker compose -f "${BACKEND_DIR}/docker-compose.postgres.yml" ps
