#!/usr/bin/env bash

set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
sentinel_path="$repository_root/.env.contract-context-probe"
fixture_directory=""
fixture_name=""
output_directory=""
sentinel_created=false
sentinel_identity=""
sentinel_marker=""
fixture_identity=""
fixture_marker=""
output_identity=""
output_marker=""
repo_quarantine_directory=""
repo_quarantine_identity=""
output_quarantine_directory=""
output_quarantine_identity=""

path_identity() {
  stat -c '%d:%i:%f' -- "$1"
}

new_marker() {
  local random_value
  IFS= read -r random_value </proc/sys/kernel/random/uuid
  printf 'opevo-contract-context:%s\n' "$random_value"
}

promote_cleanup_failure() {
  if [ "$cleanup_status" -eq 0 ]; then
    cleanup_status=1
  fi
}

create_quarantine_directory() {
  local scope=$1
  local directory=""
  local identity=""
  case "$scope" in
    repository)
      if [ -n "$repo_quarantine_directory" ]; then
        return 0
      fi
      if ! directory=$(mktemp -d \
        "$repository_root/tests/docker/.root-context-cleanup.XXXXXX"); then
        printf 'failed to create repository cleanup quarantine.\n' >&2
        promote_cleanup_failure
        return 1
      fi
      if ! identity=$(path_identity "$directory"); then
        printf 'failed to identify repository cleanup quarantine %s.\n' \
          "$directory" >&2
        rmdir -- "$directory" 2>/dev/null || true
        promote_cleanup_failure
        return 1
      fi
      repo_quarantine_directory=$directory
      repo_quarantine_identity=$identity
      ;;
    output)
      if [ -n "$output_quarantine_directory" ]; then
        return 0
      fi
      if ! directory=$(mktemp -d \
        /tmp/opevo-contract-context-cleanup.XXXXXX); then
        printf 'failed to create output cleanup quarantine.\n' >&2
        promote_cleanup_failure
        return 1
      fi
      if ! identity=$(path_identity "$directory"); then
        printf 'failed to identify output cleanup quarantine %s.\n' \
          "$directory" >&2
        rmdir -- "$directory" 2>/dev/null || true
        promote_cleanup_failure
        return 1
      fi
      output_quarantine_directory=$directory
      output_quarantine_identity=$identity
      ;;
  esac
}

restore_unexpected_object() {
  local path=$1
  local quarantine_directory=$2
  local quarantine_identity=$3
  local quarantine_name=$4
  local quarantine_path="$quarantine_directory/$quarantine_name"
  if (
    cd -P -- "$quarantine_directory" &&
      [ "$(path_identity .)" = "$quarantine_identity" ] &&
      mv -T -n -- "./$quarantine_name" "$path" &&
      [ ! -e "./$quarantine_name" ] &&
      [ ! -L "./$quarantine_name" ]
  ); then
    printf '%s ownership changed after quarantine; restored unexpected object without deleting it.\n' \
      "$path" >&2
  else
    printf '%s ownership changed after quarantine; unexpected object retained at %s; refusing deletion.\n' \
      "$path" "$quarantine_path" >&2
  fi
  promote_cleanup_failure
}

cleanup_owned_file() {
  local path=$1
  local expected_identity=$2
  local expected_marker=$3
  local quarantine_path="$repo_quarantine_directory/sentinel"
  if ! mv -T -- "$path" "$quarantine_path"; then
    printf 'failed to quarantine owned file %s; refusing deletion.\n' \
      "$path" >&2
    promote_cleanup_failure
    return
  fi
  if ! (
    cd -P -- "$repo_quarantine_directory" &&
      [ "$(path_identity .)" = "$repo_quarantine_identity" ] &&
      [ -f ./sentinel ] && [ ! -L ./sentinel ] &&
      [ "$(path_identity ./sentinel)" = "$expected_identity" ] &&
      grep -Fxq "$expected_marker" ./sentinel &&
      rm -- ./sentinel
  ); then
    restore_unexpected_object \
      "$path" \
      "$repo_quarantine_directory" \
      "$repo_quarantine_identity" \
      sentinel
  fi
}

cleanup_owned_directory() {
  local path=$1
  local expected_identity=$2
  local expected_marker=$3
  local quarantine_directory=$4
  local quarantine_name=$5
  local quarantine_path="$quarantine_directory/$quarantine_name"
  local quarantine_identity=""
  case "$quarantine_directory" in
    "$repo_quarantine_directory")
      quarantine_identity=$repo_quarantine_identity
      ;;
    "$output_quarantine_directory")
      quarantine_identity=$output_quarantine_identity
      ;;
    *)
      printf 'unknown cleanup quarantine %s; refusing recursive deletion.\n' \
        "$quarantine_directory" >&2
      promote_cleanup_failure
      return
      ;;
  esac
  if ! mv -T -- "$path" "$quarantine_path"; then
    printf 'failed to quarantine owned directory %s; refusing recursive deletion.\n' \
      "$path" >&2
    promote_cleanup_failure
    return
  fi
  if ! (
    cd -P -- "$quarantine_directory" &&
      [ "$(path_identity .)" = "$quarantine_identity" ] &&
      [ -d "./$quarantine_name" ] &&
      [ ! -L "./$quarantine_name" ] &&
      [ "$(path_identity "./$quarantine_name")" = "$expected_identity" ] &&
      [ -f "./$quarantine_name/.env.contract-context-owner" ] &&
      [ ! -L "./$quarantine_name/.env.contract-context-owner" ] &&
      grep -Fxq \
        "$expected_marker" \
        "./$quarantine_name/.env.contract-context-owner" &&
      rm -rf -- "./$quarantine_name"
  ); then
    restore_unexpected_object \
      "$path" \
      "$quarantine_directory" \
      "$quarantine_identity" \
      "$quarantine_name"
  fi
}

