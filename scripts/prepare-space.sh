#!/usr/bin/env bash
# Stage monorepo artifacts for Hugging Face Space push.
# Usage: ./scripts/prepare-space.sh /path/to/hf-space-clone
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:?usage: prepare-space.sh <hf-space-directory>}"

mkdir -p "$DEST"

rsync -a --delete \
  "$REPO_ROOT/space/" "$DEST/" \
  --exclude '.git'

rsync -a "$REPO_ROOT/lean/" "$DEST/lean/"
rsync -a "$REPO_ROOT/fixtures/" "$DEST/fixtures/"
rsync -a "$REPO_ROOT/cql/" "$DEST/cql/"

cp "$REPO_ROOT/pyproject.toml" "$REPO_ROOT/uv.lock" "$REPO_ROOT/.python-version" "$DEST/"

echo "Staged Space at $DEST"
echo "  lean/          → lake build runs in Docker"
echo "  fixtures/      → patient scenarios"
echo "  pyproject.toml → uv sync in Docker"
echo "Next: cd $DEST && git add . && git commit && git push"
