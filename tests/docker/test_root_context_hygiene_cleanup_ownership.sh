#!/usr/bin/env bash

set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
sentinel_path="$repository_root/.env.contract-context-probe"
replacement_directory=""
replacement_target=""
probe_output_path=""

cleanup() {
  original_status=$?
  cleanup_status=$original_status
  trap - EXIT

  if [ -e "$sentinel_path" ] || [ -L "$sentinel_path" ]; then
    current_target=""
    if [ -L "$sentinel_path" ] &&
      current_target=$(readlink -- "$sentinel_path") &&
      [ "$current_target" = "$replacement_target" ] &&
      [ ! -e "$replacement_target" ] &&
      [ ! -L "$replacement_target" ]; then
      if ! rm -- "$sentinel_path"; then
        printf 'failed to remove the controlled replacement sentinel.\n' >&2
        if [ "$cleanup_status" -eq 0 ]; then
          cleanup_status=1
        fi
      fi
    else
      printf 'the controlled replacement sentinel changed; refusing to remove it.\n' >&2
      if [ "$cleanup_status" -eq 0 ]; then
        cleanup_status=1
      fi
    fi
  fi

  if [ -n "$replacement_directory" ] &&
    ! rm -rf -- "$replacement_directory"; then
    printf 'failed to remove the controlled replacement directory.\n' >&2
    if [ "$cleanup_status" -eq 0 ]; then
      cleanup_status=1
    fi
  fi
  if [ -n "$probe_output_path" ] &&
    ! rm -f -- "$probe_output_path"; then
    printf 'failed to remove the controlled probe output.\n' >&2
    if [ "$cleanup_status" -eq 0 ]; then
      cleanup_status=1
    fi
  fi

  exit "$cleanup_status"
}

trap cleanup EXIT

if [ -e "$sentinel_path" ] || [ -L "$sentinel_path" ]; then
  printf '%s already exists; refusing to run the ownership test.\n' "$sentinel_path" >&2
  exit 1
fi

replacement_directory=$(mktemp -d /tmp/presvo-replacement-sentinel.XXXXXX)
replacement_target="$replacement_directory/replacement-target"
probe_output_path=$(mktemp /tmp/presvo-cleanup-ownership.XXXXXX)

probe_status=0
if PRESVO_TEST_REPLACEMENT_SENTINEL_TARGET="$replacement_target" \
  "$repository_root/tests/docker/test_root_context_hygiene_safety.sh" \
  >"$probe_output_path" 2>&1; then
  probe_status=0
else
  probe_status=$?
fi

if [ "$probe_status" -ne 1 ]; then
  printf 'expected cleanup to promote the successful probe status to 1; got %s.\n' \
    "$probe_status" >&2
  exit 1
fi

expected_diagnostic="$sentinel_path ownership changed before cleanup; refusing to remove it."
if ! grep -Fxq "$expected_diagnostic" "$probe_output_path"; then
  printf 'expected the cleanup ownership-violation diagnostic.\n' >&2
  sed -n '1,80p' "$probe_output_path" >&2
  exit 1
fi

test -L "$sentinel_path"
test "$(readlink -- "$sentinel_path")" = "$replacement_target"
test ! -e "$replacement_target"
printf 'sentinel cleanup ownership check passed\n'
