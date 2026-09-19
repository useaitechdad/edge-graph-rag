#!/usr/bin/env bash
# Embeds the chunks, indexes them in Vectorize and writes the rows into D1.
# Everything goes over the REST API, so this needs credentials but no wrangler.
set -euo pipefail
cd "$(dirname "$0")/.."

. scripts/_auth.sh
require_cloudflare_auth

if [[ ! -f corpus/chunks.jsonl ]]; then
	echo "No corpus/chunks.jsonl. Run python3 scripts/chunk.py first." >&2
	exit 1
fi

python3 scripts/ingest.py "$@"
