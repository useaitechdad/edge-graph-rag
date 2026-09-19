#!/usr/bin/env python3
"""Embed corpus/chunks.jsonl, index it in Vectorize, and write the rows into D1.

Everything goes over the Cloudflare REST API with CLOUDFLARE_API_TOKEN and
CLOUDFLARE_ACCOUNT_ID from the environment — `scripts/ingest.sh` is the way in,
and it sources scripts/_auth.sh like every other script that talks to Cloudflare.

Four stages, in this order:

  1. embed     chunk text -> 768-dimension vectors, @cf/baai/bge-base-en-v1.5
               with pooling "cls" (the query side of src/ must match, or the two
               live in different spaces)
  2. documents the seven corpus rows, from corpus/MANIFEST.json
  3. chunks    the chunk rows, so a retrieved vector id can be turned back into
               text and a page number
  4. vectors   upsert into the Vectorize index

Re-running is safe. Every write is an upsert, and a progress file under runs/tmp/
records which batches are already done, so an interrupted run picks up where it
stopped without paying for the embeddings twice.

Nothing here prints, logs or writes the token, the account id or the database id.
Error bodies from the API are masked before they are shown, because an error body
is the one place an account id turns up uninvited.

Dependencies: Python standard library only.

Usage:
    ./scripts/ingest.sh                 # the whole corpus
    ./scripts/ingest.sh --dry-run       # no network at all; prints what it would send
    ./scripts/ingest.sh --limit 50      # the first 50 chunks, for a smoke test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "corpus" / "MANIFEST.json"
CHUNKS = REPO_ROOT / "corpus" / "chunks.jsonl"
WRANGLER = REPO_ROOT / "wrangler.jsonc"
RUNS = REPO_ROOT / "runs"
TMP = RUNS / "tmp"
PROGRESS = TMP / "ingest-progress.json"
EMBEDDINGS = TMP / "embeddings.jsonl"

API_BASE = "https://api.cloudflare.com/client/v4"
EMBEDDING_MODEL = "@cf/baai/bge-base-en-v1.5"
POOLING = "cls"
DIMENSIONS = 768
INDEX_NAME = "edge-graph-rag-chunks"
TIMEOUT = 180

# Texts per Workers AI request. The sync embeddings endpoint takes an array, but
# no maximum array length is documented, so this stays well short of anything
# that could be one: 50 chunks is about 65 KB of text per request.
EMBED_BATCH = 50

# Vectors per Vectorize upsert. The documented ceiling is 1000 for the Workers
# binding and 5000 over HTTP; 1000 is the number that holds either way.
VECTOR_BATCH = 1000

# Rows per D1 statement. D1 allows 100 bound parameters per query, and a chunk
# row binds six of them — 16 rows is 96 parameters, the most that fits.
D1_CHUNK_ROWS = 16
# A document row binds five, so all seven documents go in one statement.
D1_DOCUMENT_ROWS = 20

# Response headers worth keeping in the receipt: anything that might carry rate
# or usage accounting. Matched case-insensitively, prefix-wise.
USAGE_HEADER_PREFIXES = ("x-ratelimit", "ratelimit", "cf-ai", "x-usage", "x-neuron")

# Keys the embeddings response is expected to carry. Anything else is recorded in
# the receipt, because an undocumented usage field would turn up as one.
KNOWN_ENVELOPE_KEYS = {"result", "success", "errors", "messages", "result_info"}
KNOWN_RESULT_KEYS = {"data", "shape", "pooling"}

# A 32-character hex run is an account id; the 8-4-4-4-12 form is a database id.
LOOKS_LIKE_ACCOUNT = re.compile(r"\b[0-9a-f]{32}\b", re.IGNORECASE)
LOOKS_LIKE_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)


class IngestError(Exception):
    """Already masked by the time it is raised."""


def mask(text: str, secrets: list[str]) -> str:
    """Take the account id, the database id and the token out of anything on its
    way to a terminal, a log or a receipt."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    text = LOOKS_LIKE_UUID.sub("<redacted>", text)
    return LOOKS_LIKE_ACCOUNT.sub("<redacted>", text)


