# edge-graph-rag

A small Graph RAG service on Cloudflare Workers, D1 and Vectorize, built to answer one question:
does adding a graph to vector search find passages that vector search alone misses?

The corpus is a set of public-domain NASA mishap investigation reports. The questions worth
asking about them cross documents — a contractor named in one report, a failure described in
another — which is exactly where a single vector lookup tends to stay inside one report.

**Status: M2, written but not yet run.** The corpus is cut into 1,373 chunks, there is an
ingest that embeds them and fills Vectorize and D1, and `GET /search` answers with the nearest
passages — the vector arm of the comparison. `eval/run.py` scores the frozen set against it.

What is not here is the number. The baseline run needs a real Vectorize index and a real
ingest, and that has not happened yet; when it does, the receipt lands in `runs/`.

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
including the boundary at exactly half a quote.

## Layout

| Path | What it is |
|------|------------|
| `src/index.ts` | The Worker. `GET /health` and `GET /search?q=…&k=10` |
| `src/retrieval.ts` | The `Retriever` interface and the vector implementation. M4's graph retriever arrives beside it, not inside it |
| `src/embed.ts` | The embedding model and its pooling, in one place, for the query side |
| `migrations/` | D1 schema: documents, chunks, nodes, edges, node_chunks |
| `scripts/chunk.py` | `corpus/text/` → `corpus/chunks.jsonl`. Deterministic: same corpus, same bytes |
| `scripts/ingest.py` | Embeds, indexes and writes the rows over the REST API. Resumable, and it never prints a credential |
| `eval/schema.md` | The format for `eval/questions.json` |
| `eval/validate.py` | Checks that format, and that every gold quote is verbatim on the page it claims |
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

`ingest.sh` is safe to interrupt and re-run: every write is an upsert, and the embeddings it
has already paid for are cached under `runs/tmp/`. `eval.sh` wants `dev.sh` running in another
terminal.

Embeddings come from `@cf/baai/bge-base-en-v1.5`: 768 dimensions, 512 input tokens, and
`pooling: "cls"` — which has to be the same for documents and for queries, or the two live in
different spaces. `src/embed.ts` and `scripts/ingest.py` are the two places that say so.
