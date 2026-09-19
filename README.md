# edge-graph-rag

A small Graph RAG service on Cloudflare Workers, D1 and Vectorize, built to answer one question:
does adding a graph to vector search find passages that vector search alone misses?

The corpus is a set of public-domain NASA mishap investigation reports. The questions worth
asking about them cross documents — a contractor named in one report, a failure described in
another — which is exactly where a single vector lookup tends to stay inside one report.

**Status: M1.** The eval set is frozen: 24 questions in `eval/questions.json`, how they are
scored in `eval/DESIGN.md`, and a per-question prediction in `eval/questions.notes.md`. There is
still no retrieval code; that starts at M2, after this commit.

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

`npm test` is two suites: Vitest inside the Workers runtime (the migration applies, `WITH
RECURSIVE` does or does not work on D1 — `test/recursive-cte.test.ts` records which, and the
health route answers), and `python3 eval/test_validate.py`, the eval validator's own fixture
tests.

## Layout

| Path | What it is |
|------|------------|
| `src/index.ts` | The Worker. `GET /health`, and nothing else yet |
| `migrations/` | D1 schema: documents, chunks, nodes, edges, node_chunks |
| `eval/schema.md` | The format for `eval/questions.json` |
| `eval/validate.py` | Checks that format, and that every gold quote is verbatim on the page it claims |
| `corpus/MANIFEST.json` | The seven reports: official URL, sha256, rights note. The files themselves are downloaded by `scripts/fetch-corpus.py`, never redistributed from here |
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

```sh
python3 scripts/fetch-corpus.py   # downloads the reports from their official hosts, checks each sha256
./scripts/setup-cloudflare.sh     # creates the D1 database and the 768-dim cosine index, writes wrangler.jsonc
./scripts/migrate-remote.sh
./scripts/dev.sh
```

Embeddings come from `@cf/baai/bge-base-en-v1.5`: 768 dimensions, 512 input tokens, and
`pooling: "cls"` — which has to be the same for documents and for queries, or the two live in
different spaces.
