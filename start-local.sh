#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing virtualenv interpreter at $VENV_PY" >&2
  exit 1
fi

export PATH="$ROOT_DIR/.venv/bin:/opt/homebrew/bin:$PATH"

cleanup() {
  if [[ -n "${API_PID:-}" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
  fi
  if [[ -n "${WORKER_PID:-}" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill "$WORKER_PID" 2>/dev/null || true
  fi
}

trap cleanup INT TERM EXIT

"$VENV_PY" "$ROOT_DIR/scripts/init_db.py"

"$ROOT_DIR/scripts/run_api.sh" &
API_PID=$!

WORKER_PID=""
if [[ -n "${IMAP_USERNAME:-}" && -n "${IMAP_PASSWORD:-}" ]]; then
  "$ROOT_DIR/scripts/run_mail_worker.sh" --once &
  WORKER_PID=$!
else
  echo "Skipping mail worker: IMAP_USERNAME/IMAP_PASSWORD are not set" >&2
fi

if [[ -n "$WORKER_PID" ]]; then
  wait "$API_PID" "$WORKER_PID"
else
  wait "$API_PID"
fi
