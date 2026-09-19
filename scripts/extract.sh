#!/usr/bin/env bash
# Reads every chunk with Claude and records the entities and relations it states.
# This is the only step that spends Anthropic tokens; nothing here talks to Cloudflare.
set -euo pipefail
cd "$(dirname "$0")/.."

. scripts/_anthropic.sh
require_anthropic_key

if [[ ! -f corpus/chunks.jsonl ]]; then
	echo "No corpus/chunks.jsonl. Run python3 scripts/chunk.py first." >&2
	exit 1
fi

python3 scripts/extract.py "$@"
