# edge-graph-rag

A small Graph RAG service on Cloudflare Workers, D1 and Vectorize, built to answer one question:
does adding a graph to vector search find passages that vector search alone misses?

The corpus is a set of public-domain NASA mishap investigation reports. The questions worth
asking about them cross documents — a contractor named in one report, a failure described in
another — which is exactly where a single vector lookup tends to stay inside one report.

**Status: M2 has run. M3 is written and has not.** The corpus is cut into 1,373 chunks, the
ingest has embedded them and filled Vectorize and D1, and `GET /search` answers with the
nearest passages — the vector arm of the comparison. The baseline is in: **14 of 24 questions
at k=10** under Amendment 1, 12 of 24 strict, with the receipt in `runs/`.

M3 is the code that builds the graph and nothing that uses it: `scripts/extract.py` reads every
chunk with Claude and records the entities and relations that chunk states,
`scripts/build_graph.py` resolves those into one set of nodes and edges, and
`scripts/load_graph.py` writes them into D1. None of it has been run against the real API yet,
so there is no graph in the database and no second number to put beside the first.

One result is in, though, and it comes before retrieval matters: all 45 gold passages survive
chunking. `eval/coverage.py` checks that every quote the eval set asks for is still held whole
by some chunk, because a recall number measured against chunks that lost the answer is a
measurement of the chunker.

## Milestones

| # | Milestone |
|---|-----------|
| M0 | Scaffold: Worker, D1 schema, Vectorize index, setup and deploy scripts, corpus fetched with source URLs and hashes |
| M1 | **Eval questions and gold passages, committed before any retrieval code exists.** Half single-hop (the control), half multi-hop |
| M2 | Chunk, embed, index. Vector-only search. First eval run — the baseline number |
| M3 | Extraction: an LLM reads each chunk and returns entities and relations, stored in D1 linked back to their chunk |
| M4 | Graph retrieval: seed from vector hits, walk 1–2 hops, re-rank so a graph hit can outrank a vector hit. Second eval run |
| M5 | Where it does not help: single-hop control, 1 hop against 2, over-connected nodes, latency and cost |
| M6 | Clean README, one-command setup, licence |

M1 comes before M2 on purpose. Questions written after the search works measure the tuning,
not the retrieval; freezing them first, in a commit that predates the retrieval code, is what
makes the final number worth reading.

Amendment 1 adds `eval/equivalents.json` — the other places these reports state the same
answer, so a retriever that returns one of them is not counted as a miss; `questions.json`
stays frozen byte for byte, and every run reports the strict number beside the amended one
(see `eval/DESIGN.md`).

## Running the tests

Everything here runs offline, against the local D1 simulator. No Cloudflare account, no keys.

```sh
npm install
./scripts/migrate-local.sh   # applies migrations/ to the local D1 simulator
npm test
```

`npm test` is two halves. Vitest inside the Workers runtime: the migration applies, `WITH
RECURSIVE` does or does not work on D1 (`test/recursive-cte.test.ts` records which), the health
route answers, and `/search` returns what it promises — against the real local D1, with fakes
standing in for the two bindings that have no simulator. Then the Python suites:
`eval/test_validate.py` for the validator, `eval/test_scoring.py` for the containment rule,
including the boundary at exactly half a quote, `scripts/test_extract.py` for the extractor —
retries, truncation, resume, repair, both backends and the circuit breaker, with scripted fakes
in place of the one function that touches the network and the one that starts a process — and
`scripts/test_build_graph.py` for the entity resolution and its promise that the same
extractions produce byte-identical bytes.

## Layout

| Path | What it is |
|------|------------|
| `src/index.ts` | The Worker. `GET /health` and `GET /search?q=…&k=10` |
| `src/retrieval.ts` | The `Retriever` interface and the vector implementation. M4's graph retriever arrives beside it, not inside it |
| `src/embed.ts` | The embedding model and its pooling, in one place, for the query side |
| `migrations/` | D1 schema: documents, chunks, nodes, edges, node_chunks |
| `scripts/chunk.py` | `corpus/text/` → `corpus/chunks.jsonl`. Deterministic: same corpus, same bytes |
| `scripts/ingest.py` | Embeds, indexes and writes the rows over the REST API. Resumable, and it never prints a credential |
| `scripts/extract.py` | One call per chunk to Claude, the answer forced into a schema, appended to `runs/tmp/extractions.jsonl`. Two backends — the API or the local CLI. Resumable, and it never prints the key |
| `scripts/build_graph.py` | Those extractions → `corpus/graph.json`. The entity resolution, and the only place it lives. Deterministic |
| `scripts/load_graph.py` | `corpus/graph.json` → D1's `nodes`, `edges` and `node_chunks`. Replaces the graph rather than duplicating it |
| `eval/schema.md` | The format for `eval/questions.json` |
| `eval/validate.py` | Checks that format, and that every gold quote is verbatim on the page it claims |
| `eval/equivalents.json` | Amendment 1: the other passages that state a question's answer, scored as equally found |
| `eval/equivalents.notes.md` | How each equivalent was found — the answer, the search terms, what was accepted and what was rejected |
| `eval/scoring.py` | The containment rule from `DESIGN.md`, implemented once and imported twice |
| `eval/coverage.py` | Whether the chunks can still reach every gold passage. Run it before believing a recall number |
| `eval/run.py` | The eval run: `/search` per question, recall at 10 and at 5, a receipt |
| `eval/groups.json` | Which multi-hop questions have a specific final clause and which a generic one |
| `corpus/MANIFEST.json` | The seven reports: official URL, sha256, rights note. The files themselves are downloaded by `scripts/fetch-corpus.py`, never redistributed from here |
| `runs/` | One receipt per ingest and per eval run. `runs/tmp/` is scratch — progress and cached embeddings — and is ignored |
| `scripts/` | Every operational step. Nothing in this project is hand-typed at a shell |
| `wrangler.template.jsonc` | Committed. `wrangler.jsonc` is generated from it and ignored, so no account-specific id lands in git |

