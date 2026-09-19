# Where Graph RAG Does and Does Not Help (M5)

Measured findings from the comparison between plain vector retrieval (M2) and graph-augmented retrieval with re-ranking (M4) over 1,373 chunks and 24 frozen benchmark questions.

## 1. Single-Hop Control (Zero Harm)

The single-hop questions (`q01`–`q10`) serve as the control: a graph retriever that displaces good vector hits with loosely related graph neighbors will lose single-hop recall.

- **Recall at 10**: Remained **7/10 strict, 7/10 amended** (0 regression).
- **Rank distribution**:
  - Improved: `q04` (rank 9 → 5), `q06` (rank 2 → 1), `q08` (rank 2 → 1).
  - Unchanged: `q02` (rank 1), `q07` (rank 1), `q09` (rank 1).
  - Slipped: `q05` (rank 1 → 2).
  - Missed in both: `q01`, `q03`, `q10`.
- **Verdict**: Adding the graph walk and cross-encoder re-ranking caused zero regression to single-hop retrieval accuracy. The re-ranker functions as an effective guard: graph candidates only displace vector candidates when they have higher semantic relevance to the question.

## 2. 1 Hop vs. 2 Hops

Comparing 1-hop vs. 2-hop recursive CTE walks from the top 10 seed vector chunks:

- In the current corpus graph (5,036 nodes, 9,998 edges), a 1-hop walk reaches the immediate entity neighborhood of the seed chunks.
- A 2-hop walk expands the candidate frontier exponentially, but without edge weights or relation-type constraints, 2 hops predominantly reach high-degree hub nodes.
- For `q19` (identifying the HFACS / Swiss cheese human-factors model), 1 hop between entities was sufficient (`lma` → `hfacs`).
- Across the eval set, 2-hop expansion did not add net recall beyond 1-hop + wider vector candidate pooling, but added database query overhead.

## 3. Supernodes and Hub Dilution (The Gravity Well Failure)

High-degree entities act as "supernodes" in the graph:
- `Mars Polar Lander` (1,075 edges, 6 docs)
- `Genesis Mishap Investigation Board` (910 edges, 6 docs)
- `LMA` / Lockheed Martin Astronautics (568 edges, 6 docs)
- `JPL` (342 edges, 7 docs)
- `NASA` (181 edges, 7 docs)

### Honest Failure Case: `q17`
- **Question**: *"The prime contractor that built both Mars Surveyor '98 spacecraft in Colorado belongs to a corporation that also runs a satellite integration and test plant in California. Which two satellite programmes were consolidated into that plant in 1998, and where did they move from?"*
- **Bridge 1 passage**: `mars-climate-orbiter-mib-phase-i:0010` was retrieved at **rank 1**, establishing the contractor as Lockheed Martin Astronautics in Denver.
- **The failure**: The graph walk seeds from `LMA`. Because `LMA` has 568 edges spanning 6 documents, the walk from `LMA` is flooded by hundreds of adjacent nodes and chunks inside Mars Climate Orbiter, Mars Polar Lander, and MPIAT.
- **Result**: The CTE query's candidate limit (`LIMIT 20`) is saturated by the dense local cluster of the mishap where the search started, crowding out the target passage in `noaa-n-prime-mishap:0019`. The hub node becomes a gravity well.

## 4. Latency and Query Cost

Measured over the 24-question benchmark against a Cloudflare Worker on remote bindings:

| Metric | Vector Only (M2) | Graph + Re-rank (M4) | Multiplier |
|---|---|---|---|
| Wall clock (24 queries) | 6.4 s | 24.5 s | **3.83x** |
| Latency per query | ~267 ms | ~1,020 ms | **3.8x** |
| Vectorize queries | 1 (topK=10) | 1 (topK=25) | 1x |
| D1 queries | 1 (fetch chunks) | 2 (1 recursive CTE + 1 fetch) | 2x |
| Workers AI calls | 1 embedding (`bge-base`) | 1 embedding + 1 rerank (`bge-reranker-base`) | 2x |
| Neurons per query | ~1 Neuron | ~1 Neuron + re-ranker cost (30 contexts) | ~5–10x |

On Cloudflare Workers Free plan:
- 10 ms CPU execution limit is respected (D1, Vectorize, and AI bindings run off-thread).
- 50 subrequests per invocation limit is easily met (only 3 subrequests total).
- The 10,000 daily Neurons quota means plain vector search can serve thousands of queries a day, while graph + 30-candidate cross-encoder re-ranking reaches the daily free ceiling much sooner.

## 5. The Routing Rule for the Video

When should a developer add a graph to RAG, and when should they stay with vectors?

1. **Stay with Vectors + Cross-Encoder Re-ranking if**:
   - The query directly names the entity or topic sought (single-hop).
   - The final clause is specific enough that vector similarity lands within the top 25–50 passages. Expanding vector `topK` to 25 and adding a re-ranker captures most of the "low-hanging" recall gain at 1/3 the query complexity.
2. **Add an Entity Graph if**:
   - The query asks about shared attributes, lineage, or causes across disconnected documents where vocabulary does not overlap (multi-hop generic questions).
   - Bridge entities link the reports, but queries must apply hub-node damping (penalizing or capping edges from nodes with degree > 50) to prevent supernodes from trapping retrieval in local clusters.
