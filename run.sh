#!/usr/bin/env bash
# 本地一键启动：建虚拟环境 -> 装依赖 -> 起服务
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
if [ ! -f .env ]; then
  echo "提示：没有 .env 文件，已按 .env.example 生成一份空的，请填入 DEEPSEEK_API_KEY 再重启。"
  cp .env.example .env
fi
exec .venv/bin/uvicorn app.main:app --reload --port 8000
