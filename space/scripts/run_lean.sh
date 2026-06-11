#!/usr/bin/env bash
set -euo pipefail
LEAN_BIN="${LEAN_BIN:-/app/lean/.lake/build/bin/anc-eval}"
exec "$LEAN_BIN" "$@"
