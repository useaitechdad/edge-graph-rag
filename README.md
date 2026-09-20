# edge-graph-rag

A small Graph RAG service on Cloudflare Workers, D1 and Vectorize, built to answer one question:
does adding a graph to vector search find passages that vector search alone misses?

The corpus is a set of public-domain NASA mishap investigation reports. The questions worth
asking about them cross documents — a contractor named in one report, a failure described in
another — which is exactly where a single vector lookup tends to stay inside one report.

**Status: M0 through M5 complete.** The corpus is cut into 1,373 chunks, embedded into
Vectorize and D1, and extracted into 5,036 entity nodes and 9,998 edges loaded into remote D1.
Both arms of the comparison are evaluated on the frozen 24-question benchmark:

- **Vector-only baseline (M2)**: **14 of 24 questions at k=10** under Amendment 1 (12 of 24 strict).
- **Graph retrieval + Re-ranking (M4)**: **17 of 24 questions at k=10** under Amendment 1 (15 of 24 strict).
  - Single-hop control: **7/10** (zero regression; 3 questions improved rank).
  - Multi-hop specific: **6/9** (+2 over vector).
  - Multi-hop generic: **4/5** (+1 over vector).
  - All 17 hits placed within the top 5 chunks (`@5` = 17/24).
- **Limits & routing rule (M5)**: documented in `eval/limits.md`. Graph candidates only displace
  vector hits when cross-encoder scored; 2 hops add no net recall over 1 hop without edge constraints;
  supernodes (`LMA` with 568 edges) act as gravity wells; latency increases 3.8x (~267 ms to ~1,020 ms).

All 45 gold passages survive chunking (`eval/coverage.py`), and all receipts sit under `runs/`.

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
| `src/index.ts` | The Worker. `GET /health` and `GET /search?q=…&k=10&retriever=graph` |
| `src/retrieval.ts` | The `Retriever` interface, `VectorRetriever`, and M4's `GraphRetriever` with CTE graph walk and re-ranking |
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
| `eval/run.py` | The eval run: `/search` per question, recall at 10 and at 5, writes a receipt |
| `eval/groups.json` | Which multi-hop questions have a specific final clause and which a generic one |
| `eval/limits.md` | M5 analysis: single-hop control, 1-hop vs 2-hop, hub node dilution, latency, and the routing rule |
| `docs/CLOUDFLARE_PROOF.md` | Verification receipt: exact Cloudflare D1 table counts (5,036 nodes, 9,998 edges) and Vectorize index stats |
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
./scripts/eval.sh --retriever vector # scores vector baseline (M2), writes runs/*-eval-vector.json
```

Then M3, the graph. Only the last of the three touches Cloudflare:

```sh
./scripts/extract.sh              # every chunk through Claude. --dry-run first for the request sizes
                                  # --backend cli to spend this machine's Claude Code login instead
python3 scripts/build_graph.py    # extractions -> corpus/graph.json, and prints what merged into what
./scripts/load-graph.sh           # the graph into D1's nodes, edges and node_chunks
```

Then M4 and M5, evaluating graph retrieval and individual questions:

```sh
./scripts/eval.sh                 # scores graph retriever (M4 default), writes runs/*-eval-graph.json
./scripts/ask.sh --id q11         # asks one question, prints ranked hits with gold/bridge labels
./scripts/ask.sh --id q11 --retriever vector  # asks the same question with vector-only
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

## Production Security & Rate Limiting

The `/search` endpoint coordinates Vectorize vector lookups, D1 recursive SQL queries, and Workers AI neural reranking. To protect live deployments:

1. **Authentication:** Set an `API_KEY` secret using Wrangler:
   ```sh
   npx wrangler secret put API_KEY
   ```
   When `API_KEY` is configured in the Worker environment (or in `.dev.vars`), requests to `/search` must supply `Authorization: Bearer <API_KEY>` (otherwise returning `401 Unauthorized`). When unset (default local dev), authentication remains open for local testing.

2. **Rate Limiting & Abuse Prevention:** Because each search executes vector lookups and cross-encoder reranking, attach a Cloudflare WAF Rate Limiting rule (e.g., 30–60 requests/minute per IP) under Cloudflare Dashboard → Security → WAF → Rate Limiting Rules to prevent neuron quota exhaustion.

## Content Rights & Corpus Provenance

The 7 evaluation reports originate from official NASA mishap boards and the Jet Propulsion Laboratory:
- **Zero Full PDFs Hosted:** The repository does **not** redistribute or re-host full PDF files or full text extracts (`corpus/raw/` and `corpus/text/` are strictly `.gitignore`d). Users download them directly from official NASA / NTRS servers using `python3 scripts/fetch-corpus.py`.
- **Public Releases with Redactions:** Reports are official U.S. Government works or NASA public releases. As confirmed in official NASA documentation (such as the CONTOUR release memorandum at https://discovery.larc.nasa.gov/pdf_files/Contour_Mishap_Investigation.pdf), all ITAR-controlled and proprietary information was excised prior to public release.
- **Evaluation Excerpts:** Only brief passage quotes are retained in `eval/questions.json` and `eval/equivalents.json` as frozen ground-truth test vectors for recall scoring under fair use.

## Licence

Apache-2.0. See `LICENSE`.

