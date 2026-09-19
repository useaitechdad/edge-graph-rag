#!/usr/bin/env python3
"""Fixture tests for extract.py. No real corpus, no API key, no network: the one
function that touches urllib is replaced with a scripted fake.

Run: python3 scripts/test_extract.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract  # noqa: E402

# A string shaped like a credential, so the masking can be checked against
# something that would be a real leak.
KEY = "sk-ant-notarealkey-000111222333"

CHUNKS = [
    {
        "id": "demo-report:0000",
        "document": "demo-report",
        "page_start": 4,
        "page_end": 4,
        "ordinal": 0,
        "text": "The Board found that the navigation team used pound-seconds.",
    },
    {
        "id": "other-report:0007",
        "document": "other-report",
        "page_start": 11,
        "page_end": 12,
        "ordinal": 7,
        "text": "Deep Space Industries built the entry vehicle under contract.",
    },
]

MANIFEST = {
    "documents": [
        {"slug": "demo-report", "title": "Demo Mishap Investigation Board Report"},
        {"slug": "other-report", "title": "Other Independent Assessment Report"},
    ]
}


def entity(name: str, kind: str = "organisation", **overrides: object) -> dict:
    row = {"name": name, "type": kind, "description": f"{name} appears in this passage.", "aliases": []}
    row.update(overrides)
    return row


def relation(source: str, target: str, **overrides: object) -> dict:
    row = {"source": source, "target": target, "relation": "worked_with", "evidence": "stated in the passage"}
    row.update(overrides)
    return row


def reply(
    entities: list[dict] | None = None,
    relations: list[dict] | None = None,
    stop_reason: str = "tool_use",
    tool_call: bool = True,
) -> tuple[int, dict[str, str], bytes]:
    """A 200 from the Messages API, in the shape extract.py reads."""
    content = []
    if tool_call:
        content.append(
            {
                "type": "tool_use",
                "id": "toolu_0",
                "name": extract.TOOL_NAME,
                "input": {"entities": entities or [], "relations": relations or []},
            }
        )
    payload = {
        "id": "msg_0",
        "type": "message",
        "role": "assistant",
        "model": extract.MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": 1500,
            "output_tokens": 220,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 1400,
        },
    }
    return 200, {}, json.dumps(payload).encode("utf-8")


def error(status: int, body: str = "{}", headers: dict[str, str] | None = None) -> tuple[int, dict, bytes]:
    return status, headers or {}, body.encode("utf-8")


class Fake:
    """Scripted replies for extract.http_post, in order; the last one repeats."""

    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []

    def __call__(self, url: str, headers: dict, body: bytes, timeout: int) -> tuple:
        self.requests.append({"url": url, "headers": headers, "body": json.loads(body.decode("utf-8"))})
        answer = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


class Fixture(unittest.TestCase):
    """A repo-shaped temporary directory, an API key in the environment, and no
    real clock: every test drives extract.main() end to end."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "corpus").mkdir()
        (root / "runs" / "tmp").mkdir(parents=True)

        self.chunks = root / "corpus" / "chunks.jsonl"
        self.chunks.write_text(
            "".join(json.dumps(chunk) + "\n" for chunk in CHUNKS), encoding="utf-8"
        )
        self.manifest = root / "corpus" / "MANIFEST.json"
        self.manifest.write_text(json.dumps(MANIFEST), encoding="utf-8")
        self.extractions = root / "runs" / "tmp" / "extractions.jsonl"
        self.runs = root / "runs"

        for name, value in {
            "REPO_ROOT": root,
            "CHUNKS": self.chunks,
            "MANIFEST": self.manifest,
            "EXTRACTIONS": self.extractions,
            "RUNS": self.runs,
            "TMP": root / "runs" / "tmp",
        }.items():
            patcher = patch.object(extract, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        environment = patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY})
        environment.start()
        self.addCleanup(environment.stop)

        self.slept: list[float] = []
        clock = patch.object(time, "sleep", self.slept.append)
        clock.start()
        self.addCleanup(clock.stop)

    def run_extract(self, fake: Fake, *arguments: str) -> tuple[int, str]:
        """extract.main() with the fake in place. Returns (exit code, stdout)."""
        printed = io.StringIO()
        with patch.object(extract, "http_post", fake), contextlib.redirect_stdout(printed):
            code = extract.main(["--concurrency", "1", *arguments])
        return code, printed.getvalue()

    def records(self) -> list[dict]:
        text = self.extractions.read_text(encoding="utf-8") if self.extractions.exists() else ""
        return [json.loads(line) for line in text.splitlines() if line.strip()]


