#!/usr/bin/env bash
set -euo pipefail

# Diagnostics intentionally use the standard `docker compose ps` and
# `docker compose logs` commands so failures are actionable and discoverable.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT_DIR/compose.dev.yml")
API_PORT="${GROK2API_DEV_API_PORT:-40081}"
SOLVER_PORT="${GROK2API_DEV_SOLVER_PORT:-5072}"

if ! (cd "$ROOT_DIR" && "${COMPOSE[@]}" pull); then
  echo "[g2a-dev-pull] image pull failed; existing containers were left untouched" >&2
  exit 1
fi

if ! (cd "$ROOT_DIR" && "${COMPOSE[@]}" up -d --force-recreate --no-build); then
  echo "[g2a-dev-pull] compose start failed" >&2
  "${COMPOSE[@]}" ps || true
  "${COMPOSE[@]}" logs --tail 80 api-dev solver-dev assets-dev || true
  exit 1
fi

for attempt in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null \
      && curl -fsS "http://127.0.0.1:${API_PORT}/ready" >/dev/null \
      && curl -fsS "http://127.0.0.1:${SOLVER_PORT}/health" >/dev/null; then
    "${COMPOSE[@]}" ps
    exit 0
  fi
  sleep 1
done

echo "[g2a-dev-pull] health check timed out after 90s" >&2
"${COMPOSE[@]}" ps || true
"${COMPOSE[@]}" logs --tail 120 api-dev solver-dev assets-dev || true
exit 1
