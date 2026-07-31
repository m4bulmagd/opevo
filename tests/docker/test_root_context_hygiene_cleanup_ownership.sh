#!/usr/bin/env bash

set -euo pipefail

invoked_name=$(basename -- "$0")

if [ "$invoked_name" = docker ]; then
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
  mv -T -- "$paths_file.pending" "$paths_file"
  : >"$control_directory/docker-ready"
  while [ ! -e "$control_directory/docker-release" ]; do
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

if [ "$invoked_name" = rm ]; then
  control_directory=${PRESVO_OWNERSHIP_TEST_CONTROL_DIRECTORY:?}
  real_rm=${PRESVO_OWNERSHIP_TEST_REAL_RM:?}
  test_mode=${PRESVO_OWNERSHIP_TEST_MODE:-}
  target=${!#}

  if [ "$test_mode" != post_validation ]; then
    exec "$real_rm" "$@"
  fi

  repository_root=${PRESVO_OWNERSHIP_TEST_REPOSITORY_ROOT:?}
  case "$target" in
    "$repository_root/.env.contract-context-probe" | \
    "$repository_root/tests/docker/.root-context-env."* | \
    /tmp/presvo-contract-context.* | \
    "$repository_root/tests/docker/.root-context-cleanup."*/sentinel | \
    "$repository_root/tests/docker/.root-context-cleanup."*/fixture | \
    /tmp/presvo-contract-context-cleanup.*/output)
      ;;
    *)
      exec "$real_rm" "$@"
      ;;
  esac

  printf '%s\n' "$target" >"$control_directory/delete-target.pending"
  mv -T -- \
    "$control_directory/delete-target.pending" \
    "$control_directory/delete-target"
  : >"$control_directory/delete-ready"
  while [ ! -e "$control_directory/delete-release" ]; do
    sleep 0.02
  done

  delete_status=0
  if "$real_rm" "$@"; then
    delete_status=0
  else
    delete_status=$?
  fi
  printf '%s\n' "$delete_status" >"$control_directory/delete-complete"
  while [ ! -e "$control_directory/delete-ack" ]; do
    sleep 0.02
  done
  "$real_rm" -f -- "$control_directory/delete-ack"
  exit "$delete_status"
fi

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
sentinel_path="$repository_root/.env.contract-context-probe"
real_rm=$(command -v rm)
control_directory=""
control_identity=""
control_marker=""
repo_cleanup_directory=""
repo_cleanup_identity=""
tmp_cleanup_directory=""
tmp_cleanup_identity=""
probe_pid=""
cleanup_status=0
case_name=""

fixture_path=""
output_path=""
replacement_sentinel=""
replacement_sentinel_identity=""
replacement_sentinel_marker=""
replacement_fixture=""
replacement_fixture_identity=""
replacement_fixture_marker=""
replacement_output=""
replacement_output_identity=""
replacement_output_marker=""
held_sentinel=""
held_sentinel_identity=""
held_sentinel_marker=""
held_fixture=""
held_fixture_identity=""
held_fixture_marker=""
held_output=""
held_output_identity=""
held_output_marker=""

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

wait_for_file() {
  local path=$1
  local description=$2
  local attempt
  for attempt in $(seq 1 250); do
    if [ -e "$path" ]; then
      return 0
    fi
    if [ -n "$probe_pid" ] && ! kill -0 "$probe_pid" 2>/dev/null; then
      printf 'probe exited while waiting for %s.\n' "$description" >&2
      return 1
    fi
    sleep 0.02
  done
  printf 'timed out waiting for %s.\n' "$description" >&2
  return 1
}

remove_quarantined_file() {
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
    if ! "$real_rm" -- "$path"; then
      printf 'failed to remove quarantined harness file %s.\n' "$path" >&2
      promote_cleanup_failure
      return 1
    fi
  else
    printf 'quarantined harness file %s failed ownership validation.\n' \
      "$path" >&2
    promote_cleanup_failure
    return 1
  fi
}

