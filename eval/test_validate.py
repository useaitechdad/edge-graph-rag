#!/usr/bin/env python3
"""Fixture tests for validate.py. No real corpus, no network, stdlib only.

Run: python3 eval/test_validate.py
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import validate  # noqa: E402

# A two-page document, wrapped the way a PDF text extract wraps it.
DOC = (
    "\f--- page 1 ---\n"
    "The spacecraft navigation team supplied thrust values in pound-seconds\n"
    "while the ground software expected newton-seconds, and nobody reconciled\n"
    "the two before the orbit insertion burn.\n"
    "\f--- page 2 ---\n"
    "The review board found the same contractor had built the earlier lander,\n"
    "whose touchdown sensor was triggered by leg deployment rather than by the\n"
    "surface, ending the descent early.\n"
)

# 80-400 characters once whitespace is normalised, and written as one line to
# prove the normalisation does its job.
QUOTE_P1 = (
    "The spacecraft navigation team supplied thrust values in pound-seconds while the "
    "ground software expected newton-seconds"
)
QUOTE_P2 = (
    "The review board found the same contractor had built the earlier lander, whose "
    "touchdown sensor was triggered by leg deployment"
)


def single_hop(**overrides: object) -> dict:
    question = {
        "id": "sh-1",
        "kind": "single-hop",
        "question": "What units did the navigation team supply?",
        "answer": "Pound-seconds.",
        "gold": [{"doc": "report", "page": 1, "quote": QUOTE_P1}],
    }
    question.update(overrides)
    return question


def multi_hop(**overrides: object) -> dict:
    question = {
        "id": "mh-1",
        "kind": "multi-hop",
        "question": "What else did the contractor behind the units error build?",
        "answer": "The earlier lander.",
        "hops": "the contractor, named on both pages",
        "gold": [
            {"doc": "report", "page": 1, "quote": QUOTE_P1},
            {"doc": "report", "page": 2, "quote": QUOTE_P2},
        ],
    }
    question.update(overrides)
    return question


class ValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.corpus = root / "text"
        self.corpus.mkdir()
        (self.corpus / "report.txt").write_text(DOC, encoding="utf-8")
        self.questions_path = root / "questions.json"
        self.addCleanup(self.tmp.cleanup)

    def run_on(self, questions: list) -> list[str]:
        self.questions_path.write_text(json.dumps(questions), encoding="utf-8")
        errors, _ = validate.validate(self.questions_path, self.corpus)
        return errors

    def assert_fails_with(self, questions: list, fragment: str) -> None:
        errors = self.run_on(questions)
        self.assertTrue(errors, f"expected a failure mentioning {fragment!r}, got none")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected {fragment!r} in {errors}",
        )

    def test_a_good_set_passes(self) -> None:
        self.assertEqual(self.run_on([single_hop(), multi_hop()]), [])

    def test_quote_matches_across_line_wrapping(self) -> None:
        # The quote is one line; the page is three. Normalisation bridges that.
        self.assertNotIn(QUOTE_P1, DOC)
        self.assertEqual(self.run_on([single_hop()]), [])

    def test_quote_not_on_the_page(self) -> None:
        wrong = single_hop(gold=[{"doc": "report", "page": 2, "quote": QUOTE_P1}])
        self.assert_fails_with([wrong], "not verbatim")

    def test_quote_altered_by_a_word(self) -> None:
        altered = QUOTE_P1.replace("pound-seconds", "kilogram-seconds")
        self.assert_fails_with(
            [single_hop(gold=[{"doc": "report", "page": 1, "quote": altered}])],
            "not verbatim",
        )

    def test_quote_too_short(self) -> None:
        self.assert_fails_with(
            [single_hop(gold=[{"doc": "report", "page": 1, "quote": "The spacecraft"}])],
            "outside",
        )

    def test_quote_too_long(self) -> None:
        self.assert_fails_with(
            [single_hop(gold=[{"doc": "report", "page": 1, "quote": "x " * 300}])],
            "outside",
        )

    def test_missing_page(self) -> None:
        self.assert_fails_with(
            [single_hop(gold=[{"doc": "report", "page": 9, "quote": QUOTE_P1}])],
            "no page 9",
        )

    def test_missing_document(self) -> None:
        self.assert_fails_with(
            [single_hop(gold=[{"doc": "nope", "page": 1, "quote": QUOTE_P1}])],
            "no corpus file",
        )

    def test_multi_hop_needs_two_passages(self) -> None:
        self.assert_fails_with(
            [multi_hop(gold=[{"doc": "report", "page": 1, "quote": QUOTE_P1}])],
            "at least 2 gold passages",
        )

    def test_multi_hop_needs_two_pages(self) -> None:
        both_on_page_one = multi_hop(
            gold=[
                {"doc": "report", "page": 1, "quote": QUOTE_P1},
                {"doc": "report", "page": 1, "quote": QUOTE_P1},
            ]
        )
        self.assert_fails_with([both_on_page_one], "2 different pages")

    def test_multi_hop_needs_hops_note(self) -> None:
        without_hops = multi_hop()
        del without_hops["hops"]
        self.assert_fails_with([without_hops], "'hops' note")

    def test_bad_kind(self) -> None:
        self.assert_fails_with([single_hop(kind="three-hop")], "'kind' must be one of")

    def test_duplicate_ids(self) -> None:
        self.assert_fails_with([single_hop(), single_hop()], "duplicate id")

    def test_unknown_key_is_a_typo_not_a_feature(self) -> None:
        self.assert_fails_with([single_hop(note="oops")], "unknown key")

    def test_missing_key(self) -> None:
        without_answer = single_hop()
        del without_answer["answer"]
        self.assert_fails_with([without_answer], "missing key 'answer'")

    def test_empty_file(self) -> None:
        self.assert_fails_with([], "empty")

    def test_exit_codes(self) -> None:
        args = ["--corpus", str(self.corpus), "--questions", str(self.questions_path)]

        def exit_code() -> int:
            # The CLI prints its verdict; this test only cares about the code.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return validate.main(args)

        self.questions_path.write_text(json.dumps([single_hop()]), encoding="utf-8")
        self.assertEqual(exit_code(), 0)

        self.questions_path.write_text(json.dumps([single_hop(kind="nope")]), encoding="utf-8")
        self.assertEqual(exit_code(), 1)

        self.questions_path.unlink()
        self.assertEqual(exit_code(), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
