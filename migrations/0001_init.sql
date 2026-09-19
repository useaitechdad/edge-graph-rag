-- M0 schema: the corpus, its chunks, and the graph extracted from those chunks.
-- Every graph row points back at the chunk it came from, so any answer can be
-- traced to a passage in a real document.

-- One row per source document in the corpus.
CREATE TABLE documents (
  slug        TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  source_url  TEXT NOT NULL,
  sha256      TEXT NOT NULL,
  page_count  INTEGER NOT NULL
);

-- A passage of a document: the unit that gets embedded, retrieved and quoted.
CREATE TABLE chunks (
  id          TEXT PRIMARY KEY,
  document    TEXT NOT NULL REFERENCES documents(slug),
  page_start  INTEGER NOT NULL,
  page_end    INTEGER NOT NULL,
  ordinal     INTEGER NOT NULL,
  text        TEXT NOT NULL
);

-- An entity the extractor found: a person, organisation, spacecraft, instrument, cause.
CREATE TABLE nodes (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  type        TEXT NOT NULL,
  description TEXT
);

-- A relation between two entities, kept with the chunk that stated it (provenance).
CREATE TABLE edges (
  id        TEXT PRIMARY KEY,
  source    TEXT NOT NULL REFERENCES nodes(id),
  target    TEXT NOT NULL REFERENCES nodes(id),
  relation  TEXT NOT NULL,
  chunk_id  TEXT NOT NULL REFERENCES chunks(id)
);

-- Which chunk mentioned which entity: the way back from a node to passages.
CREATE TABLE node_chunks (
  node_id   TEXT NOT NULL REFERENCES nodes(id),
  chunk_id  TEXT NOT NULL REFERENCES chunks(id),
  PRIMARY KEY (node_id, chunk_id)
);

-- Reading a document back in order.
CREATE INDEX idx_chunks_document_ordinal ON chunks (document, ordinal);
-- Finding an entity by the name the extractor used.
CREATE INDEX idx_nodes_name ON nodes (name);
-- Walking edges outward: source -> target.
CREATE INDEX idx_edges_source ON edges (source, relation);
-- Walking the same edges backward: target -> source. A hop is undirected at query time.
CREATE INDEX idx_edges_target ON edges (target, relation);
-- Going from an edge back to the passage that stated it.
CREATE INDEX idx_edges_chunk ON edges (chunk_id);
-- Going from an entity to the passages that mention it.
CREATE INDEX idx_node_chunks_chunk ON node_chunks (chunk_id);
