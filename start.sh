#!/usr/bin/env bash
# Start Huayu Coach on your own computer.   ./start.sh          -> clean start
#                                           ./start.sh demo     -> same, with sample learner/teacher/admin accounts
# Needs only `uv` (https://docs.astral.sh/uv/). No Docker, no database server, no API key.
set -euo pipefail
cd "$(dirname "$0")"
command -v uv >/dev/null 2>&1 || { echo "Cần cài 'uv' trước / Please install uv first: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example (edit it to change settings)."; }
uv sync --quiet
uv run python -m app.cli prepare
if [ "${1:-}" = "demo" ]; then uv run python -m app.cli demo; fi
PORT="${PORT:-8000}"
echo "→ http://localhost:${PORT}   (Ctrl+C to stop)"
exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --reload
