#!/usr/bin/env python3
"""Run the frozen eval set against a running Worker and score the answer passages.

Asks `/search` for k = 10 on every question in `eval/questions.json`, then scores
answer-passage recall twice off that one ranked list: at 10, and at 5 using its
first five hits. `eval/DESIGN.md` is the authority for both the metric and the
three groups it is reported in, and `eval/scoring.py` is the only implementation
of "contains" — the same one `eval/coverage.py` uses, so a passage cannot be
reachable by one definition and missing by another.

Every question is scored twice more: **strict**, the rule as frozen, where only
the question's own gold answer passage counts, and **amended**, where any passage
in `eval/equivalents.json` counts too (Amendment 1 in eval/DESIGN.md — the reports
state the same fact in more than one place). Both numbers go in the receipt, and
each question records whether its hit landed on the `gold` passage or on an
`equivalent`. Both retrievers are scored the same way.

Bridge passages are recorded in the receipt and not scored. That is DESIGN.md's
rule: they say what path a reader needs, not what retrieval is judged on.

Needs a Worker to talk to — `./scripts/dev.sh` in another terminal, or
`--base-url` pointing somewhere else. Nothing here reaches Cloudflare directly.

Standard library only.

Usage:
    ./scripts/eval.sh
    ./scripts/eval.sh --base-url http://127.0.0.1:8787 --k 10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scoring  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = REPO_ROOT / "eval" / "questions.json"
GROUPS = REPO_ROOT / "eval" / "groups.json"
RUNS = REPO_ROOT / "runs"

BASE_URL = "http://127.0.0.1:8787"
PRIMARY_K = 10
# The secondary cut. Not a second request: the same ranking, read shorter.
SECONDARY_K = 5
GROUP_ORDER = ("single", "multi-specific", "multi-generic")
TIMEOUT = 120


class EvalError(Exception):
    pass


def load_groups(questions: list[dict]) -> dict[str, str]:
    groups = json.loads(GROUPS.read_text(encoding="utf-8"))["groups"]
    for question in questions:
        label = groups.get(question["id"])
        if label not in GROUP_ORDER:
            raise EvalError(f"{question['id']}: no group in {GROUPS.name}")
        # questions.json owns which questions are the control; groups.json only
        # splits the multi-hops. If they disagree, one of them is stale.
        single = question["kind"] == "single-hop"
        if single != (label == "single"):
            raise EvalError(
                f"{question['id']}: kind '{question['kind']}' and group '{label}' disagree"
            )
    return groups


def search(base_url: str, query: str, k: int) -> dict:
    url = f"{base_url.rstrip('/')}/search?" + urllib.parse.urlencode({"q": query, "k": k})
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise EvalError(f"/search returned HTTP {exc.code}: {exc.read().decode()[:300]}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise EvalError(
            f"could not reach {base_url} ({exc}). Start it with ./scripts/dev.sh."
        ) from None


def score_question(question: dict, equivalents: list[dict], hits: list[dict], k: int) -> dict:
    """Answer-passage recall for one question, off one ranked list.

    Scored twice from the same hits. `strict_rank` is the rule as frozen: the
    question's own gold answer passage and nothing else. `rank` is the amended
    rule: that passage or any equivalent, whichever a chunk reached first. When
    both land, the gold passage takes the credit — an equivalent is only ever
    reported as the hit when it is the only thing that was found, or found first.
    """
    texts = [hit["text"] for hit in hits]
    golds = [gold["quote"] for gold in question["gold"] if gold["role"] == "answer"]
    others = [passage["quote"] for passage in equivalents]

    strict_rank = scoring.best_rank(golds, texts)
    equivalent_rank = scoring.best_rank(others, texts)
    ranks = [rank for rank in (strict_rank, equivalent_rank) if rank is not None]
    rank = min(ranks) if ranks else None

    if strict_rank is not None and (equivalent_rank is None or strict_rank <= equivalent_rank):
        hit_on = "gold"
    elif equivalent_rank is not None:
        hit_on = "equivalent"
    else:
        hit_on = None

    return {
        "id": question["id"],
        "kind": question["kind"],
        "rank": rank,
        "hit_on": hit_on,
        "equivalents": len(others),
        "found_at_5": rank is not None and rank <= SECONDARY_K,
        "found_at_10": rank is not None and rank <= k,
        "strict_rank": strict_rank,
        "strict_found_at_5": strict_rank is not None and strict_rank <= SECONDARY_K,
        "strict_found_at_10": strict_rank is not None and strict_rank <= k,
        "equivalent_rank": equivalent_rank,
        "hits_returned": len(hits),
        "documents_returned": sorted({hit["document"] for hit in hits}),
        # The ranked list itself, so a receipt can be re-scored later without
        # another run: chunk ids plus corpus/chunks.jsonl is the text back.
        "chunk_ids": [hit.get("chunk_id") for hit in hits],
        # Reported, never scored — DESIGN.md.
        "bridge_ranks": {
            f"{gold['doc']}:{gold['page']}": scoring.rank_of_first(gold["quote"], texts)
            for gold in question["gold"]
            if gold["role"] == "bridge"
        },
    }


def tally(results: list[dict], groups: dict[str, str]) -> dict:
    """Both numbers, per group: strict is the rule as frozen, amended counts the
    equivalent answer passages as well."""
    rules = {"strict": "strict_found_at_", "amended": "found_at_"}
    totals: dict[str, dict[str, dict[str, int]]] = {
        rule: {
            name: {"questions": 0, "found_at_5": 0, "found_at_10": 0}
            for name in (*GROUP_ORDER, "overall")
        }
        for rule in rules
    }
    for result in results:
        for rule, prefix in rules.items():
            for name in (groups[result["id"]], "overall"):
                totals[rule][name]["questions"] += 1
                totals[rule][name]["found_at_5"] += int(result[f"{prefix}5"])
                totals[rule][name]["found_at_10"] += int(result[f"{prefix}10"])
    return totals


def print_table(results: list[dict], groups: dict[str, str], totals: dict) -> None:
    print(f"{'id':<5} {'group':<15} {'@5':>4} {'@10':>4} {'rank':>5} {'hit':>11}")
    print("-" * 49)
    for result in results:
        rank = "-" if result["rank"] is None else str(result["rank"])
        print(
            f"{result['id']:<5} {groups[result['id']]:<15} "
            f"{'yes' if result['found_at_5'] else 'no':>4} "
            f"{'yes' if result['found_at_10'] else 'no':>4} {rank:>5} "
            f"{result['hit_on'] or '-':>11}"
        )
    print("-" * 49)
    print(f"{'':<5} {'group':<15} {'strict @5':>11} {'@10':>5} {'amended @5':>12} {'@10':>5}")
    for name in (*GROUP_ORDER, "overall"):
        strict, amended = totals["strict"][name], totals["amended"][name]
        if not strict["questions"]:
            continue
        cells = (
            f"{strict['found_at_5']}/{strict['questions']}",
            f"{strict['found_at_10']}/{strict['questions']}",
            f"{amended['found_at_5']}/{amended['questions']}",
            f"{amended['found_at_10']}/{amended['questions']}",
        )
        print(f"{'':<5} {name:<15} {cells[0]:>11} {cells[1]:>5} {cells[2]:>12} {cells[3]:>5}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=BASE_URL, help=f"default {BASE_URL}")
    parser.add_argument("--k", type=int, default=PRIMARY_K, help=f"default {PRIMARY_K}")
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    parser.add_argument("--equivalents", type=Path, default=scoring.EQUIVALENTS)
    args = parser.parse_args(argv)

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    clock = time.monotonic()

    try:
        questions = json.loads(args.questions.read_text(encoding="utf-8"))
        groups = load_groups(questions)
        equivalents = scoring.load_equivalents(args.equivalents)

        print(f"{len(questions)} questions against {args.base_url}/search at k={args.k}")
        if equivalents:
            passages = sum(len(v) for v in equivalents.values())
            print(
                f"amendment {scoring.AMENDMENT}: {passages} equivalent answer passage(s) "
                f"across {len(equivalents)} question(s)"
            )
        results: list[dict] = []
        retriever = "unknown"
        for question in questions:
            response = search(args.base_url, question["question"], args.k)
            retriever = response.get("retriever", retriever)
            results.append(
                score_question(
                    question,
                    equivalents.get(question["id"], []),
                    response.get("hits", []),
                    args.k,
                )
            )
    except (EvalError, OSError, ValueError) as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"\nFAILED: {args.questions} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    totals = tally(results, groups)
    print()
    print_table(results, groups, totals)

    receipt = {
        "milestone": "M2",
        "retriever": retriever,
        "base_url": args.base_url,
        "k": args.k,
        "secondary_k": SECONDARY_K,
        "started_utc": started,
        "wall_seconds": round(time.monotonic() - clock, 1),
        "metric": "answer-passage recall at k; a chunk contains a passage when it "
        "holds at least half of the quote's characters, whitespace-normalised and "
        "contiguous (eval/DESIGN.md, implemented in eval/scoring.py)",
        "amendment": scoring.AMENDMENT,
        "scoring_rules": {
            "strict": "the rule as frozen: only the question's own gold answer passage counts",
            "amended": "the gold answer passage or any passage in eval/equivalents.json",
        },
        "equivalents_used": sum(len(passages) for passages in equivalents.values()),
        "totals": totals,
        "questions": results,
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RUNS / f"{stamp}-eval-{retriever}.json"
    path.write_text(json.dumps(receipt, indent="\t", ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nReceipt: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