remove_quarantined_directory() {
  local path=$1
  local expected_identity=$2
  local expected_marker=$3
  local marker_name=$4
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return 0
  fi
  local current_identity=""
  local marker_path="$path/$marker_name"
  if [ -d "$path" ] && [ ! -L "$path" ] &&
    current_identity=$(path_identity "$path") &&
    [ "$current_identity" = "$expected_identity" ] &&
    [ -f "$marker_path" ] && [ ! -L "$marker_path" ] &&
    grep -Fxq "$expected_marker" "$marker_path"; then
    if ! "$real_rm" -rf -- "$path"; then
      printf 'failed to remove quarantined harness directory %s.\n' \
        "$path" >&2
      promote_cleanup_failure
      return 1
    fi
  else
    printf 'quarantined harness directory %s failed ownership validation.\n' \
      "$path" >&2
    promote_cleanup_failure
    return 1
  fi
}

quarantine_shared_file() {
  local path=$1
  local expected_identity=$2
  local expected_marker=$3
  local target=$4
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return 0
  fi
  if ! mv -T -- "$path" "$target"; then
    printf 'failed to quarantine harness file %s.\n' "$path" >&2
    promote_cleanup_failure
    return 1
  fi
  remove_quarantined_file \
    "$target" "$expected_identity" "$expected_marker"
}

quarantine_shared_directory() {
  local path=$1
  local expected_identity=$2
  local expected_marker=$3
  local marker_name=$4
  local target=$5
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return 0
  fi
  if ! mv -T -- "$path" "$target"; then
    printf 'failed to quarantine harness directory %s.\n' "$path" >&2
    promote_cleanup_failure
    return 1
  fi
  remove_quarantined_directory \
    "$target" "$expected_identity" "$expected_marker" "$marker_name"
}

cleanup_case_artifacts() {
  quarantine_shared_file \
    "$replacement_sentinel" \
    "$replacement_sentinel_identity" \
    "$replacement_sentinel_marker" \
    "$repo_cleanup_directory/$case_name.cleanup-replacement-sentinel" || true
  replacement_sentinel=""
  quarantine_shared_directory \
    "$replacement_fixture" \
    "$replacement_fixture_identity" \
    "$replacement_fixture_marker" \
    .ownership-test-marker \
    "$repo_cleanup_directory/$case_name.cleanup-replacement-fixture" || true
  replacement_fixture=""
  quarantine_shared_directory \
    "$replacement_output" \
    "$replacement_output_identity" \
    "$replacement_output_marker" \
    .ownership-test-marker \
    "$tmp_cleanup_directory/$case_name.cleanup-replacement-output" || true
  replacement_output=""

  remove_quarantined_file \
    "$held_sentinel" "$held_sentinel_identity" "$held_sentinel_marker" || true
  held_sentinel=""
  remove_quarantined_directory \
    "$held_fixture" \
    "$held_fixture_identity" \
    "$held_fixture_marker" \
    .env.contract-context-owner || true
  held_fixture=""
  remove_quarantined_directory \
    "$held_output" \
    "$held_output_identity" \
    "$held_output_marker" \
    .env.contract-context-owner || true
  held_output=""
}

cleanup_private_root() {
  local path=$1
  local expected_identity=$2
  if [ -z "$path" ]; then
    return 0
  fi
  local current_identity=""
  if [ -d "$path" ] && [ ! -L "$path" ] &&
    current_identity=$(path_identity "$path") &&
    [ "$current_identity" = "$expected_identity" ]; then
    if ! rmdir -- "$path"; then
      printf 'harness quarantine root retained because it is not empty: %s.\n' \
        "$path" >&2
      promote_cleanup_failure
      return 1
    fi
  else
    printf 'harness quarantine root changed ownership: %s.\n' "$path" >&2
    promote_cleanup_failure
    return 1
  fi
}

