#!/usr/bin/env python3
"""Write corpus/graph.json into the remote D1 database.

The last step of M3, and the only one that touches Cloudflare. It uses the same
REST client as `scripts/ingest.py` — same masking, same refusal to print a
credential, same one-statement-at-a-time D1 calls — and fills the three tables
migrations/0001_init.sql defines for the graph: nodes, edges, node_chunks.

Idempotent by replacement: the three tables are emptied and rewritten, so running
it twice leaves exactly one copy of the graph, and running it after a rebuild
leaves the rebuilt one rather than both. Nothing else in the database is touched;
documents and chunks belong to the ingest.

Every edge carries the id of the chunk that stated it, so a graph answer can
always be turned back into a passage. Those chunk ids have to exist in `chunks`
already — run the ingest first, over the whole corpus.

Dependencies: Python standard library only.

Usage:
    ./scripts/load-graph.sh              # replace the graph in D1
    ./scripts/load-graph.sh --dry-run    # no network at all; prints what it would send
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ingest  # noqa: E402  — the Cloudflare REST client and mask() live there

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAPH = REPO_ROOT / "corpus" / "graph.json"
RUNS = REPO_ROOT / "runs"

# D1 allows 100 bound parameters per statement. A node row binds four of them, an
# edge row five and a node_chunks row two; each count below is the most that fits
# with a little headroom, the same way scripts/ingest.py sizes its batches.
NODE_ROWS = 24
EDGE_ROWS = 19
NODE_CHUNK_ROWS = 48

# Emptied in this order: edges and node_chunks point at nodes, so the rows that
# reference go before the rows that are referenced.
TABLES = ("edges", "node_chunks", "nodes")


def load_graph(path: Path) -> dict:
    if not path.exists():
        raise ingest.IngestError(f"No {path.name}. Run python3 scripts/build_graph.py first.")
    graph = json.loads(path.read_text(encoding="utf-8"))
    for table in ("nodes", "edges", "node_chunks"):
        if table not in graph:
            raise ingest.IngestError(f"{path.name} has no {table}")
    return graph


def clear(api: ingest.Cloudflare, db: str) -> None:
    print(f"==> D1: emptying {', '.join(TABLES)}")
    for table in TABLES:
        api.d1_query(db, f"DELETE FROM {table}", [])


def write_rows(
    api: ingest.Cloudflare,
    db: str,
    label: str,
    sql: str,
    placeholder: str,
    rows: list[tuple],
    per_statement: int,
) -> int:
    """One table, in statements of `per_statement` rows. Prints counts, never ids."""
    batches = ingest.batched(rows, per_statement)
    print(f"==> D1: {len(rows)} {label} rows in {len(batches)} statements, {per_statement} rows each")
    written = 0
    for number, batch in enumerate(batches, start=1):
        values = ", ".join([placeholder] * len(batch))
        api.d1_query(db, f"{sql} VALUES {values}", [field for row in batch for field in row])
        written += len(batch)
        if number % 10 == 0 or number == len(batches):
            print(f"    {written}/{len(rows)} rows")
    return written


def write_nodes(api: ingest.Cloudflare, db: str, graph: dict) -> int:
    rows = [
        (node["id"], node["name"], node["type"], node.get("description") or None)
        for node in graph["nodes"]
    ]
    return write_rows(
        api,
        db,
        "node",
        "INSERT OR REPLACE INTO nodes (id, name, type, description)",
        "(?, ?, ?, ?)",
        rows,
        NODE_ROWS,
    )


def write_edges(api: ingest.Cloudflare, db: str, graph: dict) -> int:
    rows = [
        (edge["id"], edge["source"], edge["target"], edge["relation"], edge["chunk_id"])
        for edge in graph["edges"]
    ]
    return write_rows(
        api,
        db,
        "edge",
        "INSERT OR REPLACE INTO edges (id, source, target, relation, chunk_id)",
        "(?, ?, ?, ?, ?)",
        rows,
        EDGE_ROWS,
    )


def write_node_chunks(api: ingest.Cloudflare, db: str, graph: dict) -> int:
    rows = [(pair["node_id"], pair["chunk_id"]) for pair in graph["node_chunks"]]
    return write_rows(
        api,
        db,
        "node_chunks",
        "INSERT OR REPLACE INTO node_chunks (node_id, chunk_id)",
        "(?, ?)",
        rows,
        NODE_CHUNK_ROWS,
    )


def receipt_for(api: ingest.Cloudflare, graph: dict, counts: dict, seconds: float, started: str, dry_run: bool) -> dict:
    documents = sorted({document for node in graph["nodes"] for document in node.get("documents", [])})
    crossing = sum(1 for node in graph["nodes"] if len(node.get("documents", [])) > 1)
    return {
        "milestone": "M3",
        "step": "load-graph",
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wall_seconds": round(seconds, 1),
        "dry_run": dry_run,
        "counts": counts,
        "graph": {
            "documents": len(documents),
            "nodes_in_more_than_one_document": crossing,
        },
        "rows_per_statement": {
            "nodes": NODE_ROWS,
            "edges": EDGE_ROWS,
            "node_chunks": NODE_CHUNK_ROWS,
        },
        "http_status_counts": dict(sorted(api.statuses.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graph", type=Path, default=GRAPH, help="the graph to load")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="no network at all: print what would be sent, and the size of it",
    )
    args = parser.parse_args(argv)

    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not args.dry_run and not (account and token):
        print("Refusing to run: no credentials. Use ./scripts/load-graph.sh.", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    clock = time.monotonic()
    api = ingest.Cloudflare(account, token, args.dry_run)

    try:
        graph = load_graph(args.graph)
        db = "dry-run" if args.dry_run else ingest.database_id()
        api.secrets.append(db)
        print(
            f"{len(graph['nodes'])} nodes, {len(graph['edges'])} edges, "
            f"{len(graph['node_chunks'])} node_chunks from {args.graph.name}"
            + ("  [DRY RUN — no network]" if args.dry_run else "")
        )
        clear(api, db)
        counts = {
            "nodes": write_nodes(api, db, graph),
            "edges": write_edges(api, db, graph),
            "node_chunks": write_node_chunks(api, db, graph),
        }
    except ingest.IngestError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1

    receipt = receipt_for(api, graph, counts, time.monotonic() - clock, started, args.dry_run)
    text = ingest.mask(json.dumps(receipt, indent="\t", ensure_ascii=False), api.secrets)
    if args.dry_run:
        print(f"\n{text}")
        where = "Dry run — no receipt written."
    else:
        RUNS.mkdir(parents=True, exist_ok=True)
        path = RUNS / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-load-graph.json"
        path.write_text(text + "\n", encoding="utf-8")
        where = f"Receipt: {path.relative_to(REPO_ROOT)}"
    print(f"\nDone in {time.monotonic() - clock:.1f}s. {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
