#!/usr/bin/env bash

set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
sentinel_path="$repository_root/.env.contract-context-probe"
fixture_directory=""
fixture_name=""
output_directory=""
sentinel_created=false

cleanup() {
  if [ "$sentinel_created" = true ]; then
    rm -f -- "$sentinel_path"
  fi
  if [ -n "$fixture_directory" ]; then
    rm -rf -- "$fixture_directory"
  fi
  if [ -n "$output_directory" ]; then
    rm -rf -- "$output_directory"
  fi
}

trap cleanup EXIT

if [ -e "$sentinel_path" ] || [ -L "$sentinel_path" ]; then
  printf '%s already exists; refusing to overwrite it.\n' "$sentinel_path" >&2
  exit 1
fi

if (set -o noclobber; : >"$sentinel_path"); then
  sentinel_created=true
else
  printf '%s was created by another process; refusing to overwrite it.\n' "$sentinel_path" >&2
  exit 1
fi

fixture_directory=$(mktemp -d "$repository_root/tests/docker/.root-context-env.XXXXXX")
fixture_name=$(basename "$fixture_directory")
output_directory=$(mktemp -d /tmp/presvo-contract-context.XXXXXX)
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
test ! -e "$output_directory/context/Presvo_frontend"
test ! -e "$output_directory/context/tests/docker/$fixture_name/nested/.env"
test ! -e "$output_directory/context/tests/docker/$fixture_name/nested/.env.contract-context-probe"
test -f "$output_directory/context/tests/docker/$fixture_name/nested/.env.example"
grep -Fxq 'reviewed-example-value' "$output_directory/context/tests/docker/$fixture_name/nested/.env.example"
test -z "$(find "$output_directory/context" -name .env -o -name .venv -o -name node_modules -o -name coverage.json)"

printf 'root Docker context hygiene check passed\n'
