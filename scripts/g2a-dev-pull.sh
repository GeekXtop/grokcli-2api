#!/usr/bin/env bash
set -euo pipefail

# The update is a small container transaction. Before Compose replaces live
# containers, create stopped, no-label backup containers in a separate Compose
# project. They use the old image IDs and `volumes_from`, so the old data
# volumes remain attached even if Compose removes the renamed live containers.
# If creation or readiness fails, only replacement containers are force-removed
# (without `-v`) and the backups are renamed back and started.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${GROK2API_DEV_API_PORT:-40081}"
SOLVER_PORT="${GROK2API_DEV_SOLVER_PORT:-5072}"
HEALTH_TIMEOUT_SECONDS="${GROK2API_DEV_HEALTH_TIMEOUT_SECONDS:-90}"
PROBE_TIMEOUT_SECONDS="${GROK2API_DEV_PROBE_TIMEOUT_SECONDS:-3}"
DEV_IMAGE="${GROK2API_DEV_IMAGE:-}"
SOURCE_MODE="${GROK2API_DEV_SOURCE_MODE:-mount}"

if [[ -z "$DEV_IMAGE" ]]; then
  # Compose project environment (for example `.env`/`--env-file`) is resolved
  # by Compose itself; do not source an env file into this shell. A missing
  # env file falls back to the documented default and is reported by `pull`.
  if resolved_image="$(
    cd "$ROOT_DIR" &&
      docker compose -f "$ROOT_DIR/compose.dev.yml" config --format json 2>/dev/null |
      python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["api-dev"]["image"])'
  )"; then
    DEV_IMAGE="$resolved_image"
  fi
fi
DEV_IMAGE="${DEV_IMAGE:-ghcr.io/geekxtop/grokcli-2api:dev}"

if [[ "$SOURCE_MODE" != mount && "$SOURCE_MODE" != image && "$SOURCE_MODE" != pinned ]]; then
  echo "[g2a-dev-pull] GROK2API_DEV_SOURCE_MODE must be mount, image, or pinned" >&2
  exit 2
fi

# A sha-dev tag is immutable by convention and is the documented rollback
# interface. The pinned overlay removes the host /app bind mount so all three
# watchers execute code from the selected image. The ordinary dev tag keeps
# the source mount and local hot reload path.
COMPOSE_FILES=(-f "$ROOT_DIR/compose.dev.yml")
if [[ "$SOURCE_MODE" == image || "$SOURCE_MODE" == pinned || ( "$DEV_IMAGE" == *:sha-dev-* && "$DEV_IMAGE" != *@* ) ]]; then
  COMPOSE_FILES+=(-f "$ROOT_DIR/compose.dev.pinned.yml")
fi
COMPOSE=(docker compose "${COMPOSE_FILES[@]}")

SERVICES=(api-dev solver-dev assets-dev)
declare -A SERVICE_NAMES=(
  [api-dev]=grokcli-2api-api-dev
  [solver-dev]=grokcli-2api-solver-dev
  [assets-dev]=grokcli-2api-assets-dev
)
TRANSACTION_ID="$(date +%s)-$$"
BACKUP_PROJECT="g2a-dev-backup-${TRANSACTION_ID}"
BACKUP_COMPOSE=(docker compose -p "$BACKUP_PROJECT" "${COMPOSE_FILES[@]}" -f "$ROOT_DIR/compose.dev.backup.yml")

TRANSACTION_ACTIVE=0
declare -A OLD_IDS=()
declare -A OLD_NAMES=()
declare -A OLD_IMAGES=()
declare -A RENAMED_OLD_NAMES=()
declare -A BACKUP_NAMES=()
declare -A BACKUP_IDS=()

container_exists() {
  docker inspect "$1" >/dev/null 2>&1
}

compose_ids() {
  local service="$1"
  local ids
  ids="$("${COMPOSE[@]}" ps -aq "$service" 2>/dev/null || true)"
  if [[ -z "$ids" ]]; then
    # Existing installations may have been created before this script's
    # transaction project existed. Discover their fixed names directly.
    ids="$(docker ps -aq --filter "name=^/${SERVICE_NAMES[$service]}$" 2>/dev/null || true)"
  fi
  printf '%s\n' "$ids"
}

