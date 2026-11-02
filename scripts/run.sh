#!/usr/bin/env bash
# 一键启动（无密钥可用）
set -e
cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

PORT="${PORT:-8000}"
echo "CableMind 启动中 → http://127.0.0.1:${PORT}"
exec ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" --reload
