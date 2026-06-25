#!/usr/bin/env bash
# backend/run.sh — 고정 환경값(.env)으로 백엔드 기동 (bash).
#   $ ./run.sh
# .env 의 COMANTLE_SALT 등을 매번 같은 값으로 올린다(값을 새로 생성하지 않는다).
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo ".env 가 없습니다"; exit 1; }
set -a; . ./.env; set +a

echo "[run] COMANTLE_SALT set (len=${#COMANTLE_SALT}), DEV=${COMANTLE_DEV:-}"
# --reload 미사용 (orphan 워커 + data 변경 미반영 문제). 변경 후엔 끄고 다시 실행.
exec py -3.13 -m uvicorn main:app --host 127.0.0.1 --port 8000
