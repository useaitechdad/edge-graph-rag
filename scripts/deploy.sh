#!/usr/bin/env bash
# Publishes the Worker. Runs the tests first — a red build is not deployable.
set -euo pipefail
cd "$(dirname "$0")/.."

. scripts/_auth.sh
require_cloudflare_auth

if [[ ! -f wrangler.jsonc ]] || grep -q "__D1_DATABASE_ID__\|00000000-0000-0000-0000-000000000000" wrangler.jsonc; then
	echo "Refusing to run: wrangler.jsonc has no real D1 id. Run ./scripts/setup-cloudflare.sh first." >&2
	exit 1
fi

npm test
npx wrangler deploy "$@"
