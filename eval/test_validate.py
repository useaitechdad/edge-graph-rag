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

# A three-page document, wrapped the way a PDF text extract wraps it. Page 3 says
# the same thing page 1 does, twice, which is what the equivalents file is for.
DOC = (
    "\f--- page 1 ---\n"
    "The spacecraft navigation team supplied thrust values in pound-seconds\n"
    "while the ground software expected newton-seconds, and nobody reconciled\n"
    "the two before the orbit insertion burn.\n"
    "\f--- page 2 ---\n"
    "The review board found the same contractor had built the earlier lander,\n"
    "whose touchdown sensor was triggered by leg deployment rather than by the\n"
    "surface, ending the descent early.\n"
    "\f--- page 3 ---\n"
    "The appendix repeats that the navigation team reported thrust in\n"
    "pound-seconds where the specification called for newton-seconds.\n"
    "\n"
    "A later paragraph on the same page states once more that the thrust\n"
    "figures reached the ground software in the wrong units entirely.\n"
)

# The reprint pages of this document are not indexed, so nothing may be quoted
# from them — see eval/DESIGN.md and scripts/chunk.py.
REPRINT = (
    "\f--- page 57 ---\n"
    "This page is part of the report proper and is indexed like any other page\n"
    "in it, which is what makes page 60 below the interesting case.\n"
    "\f--- page 60 ---\n"
    "The reprint restates that the navigation team supplied thrust values in\n"
    "pound-seconds while the ground software expected newton-seconds.\n"
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
# Page 3's two paragraphs: each says what page 1 says, and they do not overlap.
QUOTE_P3A = (
    "The appendix repeats that the navigation team reported thrust in pound-seconds "
    "where the specification called for newton-seconds."
)
QUOTE_P3B = (
    "A later paragraph on the same page states once more that the thrust figures "
    "reached the ground software in the wrong units entirely."
)
# Starts inside QUOTE_P3A and runs past it: the same passage read twice.
QUOTE_P3_OVERLAPPING = (
    "the navigation team reported thrust in pound-seconds where the specification "
    "called for newton-seconds. A later paragraph on the same page states once more"
)
QUOTE_REPRINT = (
    "The reprint restates that the navigation team supplied thrust values in "
    "pound-seconds while the ground software expected newton-seconds."
)
REPRINT_SLUG = "mco-mib-project-management"


def single_hop(**overrides: object) -> dict:
    question = {
        "id": "sh-1",
        "kind": "single-hop",
        "question": "What units did the navigation team supply?",
        "answer": "Pound-seconds.",
        "gold": [{"doc": "report", "role": "answer", "page": 1, "quote": QUOTE_P1}],
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
            {"doc": "report", "role": "bridge", "page": 1, "quote": QUOTE_P1},
            {"doc": "report", "role": "answer", "page": 2, "quote": QUOTE_P2},
        ],
    }
    question.update(overrides)
    return question


def equivalent(**overrides: object) -> dict:
    passage = {"doc": "report", "page": 3, "quote": QUOTE_P3A}
    passage.update(overrides)
    return passage


