#!/usr/bin/env bash
# Run the Gradio demo locally (same entrypoint as Hugging Face Space).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
uv sync
exec uv run python space/app.py
