#!/bin/sh
set -eu

PROJECT_NAME=presvo-e2e
COMPOSE_FILE=compose.dev.yaml

export WEB_PORT=3300
export API_PORT=5800
export POSTGRES_PORT=55432
export REDIS_PORT=56379
export MINIO_PORT=59000
export MINIO_CONSOLE_PORT=59001

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
    compose logs api worker web || true
  fi
  down_stack
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

wait_for_running() {
  service=$1
  attempt=0
  while [ "$attempt" -lt 60 ]; do
    container_id=$(compose ps -q "$service")
    if [ -n "$container_id" ]; then
      state=$(docker inspect --format '{{.State.Status}}' "$container_id")
      if [ "$state" = "running" ]; then
        return 0
      fi
      if [ "$state" = "exited" ] || [ "$state" = "dead" ]; then
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
compose build migrate api worker web
compose up --detach postgres redis minio
wait_for_health postgres
wait_for_health redis
wait_for_health minio
compose up --detach minio-init migrate
wait_for_success minio-init
wait_for_success migrate
compose up --detach api worker web
wait_for_health api
wait_for_running worker
wait_for_health web

E2E_BASE_URL="http://127.0.0.1:${WEB_PORT}" npm --prefix apps/web run test:e2e
