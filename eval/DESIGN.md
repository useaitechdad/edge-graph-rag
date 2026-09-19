# How the comparison is scored

Written before any retrieval code exists, and committed with the questions. If a rule here
changes later, the change is a commit with a reason, and every earlier run is re-scored.

## What is compared

Two retrievers over the same chunks and the same embeddings:

- **vector**: embed the question, take the nearest chunks.
- **graph**: start from the same vector hits, walk the entity graph, and let what the walk
  finds compete for the same places.

**Both return exactly `k` chunks.** The graph retriever does not get extra room. If it wants a
passage from the walk in the context, that passage has to displace a vector hit. Otherwise
"more context finds more" would be the whole result.

## The number

**Answer-passage recall at k.** A question is *found* when at least one of the `k` returned
chunks contains its `answer` passage. `bridge` passages record the path a reader needs; they
are reported, not scored.

- A chunk *contains* a gold passage when it holds at least half of the quote's characters
  (whitespace-normalised, contiguous). Chunk boundaries will split some quotes, and half is
  the point past which a reader has the substance.
- Primary `k = 10`. Also reported: `k = 5`.
- Reported three ways, always together: single-hop, multi-hop with a **specific** final clause,
  multi-hop with a **generic** final clause.

## Why the multi-hop questions are split in two

A multi-hop question names its target indirectly ("the other spacecraft built by that
contractor") and then asks something about it.

- If what it asks is **specific** — "what deceleration was the capsule's avionics required to
  detect" — the final clause describes the answer passage by itself, and plain vector search
  can land on it without ever resolving the hop.
- If what it asks is **generic** — an attribute every report has: its chair, its proximate
  cause, its launch vehicle — the final clause matches a passage in *every* document, and only
  the hop says which one is wanted.

The expectation, recorded here before anything is built: vector search does well on the first
kind and poorly on the second, and a graph earns its place only on the second. The per-question
labels and a prediction for each are in `questions.notes.md`. If the results disagree, the
results win.

## The control

The single-hop questions are there to catch harm. A graph retriever that displaces good vector
hits with loosely related passages will lose single-hop recall, and that loss is part of the
result.

## One corpus decision that affects scoring

`mco-mib-project-management` pages 58–105 reprint the whole of
`mars-climate-orbiter-mib-phase-i`. Those pages are **not indexed**. Left in, every Phase I
passage would exist twice, one copy would crowd the other out of the top `k`, and a hit on the
reprint would have to be argued about. No gold passage sits on those pages.

## What the questions may and may not be

- Every hop is stated in the corpus text. Nothing needs outside knowledge.
- Every answer is unique within the corpus.
- Questions are written the way a person would ask them. No vocabulary is stripped to make
  vector search fail, and no question is a riddle.
- The set is frozen before retrieval code exists, so neither retriever can be tuned to it and
  it cannot be tuned to either retriever.
