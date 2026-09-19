#!/usr/bin/env python3
"""Read every chunk with Claude and record the entities and relations it states.

This is M3's offline half: nothing here talks to Cloudflare and nothing here
retrieves anything. One call per chunk, the answer forced into a schema so it
arrives as structured data rather than prose, and one JSON line per chunk
appended to runs/tmp/extractions.jsonl:

    {"chunk", "model", "backend", "status", "stop_reason", "extraction", "usage", "repairs"}

There are two ways to reach the model, and they differ only in who pays:

    --backend api   (default)  the Anthropic Messages API, on ANTHROPIC_API_KEY,
                               with the schema forced through a single tool.
    --backend cli              the Claude Code CLI already installed on this
                               machine, headless, on whatever login it holds —
                               so the run spends a subscription rather than
                               credit. Same instructions, same schema, same
                               records: the two can share one extractions.jsonl.

`scripts/build_graph.py` turns those lines into corpus/graph.json, and
`scripts/load_graph.py` writes that into D1.

Re-running is a resume, not a repeat: a chunk already in extractions.jsonl is
skipped, so an interrupted run — or a rate limit that outlasted the retries —
costs only the chunks it did not reach. `--only` is the exception, and reruns the
chunk it names; readers take the last line written for a chunk id.

The prompt is written for the genre, not for any question: these are accident
investigation reports, so the entity types are the things such reports are made
of — boards, contractors, people and their roles, missions, subsystems, software,
facilities, documents, events, causes and recommendations. The eval set played no
part in designing it.

Nothing here prints, logs or writes the API key. Error bodies are masked before
they are shown.

Dependencies: Python standard library only.

Usage:
    ./scripts/extract.sh                            # the whole corpus
    ./scripts/extract.sh --dry-run                  # no network at all; prints request sizes
    ./scripts/extract.sh --limit 20                 # the first 20 chunks, for a smoke test
    ./scripts/extract.sh --backend cli              # through the local Claude Code login
    ./scripts/extract.sh --only contour-mib:0012 --show
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ingest  # noqa: E402  — mask() and the receipt conventions live there

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "corpus" / "MANIFEST.json"
CHUNKS = REPO_ROOT / "corpus" / "chunks.jsonl"
RUNS = REPO_ROOT / "runs"
TMP = RUNS / "tmp"
EXTRACTIONS = TMP / "extractions.jsonl"

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = "claude-sonnet-5"
TOOL_NAME = "record_extraction"

# Total output budget for one chunk: the entities and relations of ~1,400
# characters of report text, and nothing else. Thinking is off (see
# request_body), so the whole budget belongs to the tool input.
MAX_TOKENS = 2000

TIMEOUT = 120

# --backend cli. The binary is looked up on PATH so the backend works wherever
# Claude Code is installed; scripts/extract.sh checks it is there before Python
# starts. 180 seconds because a CLI run pays its own start-up before it reaches
# the model, and a slow answer is still cheaper than a chunk that has to be redone.
CLAUDE_BIN = "claude"
CLI_TIMEOUT = 180

# Either of these in the child's environment makes the CLI authenticate as the
# API — the one thing --backend cli exists to avoid, and it would do it silently,
# on the card. They are stripped before the process starts.
BILLED_TO_THE_API = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# Retried with backoff; anything else is a permanent failure for that chunk.
RETRY_STATUSES = (408, 409, 429, 500, 502, 503, 504, 529)
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = 2.0
BACKOFF_CAP = 60.0

# When the far end is gone rather than merely awkward — an expired login, a
# subscription usage limit, a key that stopped working — every remaining chunk
# fails the same way, and each failure writes a line that a later resume then
# reads as done and skips. The corpus would be quietly half-extracted with no
# cheap way to tell which half. Five failures in a row is taken as "not this
# chunk's fault", and the run stops while the rest of the corpus is still
# unwritten and therefore still resumable.
ABORT_AFTER = 5

# The kinds of thing an accident investigation report is made of. Kept short on
# purpose: every extra type is another way for the same entity to arrive under
# two labels, and the build step has to reconcile them. "other" is the explicit
# escape hatch, and also where an unknown type is repaired to.
ENTITY_TYPES = [
    "organisation",
    "person",
    "board",
    "mission",
    "spacecraft",
    "subsystem",
    "software",
    "facility",
    "document",
    "event",
    "cause",
    "recommendation",
    "other",
]

INSTRUCTIONS = """\
You are reading one passage at a time from a set of public accident investigation
reports: mishap investigation boards, independent assessment teams and failure
review boards writing about spacecraft, launch vehicles and the programmes that
built them. Your job is to record the entities and the relations that THIS passage
states, so that a graph assembled from every passage can be walked later and every
edge traced back to the passage that stated it.

Record only what the passage states. Do not add what you know about the mission
from anywhere else, do not infer a relation the text does not make, and do not
guess at a name the text abbreviates beyond recognition. An empty result is a
valid answer: some passages are a table of contents, a page header, a figure
caption or a run of unreadable scanner output, and they should produce no
entities at all.

