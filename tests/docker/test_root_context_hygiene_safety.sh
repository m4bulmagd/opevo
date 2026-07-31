#!/usr/bin/env bash

set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
sentinel_path="$repository_root/.env.contract-context-probe"
target_path=$(mktemp /tmp/presvo-dangling-sentinel.XXXXXX)
probe_output_path=$(mktemp /tmp/presvo-sentinel-probe.XXXXXX)
sentinel_created=false

cleanup() {
  if [ "$sentinel_created" = true ]; then
    rm -f -- "$sentinel_path"
  fi
  rm -f -- "$target_path"
  rm -f -- "$probe_output_path"
}

if [ -e "$sentinel_path" ] || [ -L "$sentinel_path" ]; then
  printf '%s already exists; refusing to create a test symlink.\n' "$sentinel_path" >&2
  rm -f -- "$target_path"
  rm -f -- "$probe_output_path"
  exit 1
fi

trap cleanup EXIT
rm -f -- "$target_path"
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
test "$(readlink "$sentinel_path")" = "$target_path"
test ! -e "$target_path"
printf 'dangling sentinel refusal check passed\n'
