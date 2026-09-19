#!/usr/bin/env bash
# Runs the Worker locally. D1 is simulated on this machine; Vectorize and
# Workers AI have no simulator, so those bindings reach the real account
# ("remote": true in the config) and need credentials.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f wrangler.jsonc ]]; then
	echo "No wrangler.jsonc. Run ./scripts/setup-cloudflare.sh (full) or ./scripts/migrate-local.sh (local only)." >&2
	exit 1
fi

. scripts/_auth.sh
if [[ -z "${CLOUDFLARE_API_TOKEN:-}" || -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
	echo "Note: no credentials in .cloudflare.env — the VECTORS and AI bindings will not connect." >&2
fi

npx wrangler dev "$@"
