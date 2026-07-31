#!/usr/bin/env bash

set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
sentinel_path="$repository_root/.env.contract-context-probe"
target_directory=""
target_path=""
probe_output_path=""
sentinel_created=false

cleanup() {
  original_status=$?
  cleanup_status=$original_status
  trap - EXIT

  if [ "$sentinel_created" = true ]; then
    current_target=""
    if [ -L "$sentinel_path" ] &&
      current_target=$(readlink -- "$sentinel_path") &&
      [ "$current_target" = "$target_path" ] &&
      [ ! -e "$target_path" ] &&
      [ ! -L "$target_path" ]; then
      if ! rm -- "$sentinel_path"; then
        printf 'failed to remove the owned dangling sentinel.\n' >&2
        if [ "$cleanup_status" -eq 0 ]; then
          cleanup_status=1
        fi
      fi
    else
      printf '%s ownership changed before cleanup; refusing to remove it.\n' \
        "$sentinel_path" >&2
      if [ "$cleanup_status" -eq 0 ]; then
        cleanup_status=1
      fi
    fi
  fi
  if [ -n "$target_directory" ] &&
    ! rm -rf -- "$target_directory"; then
    printf 'failed to remove the owned sentinel target directory.\n' >&2
    if [ "$cleanup_status" -eq 0 ]; then
      cleanup_status=1
    fi
  fi
  if [ -n "$probe_output_path" ] &&
    ! rm -f -- "$probe_output_path"; then
    printf 'failed to remove the owned probe output.\n' >&2
    if [ "$cleanup_status" -eq 0 ]; then
      cleanup_status=1
    fi
  fi

  exit "$cleanup_status"
}

trap cleanup EXIT

if [ -e "$sentinel_path" ] || [ -L "$sentinel_path" ]; then
  printf '%s already exists; refusing to create a test symlink.\n' "$sentinel_path" >&2
  exit 1
fi

target_directory=$(mktemp -d /tmp/presvo-dangling-sentinel.XXXXXX)
target_path="$target_directory/dangling-target"
probe_output_path=$(mktemp /tmp/presvo-sentinel-probe.XXXXXX)

ln -s "$target_path" "$sentinel_path"
sentinel_created=true

probe_status=0
if "$repository_root/tests/docker/test_root_context_hygiene.sh" >"$probe_output_path" 2>&1; then
  probe_status=0
else
  probe_status=$?
fi

if [ "$probe_status" -eq 0 ]; then
  printf 'expected dangling sentinel refusal, but the hygiene probe succeeded.\n' >&2
  exit 1
fi

expected_diagnostic="$sentinel_path already exists; refusing to overwrite it."
if ! grep -Fxq "$expected_diagnostic" "$probe_output_path"; then
  printf 'expected the dangling-sentinel refusal diagnostic.\n' >&2
  sed -n '1,80p' "$probe_output_path" >&2
  exit 1
fi

test -L "$sentinel_path"
test "$(readlink -- "$sentinel_path")" = "$target_path"
test ! -e "$target_path"

replacement_target=${PRESVO_TEST_REPLACEMENT_SENTINEL_TARGET:-}
if [ -n "$replacement_target" ]; then
  if [ -e "$replacement_target" ] || [ -L "$replacement_target" ]; then
    printf '%s already exists; refusing the controlled replacement.\n' \
      "$replacement_target" >&2
    exit 1
  fi

  rm -- "$sentinel_path"
  ln -s "$replacement_target" "$sentinel_path"
fi

printf 'dangling sentinel refusal check passed\n'