def strip_jsonc(text: str) -> str:
    """wrangler.jsonc is JSON with // comments. Drop them, minding strings."""
    out: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
        elif text.startswith("//", index):
            while index < len(text) and text[index] != "\n":
                index += 1
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
            continue
        else:
            out.append(char)
        index += 1
    return "".join(out)


def database_id() -> str:
    """The D1 database id, from the generated wrangler.jsonc — the same place
    scripts/migrate-remote.sh looks, and the same refusal if it is a placeholder."""
    if not WRANGLER.exists():
        raise IngestError("No wrangler.jsonc. Run ./scripts/setup-cloudflare.sh first.")
    config = json.loads(strip_jsonc(WRANGLER.read_text(encoding="utf-8")))
    for database in config.get("d1_databases", []):
        if database.get("binding") == "DB":
            found = str(database.get("database_id", ""))
            if not found or found.startswith("__") or set(found) <= set("0-"):
                raise IngestError(
                    "wrangler.jsonc has no real D1 id. Run ./scripts/setup-cloudflare.sh first."
                )
            return found
    raise IngestError("wrangler.jsonc has no d1_databases entry bound to DB.")


class Cloudflare:
    """The three REST endpoints this milestone touches, one function each.

    Isolated on purpose: the shapes below were taken from Cloudflare's docs, and
    where a detail could not be confirmed it is marked UNVERIFIED so the fix is
    one function rather than a search.
    """

    def __init__(self, account: str, token: str, dry_run: bool) -> None:
        self._account = account
        self._token = token
        self.dry_run = dry_run
        self.secrets = [token, account]
        self.statuses: Counter[str] = Counter()
        self.usage_headers: dict[str, str] = {}
        self.unexpected_fields: set[str] = set()

    def _post(self, path: str, body: bytes, content_type: str, label: str) -> dict:
        """One POST. `label` is what the operator sees; the URL never is."""
        request = urllib.request.Request(
            f"{API_BASE}/accounts/{self._account}/{path}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": content_type,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                self.statuses[f"{label} {response.status}"] += 1
                self._remember_headers(response.headers.items())
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.statuses[f"{label} {exc.code}"] += 1
            detail = mask(exc.read().decode("utf-8", "replace")[:600], self.secrets)
            raise IngestError(f"{label} failed with HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, OSError) as exc:
            self.statuses[f"{label} network-error"] += 1
            raise IngestError(f"{label} failed: {mask(str(exc), self.secrets)}") from None

        if not payload.get("success", False):
            detail = mask(json.dumps(payload.get("errors", []))[:600], self.secrets)
            raise IngestError(f"{label} returned success=false: {detail}")
        self._remember_fields(payload)
        return payload

    def _remember_headers(self, headers) -> None:
        for name, value in headers:
            lowered = name.lower()
            if lowered.startswith(USAGE_HEADER_PREFIXES):
                self.usage_headers[lowered] = value

    def _remember_fields(self, payload: dict) -> None:
        self.unexpected_fields.update(set(payload) - KNOWN_ENVELOPE_KEYS)
        result = payload.get("result")
        if isinstance(result, dict):
            self.unexpected_fields.update(
                f"result.{key}" for key in set(result) - KNOWN_RESULT_KEYS - {"mutationId"}
            )

    # --- endpoint 1: Workers AI embeddings ---------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        """POST /accounts/{account}/ai/run/@cf/baai/bge-base-en-v1.5

        Body {"text": [...], "pooling": "cls"}; the vectors come back as
        result.data, one row per input, in order. Confirmed against the model's
        own documentation page (sources/cloudflare/model-bge-base-en-v1.5.md).
        """
        if self.dry_run:
            return [[0.0] * DIMENSIONS for _ in texts]
        body = json.dumps({"text": texts, "pooling": POOLING}).encode("utf-8")
        payload = self._post(f"ai/run/{EMBEDDING_MODEL}", body, "application/json", "workers-ai")
        vectors = payload["result"]["data"]
        if len(vectors) != len(texts):
            raise IngestError(f"asked for {len(texts)} embeddings, got {len(vectors)}")
        for vector in vectors:
            if len(vector) != DIMENSIONS:
                raise IngestError(f"expected {DIMENSIONS} dimensions, got {len(vector)}")
        return vectors

    # --- endpoint 2: Vectorize v2 upsert -----------------------------------
    def vectorize_upsert(self, vectors: list[dict]) -> None:
        """POST /accounts/{account}/vectorize/v2/indexes/{index}/upsert

        The body is NDJSON: one vector per line. UNVERIFIED — the per-line shape
        below ({"id", "values", "metadata"}) matches the Workers binding's vector
        object, but Cloudflare's REST documentation does not spell the line out,
        and its curl example uploads the NDJSON as a multipart file field named
        `body` rather than as the request body. If the API rejects this, the
        alternative is a multipart/form-data request with the same bytes as a
        file part; both are this one function.
        """
        lines = "".join(f"{json.dumps(vector)}\n" for vector in vectors)
        if self.dry_run:
            return
        self._post(
            f"vectorize/v2/indexes/{INDEX_NAME}/upsert",
            lines.encode("utf-8"),
            "application/x-ndjson",
            "vectorize-upsert",
        )

    # --- endpoint 3: D1 query ----------------------------------------------
    def d1_query(self, db: str, sql: str, params: list) -> None:
        """POST /accounts/{account}/d1/database/{database}/query

        Body {"sql": ..., "params": [...]}, one statement, at most 100 bound
        parameters. The database id is a path segment, so it never reaches the
        terminal: `label` is what gets printed.
        """
        if self.dry_run:
            return
        body = json.dumps({"sql": sql, "params": params}).encode("utf-8")
        self._post(f"d1/database/{db}/query", body, "application/json", "d1-query")


def load_chunks(limit: int | None) -> list[dict]:
    with CHUNKS.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return rows[:limit] if limit else rows


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Progress:
    """What is already written, so a second run is a resume and not a repeat."""

    def __init__(self, key: str, dry_run: bool = False) -> None:
        self.key = key
        self.dry_run = dry_run
        self.done: dict[str, list[int]] = {"d1_chunks": [], "vectors": []}
        self.documents = False
        if dry_run:
            # A dry run reports the whole job, not the part an earlier run left,
            # and it must not touch an earlier run's files.
            return
        if PROGRESS.exists():
            saved = json.loads(PROGRESS.read_text(encoding="utf-8"))
            if saved.get("key") == key:
                self.done = {stage: list(saved.get(stage, [])) for stage in self.done}
                self.documents = bool(saved.get("documents"))
            else:
                print("    chunks or batch sizes changed since the last run — starting over")
                EMBEDDINGS.unlink(missing_ok=True)

    def is_done(self, stage: str, index: int) -> bool:
        return index in self.done[stage]

    def mark(self, stage: str, index: int) -> None:
        self.done[stage].append(index)
        self.save()

    def mark_documents(self) -> None:
        self.documents = True
        self.save()

    def save(self) -> None:
        if self.dry_run:
            return
        TMP.mkdir(parents=True, exist_ok=True)
        PROGRESS.write_text(
            json.dumps(
                {"key": self.key, "documents": self.documents, **self.done}, indent="\t"
            ),
            encoding="utf-8",
        )


def cached_embeddings() -> dict[str, list[float]]:
    if not EMBEDDINGS.exists():
        return {}
    cache: dict[str, list[float]] = {}
    with EMBEDDINGS.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                cache[row["id"]] = row["values"]
    return cache


def batched(items: list, size: int) -> list[list]:
    return [items[start : start + size] for start in range(0, len(items), size)]


def embed_all(api: Cloudflare, chunks: list[dict]) -> tuple[dict[str, list[float]], int]:
    """(chunk id -> vector, how many were embedded on this run)."""
    cache = {} if api.dry_run else cached_embeddings()
    todo = [chunk for chunk in chunks if chunk["id"] not in cache]
    batches = batched(todo, EMBED_BATCH)
    print(f"==> embedding {len(todo)} chunks with {EMBEDDING_MODEL}, pooling {POOLING}")
    if cache:
        print(f"    {len(cache)} already embedded on an earlier run, reusing")
    if not batches:
        return cache, 0

    TMP.mkdir(parents=True, exist_ok=True)
    for number, batch in enumerate(batches, start=1):
        texts = [chunk["text"] for chunk in batch]
        size = sum(len(text.encode("utf-8")) for text in texts)
        print(f"    batch {number}/{len(batches)}  {len(batch):>4} chunks  {size / 1024:6.1f} KB")
        vectors = api.embed(texts)
        if api.dry_run:
            cache.update({chunk["id"]: vector for chunk, vector in zip(batch, vectors)})
            continue
        with EMBEDDINGS.open("a", encoding="utf-8") as handle:
            for chunk, vector in zip(batch, vectors):
                cache[chunk["id"]] = vector
                handle.write(json.dumps({"id": chunk["id"], "values": vector}) + "\n")
    return cache, len(todo)


def write_documents(api: Cloudflare, db: str, slugs: set[str], progress: Progress) -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = [
        (doc["slug"], doc["title"], doc["url"], doc["sha256"], doc["pages"])
        for doc in manifest["documents"]
        if doc["slug"] in slugs
    ]
    print(f"==> D1: {len(rows)} document rows")
    if progress.documents:
        print("    already written on an earlier run")
        return len(rows)
    for batch in batched(rows, D1_DOCUMENT_ROWS):
        # CAST keeps page_count an integer whichever way the REST layer types a
        # bound parameter; SQLite's column affinity would usually do it, but a
        # page number arriving as text would reach /search as a string.
        values = ", ".join(["(?, ?, ?, ?, CAST(? AS INTEGER))"] * len(batch))
        api.d1_query(
            db,
            "INSERT OR REPLACE INTO documents (slug, title, source_url, sha256, page_count)"
            f" VALUES {values}",
            [field for row in batch for field in row],
        )
    if not api.dry_run:
        progress.mark_documents()
    return len(rows)


def write_chunks(api: Cloudflare, db: str, chunks: list[dict], progress: Progress) -> int:
    batches = batched(chunks, D1_CHUNK_ROWS)
    print(f"==> D1: {len(chunks)} chunk rows in {len(batches)} statements, {D1_CHUNK_ROWS} rows each")
    written = 0
    for number, batch in enumerate(batches):
        if progress.is_done("d1_chunks", number):
            continue
        values = ", ".join(
            ["(?, ?, CAST(? AS INTEGER), CAST(? AS INTEGER), CAST(? AS INTEGER), ?)"] * len(batch)
        )
        api.d1_query(
            db,
            "INSERT OR REPLACE INTO chunks (id, document, page_start, page_end, ordinal, text)"
            f" VALUES {values}",
            [
                field
                for chunk in batch
                for field in (
                    chunk["id"],
                    chunk["document"],
                    chunk["page_start"],
                    chunk["page_end"],
                    chunk["ordinal"],
                    chunk["text"],
                )
            ],
        )
        written += len(batch)
        if not api.dry_run:
            progress.mark("d1_chunks", number)
        if (number + 1) % 10 == 0 or number + 1 == len(batches):
            print(f"    {written}/{len(chunks)} rows")
    return written


def write_vectors(
    api: Cloudflare, chunks: list[dict], cache: dict[str, list[float]], progress: Progress
) -> int:
    vectors = [
        {
            "id": chunk["id"],
            "values": cache[chunk["id"]],
            "metadata": {
                "document": chunk["document"],
                "page_start": chunk["page_start"],
                "page_end": chunk["page_end"],
            },
        }
        for chunk in chunks
    ]
    batches = batched(vectors, VECTOR_BATCH)
    print(f"==> Vectorize: {len(vectors)} vectors into {INDEX_NAME}, {len(batches)} request(s)")
    sent = 0
    for number, batch in enumerate(batches):
        if progress.is_done("vectors", number):
            continue
        size = sum(len(json.dumps(vector).encode("utf-8")) + 1 for vector in batch)
        print(f"    request {number + 1}/{len(batches)}  {len(batch)} vectors  {size / 1024 / 1024:.1f} MB")
        api.vectorize_upsert(batch)
        sent += len(batch)
        if not api.dry_run:
            progress.mark("vectors", number)
    return sent


def write_receipt(
    api: Cloudflare,
    chunks: list[dict],
    counts: dict[str, int],
    seconds: float,
    started: str,
    dry_run: bool,
) -> Path:
    characters = sum(len(chunk["text"]) for chunk in chunks)
    receipt = {
        "milestone": "M2",
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wall_seconds": round(seconds, 1),
        "dry_run": dry_run,
        "model": EMBEDDING_MODEL,
        "pooling": POOLING,
        "dimensions": DIMENSIONS,
        "index": INDEX_NAME,
        "counts": counts,
        "batch_sizes": {
            "embed": EMBED_BATCH,
            "vectorize_upsert": VECTOR_BATCH,
            "d1_chunk_rows_per_statement": D1_CHUNK_ROWS,
            "d1_document_rows_per_statement": D1_DOCUMENT_ROWS,
        },
        "http_status_counts": dict(sorted(api.statuses.items())),
        "cost": {
            "input_characters": characters,
            "estimated_input_tokens": round(characters / 4),
            "estimate_basis": "characters / 4; there is no tokenizer offline, so this "
            "is an estimate and not a billed figure",
            "usage_headers_seen": api.usage_headers,
            "unexpected_response_fields": sorted(api.unexpected_fields),
            "note": "Workers AI reports token usage only on text-generation responses; "
            "the embeddings response carries {data, shape} and no usage object, and no "
            "neuron or rate-limit header was observed. If usage_headers_seen and "
            "unexpected_response_fields are both empty, the API returned nothing usable "
            "for measuring Neuron cost and the estimate above is all there is.",
        },
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RUNS / f"{stamp}-ingest.json"
    text = mask(json.dumps(receipt, indent="\t", ensure_ascii=False), api.secrets)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="ingest only the first N chunks")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="no network at all: print what would be sent, and the size of it",
    )
    args = parser.parse_args(argv)

    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not args.dry_run and not (account and token):
        print("Refusing to run: no credentials. Use ./scripts/ingest.sh.", file=sys.stderr)
        return 2
    if not CHUNKS.exists():
        print(f"No {CHUNKS.name}. Run python3 scripts/chunk.py first.", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    clock = time.monotonic()
    api = Cloudflare(account, token, args.dry_run)

    try:
        chunks = load_chunks(args.limit)
        db = "dry-run" if args.dry_run else database_id()
        # The id is a path segment, never printed — but if it ever came back in
        # an error body it would be masked like the rest.
        api.secrets.append(db)
        key = "|".join(
            [digest_of(CHUNKS), str(args.limit), str(EMBED_BATCH), str(D1_CHUNK_ROWS), str(VECTOR_BATCH)]
        )
        progress = Progress(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], args.dry_run)

        print(f"{len(chunks)} chunks from {CHUNKS.name}" + ("  [DRY RUN — no network]" if args.dry_run else ""))
        cache, embedded = embed_all(api, chunks)
        documents = write_documents(api, db, {chunk["document"] for chunk in chunks}, progress)
        rows = write_chunks(api, db, chunks, progress)
        vectors = write_vectors(api, chunks, cache, progress)
    except IngestError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1

    counts = {
        "chunks": len(chunks),
        "documents": documents,
        "d1_chunk_rows_written": rows,
        "vectors_upserted": vectors,
        "chunks_embedded_this_run": embedded,
        "embeddings_reused_from_cache": len(chunks) - embedded,
    }
    receipt = write_receipt(api, chunks, counts, time.monotonic() - clock, started, args.dry_run)
    print(f"\nDone in {time.monotonic() - clock:.1f}s. Receipt: {receipt.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
