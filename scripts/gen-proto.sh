#!/usr/bin/env bash
# Regenerates the vendored Python protobuf/Connect bindings in
# src/chatto_bot/_pb from the sibling chatto monorepo's proto module.
#
# Requires: buf (https://buf.build/docs/installation), network access to
# the Buf Schema Registry (remote plugins + the protovalidate dependency).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
proto_dir="${repo_root}/../chatto/proto"

if [[ ! -d "${proto_dir}" ]]; then
  echo "error: expected sibling proto module at ${proto_dir}" >&2
  echo "       (chatto-bot must be checked out next to chatto/chatto)" >&2
  exit 1
fi

out_dir="${repo_root}/src/chatto_bot/_pb"
rm -rf "${out_dir}"
mkdir -p "${out_dir}"

cd "${proto_dir}"
buf generate --template "${script_dir}/buf.gen.python.yaml"

# Make every generated package directory importable as chatto_bot._pb.*
find "${out_dir}" -type d -not -path '*/__pycache__' -exec touch {}/__init__.py \;

echo "Generated Python protobuf/Connect bindings in ${out_dir}"
