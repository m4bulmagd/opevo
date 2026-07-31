#!/usr/bin/env bash

set -euo pipefail

if [ "$(basename -- "$0")" = docker ]; then
  control_directory=${PRESVO_OWNERSHIP_TEST_CONTROL_DIRECTORY:?}
  repository_root=${PRESVO_OWNERSHIP_TEST_REPOSITORY_ROOT:?}
  output_directory=""
  previous_argument=""
  for argument in "$@"; do
    if [ "$previous_argument" = --output ]; then
      output_directory=${argument#type=local,dest=}
      break
    fi
    previous_argument=$argument
  done
  if [ -z "$output_directory" ]; then
    printf 'fake docker could not resolve the output directory.\n' >&2
    exit 97
  fi

  fixture_directory=""
  fixture_count=0
  while IFS= read -r candidate; do
    fixture_directory=$candidate
    fixture_count=$((fixture_count + 1))
  done < <(
    find "$repository_root/tests/docker" -mindepth 1 -maxdepth 1 \
      -type d -name '.root-context-env.*' -print
  )
  if [ "$fixture_count" -ne 1 ]; then
    printf 'fake docker expected one fixture directory; found %s.\n' \
      "$fixture_count" >&2
    exit 98
  fi

  paths_file="$control_directory/paths"
  {
    printf '%s\n' "$fixture_directory"
    printf '%s\n' "$output_directory"
  } >"$paths_file.pending"
  mv -- "$paths_file.pending" "$paths_file"
  : >"$control_directory/ready"
  while [ ! -e "$control_directory/release" ]; do
    sleep 0.02
  done

  docker_status=$(<"$control_directory/docker-status")
  if [ "$docker_status" -eq 0 ]; then
    fixture_name=$(basename -- "$fixture_directory")
    mkdir -p \
      "$output_directory/context/apps/api" \
      "$output_directory/context/apps/agent" \
      "$output_directory/context/libs/shared/src" \
      "$output_directory/context/tests/docker/$fixture_name/nested"
    : >"$output_directory/context/apps/api/pyproject.toml"
    : >"$output_directory/context/apps/agent/pyproject.toml"
    : >"$output_directory/context/libs/shared/pyproject.toml"
    printf 'reviewed-example-value\n' \
      >"$output_directory/context/tests/docker/$fixture_name/nested/.env.example"
  fi
  exit "$docker_status"
fi

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
sentinel_path="$repository_root/.env.contract-context-probe"
control_directory=""
control_identity=""
control_marker=""
probe_pid=""
cleanup_status=0

replacement_sentinel=""
replacement_sentinel_identity=""
replacement_sentinel_marker=""
replacement_fixture=""
replacement_fixture_identity=""
replacement_fixture_marker=""
replacement_output=""
replacement_output_identity=""
replacement_output_marker=""
original_sentinel=""
original_sentinel_identity=""
original_sentinel_marker=""
original_fixture=""
original_fixture_identity=""
original_fixture_marker=""
original_output=""
original_output_identity=""
original_output_marker=""

path_identity() {
  stat -c '%d:%i:%f' -- "$1"
}

new_marker() {
  local marker
  IFS= read -r marker </proc/sys/kernel/random/uuid
  printf 'presvo-ownership-test:%s\n' "$marker"
}

promote_cleanup_failure() {
  if [ "$cleanup_status" -eq 0 ]; then
    cleanup_status=1
  fi
}

cleanup_owned_file() {
  local path=$1
  local expected_identity=$2
  local expected_marker=$3
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return 0
  fi
  local current_identity=""
  if [ -f "$path" ] && [ ! -L "$path" ] &&
    current_identity=$(path_identity "$path") &&
    [ "$current_identity" = "$expected_identity" ] &&
    grep -Fxq "$expected_marker" "$path"; then
    if ! rm -- "$path"; then
      printf 'failed to remove owned test file %s.\n' "$path" >&2
      promote_cleanup_failure
      return 1
    fi
  else
    printf 'test file %s changed ownership; refusing cleanup.\n' "$path" >&2
    promote_cleanup_failure
    return 1
  fi
  return 0
}

cleanup_owned_directory() {
  local path=$1
  local expected_identity=$2
  local expected_marker=$3
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return 0
  fi
  local current_identity=""
  local marker_path="$path/.ownership-test-marker"
  if [ -d "$path" ] && [ ! -L "$path" ] &&
    current_identity=$(path_identity "$path") &&
    [ "$current_identity" = "$expected_identity" ] &&
    [ -f "$marker_path" ] && [ ! -L "$marker_path" ] &&
    grep -Fxq "$expected_marker" "$marker_path"; then
    if ! rm -rf -- "$path"; then
      printf 'failed to remove owned test directory %s.\n' "$path" >&2
      promote_cleanup_failure
      return 1
    fi
  else
    printf 'test directory %s changed ownership; refusing cleanup.\n' "$path" >&2
    promote_cleanup_failure
    return 1
  fi
  return 0
}

cleanup() {
  original_status=$?
  cleanup_status=$original_status
  trap - EXIT

  if [ -n "$probe_pid" ]; then
    kill "$probe_pid" 2>/dev/null || true
    wait "$probe_pid" 2>/dev/null || true
  fi
  cleanup_owned_file \
    "$replacement_sentinel" \
    "$replacement_sentinel_identity" \
    "$replacement_sentinel_marker" || true
  cleanup_owned_directory \
    "$replacement_fixture" \
    "$replacement_fixture_identity" \
    "$replacement_fixture_marker" || true
  cleanup_owned_directory \
    "$replacement_output" \
    "$replacement_output_identity" \
    "$replacement_output_marker" || true
  cleanup_owned_file \
    "$original_sentinel" \
    "$original_sentinel_identity" \
    "$original_sentinel_marker" || true
  cleanup_owned_directory \
    "$original_fixture" \
    "$original_fixture_identity" \
    "$original_fixture_marker" || true
  cleanup_owned_directory \
    "$original_output" \
    "$original_output_identity" \
    "$original_output_marker" || true
  cleanup_owned_directory \
    "$control_directory" \
    "$control_identity" \
    "$control_marker" || true

  exit "$cleanup_status"
}

trap cleanup EXIT

if [ -e "$sentinel_path" ] || [ -L "$sentinel_path" ]; then
  printf '%s already exists; refusing to run the ownership test.\n' \
    "$sentinel_path" >&2
  exit 1
fi
if find "$repository_root/tests/docker" -mindepth 1 -maxdepth 1 \
  -type d -name '.root-context-env.*' -print -quit | grep -q .; then
  printf 'a root-context fixture already exists; refusing ownership test.\n' >&2
  exit 1
fi

control_directory=$(mktemp -d /tmp/presvo-root-context-ownership.XXXXXX)
control_marker=$(new_marker)
printf '%s\n' "$control_marker" >"$control_directory/.ownership-test-marker"
control_identity=$(path_identity "$control_directory")
mkdir "$control_directory/bin"
ln -s "$repository_root/tests/docker/test_root_context_hygiene_cleanup_ownership.sh" \
  "$control_directory/bin/docker"

run_case() {
  local case_name=$1
  local docker_status=$2
  local expected_status=$3
  local probe_output_path="$control_directory/$case_name.output"
  local paths_file="$control_directory/paths"

  rm -f -- \
    "$control_directory/ready" \
    "$control_directory/release" \
    "$paths_file" \
    "$paths_file.pending"
  printf '%s\n' "$docker_status" >"$control_directory/docker-status"

  PATH="$control_directory/bin:$PATH" \
    PRESVO_OWNERSHIP_TEST_CONTROL_DIRECTORY="$control_directory" \
    PRESVO_OWNERSHIP_TEST_REPOSITORY_ROOT="$repository_root" \
    "$repository_root/tests/docker/test_root_context_hygiene.sh" \
    >"$probe_output_path" 2>&1 &
  probe_pid=$!

  local attempt
  for attempt in $(seq 1 250); do
    if [ -e "$control_directory/ready" ]; then
      break
    fi
    if ! kill -0 "$probe_pid" 2>/dev/null; then
      printf 'main probe exited before fake docker reached the barrier.\n' >&2
      sed -n '1,100p' "$probe_output_path" >&2
      return 1
    fi
    sleep 0.02
  done
  if [ ! -e "$control_directory/ready" ]; then
    printf 'timed out waiting for the fake docker barrier.\n' >&2
    return 1
  fi

  local paths=()
  mapfile -t paths <"$paths_file"
  if [ "${#paths[@]}" -ne 2 ]; then
    printf 'expected fixture and output paths from fake docker.\n' >&2
    return 1
  fi
  local fixture_path=${paths[0]}
  local output_path=${paths[1]}
  case "$fixture_path" in
    "$repository_root/tests/docker/.root-context-env."*) ;;
    *) printf 'unsafe fixture path %s.\n' "$fixture_path" >&2; return 1 ;;
  esac
  case "$output_path" in
    /tmp/presvo-contract-context.*) ;;
    *) printf 'unsafe output path %s.\n' "$output_path" >&2; return 1 ;;
  esac
  if [ ! -f "$sentinel_path" ] || [ -L "$sentinel_path" ] ||
    [ ! -d "$fixture_path" ] || [ -L "$fixture_path" ] ||
    [ ! -d "$output_path" ] || [ -L "$output_path" ]; then
    printf 'probe-owned objects were not created as expected.\n' >&2
    return 1
  fi

  original_sentinel="$control_directory/$case_name.original-sentinel"
  original_fixture="$control_directory/$case_name.original-fixture"
  original_output="$control_directory/$case_name.original-output"
  mv -- "$sentinel_path" "$original_sentinel"
  mv -- "$fixture_path" "$original_fixture"
  mv -- "$output_path" "$original_output"
  original_sentinel_marker=$(new_marker)
  original_fixture_marker=$(new_marker)
  original_output_marker=$(new_marker)
  printf '%s\n' "$original_sentinel_marker" >"$original_sentinel"
  printf '%s\n' "$original_fixture_marker" \
    >"$original_fixture/.ownership-test-marker"
  printf '%s\n' "$original_output_marker" \
    >"$original_output/.ownership-test-marker"
  original_sentinel_identity=$(path_identity "$original_sentinel")
  original_fixture_identity=$(path_identity "$original_fixture")
  original_output_identity=$(path_identity "$original_output")

  replacement_sentinel=$sentinel_path
  replacement_fixture=$fixture_path
  replacement_output=$output_path
  replacement_sentinel_marker=$(new_marker)
  replacement_fixture_marker=$(new_marker)
  replacement_output_marker=$(new_marker)
  printf '%s\n' "$replacement_sentinel_marker" >"$replacement_sentinel"
  mkdir -- "$replacement_fixture" "$replacement_output"
  printf '%s\n' "$replacement_fixture_marker" \
    >"$replacement_fixture/.ownership-test-marker"
  printf '%s\n' "$replacement_output_marker" \
    >"$replacement_output/.ownership-test-marker"
  replacement_sentinel_identity=$(path_identity "$replacement_sentinel")
  replacement_fixture_identity=$(path_identity "$replacement_fixture")
  replacement_output_identity=$(path_identity "$replacement_output")

  : >"$control_directory/release"
  local actual_status=0
  if wait "$probe_pid"; then
    actual_status=0
  else
    actual_status=$?
  fi
  probe_pid=""

  if [ "$actual_status" -ne "$expected_status" ]; then
    printf 'expected probe status %s; got %s.\n' \
      "$expected_status" "$actual_status" >&2
    sed -n '1,120p' "$probe_output_path" >&2
    return 1
  fi
  for path in "$replacement_sentinel" "$replacement_fixture" "$replacement_output"; do
    if [ ! -e "$path" ] && [ ! -L "$path" ]; then
      printf 'replacement %s did not survive actual probe cleanup.\n' "$path" >&2
      return 1
    fi
    if ! grep -Fq "$path ownership changed before cleanup; refusing to remove it." \
      "$probe_output_path"; then
      printf 'missing ownership-refusal diagnostic for %s.\n' "$path" >&2
      sed -n '1,120p' "$probe_output_path" >&2
      return 1
    fi
  done

  cleanup_owned_file \
    "$replacement_sentinel" \
    "$replacement_sentinel_identity" \
    "$replacement_sentinel_marker"
  replacement_sentinel=""
  cleanup_owned_directory \
    "$replacement_fixture" \
    "$replacement_fixture_identity" \
    "$replacement_fixture_marker"
  replacement_fixture=""
  cleanup_owned_directory \
    "$replacement_output" \
    "$replacement_output_identity" \
    "$replacement_output_marker"
  replacement_output=""
  cleanup_owned_file \
    "$original_sentinel" \
    "$original_sentinel_identity" \
    "$original_sentinel_marker"
  original_sentinel=""
  cleanup_owned_directory \
    "$original_fixture" \
    "$original_fixture_identity" \
    "$original_fixture_marker"
  original_fixture=""
  cleanup_owned_directory \
    "$original_output" \
    "$original_output_identity" \
    "$original_output_marker"
  original_output=""
}

run_case cleanup_promotes_success 0 1
run_case cleanup_preserves_primary_failure 42 42

printf 'actual probe cleanup ownership checks passed\n'
