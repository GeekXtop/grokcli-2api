#!/usr/bin/env bash
set -euo pipefail

# Diagnostics intentionally use the standard `docker compose ps` and
# `docker compose logs` commands so failures are actionable and discoverable.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT_DIR/compose.dev.yml")
API_PORT="${GROK2API_DEV_API_PORT:-40081}"
SOLVER_PORT="${GROK2API_DEV_SOLVER_PORT:-5072}"
HEALTH_TIMEOUT_SECONDS="${GROK2API_DEV_HEALTH_TIMEOUT_SECONDS:-90}"
PROBE_TIMEOUT_SECONDS="${GROK2API_DEV_PROBE_TIMEOUT_SECONDS:-3}"

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

health_deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))

probe() {
  local url="$1"
  local remaining=$((health_deadline - SECONDS))
  local timeout="$PROBE_TIMEOUT_SECONDS"

  ((remaining > 0)) || return 1
  if ((timeout > remaining)); then
    timeout="$remaining"
  fi

  curl --connect-timeout "$timeout" --max-time "$timeout" -fsS "$url" >/dev/null
}

while ((SECONDS < health_deadline)); do
  if probe "http://127.0.0.1:${API_PORT}/health" \
      && probe "http://127.0.0.1:${API_PORT}/ready" \
      && probe "http://127.0.0.1:${SOLVER_PORT}/health"; then
    "${COMPOSE[@]}" ps
    exit 0
  fi
  ((SECONDS < health_deadline)) || break
  sleep 1
done

echo "[g2a-dev-pull] health check timed out after ${HEALTH_TIMEOUT_SECONDS}s" >&2
"${COMPOSE[@]}" ps || true
"${COMPOSE[@]}" logs --tail 120 api-dev solver-dev assets-dev || true
exit 1
