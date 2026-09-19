# `eval/questions.json` — the format

The eval set is written and committed **before any retrieval code exists** (milestone M1),
so the numbers later on cannot be tuned to fit it. `validate.py` is the gate: it checks the
shape of the file and that every gold quote really is on the page it claims.

`questions.json` is a JSON array. Each element is one question:

```json
{
  "id": "mco-units-01",
  "kind": "multi-hop",
  "question": "Which contractor supplied the software that used pound-seconds, and what other mission did that contractor build?",
  "answer": "…",
  "hops": "the contractor named in both reports",
  "gold": [
    { "doc": "mars-climate-orbiter-mib-phase-i", "page": 17, "role": "bridge", "quote": "verbatim text from page 17, 80–400 characters" },
    { "doc": "mars-polar-lander-ds2-loss", "page": 42, "role": "answer", "quote": "verbatim text from page 42, 80–400 characters" }
  ]
}
```

| Field      | Type      | Rule |
|------------|-----------|------|
| `id`       | string    | Non-empty, unique across the file. Stable: it is the join key for every eval run. |
| `kind`     | string    | `single-hop` or `multi-hop`. Single-hop is the control group. |
| `question` | string    | Non-empty. What a user would actually type. |
| `answer`   | string    | Non-empty. The short answer a human reading the gold passages would give. |
| `hops`     | string    | Required for `multi-hop`: names the entity that links the passages. Optional, and ignored, for `single-hop`. |
| `gold`     | array     | At least one passage. These are the passages retrieval has to reach. |

Each gold passage:

| Field   | Type    | Rule |
|---------|---------|------|
| `doc`   | string  | A corpus slug: the file is `corpus/text/<doc>.txt`. |
| `page`  | integer | 1 or greater, and the page must exist in that file. |
| `role`  | string  | `answer`: the passage that states the answer, and the one retrieval is scored on. `bridge`: a passage a reader needs on the way there. |
| `quote` | string  | Verbatim from that page, 80–400 characters once whitespace is normalised. |

A `multi-hop` question needs **at least two gold passages on at least two different pages**
(the same document is fine, a second page is not optional). That is what makes it a question
one passage cannot answer. Every question needs at least one `answer` passage, and a
`multi-hop` question at least one `bridge` as well.

No other keys are allowed, on a question or on a passage — a typo should fail the build, not
be silently ignored.

## How the quote check works

`corpus/text/<doc>.txt` marks page boundaries with lines of the form `\f--- page N ---`
(form feed, then the marker). The validator splits on those lines, then compares
whitespace-normalised text: every run of whitespace, in both the page and the quote, becomes
a single space. That survives the line wrapping a PDF text extract leaves behind, and nothing
else. Case, punctuation and wording must match exactly.

The 80-character floor keeps a quote specific enough to score against; the 400-character
ceiling stops a "gold passage" from being most of a page.

## Running it

```sh
python3 eval/validate.py                 # eval/questions.json against corpus/text/
python3 eval/validate.py --questions <path> --corpus <dir>
python3 eval/test_validate.py            # the validator's own fixture tests
```

Exit codes: `0` everything passed, `1` at least one question failed, `2` the files could not
be read. `questions.json` itself arrives in M1; until then `validate.py` exits `2`.