class Extraction(Fixture):
    def test_every_chunk_becomes_one_record(self) -> None:
        fake = Fake(reply([entity("Deep Space Industries")], [], stop_reason="tool_use"))
        code, _ = self.run_extract(fake)
        self.assertEqual(code, 0)
        records = self.records()
        self.assertEqual([record["chunk"] for record in records], [chunk["id"] for chunk in CHUNKS])
        self.assertEqual({record["status"] for record in records}, {"ok"})
        self.assertEqual(records[0]["extraction"]["entities"][0]["name"], "Deep Space Industries")

    def test_the_request_forces_the_tool_and_caches_the_prefix(self) -> None:
        fake = Fake(reply())
        self.run_extract(fake, "--limit", "1")
        sent = fake.requests[0]["body"]
        self.assertEqual(sent["model"], extract.MODEL)
        self.assertEqual(sent["tool_choice"], {"type": "tool", "name": extract.TOOL_NAME})
        self.assertEqual(sent["tools"][0]["name"], extract.TOOL_NAME)
        self.assertEqual(sent["thinking"], {"type": "disabled"})
        self.assertEqual(sent["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(fake.requests[0]["headers"]["anthropic-version"], extract.ANTHROPIC_VERSION)
        # The document title travels with the passage, so "the Board" can be resolved.
        self.assertIn("Demo Mishap Investigation Board Report", sent["messages"][0]["content"])
        self.assertIn(CHUNKS[0]["text"], sent["messages"][0]["content"])

    def test_a_truncated_answer_is_recorded_not_raised(self) -> None:
        fake = Fake(reply(stop_reason="max_tokens", tool_call=False))
        code, _ = self.run_extract(fake, "--limit", "1")
        self.assertEqual(code, 0)
        record = self.records()[0]
        self.assertEqual(record["status"], "truncated")
        self.assertEqual(record["stop_reason"], "max_tokens")
        self.assertEqual(record["extraction"], {"entities": [], "relations": []})

    def test_a_rate_limit_is_retried_and_then_succeeds(self) -> None:
        fake = Fake(
            error(429, '{"error": "slow down"}', {"retry-after": "3"}),
            reply([entity("Deep Space Industries")]),
        )
        code, _ = self.run_extract(fake, "--limit", "1")
        self.assertEqual(code, 0)
        self.assertEqual(self.records()[0]["status"], "ok")
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(self.slept, [3.0])

    def test_an_overload_is_retried_and_a_network_error_too(self) -> None:
        fake = Fake(error(529), OSError("connection reset"), reply())
        code, _ = self.run_extract(fake, "--limit", "1")
        self.assertEqual(code, 0)
        self.assertEqual(self.records()[0]["status"], "ok")
        self.assertEqual(len(self.slept), 2)

    def test_a_permanent_failure_is_recorded_and_the_run_carries_on(self) -> None:
        fake = Fake(error(400, '{"error": "bad request"}'), reply([entity("Deep Space Industries")]))
        code, _ = self.run_extract(fake)
        self.assertEqual(code, 0)
        statuses = [record["status"] for record in self.records()]
        self.assertEqual(statuses, ["failed", "ok"])
        self.assertEqual(self.slept, [], "a 400 is not worth retrying")

    def test_a_failure_body_carrying_the_key_is_masked(self) -> None:
        fake = Fake(error(401, json.dumps({"error": f"invalid key {KEY}"})))
        code, printed = self.run_extract(fake, "--limit", "1")
        self.assertEqual(code, 0)
        record = self.records()[0]
        self.assertEqual(record["status"], "failed")
        self.assertNotIn(KEY, record["error"])
        self.assertNotIn(KEY, printed)
        self.assertNotIn(KEY, "".join(path.read_text() for path in self.runs.glob("*-extract.json")))

    def test_a_second_run_skips_the_chunks_already_done(self) -> None:
        self.run_extract(Fake(reply([entity("Deep Space Industries")])))
        again = Fake(reply())
        code, printed = self.run_extract(again)
        self.assertEqual(code, 0)
        self.assertEqual(again.requests, [], "nothing should be sent for a chunk already recorded")
        self.assertIn("already extracted", printed)
        self.assertEqual(len(self.records()), len(CHUNKS))

    def test_only_reruns_the_chunk_it_names(self) -> None:
        self.run_extract(Fake(reply([entity("Deep Space Industries")])))
        again = Fake(reply([entity("Deep Space Industries"), entity("A Second Thing", "facility")]))
        code, _ = self.run_extract(again, "--only", CHUNKS[0]["id"])
        self.assertEqual(code, 0)
        self.assertEqual(len(again.requests), 1)
        records = self.records()
        self.assertEqual(records[-1]["chunk"], CHUNKS[0]["id"])
        self.assertEqual(len(records[-1]["extraction"]["entities"]), 2)

    def test_a_dry_run_sends_nothing_and_writes_no_receipt(self) -> None:
        fake = Fake(reply())
        code, printed = self.run_extract(fake, "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(fake.requests, [])
        self.assertFalse(self.extractions.exists())
        self.assertEqual(list(self.runs.glob("*-extract.json")), [])
        self.assertIn("DRY RUN", printed)

    def test_the_receipt_sums_the_usage_the_api_reported(self) -> None:
        self.run_extract(Fake(reply()))
        receipts = list(self.runs.glob("*-extract.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["tokens"]["input_tokens"], 1500 * len(CHUNKS))
        self.assertEqual(receipt["tokens"]["cache_read_input_tokens"], 1400 * len(CHUNKS))
        self.assertEqual(receipt["counts"]["processed"], len(CHUNKS))
        self.assertEqual(receipt["counts"]["failed"], 0)


class Repair(Fixture):
    """What the build step is allowed to assume about a tool result."""

    def test_a_relation_to_an_undeclared_entity_is_dropped_and_counted(self) -> None:
        fake = Fake(
            reply(
                [entity("Deep Space Industries"), entity("Entry Vehicle", "spacecraft")],
                [
                    relation("Deep Space Industries", "Entry Vehicle", relation="built"),
                    relation("Deep Space Industries", "Someone Never Declared"),
                ],
            )
        )
        code, _ = self.run_extract(fake, "--limit", "1")
        self.assertEqual(code, 0)
        record = self.records()[0]
        # The raw tool input is kept whole; the repair is a count beside it.
        self.assertEqual(len(record["extraction"]["relations"]), 2)
        self.assertEqual(record["repairs"]["dropped_relations"], 1)
        clean, _ = extract.repair(record["extraction"])
        self.assertEqual([edge["target"] for edge in clean["relations"]], ["Entry Vehicle"])

    def test_an_unknown_type_becomes_other(self) -> None:
        clean, repairs = extract.repair(
            {"entities": [entity("Something", "spaceship")], "relations": []}
        )
        self.assertEqual(clean["entities"][0]["type"], "other")
        self.assertEqual(repairs["retyped_entities"], 1)

    def test_a_self_relation_and_a_nameless_entity_go(self) -> None:
        clean, repairs = extract.repair(
            {
                "entities": [entity("Real Thing"), entity("   ")],
                "relations": [relation("Real Thing", "Real Thing")],
            }
        )
        self.assertEqual(len(clean["entities"]), 1)
        self.assertEqual(clean["relations"], [])
        self.assertEqual(repairs["dropped_entities"], 1)
        self.assertEqual(repairs["dropped_relations"], 1)

    def test_a_relation_matches_its_entity_whatever_the_case(self) -> None:
        clean, repairs = extract.repair(
            {
                "entities": [entity("Deep Space Industries"), entity("Entry Vehicle", "spacecraft")],
                "relations": [relation("DEEP SPACE INDUSTRIES", "entry vehicle", relation="Built By")],
            }
        )
        self.assertEqual(clean["relations"][0]["source"], "Deep Space Industries")
        self.assertEqual(clean["relations"][0]["target"], "Entry Vehicle")
        self.assertEqual(clean["relations"][0]["relation"], "built_by")
        self.assertNotIn("dropped_relations", repairs)

    def test_the_same_entity_twice_in_one_passage_is_recorded_once(self) -> None:
        clean, repairs = extract.repair(
            {"entities": [entity("Deep Space Industries"), entity("deep space industries")], "relations": []}
        )
        self.assertEqual(len(clean["entities"]), 1)
        self.assertEqual(repairs["duplicate_entities"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