container_name() {
  local id="$1"
  local name
  name="$(docker inspect --format '{{.Name}}' "$id" 2>/dev/null || true)"
  name="${name#/}"
  if [[ -n "$name" ]]; then
    printf '%s\n' "$name"
  else
    printf '%s\n' "$id"
  fi
}

container_image() {
  docker inspect --format '{{.Image}}' "$1" 2>/dev/null || true
}

backup_env_for() {
  local service="$1"
  local old_name="${OLD_NAMES[$service]-}"
  local old_image="${OLD_IMAGES[$service]-}"
  local backup_name="g2a-dev-backup-${service}-${TRANSACTION_ID}"
  case "$service" in
    api-dev)
      export GROK2API_DEV_OLD_API_NAME="$old_name"
      export GROK2API_DEV_BACKUP_API_IMAGE="$old_image"
      export GROK2API_DEV_BACKUP_API_NAME="$backup_name"
      ;;
    solver-dev)
      export GROK2API_DEV_OLD_SOLVER_NAME="$old_name"
      export GROK2API_DEV_BACKUP_SOLVER_IMAGE="$old_image"
      export GROK2API_DEV_BACKUP_SOLVER_NAME="$backup_name"
      ;;
    assets-dev)
      export GROK2API_DEV_OLD_ASSETS_NAME="$old_name"
      export GROK2API_DEV_BACKUP_ASSETS_IMAGE="$old_image"
      export GROK2API_DEV_BACKUP_ASSETS_NAME="$backup_name"
      ;;
  esac
  BACKUP_NAMES["$service"]="$backup_name"
}

create_backups() {
  local service id backup
  for service in "${SERVICES[@]}"; do
    id="$(compose_ids "$service" | sed -n '1p')"
    [[ -n "$id" ]] || continue

    OLD_IDS["$service"]="$id"
    OLD_NAMES["$service"]="$(container_name "$id")"
    OLD_IMAGES["$service"]="$(container_image "$id")"
    if [[ -z "${OLD_IMAGES[$service]}" ]]; then
      echo "[g2a-dev-pull] could not inspect old $service image" >&2
      return 1
    fi

    backup_env_for "$service"
    backup="${BACKUP_NAMES[$service]}"
    if ! "${BACKUP_COMPOSE[@]}" create --no-build --pull never "$service"; then
      echo "[g2a-dev-pull] could not create a backup for $service" >&2
      return 1
    fi
    BACKUP_IDS["$service"]="$("${BACKUP_COMPOSE[@]}" ps -aq "$service" 2>/dev/null | sed -n '1p')"
    if [[ -z "${BACKUP_IDS[$service]}" ]]; then
      BACKUP_IDS["$service"]="$(docker ps -aq --filter "name=^/${backup}$" 2>/dev/null | sed -n '1p')"
    fi
    if [[ -z "${BACKUP_IDS[$service]}" ]]; then
      echo "[g2a-dev-pull] backup container for $service was not created" >&2
      return 1
    fi
  done
}

reserve_old_containers() {
  TRANSACTION_ACTIVE=1
  local service id renamed
  for service in "${SERVICES[@]}"; do
    id="${OLD_IDS[$service]-}"
    [[ -n "$id" ]] || continue
    renamed="g2a-dev-old-${service}-${TRANSACTION_ID}"
    RENAMED_OLD_NAMES["$service"]="$renamed"
    if ! docker rename "$id" "$renamed"; then
      echo "[g2a-dev-pull] could not reserve existing $service container" >&2
      return 1
    fi
    if ! docker stop "$renamed"; then
      echo "[g2a-dev-pull] could not stop existing $service container" >&2
      return 1
    fi
  done
}

print_diagnostics() {
  # Equivalent to `docker compose ps` and `docker compose logs` with the
  # selected file set; keep diagnostics available even after a failed start.
  "${COMPOSE[@]}" ps || true
  "${COMPOSE[@]}" logs --tail 120 api-dev solver-dev assets-dev || true
}

