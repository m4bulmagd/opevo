#!/usr/bin/env bash

set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
sentinel_path="$repository_root/.env.contract-context-probe"
target_path=$(mktemp /tmp/presvo-dangling-sentinel.XXXXXX)
sentinel_created=false

cleanup() {
  if [ "$sentinel_created" = true ]; then
    rm -f -- "$sentinel_path"
  fi
  rm -f -- "$target_path"
}

if [ -e "$sentinel_path" ] || [ -L "$sentinel_path" ]; then
  printf '%s already exists; refusing to create a test symlink.\n' "$sentinel_path" >&2
  rm -f -- "$target_path"
  exit 1
fi

trap cleanup EXIT
rm -f -- "$target_path"
ln -s "$target_path" "$sentinel_path"
sentinel_created=true

if "$repository_root/tests/docker/test_root_context_hygiene.sh"; then
  printf 'expected dangling sentinel refusal, but the hygiene probe ran.\n' >&2
  exit 1
fi

test ! -e "$target_path"
printf 'dangling sentinel refusal check passed\n'
