#!/usr/bin/env python3
"""Can the chunks still reach every gold passage?

The eval set was frozen against `corpus/text/`, not against the chunks. If the
chunker drops a page, mangles a line join or cuts a quote so badly that no single
chunk holds half of it, recall is capped before retrieval has done anything — and
the run would look like a retrieval result instead of a bug.

So, for every gold passage in `eval/questions.json`, this asks whether at least
one chunk contains it under the rule in `eval/scoring.py`. A passage no chunk can
hold is a finding to report, not a reason to edit the questions.

Only chunks that actually cover the passage's page are considered: a chunk's text
comes from its own pages and nowhere else, so a chunk whose page span excludes
page N cannot hold a quote from page N. (That also keeps the Phase I reprint out
of it — the reprint pages are not indexed at all, see eval/DESIGN.md.)

Standard library only.

Usage:
    python3 eval/coverage.py
    python3 eval/coverage.py --chunks <path> --questions <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scoring  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = REPO_ROOT / "eval" / "questions.json"
CHUNKS = REPO_ROOT / "corpus" / "chunks.jsonl"


def load_chunks(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def candidates(chunks: list[dict], doc: str, page: int) -> list[dict]:
    return [
        chunk
        for chunk in chunks
        if chunk["document"] == doc and chunk["page_start"] <= page <= chunk["page_end"]
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    parser.add_argument("--chunks", type=Path, default=CHUNKS)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print the best chunk and fraction for every passage, not just the misses",
    )
    args = parser.parse_args(argv)

    try:
        questions = json.loads(args.questions.read_text(encoding="utf-8"))
        chunks = load_chunks(args.chunks)
    except OSError as exc:
        print(f"could not read: {exc}. Run python3 scripts/chunk.py first.", file=sys.stderr)
        return 2

    total = 0
    misses: list[str] = []
    print(f"{len(chunks)} chunks, {len(questions)} questions")
    print()

    for question in questions:
        for passage in question["gold"]:
            total += 1
            pool = candidates(chunks, passage["doc"], passage["page"])
            best = max(
                (scoring.held(passage["quote"], chunk["text"]), chunk["id"]) for chunk in pool
            ) if pool else (0.0, "-")
            fraction, chunk_id = best
            covered = any(scoring.contains(passage["quote"], chunk["text"]) for chunk in pool)
            line = (
                f"{'ok ' if covered else 'MISS'} {question['id']:<5} {passage['role']:<6} "
                f"{passage['doc']}:{passage['page']:<4} best {fraction:6.1%} in {chunk_id} "
                f"({len(pool)} chunk(s) on that page)"
            )
            if covered:
                if args.verbose:
                    print(line)
            else:
                print(line)
                misses.append(
                    f"{question['id']} ({passage['role']}) "
                    f"{passage['doc']} p{passage['page']}: best {fraction:.1%}"
                )

    covered_count = total - len(misses)
    print()
    print(f"{covered_count}/{total} gold passages are held by at least one chunk")
    if misses:
        print("\nNot reachable — a chunker bug or a finding, never a reason to edit a question:")
        for miss in misses:
            print(f"  - {miss}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
