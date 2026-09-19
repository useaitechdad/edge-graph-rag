#!/usr/bin/env python3
"""Fixture tests for scoring.py — the containment rule in eval/DESIGN.md.

No corpus, no chunks, no network, stdlib only. Everything here is a synthetic
passage, because the point is the rule and not the documents.

Run: python3 eval/test_scoring.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scoring  # noqa: E402

# 114 characters, deliberately even: half of it is 57 characters exactly, so the
# "at least half" boundary can be tested on the boundary rather than near it.
QUOTE = (
    "The ground software supplied impulse in pound-seconds while the navigation "
    "team expected newton-seconds throughout"
)
HALF = len(QUOTE) // 2

# Text that shares no long run with QUOTE, for padding a chunk out to a
# realistic size without accidentally lengthening the match.
FILLER = "Board members later reviewed every interface agreement in the project archive."


class Containment(unittest.TestCase):
    def test_the_fixture_is_the_length_the_tests_assume(self):
        self.assertEqual(len(QUOTE), 114)
        self.assertEqual(HALF, 57)
        # Neither side of the split lands on a space, so slicing does not change
        # length once normalised.
        self.assertNotIn(" ", QUOTE[HALF - 1 : HALF + 1])

    def test_whole_quote_is_contained(self):
        chunk = f"{FILLER} {QUOTE} {FILLER}"
        self.assertTrue(scoring.contains(QUOTE, chunk))
        self.assertEqual(scoring.held(QUOTE, chunk), 1.0)

    def test_exactly_half_counts_as_contained(self):
        chunk = f"{QUOTE[:HALF]} {FILLER}"
        self.assertEqual(scoring.longest_run(QUOTE, chunk), HALF)
        self.assertTrue(scoring.contains(QUOTE, chunk))

    def test_one_character_under_half_does_not(self):
        chunk = f"{QUOTE[: HALF - 1]} {FILLER}"
        self.assertEqual(scoring.longest_run(QUOTE, chunk), HALF - 1)
        self.assertFalse(scoring.contains(QUOTE, chunk))

    def test_the_half_has_to_be_contiguous(self):
        # Every word of the quote is present, and far more than half its
        # characters, but never more than a few in a row.
        scattered = " ".join(f"{word} ..." for word in QUOTE.split(" "))
        self.assertLess(scoring.longest_run(QUOTE, scattered), HALF)
        self.assertFalse(scoring.contains(QUOTE, scattered))

    def test_whitespace_is_normalised_on_both_sides(self):
        # A quote as it sits in questions.json, wrapped; a chunk as chunk.py
        # joins it, on one line. Same passage.
        wrapped = QUOTE.replace(" ", "\n   ", 4)
        chunk = f"{FILLER}\n\n{QUOTE}"
        self.assertTrue(scoring.contains(wrapped, chunk))
        self.assertEqual(scoring.held(wrapped, chunk), 1.0)

    def test_an_empty_quote_is_never_contained(self):
        self.assertFalse(scoring.contains("   ", FILLER))
        self.assertEqual(scoring.held("", FILLER), 0.0)


class SplitAcrossChunks(unittest.TestCase):
    """A quote straddling a chunk boundary — the case the 200-character overlap
    in scripts/chunk.py exists for."""

    def test_an_even_split_leaves_both_halves_scoring(self):
        first = f"{FILLER} {QUOTE[:HALF]}"
        second = f"{QUOTE[HALF:]} {FILLER}"
        self.assertTrue(scoring.contains(QUOTE, first))
        self.assertTrue(scoring.contains(QUOTE, second))

    def test_a_lopsided_split_leaves_only_the_longer_side(self):
        cut = 40
        first = f"{FILLER} {QUOTE[:cut]}"
        second = f"{QUOTE[cut:]} {FILLER}"
        self.assertFalse(scoring.contains(QUOTE, first))
        self.assertTrue(scoring.contains(QUOTE, second))
        # The cut lands on a space, which normalisation drops — so the kept
        # fraction is measured from the normalised prefix, not from the slice.
        kept = scoring.normalise(QUOTE[:cut])
        self.assertAlmostEqual(scoring.held(QUOTE, first), len(kept) / len(QUOTE))

    def test_neither_side_scores_when_the_quote_is_cut_out_of_both(self):
        # A boundary with no overlap at all, and a quote long enough that each
        # side keeps less than half: the failure the overlap is there to stop.
        first = f"{FILLER} {QUOTE[:30]}"
        second = f"{QUOTE[80:]} {FILLER}"
        self.assertFalse(scoring.contains(QUOTE, first))
        self.assertFalse(scoring.contains(QUOTE, second))


class Ranking(unittest.TestCase):
    def test_rank_is_one_based_and_takes_the_first_hit(self):
        hits = [FILLER, f"{QUOTE[:20]} {FILLER}", QUOTE, QUOTE]
        self.assertEqual(scoring.rank_of_first(QUOTE, hits), 3)

    def test_no_hit_is_none(self):
        self.assertIsNone(scoring.rank_of_first(QUOTE, [FILLER, FILLER]))

    def test_recall_at_k_reads_off_the_rank(self):
        hits = [FILLER] * 6 + [QUOTE]
        rank = scoring.rank_of_first(QUOTE, hits)
        self.assertEqual(rank, 7)
        self.assertFalse(rank <= 5)
        self.assertTrue(rank <= 10)


class EquivalentPassages(unittest.TestCase):
    """Amendment 1: reaching any passage that states the answer is reaching it."""

    OTHER = (
        "The appendix states the same finding in different words, reporting impulse "
        "in pound-seconds where newton-seconds were specified throughout"
    )

    def test_the_best_of_several_quotes_wins(self):
        hits = [FILLER, self.OTHER, QUOTE]
        self.assertEqual(scoring.best_rank([QUOTE, self.OTHER], hits), 2)

    def test_one_quote_behaves_like_rank_of_first(self):
        hits = [FILLER, QUOTE]
        self.assertEqual(scoring.best_rank([QUOTE], hits), scoring.rank_of_first(QUOTE, hits))

    def test_no_quote_reached_is_none(self):
        self.assertIsNone(scoring.best_rank([QUOTE, self.OTHER], [FILLER, FILLER]))

    def test_no_quotes_at_all_is_none(self):
        # A question with no equivalents scores strictly, and says so quietly.
        self.assertIsNone(scoring.best_rank([], [QUOTE]))

    def test_an_absent_amendment_file_is_not_an_error(self):
        self.assertEqual(scoring.load_equivalents(Path("no", "such", "file.json")), {})

    def test_the_repository_amendment_loads(self):
        loaded = scoring.load_equivalents()
        self.assertTrue(all(isinstance(quotes, list) for quotes in loaded.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