class Fixture(unittest.TestCase):
    """The corpus both suites check against: a report, and the document whose
    reprint pages are not indexed."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.corpus = root / "text"
        self.corpus.mkdir()
        (self.corpus / "report.txt").write_text(DOC, encoding="utf-8")
        (self.corpus / f"{REPRINT_SLUG}.txt").write_text(REPRINT, encoding="utf-8")
        (root / "MANIFEST.json").write_text(
            json.dumps({"documents": [{"slug": "report"}, {"slug": REPRINT_SLUG}]}),
            encoding="utf-8",
        )
        self.manifest_path = root / "MANIFEST.json"
        self.questions_path = root / "questions.json"
        self.equivalents_path = root / "equivalents.json"
        self.addCleanup(self.tmp.cleanup)


class ValidatorTest(Fixture):
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
        wrong = single_hop(gold=[{"doc": "report", "role": "answer", "page": 2, "quote": QUOTE_P1}])
        self.assert_fails_with([wrong], "not verbatim")

    def test_quote_altered_by_a_word(self) -> None:
        altered = QUOTE_P1.replace("pound-seconds", "kilogram-seconds")
        self.assert_fails_with(
            [single_hop(gold=[{"doc": "report", "role": "answer", "page": 1, "quote": altered}])],
            "not verbatim",
        )

    def test_quote_too_short(self) -> None:
        self.assert_fails_with(
            [single_hop(gold=[{"doc": "report", "role": "answer", "page": 1, "quote": "The spacecraft"}])],
            "outside",
        )

    def test_quote_too_long(self) -> None:
        self.assert_fails_with(
            [single_hop(gold=[{"doc": "report", "role": "answer", "page": 1, "quote": "x " * 300}])],
            "outside",
        )

    def test_missing_page(self) -> None:
        self.assert_fails_with(
            [single_hop(gold=[{"doc": "report", "role": "answer", "page": 9, "quote": QUOTE_P1}])],
            "no page 9",
        )

    def test_missing_document(self) -> None:
        self.assert_fails_with(
            [single_hop(gold=[{"doc": "nope", "role": "answer", "page": 1, "quote": QUOTE_P1}])],
            "no corpus file",
        )

    def test_multi_hop_needs_two_passages(self) -> None:
        self.assert_fails_with(
            [multi_hop(gold=[{"doc": "report", "role": "answer", "page": 1, "quote": QUOTE_P1}])],
            "at least 2 gold passages",
        )

    def test_multi_hop_needs_two_pages(self) -> None:
        both_on_page_one = multi_hop(
            gold=[
                {"doc": "report", "role": "bridge", "page": 1, "quote": QUOTE_P1},
                {"doc": "report", "role": "answer", "page": 1, "quote": QUOTE_P1},
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

    def test_every_question_needs_an_answer_passage(self):
        only_bridges = multi_hop()
        for passage in only_bridges["gold"]:
            passage["role"] = "bridge"
        self.assert_fails_with([only_bridges], "role 'answer'")

    def test_bad_role(self):
        wrong = single_hop(gold=[{"doc": "report", "role": "proof", "page": 1, "quote": QUOTE_P1}])
        self.assert_fails_with([wrong], "'role' must be one of")


class EquivalentsTest(Fixture):
    """Amendment 1: eval/equivalents.json, checked against the set it amends."""

    def run_on(self, passages: dict, questions: list | None = None, amendment: int = 1) -> list[str]:
        questions = [single_hop(), multi_hop()] if questions is None else questions
        self.questions_path.write_text(json.dumps(questions), encoding="utf-8")
        self.equivalents_path.write_text(
            json.dumps({"amendment": amendment, "passages": passages}), encoding="utf-8"
        )
        errors, _, _ = validate.validate_both(
            self.questions_path, self.corpus, self.equivalents_path, self.manifest_path
        )
        return errors

    def assert_fails_with(self, passages: dict, fragment: str, **kwargs: object) -> None:
        errors = self.run_on(passages, **kwargs)
        self.assertTrue(errors, f"expected a failure mentioning {fragment!r}, got none")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected {fragment!r} in {errors}",
        )

    def test_a_good_amendment_passes(self) -> None:
        self.assertEqual(self.run_on({"sh-1": [equivalent()]}), [])

    def test_two_equivalents_on_one_page_are_fine_when_they_are_different_passages(self) -> None:
        self.assertEqual(
            self.run_on({"sh-1": [equivalent(), equivalent(quote=QUOTE_P3B)]}), []
        )

    def test_no_file_is_not_a_failure(self) -> None:
        # A checkout without the amendment is the frozen set as it was.
        self.questions_path.write_text(json.dumps([single_hop()]), encoding="utf-8")
        errors, count, passages = validate.validate_both(
            self.questions_path, self.corpus, self.equivalents_path, self.manifest_path
        )
        self.assertEqual((errors, count, passages), ([], 1, None))

    def test_counts_the_passages_it_checked(self) -> None:
        self.questions_path.write_text(json.dumps([single_hop()]), encoding="utf-8")
        self.equivalents_path.write_text(
            json.dumps({"amendment": 1, "passages": {"sh-1": [equivalent(), equivalent(quote=QUOTE_P3B)]}}),
            encoding="utf-8",
        )
        _, _, passages = validate.validate_both(
            self.questions_path, self.corpus, self.equivalents_path, self.manifest_path
        )
        self.assertEqual(passages, 2)

    def test_quote_not_on_the_page(self) -> None:
        self.assert_fails_with({"sh-1": [equivalent(page=2)]}, "not verbatim")

    def test_quote_too_short(self) -> None:
        self.assert_fails_with({"sh-1": [equivalent(quote="The appendix repeats")]}, "outside")

    def test_unknown_question_id(self) -> None:
        self.assert_fails_with({"nope": [equivalent()]}, "no question has that id")

    def test_document_not_in_the_manifest(self) -> None:
        # On disk, but not one of the seven the corpus manifest lists.
        (self.corpus / "stray.txt").write_text(DOC, encoding="utf-8")
        self.assert_fails_with(
            {"sh-1": [equivalent(doc="stray")]}, "not a document in the corpus manifest"
        )

    def test_page_that_is_not_indexed(self) -> None:
        self.assert_fails_with(
            {"sh-1": [{"doc": REPRINT_SLUG, "page": 60, "quote": QUOTE_REPRINT}]},
            "is not indexed",
        )

    def test_an_indexed_page_of_the_same_document_is_fine(self) -> None:
        self.assertEqual(
            self.run_on(
                {
                    "sh-1": [
                        {
                            "doc": REPRINT_SLUG,
                            "page": 57,
                            "quote": "This page is part of the report proper and is indexed "
                            "like any other page in it, which is what makes page 60 below "
                            "the interesting case.",
                        }
                    ]
                }
            ),
            [],
        )

    def test_restating_the_questions_own_gold_answer(self) -> None:
        self.assert_fails_with(
            {"sh-1": [equivalent(page=1, quote=QUOTE_P1)]}, "restates the question's own gold"
        )

    def test_two_equivalents_that_are_the_same_passage_twice(self) -> None:
        self.assert_fails_with(
            {"sh-1": [equivalent(), equivalent(quote=QUOTE_P3_OVERLAPPING)]},
            "by more than half",
        )

    def test_unknown_key_is_a_typo_not_a_feature(self) -> None:
        # 'role' is a gold passage's key; every equivalent is an answer.
        self.assert_fails_with({"sh-1": [equivalent(role="answer")]}, "unknown key")

    def test_missing_key(self) -> None:
        passage = equivalent()
        del passage["page"]
        self.assert_fails_with({"sh-1": [passage]}, "missing key 'page'")

    def test_empty_list_for_a_question(self) -> None:
        self.assert_fails_with({"sh-1": []}, "non-empty list")

    def test_wrong_amendment_number(self) -> None:
        self.assert_fails_with({"sh-1": [equivalent()]}, "'amendment' must be 1", amendment=2)

    def test_a_multi_hop_equivalent_is_checked_the_same_way(self) -> None:
        self.assertEqual(self.run_on({"mh-1": [equivalent()]}), [])

    def test_exit_codes(self) -> None:
        args = [
            "--corpus",
            str(self.corpus),
            "--questions",
            str(self.questions_path),
        ]

        def exit_code() -> int:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return validate.main(args)

        # main() finds equivalents.json beside the question set on its own.
        self.questions_path.write_text(json.dumps([single_hop()]), encoding="utf-8")
        self.equivalents_path.write_text(
            json.dumps({"amendment": 1, "passages": {"sh-1": [equivalent()]}}), encoding="utf-8"
        )
        self.assertEqual(exit_code(), 0)

        self.equivalents_path.write_text(
            json.dumps({"amendment": 1, "passages": {"sh-1": [equivalent(page=2)]}}),
            encoding="utf-8",
        )
        self.assertEqual(exit_code(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