cleanup_quarantine_directory() {
  local path=$1
  local expected_identity=$2
  if [ -z "$path" ]; then
    return
  fi
  local current_identity=""
  if [ -d "$path" ] && [ ! -L "$path" ] &&
    current_identity=$(path_identity "$path") &&
    [ "$current_identity" = "$expected_identity" ]; then
    if ! rmdir -- "$path"; then
      printf 'cleanup quarantine retained because it is not empty: %s.\n' \
        "$path" >&2
      promote_cleanup_failure
    fi
  else
    printf 'cleanup quarantine ownership changed; retained without recursive deletion: %s.\n' \
      "$path" >&2
    promote_cleanup_failure
  fi
}

cleanup() {
  original_status=$?
  cleanup_status=$original_status
  trap - EXIT

  if [ "$sentinel_created" = true ]; then
    if create_quarantine_directory repository; then
      cleanup_owned_file \
        "$sentinel_path" "$sentinel_identity" "$sentinel_marker"
    fi
  fi
  if [ -n "$fixture_directory" ]; then
    if create_quarantine_directory repository; then
      cleanup_owned_directory \
        "$fixture_directory" \
        "$fixture_identity" \
        "$fixture_marker" \
        "$repo_quarantine_directory" \
        fixture
    fi
  fi
  if [ -n "$output_directory" ]; then
    if create_quarantine_directory output; then
      cleanup_owned_directory \
        "$output_directory" \
        "$output_identity" \
        "$output_marker" \
        "$output_quarantine_directory" \
        output
    fi
  fi
  cleanup_quarantine_directory \
    "$repo_quarantine_directory" "$repo_quarantine_identity"
  cleanup_quarantine_directory \
    "$output_quarantine_directory" "$output_quarantine_identity"

  exit "$cleanup_status"
}

trap cleanup EXIT

if [ -e "$sentinel_path" ] || [ -L "$sentinel_path" ]; then
  printf '%s already exists; refusing to overwrite it.\n' "$sentinel_path" >&2
  exit 1
fi

sentinel_marker=$(new_marker)
if (set -o noclobber; printf '%s\n' "$sentinel_marker" >"$sentinel_path"); then
    sentinel_created=true
    sentinel_identity=$(path_identity "$sentinel_path")
else
  printf '%s was created by another process; refusing to overwrite it.\n' "$sentinel_path" >&2
  exit 1
fi

fixture_marker=$(new_marker)
fixture_candidate=$(mktemp -d "$repository_root/tests/docker/.root-context-env.XXXXXX")
fixture_candidate_identity=$(path_identity "$fixture_candidate")
if ! printf '%s\n' "$fixture_marker" \
  >"$fixture_candidate/.env.contract-context-owner"; then
  if [ -d "$fixture_candidate" ] && [ ! -L "$fixture_candidate" ] &&
    [ "$(path_identity "$fixture_candidate")" = "$fixture_candidate_identity" ]; then
    rmdir -- "$fixture_candidate"
  fi
  printf 'failed to initialize fixture ownership marker.\n' >&2
  exit 1
fi
fixture_directory=$fixture_candidate
fixture_identity=$fixture_candidate_identity
fixture_name=$(basename "$fixture_directory")
output_marker=$(new_marker)
output_candidate=$(mktemp -d /tmp/opevo-contract-context.XXXXXX)
output_candidate_identity=$(path_identity "$output_candidate")
if ! printf '%s\n' "$output_marker" \
  >"$output_candidate/.env.contract-context-owner"; then
  if [ -d "$output_candidate" ] && [ ! -L "$output_candidate" ] &&
    [ "$(path_identity "$output_candidate")" = "$output_candidate_identity" ]; then
    rmdir -- "$output_candidate"
  fi
  printf 'failed to initialize output ownership marker.\n' >&2
  exit 1
fi
output_directory=$output_candidate
output_identity=$output_candidate_identity
mkdir -p "$fixture_directory/nested"
printf 'disposable-test-value\n' >"$fixture_directory/nested/.env"
printf 'disposable-prefixed-test-value\n' >"$fixture_directory/nested/.env.contract-context-probe"
printf 'reviewed-example-value\n' >"$fixture_directory/nested/.env.example"

docker build \
  --file "$repository_root/tests/docker/root-context.Dockerfile" \
  --output "type=local,dest=$output_directory" \
  "$repository_root"

test -f "$output_directory/context/apps/api/pyproject.toml"
test -f "$output_directory/context/apps/agent/pyproject.toml"
test -f "$output_directory/context/libs/shared/pyproject.toml"
test -d "$output_directory/context/libs/shared/src"
test ! -e "$output_directory/context/.git"
test ! -e "$output_directory/context/.env.contract-context-probe"
test ! -e "$output_directory/context/Opevo_frontend"
test ! -e "$output_directory/context/tests/docker/$fixture_name/nested/.env"
test ! -e "$output_directory/context/tests/docker/$fixture_name/nested/.env.contract-context-probe"
test -f "$output_directory/context/tests/docker/$fixture_name/nested/.env.example"
grep -Fxq 'reviewed-example-value' "$output_directory/context/tests/docker/$fixture_name/nested/.env.example"
test -z "$(find "$output_directory/context" -name .env -o -name .venv -o -name node_modules -o -name coverage.json)"

printf 'root Docker context hygiene check passed\n'
