#!/usr/bin/env bash

set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
sentinel_path="$repository_root/.env.contract-context-probe"
fixture_directory=$(mktemp -d "$repository_root/tests/docker/.root-context-env.XXXXXX")
fixture_name=$(basename "$fixture_directory")
output_directory=$(mktemp -d /tmp/presvo-contract-context.XXXXXX)

cleanup() {
  rm -f "$sentinel_path"
  rm -rf "$fixture_directory" "$output_directory"
}

if [ -e "$sentinel_path" ]; then
  printf '%s already exists; refusing to overwrite it.\n' "$sentinel_path" >&2
  rm -rf "$fixture_directory" "$output_directory"
  exit 1
fi

trap cleanup EXIT
mkdir -p "$fixture_directory/nested"
printf 'disposable-test-value\n' >"$fixture_directory/nested/.env"
: >"$sentinel_path"

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
test -z "$(find "$output_directory/context" -name .env -o -name .venv -o -name node_modules -o -name coverage.json)"

printf 'root Docker context hygiene check passed\n'
