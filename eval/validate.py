#!/usr/bin/env python3
"""Validate eval/questions.json against the corpus.

Checks the schema described in eval/schema.md, and that every gold quote occurs
verbatim (whitespace-normalised) on the page it claims. Exits non-zero on any
failure, so it can gate a commit.

It also checks eval/equivalents.json when that file sits beside the question set
— Amendment 1, the equivalent answer passages (eval/DESIGN.md). Those quotes get
the same verbatim-on-the-page treatment, plus the three rules the amendment adds:
the page has to be one that is actually indexed, an equivalent may not restate the
question's own gold answer passage, and two equivalents for the same question may
not be two readings of the same sentence.

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
PASSAGE_KEYS = {"doc", "page", "role", "quote"}
# "answer": the passage that states the answer — the one retrieval is scored on.
# "bridge": a passage a reader needs on the way there.
ROLES = ("bridge", "answer")
QUOTE_MIN = 80
QUOTE_MAX = 400

# eval/equivalents.json — Amendment 1. Same quote rules, no `role`: every
# passage in it is an answer.
EQUIVALENTS_KEYS = {"amendment", "passages"}
EQUIVALENT_KEYS = {"doc", "page", "quote"}
AMENDMENT = 1

# eval/DESIGN.md: mco-mib-project-management pages 58–105 reprint the whole of
# mars-climate-orbiter-mib-phase-i and are not indexed. scripts/chunk.py skips
# them, so a quote from one of those pages is in no chunk and could never be
# retrieved; naming one as an equivalent would be a free miss.
NOT_INDEXED = {"mco-mib-project-management": frozenset(range(58, 106))}

# Two passages that share more than half of the shorter one are the same passage
# read twice, not two places the report states the answer.
OVERLAP_NUMERATOR = 1
OVERLAP_DENOMINATOR = 2

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


def check_quote(where: str, doc: object, page: object, quote: object, corpus: Corpus) -> list[str]:
    """doc/page/quote: the part a gold passage and an equivalent have in common."""
    errors: list[str] = []

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

    if passage["role"] not in ROLES:
        errors.append(f"{where}: 'role' must be one of {', '.join(ROLES)}")

    errors.extend(check_quote(where, passage["doc"], passage["page"], passage["quote"], corpus))
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

    roles = [p.get("role") for p in gold if isinstance(p, dict)]
    if "answer" not in roles:
        errors.append(f"{where}: needs at least one gold passage with role 'answer'")
    if kind == "multi-hop" and "bridge" not in roles:
        errors.append(f"{where}: multi-hop needs at least one 'bridge' passage")

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


def manifest_slugs(manifest_path: Path) -> set[str] | None:
    """The document slugs the corpus manifest lists, or None if it cannot be read."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    documents = manifest.get("documents") if isinstance(manifest, dict) else None
    if not isinstance(documents, list):
        return None
    return {d["slug"] for d in documents if isinstance(d, dict) and isinstance(d.get("slug"), str)}


def span_on_page(quote: str, corpus: Corpus, doc: str, page: int) -> tuple[int, int] | None:
    """Where the quote sits in the normalised page, as [start, end), or None if it
    is not on that page at all.

    Both quotes being compared are already known to be verbatim on their page, so
    an offset pair is an exact way to ask whether two of them are the same words —
    no fuzzy matching, and no second opinion about what "the same passage" means.
    """
    pages = corpus.pages(doc)
    if pages is None or page not in pages:
        return None
    normalised = normalise(quote)
    start = pages[page].find(normalised)
    if start < 0:
        return None
    return (start, start + len(normalised))


def overlaps(first: tuple[int, int], second: tuple[int, int]) -> bool:
    """True when the two spans share more than half of the shorter one."""
    shared = min(first[1], second[1]) - max(first[0], second[0])
    if shared <= 0:
        return False
    shortest = min(first[1] - first[0], second[1] - second[0])
    return shared * OVERLAP_DENOMINATOR > shortest * OVERLAP_NUMERATOR


def check_equivalent(
    where: str, passage: object, corpus: Corpus, slugs: set[str]
) -> list[str]:
    errors: list[str] = []
    if not isinstance(passage, dict):
        return [f"{where}: passage is not an object"]

    unknown = sorted(set(passage) - EQUIVALENT_KEYS)
    if unknown:
        errors.append(f"{where}: unknown key(s) {', '.join(unknown)}")
    for key in sorted(EQUIVALENT_KEYS - set(passage)):
        errors.append(f"{where}: missing key '{key}'")
    if errors:
        return errors

    doc, page = passage["doc"], passage["page"]
    errors.extend(check_quote(where, doc, page, passage["quote"], corpus))
    if errors:
        return errors

    if doc not in slugs:
        errors.append(f"{where}: '{doc}' is not a document in the corpus manifest")
    if page in NOT_INDEXED.get(doc, frozenset()):
        errors.append(f"{where}: {doc} page {page} is not indexed (eval/DESIGN.md)")
    return errors


