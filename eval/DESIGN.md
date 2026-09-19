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

## Amendment 1 — equivalent answer passages

The first baseline run exposed a flaw in the metric above, not in the retrievers. These
reports state the same fact in more than one place: an executive summary, a findings list, the
body section, an appendix that reprints the section, and sometimes a second report writing up
the same mishap. Each question carries exactly one `answer` passage, so a retriever that
returns a *different* passage stating the same answer scores as a miss. That is a mistake
about what was asked, and it would be made against both retrievers unevenly — whichever one
happens to favour summaries over body text pays for it.

**The rule.** A passage is an equivalent answer passage only if a reader given that passage
alone, plus the question, could give the full answer the question asks for — the same standard
the original gold passage meets. For a multi-hop question the passage has to state the final
attribute *for the correct target entity*: the chair of the right board, not the chair of
another one. A passage that mentions the topic, or gives one of two things asked for, or names
the outcome without the mechanism the question asks about, is not equivalent. Where it was
arguable, the passage was left out and the reason recorded.

**How they were found.** By plain lexical search of `corpus/text/` — the answer's distinctive
values, names and phrases, plus the variants this corpus forces (spelled-out numbers, OCR that
glues words together or breaks them, the synonyms the reports themselves use) — by an author
who had seen no retrieval output of any kind: no `/search` call, no eval or ingest receipt, no
reasoning about what an embedding model would or would not find. The search terms used for
each question, the passages accepted, and the borderline ones rejected with their reason, are
in `equivalents.notes.md`. Equivalents are `answer` passages only; the amendment adds no
bridges.

**What did not change.** `questions.json` is untouched, byte for byte: the frozen set is still
the frozen set, and the equivalents live beside it in `equivalents.json`, which
`validate.py` holds to the same verbatim-on-the-page rules plus three of its own — the page
has to be one that is indexed, an equivalent may not restate its own question's gold answer,
and two equivalents may not be one passage read twice.

**Both numbers, always together.** Every run reports **strict** recall, the rule as frozen
with only the gold answer passage counting, beside **amended** recall, where any equivalent
counts too. Both retrievers are scored with the same amended rule, on the same passages, so
the comparison the project exists to make is unaffected either way; the amendment moves both
numbers up or neither. Each question also records whether its hit landed on the `gold` passage
or on an `equivalent`.

Receipts written before this amendment cannot be re-scored offline: they record each
question's rank, not the chunks that were returned. `run.py` now stores the ranked chunk ids,
so a future receipt can be re-scored against a later amendment without another run.
