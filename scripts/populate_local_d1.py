#!/usr/bin/env python3
"""Populate the local D1 SQLite database from corpus/ artifacts."""

from __future__ import annotations

import glob
import json
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "corpus" / "MANIFEST.json"
CHUNKS = REPO_ROOT / "corpus" / "chunks.jsonl"
GRAPH = REPO_ROOT / "corpus" / "graph.json"


def find_db_path() -> Path:
    candidates = glob.glob(
        str(REPO_ROOT / ".wrangler/state/v3/d1/miniflare-D1DatabaseObject/0b3bbe*.sqlite")
    )
    if not candidates:
        raise RuntimeError("Local D1 SQLite database not found.")
    return Path(candidates[0])


def main():
    db_path = find_db_path()
    print(f"Connecting to {db_path.name}...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Documents
    print("Loading documents from MANIFEST.json...")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    doc_rows = [
        (doc["slug"], doc["title"], doc["url"], doc["sha256"], int(doc["pages"]))
        for doc in manifest.get("documents", [])
    ]
    cur.executemany(
        "INSERT OR REPLACE INTO documents (slug, title, source_url, sha256, page_count) VALUES (?, ?, ?, ?, ?)",
        doc_rows,
    )
    print(f"  Inserted {len(doc_rows)} documents.")

    # 2. Chunks
    print("Loading chunks from chunks.jsonl...")
    chunk_rows = []
    with CHUNKS.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                c = json.loads(line)
                chunk_rows.append(
                    (
                        c["id"],
                        c["document"],
                        int(c["page_start"]),
                        int(c["page_end"]),
                        int(c["ordinal"]),
                        c["text"],
                    )
                )
    cur.executemany(
        "INSERT OR REPLACE INTO chunks (id, document, page_start, page_end, ordinal, text) VALUES (?, ?, ?, ?, ?, ?)",
        chunk_rows,
    )
    print(f"  Inserted {len(chunk_rows)} chunks.")

    # 3. Graph
    print("Loading graph from graph.json...")
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    node_rows = [
        (n["id"], n["name"], n["type"], n.get("description"))
        for n in graph.get("nodes", [])
    ]
    cur.executemany(
        "INSERT OR REPLACE INTO nodes (id, name, type, description) VALUES (?, ?, ?, ?)",
        node_rows,
    )
    print(f"  Inserted {len(node_rows)} nodes.")

    edge_rows = [
        (e["id"], e["source"], e["target"], e["relation"], e["chunk_id"])
        for e in graph.get("edges", [])
    ]
    cur.executemany(
        "INSERT OR REPLACE INTO edges (id, source, target, relation, chunk_id) VALUES (?, ?, ?, ?, ?)",
        edge_rows,
    )
    print(f"  Inserted {len(edge_rows)} edges.")

    nc_rows = [
        (pair["node_id"], pair["chunk_id"])
        for pair in graph.get("node_chunks", [])
    ]
    cur.executemany(
        "INSERT OR REPLACE INTO node_chunks (node_id, chunk_id) VALUES (?, ?)",
        nc_rows,
    )
    print(f"  Inserted {len(nc_rows)} node_chunks.")

    conn.commit()
    conn.close()
    print("Local D1 population complete.")


if __name__ == "__main__":
    main()