ENTITY TYPES

  organisation    An agency, contractor, company, programme office, directorate,
                  laboratory, team or working group.
  person          A named individual. Put their role and affiliation in the
                  description.
  board           An investigation board, review board, assessment team or panel
                  convened to examine something.
  mission         A mission, project or programme: the endeavour, as distinct
                  from the vehicle that flew it.
  spacecraft      Flight hardware as a whole: an orbiter, lander, probe, entry
                  vehicle, upper stage or launch vehicle.
  subsystem       A part of the vehicle or the ground system: a subsystem,
                  assembly, component, sensor, instrument, engine, thruster,
                  tank, harness or structure.
  software        Software, flight code, a ground tool, a model or a simulation.
  facility        A physical place: a centre, launch site, test stand, clean
                  room, plant or control room.
  document        A report, requirement, specification, procedure, standard,
                  plan, drawing or waiver.
  event           Something that happened at a point in time: a launch, a
                  separation, a manoeuvre, a test, a review, an anomaly, a loss.
  cause           A stated cause, contributing factor, finding, failure mode or
                  root cause.
  recommendation  A recommendation, corrective action or lesson learned that the
                  report puts forward.
  other           Anything real and named that none of the above fits. Use it
                  sparingly.

NAMES AND ALIASES

  * Prefer the full proper name as the passage writes it: "Mars Climate Orbiter
    Mishap Investigation Board", not "the Board".
  * When the passage shows both a full name and its acronym or short form, use
    the full name and put the short form in aliases. When it shows only the
    acronym, use the acronym as the name and leave aliases empty.
  * Do not invent aliases. Only record a form the passage actually contains.
  * The document title is given to you above the passage. Use it to resolve a
    bare "the Board", "the spacecraft", "the Project" or "the lander" to the
    named thing WHEN THE PASSAGE MAKES THAT UNAMBIGUOUS. When it does not, leave
    the entity out rather than guess.
  * Keep the same name for the same thing across the passage. Do not record a
    thing twice under two spellings.

DESCRIPTIONS

  One sentence, drawn from this passage. For a person, say what they did or which
  organisation or board they belonged to. For a cause or a recommendation, say
  what it was. Leave it short; it is a label, not a summary.

RELATIONS

  * source and target must both be names that appear in your own entities list
    for this passage. A relation to anything else will be discarded.
  * relation is a short verb phrase in snake_case: built_by, contracted_to,
    member_of, chaired, operated_from, part_of, caused_by, led_to,
    recommended_by, tested_at, documented_in, reported_to, launched_on.
    Reuse a phrase you have already used rather than inventing a synonym.
  * Prefer the relations that connect a thing to a different kind of thing: a
    person to their organisation or board, a contractor to the spacecraft or
    subsystem it built, a cause to the event it produced, a recommendation to
    what it addresses, a subsystem to the vehicle it is part of.
  * evidence is at most twenty words, quoted from the passage or paraphrased
    tightly from it, showing where the relation is stated.

WHAT TO SKIP

  Page furniture: running headers and footers, page numbers, report numbers
  printed on every page, tables of contents, lists of figures, signature blocks
  that carry no statement, and acronym glossaries. Scanner debris: fragments of
  words, stray characters, and lines where the text has clearly been garbled by
  optical character recognition — several of these reports are scans, and a
  mangled string is not an entity. If a passage is nothing but furniture or
  debris, return empty lists.

