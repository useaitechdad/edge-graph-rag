#!/usr/bin/env bash
# Applies migrations/ to the real D1 database.
set -euo pipefail
cd "$(dirname "$0")/.."

DB_NAME="edge-graph-rag"

. scripts/_auth.sh
require_cloudflare_auth

if [[ ! -f wrangler.jsonc ]] || grep -q "__D1_DATABASE_ID__\|00000000-0000-0000-0000-000000000000" wrangler.jsonc; then
	echo "Refusing to run: wrangler.jsonc has no real D1 id. Run ./scripts/setup-cloudflare.sh first." >&2
	exit 1
fi

npx wrangler d1 migrations apply "${DB_NAME}" --remote
