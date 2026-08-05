#!/bin/sh
set -eu

PROJECT_NAME=presvo-e2e
COMPOSE_FILE=compose.dev.yaml
e2e_state_dir=

export WEB_PORT=3300
export API_PORT=5800
export POSTGRES_PORT=55432
export REDIS_PORT=56379
export MINIO_PORT=59000
export MINIO_CONSOLE_PORT=59001
export DASHBOARD_METRICS_REFERENCE_TIME=2026-07-29T12:00:00Z
export AUTH_MODE=local
export LOCAL_AUTH_TOKEN=presvo-local-development-token

compose() {
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

down_stack() {
  compose down --volumes --remove-orphans || true
}

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ]; then
    compose ps || true
    compose logs api worker-lifecycle worker-background web || true
  fi
  down_stack
  if [ -n "$e2e_state_dir" ] && [ -d "$e2e_state_dir" ]; then
    rm -rf -- "$e2e_state_dir"
  fi
  exit "$status"
}

handle_hup() {
  exit 129
}

handle_int() {
  exit 130
}

handle_term() {
  exit 143
}

trap cleanup EXIT
trap handle_hup HUP
trap handle_int INT
trap handle_term TERM

e2e_state_dir=$(mktemp -d)
export E2E_STATE_FILE="${e2e_state_dir}/account-lifecycle.json"
export E2E_API_BASE_URL="http://127.0.0.1:${API_PORT}"
export E2E_LOCAL_AUTH_TOKEN="$LOCAL_AUTH_TOKEN"
update_snapshots=${E2E_UPDATE_SNAPSHOTS:-${UPDATE_SNAPSHOTS:-0}}
e2e_focus=${E2E_FOCUS:-all}

if [ "$e2e_focus" != "all" ] && [ "$e2e_focus" != "configuration" ]; then
  echo "E2E_FOCUS must be 'all' or 'configuration'." >&2
  exit 2
fi

wait_for_health() {
  service=$1
  attempt=0
  while [ "$attempt" -lt 180 ]; do
    container_id=$(compose ps -q "$service")
    if [ -n "$container_id" ]; then
      state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")
      if [ "$state" = "healthy" ]; then
        return 0
      fi
      if [ "$state" = "unhealthy" ] || [ "$state" = "exited" ] || [ "$state" = "dead" ]; then
        compose logs "$service" || true
        return 1
      fi
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  compose logs "$service" || true
  return 1
}

wait_for_success() {
  service=$1
  attempt=0
  while [ "$attempt" -lt 180 ]; do
    container_id=$(compose ps -a -q "$service")
    if [ -n "$container_id" ]; then
      state=$(docker inspect --format '{{.State.Status}}' "$container_id")
      if [ "$state" = "exited" ]; then
        exit_code=$(docker inspect --format '{{.State.ExitCode}}' "$container_id")
        if [ "$exit_code" = "0" ]; then
          return 0
        fi
        compose logs "$service" || true
        return 1
      fi
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  compose logs "$service" || true
  return 1
}

down_stack
compose build migrate api worker-lifecycle worker-background web
compose up --detach postgres redis minio
wait_for_health postgres
wait_for_health redis
wait_for_health minio
compose up --detach minio-init migrate
wait_for_success minio-init
wait_for_success migrate
compose up --detach api worker-lifecycle worker-background web
wait_for_health api
wait_for_health worker-lifecycle
wait_for_health worker-background
wait_for_health web

if [ "$e2e_focus" = "all" ]; then
  if [ "$update_snapshots" = "1" ]; then
    E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
      npm --prefix apps/web run test:e2e -- tests/e2e/entry-activation-visual.spec.ts --update-snapshots
  else
    E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
      npm --prefix apps/web run test:e2e -- tests/e2e/entry-activation-visual.spec.ts
  fi
fi

E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
  npm --prefix apps/web run test:e2e -- tests/e2e/activation.spec.ts

if [ "$e2e_focus" = "all" ]; then
  compose exec -T postgres psql -U postgres -d ai_call < scripts/seed-local-e2e-calls.sql

  if [ "$update_snapshots" = "1" ]; then
    E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
      npm --prefix apps/web run test:e2e -- tests/e2e/dashboard-visual.spec.ts --update-snapshots
  else
    E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
      npm --prefix apps/web run test:e2e -- tests/e2e/dashboard-visual.spec.ts
  fi

  if [ "$update_snapshots" = "1" ]; then
    E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
      npm --prefix apps/web run test:e2e -- tests/e2e/dashboard-calls-visual.spec.ts --update-snapshots
  else
    E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
      npm --prefix apps/web run test:e2e -- tests/e2e/dashboard-calls-visual.spec.ts
  fi
fi

if [ "$update_snapshots" = "1" ]; then
  E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
    npm --prefix apps/web run test:e2e -- tests/e2e/configuration-visual.spec.ts --update-snapshots
else
  E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
    npm --prefix apps/web run test:e2e -- tests/e2e/configuration-visual.spec.ts
fi

if [ "$e2e_focus" = "all" ]; then
  E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
    npm --prefix apps/web run test:e2e -- tests/e2e/deactivation-start.spec.ts

  compose restart api worker-lifecycle worker-background
  wait_for_health api
  wait_for_health worker-lifecycle
  wait_for_health worker-background

  E2E_AFTER_SERVICE_RESTART=true E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
    npm --prefix apps/web run test:e2e -- tests/e2e/restart-resume.spec.ts
fi