cleanup() {
  original_status=$?
  cleanup_status=$original_status
  trap - EXIT

  if [ -n "$probe_pid" ]; then
    : >"$control_directory/docker-release" 2>/dev/null || true
    : >"$control_directory/delete-release" 2>/dev/null || true
    : >"$control_directory/delete-ack" 2>/dev/null || true
    kill "$probe_pid" 2>/dev/null || true
    wait "$probe_pid" 2>/dev/null || true
  fi
  cleanup_case_artifacts

  if [ -n "$control_directory" ] &&
    { [ -e "$control_directory" ] || [ -L "$control_directory" ]; }; then
    control_quarantine="$tmp_cleanup_directory/control-directory"
    if mv -T -- "$control_directory" "$control_quarantine"; then
      remove_quarantined_directory \
        "$control_quarantine" \
        "$control_identity" \
        "$control_marker" \
        .ownership-test-marker || true
    else
      printf 'failed to quarantine harness control directory %s.\n' \
        "$control_directory" >&2
      promote_cleanup_failure
    fi
  fi
  cleanup_private_root "$repo_cleanup_directory" "$repo_cleanup_identity" || true
  cleanup_private_root "$tmp_cleanup_directory" "$tmp_cleanup_identity" || true

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

repo_cleanup_directory=$(mktemp -d \
  "$repository_root/tests/docker/.root-context-ownership-cleanup.XXXXXX")
repo_cleanup_identity=$(path_identity "$repo_cleanup_directory")
tmp_cleanup_directory=$(mktemp -d \
  /tmp/presvo-root-context-ownership-cleanup.XXXXXX)
tmp_cleanup_identity=$(path_identity "$tmp_cleanup_directory")
control_directory=$(mktemp -d /tmp/presvo-root-context-ownership.XXXXXX)
control_marker=$(new_marker)
printf '%s\n' "$control_marker" >"$control_directory/.ownership-test-marker"
control_identity=$(path_identity "$control_directory")
mkdir "$control_directory/bin"
ln -s "$repository_root/tests/docker/test_root_context_hygiene_cleanup_ownership.sh" \
  "$control_directory/bin/docker"
ln -s "$repository_root/tests/docker/test_root_context_hygiene_cleanup_ownership.sh" \
  "$control_directory/bin/rm"

clear_barriers() {
  "$real_rm" -f -- \
    "$control_directory/docker-ready" \
    "$control_directory/docker-release" \
    "$control_directory/delete-ready" \
    "$control_directory/delete-release" \
    "$control_directory/delete-complete" \
    "$control_directory/delete-target" \
    "$control_directory/delete-target.pending" \
    "$control_directory/delete-ack" \
    "$control_directory/paths" \
    "$control_directory/paths.pending"
}

start_probe() {
  local mode=$1
  local docker_status=$2
  local probe_output_path=$3
  clear_barriers
  printf '%s\n' "$docker_status" >"$control_directory/docker-status"
  PATH="$control_directory/bin:$PATH" \
    PRESVO_OWNERSHIP_TEST_CONTROL_DIRECTORY="$control_directory" \
    PRESVO_OWNERSHIP_TEST_REPOSITORY_ROOT="$repository_root" \
    PRESVO_OWNERSHIP_TEST_REAL_RM="$real_rm" \
    PRESVO_OWNERSHIP_TEST_MODE="$mode" \
    "$repository_root/tests/docker/test_root_context_hygiene.sh" \
    >"$probe_output_path" 2>&1 &
  probe_pid=$!
  wait_for_file "$control_directory/docker-ready" 'the fake Docker barrier'
  local paths=()
  mapfile -t paths <"$control_directory/paths"
  if [ "${#paths[@]}" -ne 2 ]; then
    printf 'expected fixture and output paths from fake docker.\n' >&2
    return 1
  fi
  fixture_path=${paths[0]}
  output_path=${paths[1]}
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
}

wait_for_probe() {
  local expected_status=$1
  local probe_output_path=$2
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
    sed -n '1,160p' "$probe_output_path" >&2
    return 1
  fi
}

create_replacements() {
  replacement_sentinel=$sentinel_path
  replacement_fixture=$fixture_path
  replacement_output=$output_path
  replacement_sentinel_marker=$(new_marker)
  replacement_fixture_marker=$(new_marker)
  replacement_output_marker=$(new_marker)
  (set -o noclobber; printf '%s\n' "$replacement_sentinel_marker" \
    >"$replacement_sentinel")
  mkdir -- "$replacement_fixture" "$replacement_output"
  printf '%s\n' "$replacement_fixture_marker" \
    >"$replacement_fixture/.ownership-test-marker"
  printf '%s\n' "$replacement_output_marker" \
    >"$replacement_output/.ownership-test-marker"
  replacement_sentinel_identity=$(path_identity "$replacement_sentinel")
  replacement_fixture_identity=$(path_identity "$replacement_fixture")
  replacement_output_identity=$(path_identity "$replacement_output")
}

hold_originals() {
  held_sentinel="$repo_cleanup_directory/$case_name.held-sentinel"
  held_fixture="$repo_cleanup_directory/$case_name.held-fixture"
  held_output="$tmp_cleanup_directory/$case_name.held-output"
  held_sentinel_identity=$(path_identity "$sentinel_path")
  held_fixture_identity=$(path_identity "$fixture_path")
  held_output_identity=$(path_identity "$output_path")
  held_sentinel_marker=$(<"$sentinel_path")
  held_fixture_marker=$(<"$fixture_path/.env.contract-context-owner")
  held_output_marker=$(<"$output_path/.env.contract-context-owner")
  mv -T -- "$sentinel_path" "$held_sentinel"
  mv -T -- "$fixture_path" "$held_fixture"
  mv -T -- "$output_path" "$held_output"
}

assert_replacements_survive() {
  local probe_output_path=$1
  local path
  for path in \
    "$replacement_sentinel" \
    "$replacement_fixture" \
    "$replacement_output"; do
    if [ ! -e "$path" ] && [ ! -L "$path" ]; then
      printf 'replacement %s did not survive actual probe cleanup.\n' \
        "$path" >&2
      sed -n '1,160p' "$probe_output_path" >&2
      return 1
    fi
  done
  grep -Fxq "$replacement_sentinel_marker" "$replacement_sentinel"
  grep -Fxq \
    "$replacement_fixture_marker" \
    "$replacement_fixture/.ownership-test-marker"
  grep -Fxq \
    "$replacement_output_marker" \
    "$replacement_output/.ownership-test-marker"
}

run_pre_move_case() {
  case_name=$1
  local docker_status=$2
  local expected_status=$3
  local probe_output_path="$control_directory/$case_name.output"

  start_probe pre_move "$docker_status" "$probe_output_path"
  hold_originals
  create_replacements
  : >"$control_directory/docker-release"
  wait_for_probe "$expected_status" "$probe_output_path"
  assert_replacements_survive "$probe_output_path"
  local path
  for path in "$sentinel_path" "$fixture_path" "$output_path"; do
    if ! grep -Fq "$path ownership changed" "$probe_output_path"; then
      printf 'missing ownership-change diagnostic for %s.\n' "$path" >&2
      sed -n '1,160p' "$probe_output_path" >&2
      return 1
    fi
  done
  cleanup_case_artifacts
}

hold_old_delete_target() {
  local kind=$1
  local target=$2
  case "$kind" in
    sentinel)
      held_sentinel="$repo_cleanup_directory/$case_name.held-sentinel"
      held_sentinel_identity=$(path_identity "$target")
      held_sentinel_marker=$(<"$target")
      mv -T -- "$target" "$held_sentinel"
      ;;
    fixture)
      held_fixture="$repo_cleanup_directory/$case_name.held-fixture"
      held_fixture_identity=$(path_identity "$target")
      held_fixture_marker=$(<"$target/.env.contract-context-owner")
      mv -T -- "$target" "$held_fixture"
      ;;
    output)
      held_output="$tmp_cleanup_directory/$case_name.held-output"
      held_output_identity=$(path_identity "$target")
      held_output_marker=$(<"$target/.env.contract-context-owner")
      mv -T -- "$target" "$held_output"
      ;;
  esac
}

