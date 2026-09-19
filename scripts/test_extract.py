#!/usr/bin/env python3
"""Fixture tests for extract.py. No real corpus, no API key, no network, and no
real `claude`: the one function that touches urllib and the one that starts a
process are both replaced with scripted fakes.

Run: python3 scripts/test_extract.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
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


def cli_result(
    entities: list[dict] | None = None,
    relations: list[dict] | None = None,
    stop_reason: str = "tool_use",
    is_error: bool = False,
    structured: bool = True,
    model: str = "claude-sonnet-5",
) -> tuple[int, str, str]:
    """A successful `claude -p --output-format json` run, in the shape extract.py reads.

    Recorded from CLI 2.1.278: a session result, not a message — which is why the
    tokens sit beside strings and nested objects that are not tokens.
    """
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "stop_reason": stop_reason,
        "num_turns": 2,
        "session_id": "00000000-0000-4000-8000-000000000000",
        "total_cost_usd": 0.0123,
        "modelUsage": {model: {"inputTokens": 2, "outputTokens": 444}},
        "usage": {
            "input_tokens": 2,
            "output_tokens": 444,
            "cache_creation_input_tokens": 3161,
            "cache_read_input_tokens": 0,
            "service_tier": "standard",
            "speed": "standard",
            "iterations": [{"input_tokens": 2}],
        },
    }
    if structured:
        answer = {"entities": entities or [], "relations": relations or []}
        payload["structured_output"] = answer
        payload["result"] = json.dumps(answer)
    else:
        payload["result"] = "I could not do that."
    return 0, json.dumps(payload), ""


def cli_failure(code: int = 1, stderr: str = "the CLI fell over") -> tuple[int, str, str]:
    return code, "", stderr


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


class FakeCli:
    """Scripted results for extract.claude_cli, in order; the last one repeats.

    It records the whole call, environment included, because the environment is
    the part of this backend that can be wrong without anything looking wrong.
    """

    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def __call__(self, command: list[str], stdin: str, env: dict, timeout: int) -> tuple:
        self.calls.append(
            {"command": list(command), "stdin": stdin, "env": dict(env), "timeout": timeout}
        )
        answer = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(answer, BaseException):
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

    def run_cli(self, fake: FakeCli, *arguments: str) -> tuple[int, str]:
        """The same, through --backend cli and the process fake."""
        printed = io.StringIO()
        with patch.object(extract, "claude_cli", fake), contextlib.redirect_stdout(printed):
            code = extract.main(["--backend", "cli", "--concurrency", "1", *arguments])
        return code, printed.getvalue()

    def write_chunks(self, count: int) -> None:
        """A longer corpus, for the tests that need more chunks than failures."""
        rows = [
            dict(CHUNKS[0], id=f"demo-report:{index:04d}", ordinal=index) for index in range(count)
        ]
        self.chunks.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    def receipt(self) -> dict:
        receipts = list(self.runs.glob("*-extract.json"))
        self.assertEqual(len(receipts), 1)
        return json.loads(receipts[0].read_text(encoding="utf-8"))

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
        self.assertEqual({record["backend"] for record in records}, {"api"})
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
        receipt = self.receipt()
        self.assertEqual(receipt["backend"], "api")
        self.assertEqual(receipt["max_tokens"], extract.MAX_TOKENS)
        self.assertNotIn("aborted", receipt)
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


class Cli(Fixture):
    """--backend cli: the same records, from a process instead of a socket."""

    def test_a_cli_run_records_the_same_shape_as_the_api(self) -> None:
        fake = FakeCli(cli_result([entity("Deep Space Industries")]))
        code, _ = self.run_cli(fake)
        self.assertEqual(code, 0)
        records = self.records()
        self.assertEqual([record["chunk"] for record in records], [chunk["id"] for chunk in CHUNKS])
        self.assertEqual({record["status"] for record in records}, {"ok"})
        self.assertEqual({record["backend"] for record in records}, {"cli"})
        self.assertEqual(records[0]["extraction"]["entities"][0]["name"], "Deep Space Industries")
        # The id that answered, not the alias that was asked for.
        self.assertEqual(records[0]["model"], "claude-sonnet-5")
        # Tokens only: the strings and the nested objects beside them are left out.
        self.assertEqual(
            records[0]["usage"],
            {
                "input_tokens": 2,
                "output_tokens": 444,
                "cache_creation_input_tokens": 3161,
                "cache_read_input_tokens": 0,
            },
        )
        receipt = self.receipt()
        self.assertEqual(receipt["backend"], "cli")
        self.assertIsNone(receipt["max_tokens"])
        self.assertEqual(receipt["http_status_counts"], {"cli-ok": len(CHUNKS)})
        self.assertEqual(receipt["tokens"]["output_tokens"], 444 * len(CHUNKS))
        # The CLI reports a price. The receipt still does not.
        self.assertNotIn("total_cost_usd", json.dumps(receipt))

    def test_the_child_never_sees_a_key_that_would_bill_the_api(self) -> None:
        """The whole point of the backend, and the one failure with no symptom."""
        fake = FakeCli(cli_result())
        with patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_AUTH_TOKEN": "oat-alsonotreal"}
        ):
            code, _ = self.run_cli(fake, "--limit", "1")
        self.assertEqual(code, 0)
        handed_over = fake.calls[0]["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", handed_over)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", handed_over)
        self.assertNotIn(KEY, json.dumps(handed_over))
        # Everything else goes through: the CLI needs its own home to find the login.
        self.assertEqual(handed_over.get("PATH"), os.environ.get("PATH"))

    def test_the_command_carries_the_instructions_and_the_schema(self) -> None:
        fake = FakeCli(cli_result())
        self.run_cli(fake, "--limit", "1")
        command = fake.calls[0]["command"]
        self.assertEqual(command[:3], [extract.CLAUDE_BIN, "-p", "--safe-mode"])
        self.assertNotIn("--bare", command, "--bare would force API-key authentication")
        for flag, value in (
            ("--model", extract.MODEL),
            ("--tools", ""),
            ("--system-prompt", extract.INSTRUCTIONS),
            ("--output-format", "json"),
            ("--json-schema", json.dumps(extract.tool_definition()["input_schema"])),
        ):
            self.assertIn(flag, command)
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn("--no-session-persistence", command)
        # The passage goes in on stdin, so a passage starting with "-" is not a flag.
        self.assertEqual(
            fake.calls[0]["stdin"], extract.passage(MANIFEST["documents"][0]["title"], CHUNKS[0])
        )
        self.assertEqual(fake.calls[0]["timeout"], extract.CLI_TIMEOUT)

    def test_a_dead_process_is_retried_and_then_succeeds(self) -> None:
        fake = FakeCli(cli_failure(), cli_result([entity("Deep Space Industries")]))
        code, _ = self.run_cli(fake, "--limit", "1")
        self.assertEqual(code, 0)
        self.assertEqual(self.records()[0]["status"], "ok")
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(self.slept, [extract.BACKOFF_SECONDS])
        self.assertEqual(self.receipt()["http_status_counts"], {"cli-exit-1": 1, "cli-ok": 1})

    def test_a_turn_the_cli_calls_an_error_becomes_a_failed_record(self) -> None:
        fake = FakeCli(cli_result(is_error=True))
        code, _ = self.run_cli(fake, "--limit", "1")
        self.assertEqual(code, 0)
        record = self.records()[0]
        self.assertEqual(record["status"], "failed")
        self.assertIn("cli-error", record["error"])
        self.assertEqual(len(fake.calls), extract.MAX_ATTEMPTS)

    def test_an_answer_with_no_structured_output_becomes_a_failed_record(self) -> None:
        fake = FakeCli(cli_result(structured=False))
        code, _ = self.run_cli(fake, "--limit", "1")
        self.assertEqual(code, 0)
        record = self.records()[0]
        self.assertEqual(record["status"], "failed")
        self.assertIn("cli-no-result", record["error"])
        self.assertEqual(record["extraction"], {"entities": [], "relations": []})

    def test_unreadable_stdout_becomes_a_failed_record(self) -> None:
        fake = FakeCli((0, "Welcome to Claude Code!\n", ""))
        code, _ = self.run_cli(fake, "--limit", "1")
        self.assertEqual(code, 0)
        self.assertIn("cli-unreadable", self.records()[0]["error"])

    def test_a_run_that_never_answers_times_out_and_is_retried(self) -> None:
        stuck = subprocess.TimeoutExpired(cmd=[extract.CLAUDE_BIN], timeout=extract.CLI_TIMEOUT)
        fake = FakeCli(stuck, cli_result([entity("Deep Space Industries")]))
        code, _ = self.run_cli(fake, "--limit", "1")
        self.assertEqual(code, 0)
        self.assertEqual(self.records()[0]["status"], "ok")
        self.assertEqual(self.receipt()["http_status_counts"], {"cli-timeout": 1, "cli-ok": 1})

    def test_a_truncated_answer_is_recorded_not_raised(self) -> None:
        fake = FakeCli(cli_result(stop_reason="max_tokens"))
        code, _ = self.run_cli(fake, "--limit", "1")
        self.assertEqual(code, 0)
        self.assertEqual(self.records()[0]["status"], "truncated")

    def test_the_cli_backend_asks_for_no_api_key(self) -> None:
        """A clone with no .anthropic.env can still run this backend."""
        fake = FakeCli(cli_result())
        with patch.dict(os.environ):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            code, _ = self.run_cli(fake, "--limit", "1")
        self.assertEqual(code, 0)
        self.assertEqual(self.records()[0]["status"], "ok")

    def test_a_dry_run_starts_no_process_and_keeps_the_prompt_to_itself(self) -> None:
        fake = FakeCli(cli_result())
        code, printed = self.run_cli(fake, "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls, [])
        self.assertFalse(self.extractions.exists())
        self.assertEqual(list(self.runs.glob("*-extract.json")), [])
        self.assertIn("DRY RUN", printed)
        self.assertIn("--no-session-persistence", printed)
        self.assertIn("<INSTRUCTIONS>", printed)
        self.assertIn("ANTHROPIC_API_KEY", printed)
        self.assertNotIn(extract.INSTRUCTIONS.strip(), printed)


class CircuitBreaker(Fixture):
    """A usage limit or an expired login fails every chunk the same way. Five in a
    row is the signal that carrying on would only fill extractions.jsonl with
    lines a resume then skips."""

    def setUp(self) -> None:
        super().setUp()
        # One call per chunk, so the script below reads as one entry per chunk.
        attempts = patch.object(extract, "MAX_ATTEMPTS", 1)
        attempts.start()
        self.addCleanup(attempts.stop)

    def test_five_failures_in_a_row_stop_the_run_with_the_rest_still_resumable(self) -> None:
        self.write_chunks(8)
        code, printed = self.run_cli(FakeCli(cli_failure(code=1, stderr="usage limit reached")))
        self.assertEqual(code, 1, "an aborted run must not look like a finished one")
        records = self.records()
        self.assertEqual(len(records), extract.ABORT_AFTER)
        self.assertEqual({record["status"] for record in records}, {"failed"})
        self.assertIn("re-run the same command", printed.lower())
        receipt = self.receipt()
        self.assertIn("failed in a row", receipt["aborted"])
        self.assertIn("cli", receipt["aborted"])

    def test_a_success_in_the_middle_means_nine_failures_are_not_five_in_a_row(self) -> None:
        self.write_chunks(9)
        script = [cli_failure()] * 4 + [cli_result([entity("Deep Space Industries")])]
        code, _ = self.run_cli(FakeCli(*script, cli_failure()))
        self.assertEqual(code, 0)
        records = self.records()
        self.assertEqual(len(records), 9, "every chunk should have been attempted")
        self.assertEqual([record["status"] for record in records].count("failed"), 8)
        self.assertNotIn("aborted", self.receipt())

    def test_the_breaker_guards_the_api_backend_too(self) -> None:
        self.write_chunks(8)
        code, _ = self.run_extract(Fake(error(400, '{"error": "bad request"}')))
        self.assertEqual(code, 1)
        self.assertEqual(len(self.records()), extract.ABORT_AFTER)
        self.assertIn("api", self.receipt()["aborted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