Call the tool exactly once. Return no prose.
"""

# Response keys worth noticing. Anything else is recorded in the receipt, because
# a new usage field would turn up as one.
KNOWN_TOP_KEYS = {
    "id",
    "type",
    "role",
    "model",
    "content",
    "stop_reason",
    "stop_sequence",
    "usage",
    "container",
    "context_management",
}
KNOWN_USAGE_KEYS = {
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cache_creation",
    "service_tier",
    "server_tool_use",
}
# The four the receipt sums. The rest are recorded as unexpected and left alone.
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

# The same discipline for the CLI's result object, which is a different shape
# entirely: it reports on a session rather than on a request. Recorded from CLI
# 2.1.278; a newer CLI that grows a field shows it in the receipt rather than
# changing what is extracted.
KNOWN_CLI_KEYS = {
    "api_error_status",
    "duration_api_ms",
    "duration_ms",
    "fast_mode_disabled_reason",
    "fast_mode_state",
    "first_content_frame_ms",
    "is_error",
    "modelUsage",
    "num_turns",
    "permission_denials",
    "queued_turn_count",
    "result",
    "result_index",
    "session_id",
    "stop_reason",
    "structured_output",
    "subagent_stats",
    "subtype",
    "terminal_reason",
    "time_to_request_ms",
    "total_cost_usd",
    "ttft_ms",
    "ttft_stream_ms",
    "type",
    "usage",
    "uuid",
}
KNOWN_CLI_USAGE_KEYS = KNOWN_USAGE_KEYS | {
    "output_tokens_details",
    "inference_geo",
    "iterations",
    "speed",
}

NOT_SNAKE = re.compile(r"[^a-z0-9]+")


class ExtractError(Exception):
    """Already masked by the time it is raised."""


def http_post(url: str, headers: dict[str, str], body: bytes, timeout: int) -> tuple[int, dict[str, str], bytes]:
    """One POST: no retries, no interpretation, no logging.

    The only place this milestone touches the network, and the seam the tests
    replace with a fake. An HTTP error status comes back as a value like any
    other; only a connection failure raises.
    """
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                {name.lower(): value for name, value in response.headers.items()},
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            {name.lower(): value for name, value in (exc.headers or {}).items()},
            exc.read(),
        )


def claude_cli(command: list[str], stdin: str, env: dict[str, str], timeout: int) -> tuple[int, str, str]:
    """One run of the local CLI: no retries, no interpretation, no logging.

    What http_post is to the API backend — the only place --backend cli leaves
    this process, and the seam the tests replace with a fake.

    The working directory is a fresh empty one for every run, and that is not
    tidiness. The CLI reads the directory it is started in: a CLAUDE.md, a
    settings file or a git repo above the extraction would join the prompt
    without ever appearing in it, and the passages would then be read under
    instructions this file does not contain. `env` arrives already stripped —
    see ClaudeCli.child_env — because the caller is what the tests check.

    Returns (exit code, stdout, stderr); a timeout raises, as subprocess does.
    """
    with tempfile.TemporaryDirectory(prefix="extract-cli-") as nowhere:
        finished = subprocess.run(
            command,
            input=stdin,
            cwd=nowhere,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    return finished.returncode, finished.stdout, finished.stderr


def tool_definition() -> dict:
    """The one tool the model is allowed to call, and the shape of its answer."""
    return {
        "name": TOOL_NAME,
        "description": (
            "Record the entities and the relations that this passage of an accident "
            "investigation report states. Both lists may be empty."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "description": "Every entity this passage names, at most once each.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": (
                                    "The full proper name as the passage writes it, with the "
                                    "acronym in aliases when the passage shows both."
                                ),
                            },
                            "type": {
                                "type": "string",
                                "enum": ENTITY_TYPES,
                                "description": "Which kind of thing this is.",
                            },
                            "description": {
                                "type": "string",
                                "description": (
                                    "One sentence drawn from this passage. For a person, their "
                                    "role and affiliation."
                                ),
                            },
                            "aliases": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Other forms of this name that appear in this passage: "
                                    "acronyms, short forms. Empty when there are none."
                                ),
                            },
                        },
                        "required": ["name", "type", "description", "aliases"],
                    },
                },
                "relations": {
                    "type": "array",
                    "description": "Every relation this passage states between two of the entities above.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "string",
                                "description": "An entity name from the entities list above.",
                            },
                            "target": {
                                "type": "string",
                                "description": "A different entity name from the entities list above.",
                            },
                            "relation": {
                                "type": "string",
                                "description": "A short verb phrase in snake_case, e.g. built_by, member_of, caused_by.",
                            },
                            "evidence": {
                                "type": "string",
                                "description": (
                                    "At most twenty words from this passage, quoted or tightly "
                                    "paraphrased, showing where the relation is stated."
                                ),
                            },
                        },
                        "required": ["source", "target", "relation", "evidence"],
                    },
                },
            },
            "required": ["entities", "relations"],
        },
    }


def snake(phrase: str) -> str:
    """A relation name, tidied: lower case, snake_case, nothing exotic."""
    return NOT_SNAKE.sub("_", phrase.strip().casefold()).strip("_")


def repair(extraction: dict) -> tuple[dict, dict]:
    """What the graph can use, and what had to be thrown away to get there.

    The model is asked for relations whose endpoints are its own entities, and for
    a type from the enum. When it misses — a relation to a name it never declared,
    a type outside the list — the fix is small and mechanical, so it is made here
    rather than paid for with a second request. Both the raw tool input and this
    count are kept, so the repairs stay visible.
    """
    report = Counter()
    entities: list[dict] = []
    by_name: dict[str, str] = {}

    for entity in extraction.get("entities") or []:
        if not isinstance(entity, dict):
            report["dropped_entities"] += 1
            continue
        name = str(entity.get("name") or "").strip()
        if not name:
            report["dropped_entities"] += 1
            continue
        if name.casefold() in by_name:
            report["duplicate_entities"] += 1
            continue
        kind = str(entity.get("type") or "").strip().casefold()
        if kind not in ENTITY_TYPES:
            report["retyped_entities"] += 1
            kind = "other"
        aliases = [
            str(alias).strip()
            for alias in (entity.get("aliases") or [])
            if str(alias).strip() and str(alias).strip().casefold() != name.casefold()
        ]
        entities.append(
            {
                "name": name,
                "type": kind,
                "description": str(entity.get("description") or "").strip(),
                "aliases": sorted(dict.fromkeys(aliases)),
            }
        )
        by_name[name.casefold()] = name

    relations: list[dict] = []
    for relation in extraction.get("relations") or []:
        if not isinstance(relation, dict):
            report["dropped_relations"] += 1
            continue
        source = by_name.get(str(relation.get("source") or "").strip().casefold())
        target = by_name.get(str(relation.get("target") or "").strip().casefold())
        verb = snake(str(relation.get("relation") or ""))
        if not source or not target or source == target or not verb:
            report["dropped_relations"] += 1
            continue
        relations.append(
            {
                "source": source,
                "target": target,
                "relation": verb,
                "evidence": str(relation.get("evidence") or "").strip(),
            }
        )

    return {"entities": entities, "relations": relations}, dict(report)


class Answer(NamedTuple):
    """What a backend got back, reduced to the four things a record needs.

    The two backends return wildly different objects — a Messages API response
    and a CLI session result — and this is where that difference stops. Anything
    below this line treats them the same.
    """

    extraction: dict
    stop_reason: str | None
    usage: dict[str, int]
    model: str


class Backend:
    """What `run`, `receipt_for` and `main` need, whichever way the model is reached.

    Subclasses supply `call`, which does the reaching. Everything else — the
    counters, the lock that guards them, what a record says, and when a run of
    failures means the far end is gone rather than this chunk being awkward — is
    decided once, here, so that the two backends cannot drift into writing
    different records for the same passage.
    """

    name = "?"
    max_tokens: int | None = None
    known_top: set[str] = set()
    known_usage: set[str] = set()

    def __init__(self, model: str, dry_run: bool) -> None:
        self.model = model
        self.dry_run = dry_run
        self.secrets: list[str] = []
        self.lock = threading.Lock()
        self.statuses: Counter[str] = Counter()
        self.usage: Counter[str] = Counter()
        self.retries = 0
        self.unexpected_fields: set[str] = set()
        self.consecutive_failures = 0
        self.aborted: str | None = None

    # --- one chunk -----------------------------------------------------------
    def call(self, title: str, chunk: dict) -> Answer:
        """Reach the model once, with retries. Raises ExtractError when it cannot."""
        raise NotImplementedError

    def extract(self, title: str, chunk: dict) -> dict:
        """One chunk in, one record for extractions.jsonl out. Never raises."""
        record: dict = {"chunk": chunk["id"], "model": self.model, "backend": self.name}
        if self.dry_run:
            record["status"] = "dry-run"
            record["extraction"] = {"entities": [], "relations": []}
            record["repairs"] = {}
            return record
        try:
            answer = self.call(title, chunk)
        except ExtractError as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
            record["extraction"] = {"entities": [], "relations": []}
            record["repairs"] = {}
            return record

        _, repairs = repair(answer.extraction)
        record["model"] = answer.model
        record["stop_reason"] = answer.stop_reason
        record["status"] = "truncated" if answer.stop_reason == "max_tokens" else "ok"
        record["extraction"] = answer.extraction
        record["usage"] = answer.usage
        record["repairs"] = repairs
        return record

    def remember(self, payload: dict) -> None:
        """Sum the tokens, and notice any field this backend has not seen before.

        A new field lands in the receipt rather than in a surprise: the usage
        objects are the part of both answers most likely to grow a key between
        one run and the next.
        """
        usage = payload.get("usage") or {}
        with self.lock:
            for field in USAGE_FIELDS:
                value = usage.get(field)
                if isinstance(value, int):
                    self.usage[field] += value
            self.unexpected_fields.update(set(payload) - self.known_top)
            self.unexpected_fields.update(f"usage.{key}" for key in set(usage) - self.known_usage)

    # --- the circuit breaker -------------------------------------------------
    def note(self, record: dict) -> bool:
        """Count consecutive failures. True on the one call that trips the breaker.

        Called under the writer lock in `run`, so the sequence it sees is the
        order the records were written, and only one thread can be the one that
        trips it however many are in flight.
        """
        with self.lock:
            if record.get("status") != "failed":
                self.consecutive_failures = 0
                return False
            self.consecutive_failures += 1
            if self.consecutive_failures < ABORT_AFTER or self.aborted is not None:
                return False
            self.aborted = (
                f"{ABORT_AFTER} chunks failed in a row, so this is the {self.name} backend "
                f"and not the passages. Last error: {record.get('error') or 'unknown'}"
            )
            return True

    def stopped(self) -> bool:
        """Read without the lock, deliberately: every worker asks this before every
        chunk, and the only cost of reading a stale None is one more chunk."""
        return self.aborted is not None

    # --- what a dry run says -------------------------------------------------
    def dry_run_lines(self, count: int) -> list[str]:
        """The destination, and the shape of what would go to it."""
        raise NotImplementedError

    def prefix_bytes(self) -> int:
        """The fixed part of every call: the instructions and the schema."""
        raise NotImplementedError

    def dry_run_limits(self) -> list[str]:
        """Whatever ceiling this backend runs under, if it has one worth printing."""
        return []


class Anthropic(Backend):
    """The one endpoint this milestone touches, in one place.

    The request shape below was taken from Anthropic's Messages API
    documentation; where a choice could go either way it is marked so the change
    is one function rather than a search.
    """

    name = "api"
    max_tokens = MAX_TOKENS
    known_top = KNOWN_TOP_KEYS
    known_usage = KNOWN_USAGE_KEYS

    def __init__(self, key: str, model: str, dry_run: bool) -> None:
        super().__init__(model, dry_run)
        self._key = key
        self.secrets = [key]

    # --- the request ---------------------------------------------------------
    def system(self) -> list[dict]:
        """The cached prefix, as its own method so a dry run can size it without a chunk."""
        return [
            {
                "type": "text",
                "text": INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def request_body(self, title: str, chunk: dict) -> dict:
        """POST https://api.anthropic.com/v1/messages

        Two deliberate choices, both reversible here:

          * `thinking: disabled`. On this model thinking is on by default and
            `max_tokens` is a ceiling on thinking plus output together, so leaving
            it on would spend the chunk's budget on reasoning about a passage that
            mostly needs reading. If the extractions come back thin, the knob to
            turn is this one — drop the field and raise max_tokens.
          * `tool_choice: tool`. Forcing the single tool is what makes the answer
            structured. This model accepts forced tool choice; some newer ones
            reject it, and the fallback there is tool_choice auto with the tool
            named in the instructions.

        The cache breakpoint sits on the system block. Tools render before system,
        so one marker caches the whole fixed prefix — instructions and schema —
        and the passage after it is all that is billed at full rate.
        """
        return {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "thinking": {"type": "disabled"},
            "system": self.system(),
            "tools": [tool_definition()],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
            "messages": [{"role": "user", "content": passage(title, chunk)}],
        }

    def headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
            "accept": "application/json",
        }

    # --- one chunk, with retries --------------------------------------------
    def send(self, body: dict) -> dict:
        """The response payload, after as many attempts as the limits allow."""
        encoded = json.dumps(body).encode("utf-8")
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                status, headers, raw = http_post(API_URL, self.headers(), encoded, TIMEOUT)
            except (urllib.error.URLError, OSError) as exc:
                status, headers, raw = 0, {}, str(exc).encode("utf-8")

            with self.lock:
                self.statuses[str(status) if status else "network-error"] += 1

            if status == 200:
                payload = json.loads(raw.decode("utf-8"))
                self.remember(payload)
                return payload

            detail = ingest.mask(raw.decode("utf-8", "replace")[:400], self.secrets)
            retryable = status == 0 or status in RETRY_STATUSES
            if not retryable or attempt == MAX_ATTEMPTS:
                where = "network error" if status == 0 else f"HTTP {status}"
                raise ExtractError(f"{where} after {attempt} attempt(s): {detail}")

            with self.lock:
                self.retries += 1
            time.sleep(wait_for(headers, attempt))
        raise ExtractError("unreachable")  # pragma: no cover

    def call(self, title: str, chunk: dict) -> Answer:
        payload = self.send(self.request_body(title, chunk))
        return Answer(
            extraction=tool_input(payload),
            stop_reason=payload.get("stop_reason"),
            usage=int_fields(payload.get("usage")),
            model=self.model,
        )

    # --- what a dry run says -------------------------------------------------
    def dry_run_lines(self, count: int) -> list[str]:
        return [f"==> {plural(count, 'request')} to {API_URL}, model {self.model}"]

    def prefix_bytes(self) -> int:
        return len(json.dumps({"system": self.system(), "tools": [tool_definition()]}).encode("utf-8"))

    def dry_run_limits(self) -> list[str]:
        return [f"    max output tokens per request               {MAX_TOKENS}"]


class ClaudeCli(Backend):
    """The same extraction, through the Claude Code CLI already on this machine.

    The point of it is who pays. `claude -p` runs headless on whatever login the
    machine holds, so extracting 1,373 chunks spends a subscription that is
    already bought rather than API credit. Nothing about the reading changes: the
    system prompt is the same INSTRUCTIONS, and the schema is the same schema —
    handed over as --json-schema instead of as a forced tool, which is the CLI's
    way of saying the same thing. The records it writes are therefore the same
    shape as the API's, and one extractions.jsonl can hold both.

    Two things have to be true of the child process or the saving evaporates.
    Neither ANTHROPIC_API_KEY nor ANTHROPIC_AUTH_TOKEN may be in its environment,
    because either one makes the CLI bill the API instead — quietly, with no sign
    of it in the output. And its working directory has to be somewhere with no
    CLAUDE.md, no settings file and no git repo, so that nothing local joins the
    prompt. --safe-mode covers the rest: no hooks, no skills, no MCP servers, and
    authentication untouched.
    """

    name = "cli"
    max_tokens = None
    known_top = KNOWN_CLI_KEYS
    known_usage = KNOWN_CLI_USAGE_KEYS

    def __init__(self, model: str, dry_run: bool) -> None:
        super().__init__(model, dry_run)
        # The API backend's tool schema, which is the whole of what --json-schema
        # wants: the CLI has no tool to name, only the shape of the answer.
        self.schema = json.dumps(tool_definition()["input_schema"])

    # --- the call ------------------------------------------------------------
    def command(self) -> list[str]:
        """The exact argv, and why each flag is on it.

          -p                        headless: read one prompt, print one answer, exit.
          --safe-mode               no CLAUDE.md, no hooks, no skills, no MCP servers, so
                                    the instructions are INSTRUCTIONS and nothing else.
                                    It does not touch authentication.
          --model                   an alias such as `sonnet` or a full id; the id that
                                    actually answered is read back out of the result.
          --tools ""                no tools at all. This is one turn of reading, and a
                                    run that could open files or run commands would not
                                    be the same experiment as the API backend's.
          --no-session-persistence  1,373 runs should leave nothing behind.
          --output-format json      one JSON object on stdout, to be parsed rather than
                                    scraped.
          --json-schema             the answer, in `structured_output`, in the shape the
                                    build step already knows how to read.

        Not --bare, which forces ANTHROPIC_API_KEY authentication and would put
        the run straight back on the bill this backend exists to get off.

        The passage is not here: it goes in on stdin, because a passage is
        arbitrary report text and some of them begin with a dash.
        """
        return [
            CLAUDE_BIN,
            "-p",
            "--safe-mode",
            "--model",
            self.model,
            "--tools",
            "",
            "--no-session-persistence",
            "--system-prompt",
            INSTRUCTIONS,
            "--output-format",
            "json",
            "--json-schema",
            self.schema,
        ]

    def child_env(self) -> dict[str, str]:
        """This process's environment, minus the two variables that change who pays.

        Everything else is passed through: the CLI needs HOME to find the login
        it is meant to use, and PATH to find itself.
        """
        env = dict(os.environ)
        for name in BILLED_TO_THE_API:
            env.pop(name, None)
        return env

    def attempt(self, command: list[str], env: dict[str, str], text: str) -> tuple[str, str, dict | None]:
        """One run of the CLI, classified: (status key, masked detail, result or None).

        The status keys are to `--backend cli` what HTTP statuses are to the API
        backend, and they end up in the same place in the receipt:

            cli-ok          an answer, in the schema
            cli-exit-N      the process died; N is its exit code
            cli-timeout     no answer inside CLI_TIMEOUT
            cli-unreadable  stdout was not one JSON object
            cli-error       the CLI reported the turn as an error
            cli-no-result   no structured_output, so nothing was extracted
        """
        try:
            code, out, err = claude_cli(command, text, env, CLI_TIMEOUT)
        except subprocess.TimeoutExpired:
            return "cli-timeout", f"no answer within {CLI_TIMEOUT}s", None
        if code != 0:
            return f"cli-exit-{code}", self.detail(err or out), None
        try:
            payload = json.loads(out)
        except ValueError:
            return "cli-unreadable", self.detail(out), None
        if not isinstance(payload, dict):
            return "cli-unreadable", self.detail(out), None
        if payload.get("is_error"):
            reason = payload.get("api_error_status") or payload.get("subtype") or ""
            return "cli-error", self.detail(f"{reason} {payload.get('result') or ''}"), None
        if not isinstance(payload.get("structured_output"), dict):
            return "cli-no-result", self.detail(str(payload.get("result") or "")), None
        return "cli-ok", "", payload

    def detail(self, text: str) -> str:
        """Enough of a failure to recognise it, and no more."""
        return ingest.mask(text.strip()[:400], self.secrets)

    def send(self, text: str) -> dict:
        """The CLI's result object, after as many attempts as the limits allow.

        Every failure here is retried, unlike the API backend where a 400 is
        final. A local process has no status code that distinguishes "you asked
        wrongly" from "not right now": a killed process, a login that expired
        mid-run and a usage limit all arrive as some kind of error, and the
        prompt is fixed, so asking again is the only thing worth doing. The
        circuit breaker is what stops that being infinite optimism.
        """
        command = self.command()
        env = self.child_env()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            outcome, detail, payload = self.attempt(command, env, text)
            with self.lock:
                self.statuses[outcome] += 1
            if payload is not None:
                self.remember(payload)
                return payload
            if attempt == MAX_ATTEMPTS:
                raise ExtractError(f"{outcome} after {attempt} attempt(s): {detail}")
            with self.lock:
                self.retries += 1
            time.sleep(wait_for({}, attempt))
        raise ExtractError("unreachable")  # pragma: no cover

    def call(self, title: str, chunk: dict) -> Answer:
        payload = self.send(passage(title, chunk))
        return Answer(
            extraction=payload["structured_output"],
            stop_reason=payload.get("stop_reason"),
            usage=int_fields(payload.get("usage")),
            model=resolved_model(payload) or self.model,
        )

    # --- what a dry run says -------------------------------------------------
    def dry_run_lines(self, count: int) -> list[str]:
        """The command, with the two long arguments named rather than printed."""
        shape = " ".join(
            "<INSTRUCTIONS>"
            if word == INSTRUCTIONS
            else "<SCHEMA>"
            if word == self.schema
            else shlex.quote(word)
            for word in self.command()
        )
        return [
            f"==> {plural(count, 'run')} of the local {CLAUDE_BIN} CLI, model {self.model}",
            f"    {shape}",
            "    passage on stdin, working directory a fresh empty one",
            f"    stripped from its environment: {', '.join(BILLED_TO_THE_API)}",
        ]

    def prefix_bytes(self) -> int:
        return len(INSTRUCTIONS.encode("utf-8")) + len(self.schema.encode("utf-8"))

    def dry_run_limits(self) -> list[str]:
        return [f"    timeout per run                             {CLI_TIMEOUT}s"]


def wait_for(headers: dict[str, str], attempt: int) -> float:
    """How long to wait before the next attempt, honouring retry-after."""
    after = headers.get("retry-after")
    if after:
        try:
            return min(BACKOFF_CAP, max(0.0, float(after)))
        except ValueError:
            pass
    return min(BACKOFF_CAP, BACKOFF_SECONDS * (2 ** (attempt - 1)))


def int_fields(usage: dict | None) -> dict[str, int]:
    """The counted part of a usage object.

    Both backends report tokens beside things that are not tokens — a service
    tier, a speed, a nested breakdown, a list of iterations. Keeping only the
    integers is what makes one record shape hold both, and it is why a new
    counter needs no code here to be recorded.
    """
    return {field: value for field, value in (usage or {}).items() if isinstance(value, int)}


def resolved_model(payload: dict) -> str | None:
    """Which model actually answered, when the CLI names exactly one.

    `--model sonnet` is an alias, and a record that says `sonnet` cannot be
    compared with one from six months later. More than one key means more than
    one model ran, and then there is no single honest answer to write down.
    """
    reported = payload.get("modelUsage")
    if isinstance(reported, dict) and len(reported) == 1:
        return next(iter(reported))
    return None


def tool_input(payload: dict) -> dict:
    """The tool call's arguments, or empty lists when the model returned none.

    A truncated response can stop part-way through the tool call, in which case
    there is nothing parseable to take — that is recorded as a truncated
    extraction, not as a crash.
    """
    for block in payload.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == TOOL_NAME:
            value = block.get("input")
            if isinstance(value, dict):
                return value
    return {"entities": [], "relations": []}


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def passage(title: str, chunk: dict) -> str:
    """The user turn: the document title, the pages, and the chunk text.

    The title is here so that a bare "the Board" or "the spacecraft" can be
    resolved to the named one when the passage makes that unambiguous — the
    instructions say when not to.
    """
    pages = (
        f"page {chunk['page_start']}"
        if chunk["page_start"] == chunk["page_end"]
        else f"pages {chunk['page_start']}-{chunk['page_end']}"
    )
    return f"Document: {title}\nFrom {pages}.\n\nPassage:\n{chunk['text']}"


def titles() -> dict[str, str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {document["slug"]: document["title"] for document in manifest["documents"]}


def load_chunks(limit: int | None, only: str | None) -> list[dict]:
    with CHUNKS.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if only:
        rows = [row for row in rows if row["id"] == only]
        if not rows:
            raise ExtractError(f"no chunk with id {only!r} in {CHUNKS.name}")
        return rows
    return rows[:limit] if limit else rows


def already_done(path: Path) -> dict[str, str]:
    """chunk id -> status, from an earlier run. The last line for an id wins."""
    if not path.exists():
        return {}
    done: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                done[row["chunk"]] = row.get("status", "ok")
    return done


def show(record: dict, chunk: dict) -> None:
    """The extraction, printed for someone watching rather than parsing."""
    clean, repairs = repair(record.get("extraction") or {})
    entities = clean["entities"]
    relations = clean["relations"]
    print(f"\n┌─ {chunk['id']}  ({record.get('status', '?')})")
    print(f"│  {len(entities)} entities, {len(relations)} relations")
    if repairs:
        print(f"│  repaired: {', '.join(f'{key} {value}' for key, value in sorted(repairs.items()))}")
    print("│")
    width = max([len(entity["type"]) for entity in entities] + [0])
    for entity in entities:
        alias = f"  ({', '.join(entity['aliases'])})" if entity["aliases"] else ""
        print(f"│  {entity['type']:<{width}}  {entity['name']}{alias}")
        for line in textwrap.wrap(entity["description"], 72):
            print(f"│  {'':<{width}}  {line}")
    if relations:
        print("│")
    for relation in relations:
        print(f"│  {relation['source']}  --[{relation['relation']}]->  {relation['target']}")
        for line in textwrap.wrap(f"“{relation['evidence']}”", 72):
            print(f"│      {line}")
    print("└─")


def receipt_for(
    api: Backend,
    counts: dict[str, int],
    seconds: float,
    started: str,
    dry_run: bool,
    concurrency: int,
) -> dict:
    """The run, accounted for.

    `http_status_counts` keeps its name under either backend: for `--backend cli`
    it holds the cli-* keys from ClaudeCli.attempt, which are the same thing one
    layer down. Renaming it would have made every receipt already in runs/ the
    odd one out, which is a worse trade than a slightly literal key.
    """
    receipt = {
        "milestone": "M3",
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wall_seconds": round(seconds, 1),
        "dry_run": dry_run,
        "backend": api.name,
        "model": api.model,
        "max_tokens": api.max_tokens,
        "concurrency": concurrency,
        "counts": counts,
        "http_status_counts": dict(sorted(api.statuses.items())),
        "retries": api.retries,
        "tokens": {
            **{field: api.usage.get(field, 0) for field in USAGE_FIELDS},
            "note": "summed from the usage object of every response. Tokens only — this "
            "file carries no prices, because a price that is wrong later is worse than "
            "no price at all.",
        },
        "unexpected_response_fields": sorted(api.unexpected_fields),
    }
    # Only when it happened: a receipt that always says "aborted" trains the eye
    # to skip the line that matters.
    if api.aborted:
        receipt["aborted"] = api.aborted
    return receipt


def write_receipt(receipt: dict, secrets: list[str], dry_run: bool) -> Path | None:
    """The receipt on disk, or printed when there is nothing to account for."""
    text = ingest.mask(json.dumps(receipt, indent="\t", ensure_ascii=False), secrets)
    if dry_run:
        print(f"\n{text}")
        return None
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-extract.json"
    path.write_text(text + "\n", encoding="utf-8")
    return path


def run(api: Backend, chunks: list[dict], args, book: dict[str, str]) -> dict[str, int]:
    """Every chunk, `--concurrency` at a time, appended as each one lands.

    Once the circuit breaker has tripped, the chunks still to come are declined
    rather than attempted. The pool was handed all of them at the start, so
    stopping the run means each worker refusing the rest — the few already in
    flight finish and are written, because they are paid for either way.
    """
    counts = Counter({"processed": 0, "failed": 0, "truncated": 0, "entities": 0, "relations": 0})
    for state in ("dropped_relations", "retyped_entities", "dropped_entities", "duplicate_entities"):
        counts[state] = 0
    writer = threading.Lock()
    handle = None
    if not api.dry_run:
        TMP.mkdir(parents=True, exist_ok=True)
        handle = EXTRACTIONS.open("a", encoding="utf-8")

    def one(chunk: dict) -> None:
        if api.stopped():
            return
        record = api.extract(book[chunk["document"]], chunk)
        clean, repairs = repair(record.get("extraction") or {})
        with writer:
            if handle is not None:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
            counts["processed"] += 1
            counts["entities"] += len(clean["entities"])
            counts["relations"] += len(clean["relations"])
            for key, value in repairs.items():
                counts[key] += value
            if record["status"] == "failed":
                counts["failed"] += 1
            if record["status"] == "truncated":
                counts["truncated"] += 1
            if args.show:
                show(record, chunk)
            elif counts["processed"] % 25 == 0 or counts["processed"] == len(chunks):
                print(
                    f"    {counts['processed']:>5}/{len(chunks)} chunks"
                    f"   {counts['entities']:>6} entities"
                    f"   {counts['relations']:>6} relations"
                    f"   {counts['failed']} failed"
                )
            if api.note(record):
                print(f"\n!! {api.aborted}")
                print("   Stopping, rather than writing a failed line for every chunk that is left.")
                print("   Fix it and re-run the same command: what is already recorded is skipped,")
                print(f"   so the run picks up at chunk {counts['processed'] + 1} of {len(chunks)}.")

    try:
        if args.concurrency <= 1:
            for chunk in chunks:
                one(chunk)
        else:
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                list(pool.map(one, chunks))
    finally:
        if handle is not None:
            handle.close()
    return dict(counts)


def dry_run_sizes(api: Backend, chunks: list[dict], book: dict[str, str]) -> None:
    """What would be sent, and how big it is. No network, no process, no receipt."""
    prefix = api.prefix_bytes()
    passages = [len(passage(book[chunk["document"]], chunk).encode("utf-8")) for chunk in chunks]
    for line in api.dry_run_lines(len(chunks)):
        print(line)
    print(f"    cached prefix (instructions + schema)       {prefix / 1024:6.1f} KB")
    print(f"    passage per request, smallest / largest     {min(passages)} / {max(passages)} bytes")
    print(f"    passages in total                           {sum(passages) / 1024:6.1f} KB")
    print(
        "    rough input tokens per request              "
        f"~{round(prefix / 4) + round(sum(passages) / len(passages) / 4)}"
        " (characters / 4; there is no tokenizer offline)"
    )
    for line in api.dry_run_limits():
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="read only the first N chunks")
    parser.add_argument("--only", default=None, help="one chunk id; reruns it even if it is already done")
    parser.add_argument("--model", default=MODEL, help=f"model id (default {MODEL})")
    parser.add_argument(
        "--backend",
        choices=("api", "cli"),
        default="api",
        help="api: the Messages API on ANTHROPIC_API_KEY (default). "
        "cli: the local Claude Code CLI, on whatever login this machine holds",
    )
    parser.add_argument("--concurrency", type=int, default=4, help="requests in flight (default 4)")
    parser.add_argument("--show", action="store_true", help="pretty-print what came back for each chunk")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="no network at all: print what would be sent, and the size of it",
    )
    args = parser.parse_args(argv)

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if args.backend == "api" and not args.dry_run and not key:
        print("Refusing to run: no ANTHROPIC_API_KEY. Use ./scripts/extract.sh.", file=sys.stderr)
        return 2
    if not CHUNKS.exists():
        print(f"No {CHUNKS.name}. Run python3 scripts/chunk.py first.", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    clock = time.monotonic()
    # The cli backend takes no key on purpose: it must not have one to give away.
    api: Backend = (
        ClaudeCli(args.model, args.dry_run)
        if args.backend == "cli"
        else Anthropic(key, args.model, args.dry_run)
    )

    try:
        book = titles()
        chunks = load_chunks(args.limit, args.only)
        done = {} if args.dry_run else already_done(EXTRACTIONS)
        if args.only:
            done.pop(args.only, None)
        todo = [chunk for chunk in chunks if chunk["id"] not in done]
        skipped = len(chunks) - len(todo)

        print(
            f"{plural(len(chunks), 'chunk')} from {CHUNKS.name}"
            + ("  [DRY RUN — no network]" if args.dry_run else "")
        )
        if skipped:
            failures = sum(1 for chunk in chunks if done.get(chunk["id"]) == "failed")
            note = f", {failures} of them failed — delete their lines to retry" if failures else ""
            print(f"    {skipped} already extracted on an earlier run, skipping{note}")

        if args.dry_run:
            dry_run_sizes(api, todo or chunks, book)
            counts = {"processed": 0, "would_process": len(todo)}
        elif not todo:
            print("    nothing left to do")
            counts = {"processed": 0}
        else:
            print(
                f"==> extracting {plural(len(todo), 'chunk')} with {api.model} "
                f"via the {api.name} backend, {args.concurrency} at a time"
            )
            counts = run(api, todo, args, book)
    except ExtractError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"\nFAILED: {ingest.mask(str(exc), api.secrets)}", file=sys.stderr)
        return 1

    counts["skipped"] = skipped
    receipt = receipt_for(api, counts, time.monotonic() - clock, started, args.dry_run, args.concurrency)
    path = write_receipt(receipt, api.secrets, args.dry_run)
    where = f"Receipt: {path.relative_to(REPO_ROOT)}" if path else "Dry run — no receipt written."
    print(f"\n{'Aborted after' if api.aborted else 'Done in'} {time.monotonic() - clock:.1f}s. {where}")
    return 1 if api.aborted else 0


if __name__ == "__main__":
    raise SystemExit(main())
