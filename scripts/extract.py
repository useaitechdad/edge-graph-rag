#!/usr/bin/env python3
"""Read every chunk with Claude and record the entities and relations it states.

This is M3's offline half: nothing here talks to Cloudflare and nothing here
retrieves anything. One request per chunk to the Anthropic Messages API, forced
through a single tool so the answer arrives as structured data rather than prose,
and one JSON line per chunk appended to runs/tmp/extractions.jsonl:

    {"chunk", "model", "status", "stop_reason", "extraction", "usage", "repairs"}

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
    ./scripts/extract.sh --only contour-mib:0012 --show
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

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

# Retried with backoff; anything else is a permanent failure for that chunk.
RETRY_STATUSES = (408, 409, 429, 500, 502, 503, 504, 529)
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = 2.0
BACKOFF_CAP = 60.0

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


class Anthropic:
    """The one endpoint this milestone touches, in one place.

    The request shape below was taken from Anthropic's Messages API
    documentation; where a choice could go either way it is marked so the change
    is one function rather than a search.
    """

    def __init__(self, key: str, model: str, dry_run: bool) -> None:
        self._key = key
        self.model = model
        self.dry_run = dry_run
        self.secrets = [key]
        self.lock = threading.Lock()
        self.statuses: Counter[str] = Counter()
        self.usage: Counter[str] = Counter()
        self.retries = 0
        self.unexpected_fields: set[str] = set()

    # --- the request ---------------------------------------------------------
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
            "system": [
                {
                    "type": "text",
                    "text": INSTRUCTIONS,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
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

    def remember(self, payload: dict) -> None:
        usage = payload.get("usage") or {}
        with self.lock:
            for field in USAGE_FIELDS:
                value = usage.get(field)
                if isinstance(value, int):
                    self.usage[field] += value
            self.unexpected_fields.update(set(payload) - KNOWN_TOP_KEYS)
            self.unexpected_fields.update(f"usage.{key}" for key in set(usage) - KNOWN_USAGE_KEYS)

    def extract(self, title: str, chunk: dict) -> dict:
        """One chunk in, one record for extractions.jsonl out. Never raises."""
        body = self.request_body(title, chunk)
        record: dict = {"chunk": chunk["id"], "model": self.model}
        if self.dry_run:
            record["status"] = "dry-run"
            record["extraction"] = {"entities": [], "relations": []}
            record["repairs"] = {}
            return record
        try:
            payload = self.send(body)
        except ExtractError as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
            record["extraction"] = {"entities": [], "relations": []}
            record["repairs"] = {}
            return record

        raw = tool_input(payload)
        _, repairs = repair(raw)
        record["stop_reason"] = payload.get("stop_reason")
        record["status"] = "truncated" if payload.get("stop_reason") == "max_tokens" else "ok"
        record["extraction"] = raw
        record["usage"] = {
            field: value for field, value in (payload.get("usage") or {}).items() if isinstance(value, int)
        }
        record["repairs"] = repairs
        return record


def wait_for(headers: dict[str, str], attempt: int) -> float:
    """How long to wait before the next attempt, honouring retry-after."""
    after = headers.get("retry-after")
    if after:
        try:
            return min(BACKOFF_CAP, max(0.0, float(after)))
        except ValueError:
            pass
    return min(BACKOFF_CAP, BACKOFF_SECONDS * (2 ** (attempt - 1)))


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
    api: Anthropic,
    counts: dict[str, int],
    seconds: float,
    started: str,
    dry_run: bool,
    concurrency: int,
) -> dict:
    return {
        "milestone": "M3",
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wall_seconds": round(seconds, 1),
        "dry_run": dry_run,
        "model": api.model,
        "max_tokens": MAX_TOKENS,
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


def run(api: Anthropic, chunks: list[dict], args, book: dict[str, str]) -> dict[str, int]:
    """Every chunk, `--concurrency` at a time, appended as each one lands."""
    counts = Counter({"processed": 0, "failed": 0, "truncated": 0, "entities": 0, "relations": 0})
    for state in ("dropped_relations", "retyped_entities", "dropped_entities", "duplicate_entities"):
        counts[state] = 0
    writer = threading.Lock()
    handle = None
    if not api.dry_run:
        TMP.mkdir(parents=True, exist_ok=True)
        handle = EXTRACTIONS.open("a", encoding="utf-8")

    def one(chunk: dict) -> None:
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


def dry_run_sizes(api: Anthropic, chunks: list[dict], book: dict[str, str]) -> None:
    """What would be sent, and how big it is. No network, no receipt on disk."""
    prefix = json.dumps({"system": api.request_body("", chunks[0])["system"], "tools": [tool_definition()]})
    passages = [len(passage(book[chunk["document"]], chunk).encode("utf-8")) for chunk in chunks]
    print(f"==> {plural(len(chunks), 'request')} to {API_URL}, model {api.model}")
    print(f"    cached prefix (instructions + tool schema)  {len(prefix.encode('utf-8')) / 1024:6.1f} KB")
    print(f"    passage per request, smallest / largest     {min(passages)} / {max(passages)} bytes")
    print(f"    passages in total                           {sum(passages) / 1024:6.1f} KB")
    print(
        "    rough input tokens per request              "
        f"~{round(len(prefix) / 4) + round(sum(passages) / len(passages) / 4)}"
        " (characters / 4; there is no tokenizer offline)"
    )
    print("    max output tokens per request               " f"{MAX_TOKENS}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="read only the first N chunks")
    parser.add_argument("--only", default=None, help="one chunk id; reruns it even if it is already done")
    parser.add_argument("--model", default=MODEL, help=f"model id (default {MODEL})")
    parser.add_argument("--concurrency", type=int, default=4, help="requests in flight (default 4)")
    parser.add_argument("--show", action="store_true", help="pretty-print what came back for each chunk")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="no network at all: print what would be sent, and the size of it",
    )
    args = parser.parse_args(argv)

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not args.dry_run and not key:
        print("Refusing to run: no ANTHROPIC_API_KEY. Use ./scripts/extract.sh.", file=sys.stderr)
        return 2
    if not CHUNKS.exists():
        print(f"No {CHUNKS.name}. Run python3 scripts/chunk.py first.", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    clock = time.monotonic()
    api = Anthropic(key, args.model, args.dry_run)

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
                f"==> extracting {plural(len(todo), 'chunk')} with {api.model}, "
                f"{args.concurrency} at a time"
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
    print(f"\nDone in {time.monotonic() - clock:.1f}s. {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
