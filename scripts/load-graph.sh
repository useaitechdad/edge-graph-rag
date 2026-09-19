#!/usr/bin/env bash
# Writes corpus/graph.json into the remote D1 database, replacing whatever graph
# is there. Everything goes over the REST API, so this needs credentials but no
# wrangler. Run ./scripts/ingest.sh first: every edge points at a chunk row.
set -euo pipefail
cd "$(dirname "$0")/.."

. scripts/_auth.sh
require_cloudflare_auth

if [[ ! -f corpus/graph.json ]]; then
	echo "No corpus/graph.json. Run python3 scripts/build_graph.py first." >&2
	exit 1
fi

python3 scripts/load_graph.py "$@"
