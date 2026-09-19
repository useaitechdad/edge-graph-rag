#!/usr/bin/env python3
"""Validate eval/questions.json against the corpus.

Checks the schema described in eval/schema.md, and that every gold quote occurs
verbatim (whitespace-normalised) on the page it claims. Exits non-zero on any
failure, so it can gate a commit.

Standard library only, on purpose: the eval set has to stay checkable by anyone
who clones the repo, with no install step.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

KINDS = ("single-hop", "multi-hop")
QUESTION_KEYS = {"id", "kind", "question", "answer", "gold", "hops"}
PASSAGE_KEYS = {"doc", "page", "quote"}
QUOTE_MIN = 80
QUOTE_MAX = 400

# Page breaks look like: \f--- page 12 ---
PAGE_BREAK = re.compile(r"^\f--- page (\d+) ---$", re.MULTILINE)


def normalise(text: str) -> str:
    """Collapse every run of whitespace to one space, so PDF line wrapping does
    not decide whether a quote matches."""
    return " ".join(text.split())


def split_pages(text: str) -> dict[int, str]:
    """Map page number -> that page's text, from the \\f--- page N --- markers."""
    pages: dict[int, str] = {}
    matches = list(PAGE_BREAK.finditer(text))
    for i, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        pages[number] = text[match.end():end]
    return pages


class Corpus:
    """Lazily loads corpus/text/<slug>.txt and keeps the split pages around."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._cache: dict[str, dict[int, str] | None] = {}

    def pages(self, slug: str) -> dict[int, str] | None:
        if slug not in self._cache:
            path = self.directory / f"{slug}.txt"
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                self._cache[slug] = None
            else:
                self._cache[slug] = {n: normalise(t) for n, t in split_pages(text).items()}
        return self._cache[slug]


def check_passage(where: str, passage: object, corpus: Corpus) -> list[str]:
    errors: list[str] = []
    if not isinstance(passage, dict):
        return [f"{where}: passage is not an object"]

    unknown = sorted(set(passage) - PASSAGE_KEYS)
    if unknown:
        errors.append(f"{where}: unknown key(s) {', '.join(unknown)}")
    for key in sorted(PASSAGE_KEYS - set(passage)):
        errors.append(f"{where}: missing key '{key}'")
    if errors:
        return errors

    doc, page, quote = passage["doc"], passage["page"], passage["quote"]

    if not isinstance(doc, str) or not doc.strip():
        errors.append(f"{where}: 'doc' must be a non-empty corpus slug")
    # bool is an int in Python; a page of `true` is a mistake, not a page.
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        errors.append(f"{where}: 'page' must be an integer >= 1")
    if not isinstance(quote, str):
        errors.append(f"{where}: 'quote' must be a string")
    if errors:
        return errors

    normalised = normalise(quote)
    if not QUOTE_MIN <= len(normalised) <= QUOTE_MAX:
        errors.append(
            f"{where}: quote is {len(normalised)} characters, "
            f"outside {QUOTE_MIN}–{QUOTE_MAX} (normalised)"
        )
        return errors

    pages = corpus.pages(doc)
    if pages is None:
        errors.append(f"{where}: no corpus file for '{doc}' in {corpus.directory}")
        return errors
    if page not in pages:
        errors.append(f"{where}: '{doc}' has no page {page}")
        return errors
    if normalised not in pages[page]:
        errors.append(f"{where}: quote is not verbatim on {doc} page {page}")

    return errors


def check_question(index: int, question: object, corpus: Corpus, seen_ids: set[str]) -> list[str]:
    where = f"question[{index}]"
    if not isinstance(question, dict):
        return [f"{where}: not an object"]

    errors: list[str] = []
    unknown = sorted(set(question) - QUESTION_KEYS)
    if unknown:
        errors.append(f"{where}: unknown key(s) {', '.join(unknown)}")
    for key in sorted({"id", "kind", "question", "answer", "gold"} - set(question)):
        errors.append(f"{where}: missing key '{key}'")
    if errors:
        return errors

    qid = question["id"]
    if not isinstance(qid, str) or not qid.strip():
        errors.append(f"{where}: 'id' must be a non-empty string")
    else:
        where = f"question '{qid}'"
        if qid in seen_ids:
            errors.append(f"{where}: duplicate id")
        seen_ids.add(qid)

    kind = question["kind"]
    if kind not in KINDS:
        errors.append(f"{where}: 'kind' must be one of {', '.join(KINDS)}")

    for key in ("question", "answer"):
        value = question[key]
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{where}: '{key}' must be a non-empty string")

    hops = question.get("hops")
    if kind == "multi-hop" and (not isinstance(hops, str) or not hops.strip()):
        errors.append(f"{where}: multi-hop needs a 'hops' note naming the linking entity")
    if hops is not None and not isinstance(hops, str):
        errors.append(f"{where}: 'hops' must be a string")

    gold = question["gold"]
    if not isinstance(gold, list) or not gold:
        errors.append(f"{where}: 'gold' must be a non-empty list of passages")
        return errors

    for i, passage in enumerate(gold):
        errors.extend(check_passage(f"{where} gold[{i}]", passage, corpus))

    if kind == "multi-hop":
        if len(gold) < 2:
            errors.append(f"{where}: multi-hop needs at least 2 gold passages")
        locations = {
            (p.get("doc"), p.get("page"))
            for p in gold
            if isinstance(p, dict)
        }
        if len(locations) < 2:
            errors.append(f"{where}: multi-hop needs gold passages on at least 2 different pages")

    return errors


def validate(questions_path: Path, corpus_dir: Path) -> tuple[list[str], int]:
    """Return (failures, questions checked). No failures means the set is good."""
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    if not isinstance(questions, list):
        return (["questions.json must be a JSON array of questions"], 0)
    if not questions:
        return (["questions.json is empty"], 0)

    corpus = Corpus(corpus_dir)
    seen_ids: set[str] = set()
    errors: list[str] = []
    for index, question in enumerate(questions):
        errors.extend(check_question(index, question, corpus, seen_ids))
    return (errors, len(questions))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--questions", type=Path, default=REPO_ROOT / "eval" / "questions.json")
    parser.add_argument("--corpus", type=Path, default=REPO_ROOT / "corpus" / "text")
    args = parser.parse_args(argv)

    try:
        errors, count = validate(args.questions, args.corpus)
    except OSError as exc:
        print(f"could not read {args.questions}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"{args.questions} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        print(f"\n{len(errors)} problem(s) in {args.questions}", file=sys.stderr)
        return 1

    print(f"OK {count} question(s) in {args.questions}, every gold quote verbatim")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
