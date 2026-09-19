# Sourced by every script that talks to Cloudflare. Not run on its own.
#
# Credentials live in .cloudflare.env at the repo root (ignored by git):
#
#     CLOUDFLARE_API_TOKEN=...
#     CLOUDFLARE_ACCOUNT_ID=...
#
# It is deliberately NOT called .env: wrangler loads .env files into the
# Worker's own `env` during local development, and a deploy token has no
# business being readable by the code it deploys.
#
# Token permissions: Workers Scripts Edit, D1 Edit, Vectorize Edit, Workers AI Edit.
# This never runs `wrangler login`, so it cannot pick up whichever account the
# machine happens to be logged into. Nothing here prints either value.

if [[ -f .cloudflare.env ]]; then
	set -a
	# shellcheck disable=SC1091
	. ./.cloudflare.env
	set +a
fi

require_cloudflare_auth() {
	if [[ -z "${CLOUDFLARE_API_TOKEN:-}" || -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
		echo "Refusing to run: put CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in .cloudflare.env (see scripts/_auth.sh)." >&2
		exit 1
	fi
}