## Cloudflare, when you want to run it for real

D1 runs locally. Vectorize and Workers AI have no local simulator, so those bindings are
marked `"remote": true` and reach a real account from M2 on.

Create an API token with Workers Scripts Edit, D1 Edit, Vectorize Edit and Workers AI Edit, and
put it with your account id in `.cloudflare.env` (ignored by git; `scripts/_auth.sh` says why
it is not called `.env`):

```sh
CLOUDFLARE_API_TOKEN=...
CLOUDFLARE_ACCOUNT_ID=...
```

M3's extraction is the one step that spends Anthropic tokens rather than Cloudflare's. Its key
lives on its own, in `.anthropic.env` (also ignored by git; `scripts/_anthropic.sh` is the only
thing that reads it), because nothing that deploys code needs it:

```sh
ANTHROPIC_API_KEY=...
```

That is for the default backend. `./scripts/extract.sh --backend cli` needs no key — it runs
the Claude Code CLI on this machine's own login instead, and is described below.

In order. The first three steps need no account at all:

```sh
python3 scripts/fetch-corpus.py   # downloads the reports from their official hosts, checks each sha256
python3 scripts/chunk.py          # corpus/text/ -> corpus/chunks.jsonl, and prints the size spread
python3 eval/coverage.py          # is every gold passage still held by a chunk?

./scripts/setup-cloudflare.sh     # creates the D1 database and the 768-dim cosine index, writes wrangler.jsonc
./scripts/migrate-remote.sh
./scripts/ingest.sh               # embeds, indexes, writes the rows. --dry-run first if you want the sizes
./scripts/dev.sh                  # serves /search on 127.0.0.1:8787
./scripts/eval.sh                 # scores the frozen set, writes runs/<timestamp>-eval-vector.json
```

Then M3, the graph. Only the last of the three touches Cloudflare:

```sh
./scripts/extract.sh              # every chunk through Claude. --dry-run first for the request sizes
                                  # --backend cli to spend this machine's Claude Code login instead
python3 scripts/build_graph.py    # extractions -> corpus/graph.json, and prints what merged into what
./scripts/load-graph.sh           # the graph into D1's nodes, edges and node_chunks
```

`ingest.sh` is safe to interrupt and re-run: every write is an upsert, and the embeddings it
has already paid for are cached under `runs/tmp/`. So is `extract.sh`: a chunk already in
`runs/tmp/extractions.jsonl` is skipped, so a second run pays only for what the first did not
reach. `./scripts/extract.sh --only <chunk id> --show` reads one chunk and prints what came
back. `eval.sh` wants `dev.sh` running in another terminal.

`extract.sh` can reach the model two ways, and they differ only in who pays. The default,
`--backend api`, is the Messages API on `ANTHROPIC_API_KEY`: it is the default because it works
from a clone, on any machine, with nothing but that key. `--backend cli` instead runs the Claude
Code CLI that is already installed, headless (`claude -p`), once per chunk — so the run spends
whatever Claude Code login the machine holds rather than API credit. Same instructions, same
schema, same records: one `extractions.jsonl` can hold both, and every line says which backend
wrote it. It needs `claude` on `PATH` and a logged-in Claude Code; `.anthropic.env` is not even
loaded for it, and any API key in the environment is unset before the CLI starts, because the
CLI prefers a key when it sees one and would bill it without saying so. Either way, five failed
chunks in a row stop the run rather than writing a failed line for every chunk that is left —
those lines would read as done on the next resume. Re-run the same command to carry on.

Embeddings come from `@cf/baai/bge-base-en-v1.5`: 768 dimensions, 512 input tokens, and
`pooling: "cls"` — which has to be the same for documents and for queries, or the two live in
different spaces. `src/embed.ts` and `scripts/ingest.py` are the two places that say so.
