# edge-graph-rag

A small Graph RAG service on Cloudflare Workers, D1 and Vectorize, built to answer one question:
does adding a graph to vector search find passages that vector search alone misses?

Work in progress. The eval questions in `eval/` are committed before any retrieval code exists,
so the result cannot be tuned to fit them.
