#!/usr/bin/env bash
# Resolve the Dockerfile's base-image tags to immutable digests and write them
# back, so every build uses byte-identical base layers (reproducible builds).
# Re-run deliberately when you want to pick up base-image security patches.
set -euo pipefail

cd "$(dirname "$0")/.."

resolve() {
  docker buildx imagetools inspect "$1" --format '{{json .Manifest}}' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["digest"])'
}

for arg in BUILDER_IMAGE RUNTIME_IMAGE; do
  current=$(grep -E "^ARG ${arg}=" Dockerfile | cut -d= -f2-)
  tag=${current%@*}
  digest=$(resolve "$tag")
  sed -i.bak -E "s|^ARG ${arg}=.*$|ARG ${arg}=${tag}@${digest}|" Dockerfile
  echo "${arg}: ${tag}@${digest}"
done
rm -f Dockerfile.bak
