#!/usr/bin/env python3
"""Ask the running Worker one question and print what it retrieved.

    ./scripts/ask.sh --id q11            a question from the frozen set
    ./scripts/ask.sh "free text..."      anything else

With --id, each hit is marked when it holds one of that question's gold
passages (bridge or answer) or an Amendment 1 equivalent, using the same
containment rule the eval scores with (eval/scoring.py).
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run import search  # noqa: E402
from scoring import contains, load_equivalents  # noqa: E402

QUESTIONS = Path(__file__).resolve().parent / "questions.json"
WIDTH = 100


def load_question(question_id: str) -> dict:
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    questions = data["questions"] if isinstance(data, dict) else data
    for question in questions:
        if question["id"] == question_id:
            return question
    raise SystemExit(f"no question {question_id} in eval/questions.json")


def label_for(text: str, gold: list[dict], equivalents: list[dict]) -> str:
    for passage in gold:
        if contains(passage["quote"], text):
            return passage["role"].upper()
    for passage in equivalents:
        if contains(passage["quote"], text):
            return "ANSWER (equivalent)"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text", nargs="*", help="a free-text question")
    parser.add_argument("--id", help="a question id from eval/questions.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument(
        "--retriever",
        choices=("graph", "vector"),
        default="graph",
        help="retriever arm: graph (default) or vector",
    )
    args = parser.parse_args(argv)

    gold: list[dict] = []
    equivalents: list[dict] = []
    if args.id:
        question = load_question(args.id)
        text = question["question"]
        gold = question["gold"]
        equivalents = load_equivalents().get(args.id, [])
    elif args.text:
        text = " ".join(args.text)
    else:
        parser.error("give a question, or --id")

    print(textwrap.fill(text, WIDTH))
    print()
    response = search(args.base_url, text, args.k, args.retriever)
    found_answer = False
    for rank, hit in enumerate(response["hits"], 1):
        label = label_for(hit["text"], gold, equivalents)
        found_answer = found_answer or label.startswith("ANSWER")
        pages = hit["page_start"] if hit["page_start"] == hit["page_end"] else f'{hit["page_start"]}-{hit["page_end"]}'
        print(f'{rank:>2}  {hit["score"]:.3f}  {hit["document"]}  p.{pages}  {label}')
        print(f'    {" ".join(hit["text"].split())[: WIDTH - 8]}')
    if args.id:
        print()
        verdict = "is" if found_answer else "is NOT"
        print(f'{response["retriever"]}: the answer passage {verdict} in the top {args.k}.')
    return 0


if __name__ == "__main__":
    sys.exit(main())
