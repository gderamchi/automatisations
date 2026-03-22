#!/usr/bin/env bash
set -euo pipefail

python -m apps.workers.cli mail-worker "$@"
