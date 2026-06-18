#!/usr/bin/env bash
# Sparse-clone the WHO SMART ANC repository so only input/cql is checked out.
# Run once: ./scripts/setup-vendor.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/vendor/smart-anc"

if [ -d "$VENDOR_DIR/.git" ]; then
  echo "vendor/smart-anc already present. To refresh: rm -rf vendor/smart-anc && $0"
  exit 0
fi

mkdir -p "$REPO_ROOT/vendor"

git clone \
  --depth 1 \
  --filter=blob:none \
  --sparse \
  https://github.com/WorldHealthOrganization/smart-anc.git \
  "$VENDOR_DIR"

git -C "$VENDOR_DIR" sparse-checkout set input/cql

echo "WHO SMART ANC CQL ready at $VENDOR_DIR/input/cql"