classify_delete_target() {
  local expected_kind=$1
  local target=$2
  local original_path=""
  local original_parent=""
  case "$expected_kind" in
    sentinel)
      original_path=$sentinel_path
      original_parent=$repository_root
      ;;
    fixture)
      original_path=$fixture_path
      original_parent="$repository_root/tests/docker"
      ;;
    output)
      original_path=$output_path
      original_parent=/tmp
      ;;
  esac

  if [ "$target" = "$original_path" ]; then
    hold_old_delete_target "$expected_kind" "$target"
    return 0
  fi

  case "$expected_kind:$target" in
    sentinel:"$repository_root/tests/docker/.root-context-cleanup."*/sentinel | \
    fixture:"$repository_root/tests/docker/.root-context-cleanup."*/fixture | \
    output:/tmp/presvo-contract-context-cleanup.*/output)
      ;;
    *)
      printf 'unexpected %s delete target %s.\n' "$expected_kind" "$target" >&2
      return 1
      ;;
  esac
  if [ "$(stat -c '%d' -- "$target")" != \
    "$(stat -c '%d' -- "$original_parent")" ]; then
    printf '%s quarantine is not on the original filesystem: %s.\n' \
      "$expected_kind" "$target" >&2
    return 1
  fi
  if [ -e "$original_path" ] || [ -L "$original_path" ]; then
    printf '%s original path was not vacated before deletion: %s.\n' \
      "$expected_kind" "$original_path" >&2
    return 1
  fi
}

