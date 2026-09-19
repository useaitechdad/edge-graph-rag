#!/usr/bin/env python3
"""The one place a chunk is judged to hold a gold passage.

`eval/DESIGN.md` states the rule:

    A chunk *contains* a gold passage when it holds at least half of the quote's
    characters (whitespace-normalised, contiguous).

Both readers of that rule import it from here — `eval/coverage.py`, which asks
whether the chunker put every gold passage somewhere reachable, and `eval/run.py`,
which asks whether retrieval returned it. A second implementation would mean the
coverage number and the recall number could disagree about the same passage.

"Contiguous" is the load-bearing word. Half the quote's characters scattered
across a chunk is not the passage; half of it in one run is a reader's half. So
what gets measured is the longest run of the quote that occurs in the chunk, and
the threshold is on that run.

Whitespace normalisation is `validate.py`'s, imported rather than repeated: the
same collapse that lets a gold quote match a hard-wrapped PDF extract has to be
the one used here, or a quote could validate and then score as absent.

Standard library only.
"""

from __future__ import annotations

import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate import normalise  # noqa: E402

# eval/DESIGN.md: "at least half". Compared as an integer ratio below, so a quote
# of odd length has no rounding to argue about.
CONTAINMENT_NUMERATOR = 1
CONTAINMENT_DENOMINATOR = 2


def longest_run(quote: str, chunk: str) -> int:
    """Characters in the longest contiguous stretch of `quote` found in `chunk`,
    both whitespace-normalised."""
    wanted = normalise(quote)
    haystack = normalise(chunk)
    if not wanted or not haystack:
        return 0
    if wanted in haystack:
        return len(wanted)
    # autojunk would treat the space character as noise on any sequence this
    # long, which is precisely the character prose is made of.
    matcher = SequenceMatcher(None, wanted, haystack, autojunk=False)
    return matcher.find_longest_match(0, len(wanted), 0, len(haystack)).size


def held(quote: str, chunk: str) -> float:
    """The fraction of the quote the chunk holds contiguously, 0.0 to 1.0."""
    wanted = normalise(quote)
    if not wanted:
        return 0.0
    return longest_run(wanted, chunk) / len(wanted)


def contains(quote: str, chunk: str) -> bool:
    """eval/DESIGN.md's containment test."""
    wanted = normalise(quote)
    if not wanted:
        return False
    run = longest_run(wanted, chunk)
    return run * CONTAINMENT_DENOMINATOR >= len(wanted) * CONTAINMENT_NUMERATOR


def rank_of_first(quote: str, chunks: list[str]) -> int | None:
    """1-based position of the first chunk containing the quote, or None.

    `chunks` is a ranked list, so this is the rank a retriever earned. Recall at
    k is then `rank is not None and rank <= k` — one list, scored twice, which is
    what eval/DESIGN.md means by k = 5 being the first five of the same ranking.
    """
    for position, chunk in enumerate(chunks, start=1):
        if contains(quote, chunk):
            return position
    return None
