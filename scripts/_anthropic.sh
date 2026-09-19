# Sourced by every script that talks to the Anthropic API. Not run on its own.
#
# The key lives in .anthropic.env at the repo root (ignored by git):
#
#     ANTHROPIC_API_KEY=...
#
# Separate from .cloudflare.env on purpose: the extraction run is the only step
# that spends Anthropic tokens, and nothing that deploys code needs this key.
#
# It is deliberately NOT called .env: wrangler loads .env files into the
# Worker's own `env` during local development, and this key has no business
# being readable by a Worker. Nothing here prints the value.

if [[ -f .anthropic.env ]]; then
	set -a
	# shellcheck disable=SC1091
	. ./.anthropic.env
	set +a
fi

require_anthropic_key() {
	if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
		echo "Refusing to run: put ANTHROPIC_API_KEY in .anthropic.env (see scripts/_anthropic.sh)." >&2
		exit 1
	fi
}