release_delete() {
  : >"$control_directory/delete-release"
  wait_for_file "$control_directory/delete-complete" 'the real delete completion'
  local delete_status
  delete_status=$(<"$control_directory/delete-complete")
  if [ "$delete_status" -ne 0 ]; then
    printf 'real delete returned status %s.\n' "$delete_status" >&2
    return 1
  fi
  "$real_rm" -f -- \
    "$control_directory/delete-ready" \
    "$control_directory/delete-release" \
    "$control_directory/delete-complete" \
    "$control_directory/delete-target"
  : >"$control_directory/delete-ack"
  local attempt
  for attempt in $(seq 1 250); do
    if [ ! -e "$control_directory/delete-ack" ]; then
      return 0
    fi
    sleep 0.02
  done
  printf 'timed out acknowledging the real delete.\n' >&2
  return 1
}

run_post_validation_case() {
  case_name=$1
  local docker_status=$2
  local expected_status=$3
  local probe_output_path="$control_directory/$case_name.output"

  start_probe post_validation "$docker_status" "$probe_output_path"
  : >"$control_directory/docker-release"

  local kind
  local delete_target
  for kind in sentinel fixture output; do
    wait_for_file "$control_directory/delete-ready" "$kind delete barrier"
    delete_target=$(<"$control_directory/delete-target")
    classify_delete_target "$kind" "$delete_target"
    case "$kind" in
      sentinel)
        replacement_sentinel=$sentinel_path
        replacement_sentinel_marker=$(new_marker)
        (set -o noclobber; printf '%s\n' "$replacement_sentinel_marker" \
          >"$replacement_sentinel")
        replacement_sentinel_identity=$(path_identity "$replacement_sentinel")
        ;;
      fixture)
        replacement_fixture=$fixture_path
        replacement_fixture_marker=$(new_marker)
        mkdir -- "$replacement_fixture"
        printf '%s\n' "$replacement_fixture_marker" \
          >"$replacement_fixture/.ownership-test-marker"
        replacement_fixture_identity=$(path_identity "$replacement_fixture")
        ;;
      output)
        replacement_output=$output_path
        replacement_output_marker=$(new_marker)
        mkdir -- "$replacement_output"
        printf '%s\n' "$replacement_output_marker" \
          >"$replacement_output/.ownership-test-marker"
        replacement_output_identity=$(path_identity "$replacement_output")
        ;;
    esac
    release_delete
    if [ -e "$delete_target" ] || [ -L "$delete_target" ]; then
      printf 'validated delete target survived cleanup: %s.\n' \
        "$delete_target" >&2
      return 1
    fi
  done

  wait_for_probe "$expected_status" "$probe_output_path"
  assert_replacements_survive "$probe_output_path"
  cleanup_case_artifacts
}

run_pre_move_case cleanup_promotes_success 0 1
run_pre_move_case cleanup_preserves_primary_failure 42 42
run_post_validation_case quarantine_preserves_replacements 0 0
run_post_validation_case quarantine_preserves_failure_status 42 42

printf 'actual probe cleanup quarantine checks passed\n'