def check_question_equivalents(
    qid: str, items: object, question: dict, corpus: Corpus, slugs: set[str]
) -> list[str]:
    where = f"equivalents '{qid}'"
    if not isinstance(items, list) or not items:
        return [f"{where}: must be a non-empty list of passages"]

    errors: list[str] = []
    spans: list[tuple[int, tuple[str, int], tuple[int, int]]] = []
    for i, passage in enumerate(items):
        found = check_equivalent(f"{where}[{i}]", passage, corpus, slugs)
        errors.extend(found)
        if found:
            continue
        place = (passage["doc"], passage["page"])
        spans.append((i, place, span_on_page(passage["quote"], corpus, *place)))

    golds: list[tuple[tuple[str, int], tuple[int, int]]] = []
    for passage in question["gold"]:
        if not isinstance(passage, dict) or passage.get("role") != "answer":
            continue
        doc, page, quote = passage.get("doc"), passage.get("page"), passage.get("quote")
        if not (isinstance(doc, str) and isinstance(page, int) and isinstance(quote, str)):
            continue
        span = span_on_page(quote, corpus, doc, page)
        if span is not None:
            golds.append(((doc, page), span))

    for i, place, span in spans:
        for gold_place, gold_span in golds:
            if gold_place == place and overlaps(span, gold_span):
                errors.append(
                    f"{where}[{i}]: restates the question's own gold answer passage "
                    f"on {place[0]} page {place[1]}"
                )

    for a in range(len(spans)):
        for b in range(a + 1, len(spans)):
            if spans[a][1] == spans[b][1] and overlaps(spans[a][2], spans[b][2]):
                errors.append(
                    f"{where}[{spans[b][0]}]: overlaps equivalent[{spans[a][0]}] on "
                    f"{spans[a][1][0]} page {spans[a][1][1]} by more than half"
                )

    return errors


def validate_equivalents(
    equivalents: object, questions: list, corpus: Corpus, slugs: set[str]
) -> tuple[list[str], int]:
    """Amendment 1's file, against the questions it amends. Returns (failures, passages)."""
    if not isinstance(equivalents, dict):
        return (["equivalents.json must be a JSON object"], 0)

    errors: list[str] = []
    unknown = sorted(set(equivalents) - EQUIVALENTS_KEYS)
    if unknown:
        errors.append(f"equivalents.json: unknown key(s) {', '.join(unknown)}")
    if equivalents.get("amendment") != AMENDMENT:
        errors.append(f"equivalents.json: 'amendment' must be {AMENDMENT}")

    passages = equivalents.get("passages")
    if not isinstance(passages, dict):
        errors.append("equivalents.json: 'passages' must be an object keyed by question id")
        return (errors, 0)

    by_id = {
        question["id"]: question
        for question in questions
        if isinstance(question, dict) and isinstance(question.get("id"), str)
    }

    count = 0
    for qid, items in passages.items():
        question = by_id.get(qid)
        if question is None:
            errors.append(f"equivalents '{qid}': no question has that id")
            continue
        count += len(items) if isinstance(items, list) else 0
        errors.extend(check_question_equivalents(qid, items, question, corpus, slugs))

    return (errors, count)


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


def validate_both(
    questions_path: Path, corpus_dir: Path, equivalents_path: Path, manifest_path: Path
) -> tuple[list[str], int, int | None]:
    """Questions, then the amendment beside them. The amendment is optional: a
    checkout without one is the frozen set as it was, and that still validates."""
    errors, count = validate(questions_path, corpus_dir)
    if not equivalents_path.exists():
        return (errors, count, None)
    if errors:
        # Every equivalent check is stated against a question and its gold answer.
        # Checking them against a set that does not hold up would only produce
        # errors about the errors.
        return (errors, count, None)

    slugs = manifest_slugs(manifest_path)
    if slugs is None:
        return (errors + [f"could not read the corpus manifest at {manifest_path}"], count, None)

    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    equivalents = json.loads(equivalents_path.read_text(encoding="utf-8"))
    more, passages = validate_equivalents(equivalents, questions, Corpus(corpus_dir), slugs)
    return (errors + more, count, passages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--questions", type=Path, default=REPO_ROOT / "eval" / "questions.json")
    parser.add_argument("--corpus", type=Path, default=REPO_ROOT / "corpus" / "text")
    parser.add_argument(
        "--equivalents",
        type=Path,
        default=None,
        help="default: equivalents.json beside the question set, if there is one",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="default: MANIFEST.json beside the corpus text",
    )
    args = parser.parse_args(argv)

    # The amendment travels with the set it amends, and the manifest with the text.
    equivalents = args.equivalents or args.questions.parent / "equivalents.json"
    manifest = args.manifest or args.corpus.parent / "MANIFEST.json"

    try:
        errors, count, passages = validate_both(args.questions, args.corpus, equivalents, manifest)
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
    if passages is not None:
        print(f"OK {passages} equivalent answer passage(s) in {equivalents} (amendment {AMENDMENT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
