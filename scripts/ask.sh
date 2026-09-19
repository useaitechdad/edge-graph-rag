#!/usr/bin/env bash
# Asks the Worker that ./scripts/dev.sh is serving one question and prints the ranked hits.
# Nothing here talks to Cloudflare; it only talks to that Worker.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 eval/ask.py "$@"
