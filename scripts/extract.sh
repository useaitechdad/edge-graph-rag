#!/usr/bin/env bash
# Reads every chunk with Claude and records the entities and relations it states.
# Nothing here talks to Cloudflare.
#
# Two backends, and which one is chosen decides who pays — so it also decides
# what this script is allowed to put in the environment:
#
#   --backend api   (default)  the Messages API. Needs ANTHROPIC_API_KEY, which
#                              is what .anthropic.env is for. Spends credit.
#   --backend cli              the Claude Code CLI already installed here, on
#                              whatever login it holds. .anthropic.env is NOT
#                              loaded, and any key already in the environment is
#                              unset: the CLI prefers a key when it sees one, so
#                              leaving one in place would bill the API and give
#                              no sign of having done it.
set -euo pipefail
cd "$(dirname "$0")/.."

backend=api
previous=
for argument in "$@"; do
	case "$argument" in
	--backend=*)
		backend="${argument#--backend=}"
		;;
	*)
		if [[ "$previous" == "--backend" ]]; then
			backend="$argument"
		fi
		;;
	esac
	previous="$argument"
done

if [[ "$backend" == "cli" ]]; then
	if ! command -v claude >/dev/null 2>&1; then
		echo "Refusing to run: --backend cli needs the claude CLI on PATH." >&2
		exit 1
	fi
	unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
else
	. scripts/_anthropic.sh
	require_anthropic_key
fi

if [[ ! -f corpus/chunks.jsonl ]]; then
	echo "No corpus/chunks.jsonl. Run python3 scripts/chunk.py first." >&2
	exit 1
fi

python3 scripts/extract.py "$@"
