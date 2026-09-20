# Cloudflare Infrastructure Verification & Benchmark Receipt

This document records the exact, empirical Cloudflare infrastructure provisioned, populated, and benchmarked for the **Graph RAG at the Edge** project. It serves as public verification for the performance and recall metrics reported in the paper and explainer video.

---

## 1. Cloudflare D1 Database (Serverless SQLite)

The knowledge graph and document chunks were stored in a production Cloudflare D1 database.

- **Database Name:** `edge-graph-rag`
- **Database UUID:** `5aeda91d-1071-449a-83ca-871bb6a0fe18`
- **Created Timestamp:** `2026-09-19T04:25:46.898Z`
- **Engine Version:** `production` (SQLite via Cloudflare Workerd engine)
- **Deployment Region:** `WNAM` (Western North America)
- **Database Size:** `8,646,656 bytes` (~8.65 MB)
- **Tables (Schema Migrations Applied):** 6 tables

### Live Table Row Counts (Verified via Cloudflare D1 Query API)
```sql
SELECT 'documents' as tbl, count(*) as count FROM documents
UNION ALL SELECT 'chunks', count(*) FROM chunks
UNION ALL SELECT 'nodes', count(*) FROM nodes
UNION ALL SELECT 'edges', count(*) FROM edges
UNION ALL SELECT 'node_chunks', count(*) FROM node_chunks;
```

| Table Name | Row Count | Description |
| :--- | :--- | :--- |
| `documents` | **7** | Official NASA investigation reports |
| `chunks` | **1,373** | Text passages with exact document & page boundaries |
| `nodes` | **5,036** | Resolved entities extracted across reports |
| `edges` | **9,998** | Directed relationships with strict `chunk_id` provenance |
| `node_chunks` | **12,444** | Bidirectional entity-to-passage index mappings |

---

## 2. Cloudflare Vectorize Index (Dense Vector Search)

Vector seed search was executed using Cloudflare Vectorize v2.

- **Index Name:** `edge-graph-rag-chunks`
- **Created Timestamp:** `2026-09-19T04:30:34.427068Z`
- **Embedding Model:** `@cf/baai/bge-base-en-v1.5` (via Workers AI)
- **Dimensions:** `768`
- **Distance Metric:** `cosine`
- **Indexed Vectors:** **1,373** (1:1 with D1 chunks)
- **Last Mutation ID:** `3d92ca57-2bfd-46a2-b554-3d127529d9eb`

---

## 3. Cloudflare Workers AI (Embeddings & Cross-Encoder Reranking)

All neural inference ran serverless on Cloudflare's edge GPU fabric:
- **Dense Embedding Model:** `@cf/baai/bge-base-en-v1.5` (768 dimensions, `cls` pooling)
- **Neural Cross-Encoder Reranker:** `@cf/baai/bge-reranker-base`
- **Token / Neuron Metering:** 49.32 CF AI neurons billed during ingestion (recorded in `runs/20260919T045141Z-ingest.json`)

---

## 4. Empirical Evaluation Benchmark Results

Evaluated across **24 frozen questions** on 7 NASA mishap investigation reports:

| Evaluation Metric | Vector-Only Baseline | Edge Graph RAG (D1 CTE + Workers AI) | Delta |
| :--- | :---: | :---: | :---: |
| **Multi-Hop Recall (Amendment 1)** | 7/14 (50.0%) | **10/14 (71.4%)** | **+42.9% relative gain (+3 queries solved)** |
| **Overall Benchmark Recall (k=10)** | 14/24 (58.3%) | **17/24 (70.8%)** | **+21.4% relative gain** |
| **Single-Hop Control Lookups** | 7/10 (70.0%) | **7/10 (70.0%)** | **0 regressions** |
| **Median End-to-End Latency** | **267 ms** | **1,020 ms** | 3.8x latency multiplier |

### Key Benchmark Question Receipts
- **Question 11 (Mars '98 Contractor $\rightarrow$ Genesis Deceleration Switch):**
  - Vector search: Target evidence buried at **Rank 42** (cosine similarity failure).
  - Edge Graph RAG: Target bridge passage at **Rank 1**, switch requirement at **Rank 2**.
- **Question 17 (Supernode Hub Dilution):**
  - High-degree entity (Lockheed Martin, degree 317) floods candidate window, demonstrating the need for degree caps.

### Raw Execution Receipts in Repository
- Ingestion receipt: [`runs/20260919T045141Z-ingest.json`](../runs/20260919T045141Z-ingest.json)
- Graph load receipt: [`runs/20260919T091213Z-load-graph.json`](../runs/20260919T091213Z-load-graph.json)
- Final graph eval receipt: [`runs/20260919T103245Z-eval-graph.json`](../runs/20260919T103245Z-eval-graph.json)
