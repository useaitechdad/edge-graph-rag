#!/usr/bin/env bash
# Scores the frozen eval set against a running Worker and writes a receipt
# under runs/. Start the Worker first, in another terminal:
#
#     ./scripts/dev.sh
#
# Nothing here talks to Cloudflare; it only talks to that Worker.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 eval/run.py "$@"