remove_container_without_volumes() {
  local id="$1"
  # Deliberately omit --volumes/-v: no transaction cleanup may delete data.
  docker rm -f "$id" || true
}

rollback_transaction() {
  local service id candidate_ids backup backup_id original renamed old_id

  echo "[g2a-dev-pull] rolling back development containers" >&2

  # Compose may report a replacement and a renamed old container because
  # labels survive rename; skip saved old IDs and remove only replacements.
  for service in "${SERVICES[@]}"; do
    candidate_ids="$("${COMPOSE[@]}" ps -aq "$service" 2>/dev/null || true)"
    while IFS= read -r id; do
      [[ -n "$id" ]] || continue
      old_id="${OLD_IDS[$service]-}"
      [[ -n "$old_id" && "$id" == "$old_id" ]] && continue
      remove_container_without_volumes "$id"
    done <<< "$candidate_ids"
  done

  for service in "${SERVICES[@]}"; do
    backup="${BACKUP_NAMES[$service]-}"
    backup_id="${BACKUP_IDS[$service]-}"
    original="${OLD_NAMES[$service]-}"
    renamed="${RENAMED_OLD_NAMES[$service]-}"

    if [[ -z "$renamed" ]]; then
      if [[ -n "$backup" && -n "$backup_id" ]] && container_exists "$backup"; then
        remove_container_without_volumes "$backup"
      fi
      continue
    fi

    if [[ -n "$backup" && -n "$backup_id" ]] && container_exists "$backup"; then
      # If Compose left the renamed old container around, remove only that
      # container; the backup still holds the same data volume references.
      if [[ -n "$renamed" ]] && container_exists "$renamed"; then
        remove_container_without_volumes "$renamed"
      fi
      if docker rename "$backup" "$original"; then
        docker start "$original" || true
      else
        echo "[g2a-dev-pull] warning: could not restore $service backup" >&2
      fi
    elif [[ -n "$renamed" ]] && container_exists "$renamed"; then
      # Backup creation can fail before all services are cloned. Restore any
      # old container that was already renamed in that partial transaction.
      if docker rename "$renamed" "$original"; then
        docker start "$original" || true
      fi
    fi
  done

  TRANSACTION_ACTIVE=0
  echo "[g2a-dev-pull] rollback complete; existing containers and volumes were preserved" >&2
}

cleanup_successful_transaction() {
  local service backup renamed
  for service in "${SERVICES[@]}"; do
    renamed="${RENAMED_OLD_NAMES[$service]-}"
    backup="${BACKUP_NAMES[$service]-}"
    if [[ -n "$renamed" ]] && container_exists "$renamed"; then
      remove_container_without_volumes "$renamed"
    fi
    if [[ -n "$backup" ]] && container_exists "$backup"; then
      remove_container_without_volumes "$backup"
    fi
  done
  TRANSACTION_ACTIVE=0
}

on_exit() {
  local status=$?
  trap - EXIT
  if ((TRANSACTION_ACTIVE)); then
    rollback_transaction || true
  fi
  exit "$status"
}
trap on_exit EXIT

if ! (cd "$ROOT_DIR" && "${COMPOSE[@]}" pull); then
  echo "[g2a-dev-pull] image pull failed; existing containers were left untouched" >&2
  exit 1
fi

TRANSACTION_ACTIVE=1
if ! create_backups; then
  echo "[g2a-dev-pull] could not prepare update transaction" >&2
  exit 1
fi
if ! reserve_old_containers; then
  echo "[g2a-dev-pull] could not reserve existing containers" >&2
  exit 1
fi

if ! (cd "$ROOT_DIR" && "${COMPOSE[@]}" up -d --force-recreate --no-build); then
  echo "[g2a-dev-pull] compose start failed; restoring previous containers" >&2
  print_diagnostics
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
    cleanup_successful_transaction
    "${COMPOSE[@]}" ps || true
    exit 0
  fi
  ((SECONDS < health_deadline)) || break
  sleep 1
done

echo "[g2a-dev-pull] health check timed out after ${HEALTH_TIMEOUT_SECONDS}s; restoring previous containers" >&2
print_diagnostics
exit 1
