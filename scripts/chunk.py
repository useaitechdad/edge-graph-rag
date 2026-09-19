#!/usr/bin/env python3
"""Cut corpus/text/*.txt into the passages that get embedded, retrieved and quoted.

Reads the per-page text `scripts/fetch-corpus.py` extracts and writes
corpus/chunks.jsonl, one JSON object per line:

    {"id", "document", "page_start", "page_end", "ordinal", "text"}

The output is deterministic: the same corpus/text/ produces byte-identical
chunks.jsonl, so an ingest can be re-run, compared or resumed without the
chunk ids moving underneath it.

Two properties the rest of the pipeline leans on:

  * A chunk's text keeps every non-whitespace character of the source, in
    order, with runs of whitespace collapsed to one space. Whitespace-normalise
    a chunk and you get a *contiguous slice* of the whitespace-normalised page
    text — which is exactly the comparison eval/scoring.py makes, so a chunk can
    never half-match a gold quote for a reason the chunker invented.
  * Nothing is de-hyphenated, de-glued or otherwise repaired. `eval/validate.py`
    checks gold quotes against the raw extraction; a chunker that "fixed" the
    text would stop matching it.

The corpus is not uniform and the chunker must not care:
`mars-polar-lander-ds2-loss` is OCR with one phrase per line,
`noaa-n-prime-mishap` pages 1–21 are scanned images with no text layer at all,
and `genesis-mib-vol-i` carries soft hyphens inside words.

Dependencies: Python standard library only.

Usage:
    python3 scripts/chunk.py                 # corpus/text/ -> corpus/chunks.jsonl
    python3 scripts/chunk.py --out -         # write to stdout instead
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "corpus"
MANIFEST = CORPUS / "MANIFEST.json"
TEXT = CORPUS / "text"
CHUNKS = CORPUS / "chunks.jsonl"

# The marker scripts/fetch-corpus.py writes before each page.
PAGE_BREAK = re.compile(r"^\f--- page (\d+) ---$", re.MULTILINE)

# @cf/baai/bge-base-en-v1.5 accepts 512 input tokens and silently truncates past
# that. There is no offline tokenizer to measure against — the budget is spent in
# characters instead, conservatively: BERT WordPiece averages ~4 characters per
# token on clean English prose, and the worst text in this corpus (the OCR'd MPL
# report, which fragments words and glues others together) still measures above
# ~3 characters per token. 1400 characters is therefore ~470 tokens at that worst
# case and ~350 on the clean documents: under 512 with room to spare, and large
# enough that a chunk is still a passage a person would recognise.
MAX_CHARS = 1400

# Carried from the end of one chunk to the start of the next. eval/schema.md caps
# a gold quote at 400 normalised characters, so an overlap of 200 means any span
# of 400 characters or less is at least half-contained in one single chunk even
# when a boundary lands in the middle of it: the half before the boundary is
# >= 200 characters, or the whole span fits inside the carried tail. That is the
# 50% rule in eval/DESIGN.md, satisfied by construction rather than by luck.
OVERLAP_CHARS = 200

# Below this a chunk is a fragment rather than a passage. Only the packer's edges
# produce one — the tail of a segment, or a chunk flushed early to make room for
# a long sentence — and the fix is to start it further back, never to drop text.
MIN_CHARS = 300

# No single unit may be big enough that a fragment cannot absorb it. At this
# ceiling a chunk below MIN_CHARS can always swallow the unit before it and stay
# inside MAX_CHARS (299 + 2 separators + 1098 = 1399), which is what makes the
# minimum above a guarantee rather than an attempt. Only runaway OCR "sentences"
# ever reach it.
UNIT_MAX_CHARS = MAX_CHARS - MIN_CHARS - 2

# eval/DESIGN.md: these pages reprint the whole of mars-climate-orbiter-mib-phase-i.
# Indexed, every Phase I passage would exist twice and one copy would crowd the
# other out of the top k. A chunk never spans the gap either — page 57 and page
# 106 are not neighbours in any sense a reader would accept.
SKIP_PAGES = {"mco-mib-project-management": frozenset(range(58, 106))}

# A sentence end: terminal punctuation, then whitespace, then something that
# starts a sentence. Splitting too eagerly (on "U.S. Government") costs nothing —
# the pieces are packed straight back together — but splitting too rarely would
# push a boundary into the middle of a sentence.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")


class Unit:
    """The smallest thing a chunk boundary is allowed to fall between."""

    __slots__ = ("page", "text", "starts_paragraph")

    def __init__(self, page: int, text: str, starts_paragraph: bool) -> None:
        self.page = page
        self.text = text
        self.starts_paragraph = starts_paragraph


def split_pages(text: str) -> list[tuple[int, str]]:
    """[(page number, that page's text)], in page order."""
    pages: list[tuple[int, str]] = []
    matches = list(PAGE_BREAK.finditer(text))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        pages.append((int(match.group(1)), text[match.end():end]))
    return pages


def paragraphs(page_text: str) -> list[str]:
    """Re-join hard-wrapped lines into paragraphs, splitting on blank lines.

    Every line in a paragraph is joined with a single space and nothing else, so
    the result normalises to the same string the page does. A PDF extract wraps
    at the page width; the OCR'd MPL report wraps after almost every phrase.
    Both come back as prose here.
    """
    out: list[str] = []
    current: list[str] = []
    for line in page_text.split("\n"):
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        elif current:
            out.append(" ".join(current))
            current = []
    if current:
        out.append(" ".join(current))
    return out


def hard_split(text: str, limit: int) -> list[str]:
    """Break a too-long string on word boundaries, or mid-word if a single word
    is longer than the limit — the OCR of mars-program-independent-assessment
    glues whole clauses into one token."""
    pieces: list[str] = []
    current = ""
    for word in text.split(" "):
        while len(word) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(word[:limit])
            word = word[limit:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= limit:
            current = f"{current} {word}"
        else:
            pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces


def units_for(pages: list[tuple[int, str]]) -> list[Unit]:
    """Pages -> the sentence-sized units a chunk is assembled from."""
    units: list[Unit] = []
    for number, page_text in pages:
        for paragraph in paragraphs(page_text):
            first = True
            for sentence in SENTENCE_END.split(paragraph):
                if not sentence:
                    continue
                for piece in hard_split(sentence, UNIT_MAX_CHARS):
                    units.append(Unit(number, piece, first))
                    first = False
    return units


def segments(slug: str, pages: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Runs of consecutive kept pages. A skipped range ends a run, so no chunk
    is ever stitched across the hole it leaves."""
    skip = SKIP_PAGES.get(slug, frozenset())
    runs: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for number, page_text in pages:
        if number in skip:
            if current:
                runs.append(current)
                current = []
            continue
        current.append((number, page_text))
    if current:
        runs.append(current)
    return runs


def joined(units: list[Unit]) -> str:
    """Units back into readable text. A paragraph break is a blank line, which
    normalises to the same single space a line break does."""
    out: list[str] = []
    for i, unit in enumerate(units):
        if i:
            out.append("\n\n" if unit.starts_paragraph else " ")
        out.append(unit.text)
    return "".join(out)


class Ruler:
    """Costs a run of units without rebuilding its text.

    `joined` puts a blank line between paragraphs and a single space inside one,
    so the separator before a unit is two characters or one. Getting that wrong
    by a character is how a chunk quietly ends up over the token budget.
    """

    def __init__(self, units: list[Unit]) -> None:
        self.prefix = [0]
        for index, unit in enumerate(units):
            self.prefix.append(self.prefix[-1] + self.separator(units, index) + len(unit.text))

    @staticmethod
    def separator(units: list[Unit], index: int) -> int:
        if index == 0:
            return 0
        return 2 if units[index].starts_paragraph else 1

    def measure(self, units: list[Unit], start: int, end: int) -> int:
        """Characters in units[start:end] once joined — the leading separator of
        the first unit is not emitted, so it is not counted."""
        if end <= start:
            return 0
        return self.prefix[end] - self.prefix[start] - self.separator(units, start)


def overlap_start(units: list[Unit], ruler: Ruler, start: int, end: int) -> int:
    """Where the next chunk begins: far enough back to cover OVERLAP_CHARS,
    never back to `start` itself (that would stall), never more than half a
    chunk (a carried tail is context, not content)."""
    carried = end
    while carried - 1 > start:
        if ruler.measure(units, carried - 1, end) > MAX_CHARS // 2:
            break
        carried -= 1
        if ruler.measure(units, carried, end) >= OVERLAP_CHARS:
            break
    if ruler.measure(units, carried, end) > MAX_CHARS // 2:
        return end
    return carried


def pack(units: list[Unit]) -> list[tuple[int, int]]:
    """Greedily fill chunks up to MAX_CHARS, carrying an overlap between them.

    Returns half-open [start, end) ranges over `units`; consecutive ranges
    deliberately overlap.
    """
    ruler = Ruler(units)
    ranges: list[tuple[int, int]] = []
    start = 0
    fresh = 0  # units added since the last flush; a flush needs one, so it advances
    index = 0

    while index < len(units):
        while index > start and (
            ruler.measure(units, start, index + 1) > MAX_CHARS
        ):
            if fresh:
                ranges.append((start, index))
                start = overlap_start(units, ruler, start, index)
            else:
                # The carried tail alone leaves no room for this unit. The
                # overlap is a convenience, not the content: drop it.
                start = index
            fresh = 0
        index += 1
        fresh += 1

    if fresh:
        ranges.append((start, len(units)))

    # A chunk of a dozen characters — a page number stranded by a flush, or the
    # last line of a report — is not a passage anyone could answer from, and it
    # still costs a vector. Grow it backwards instead: more overlap, same text.
    for position, (begin, end) in enumerate(ranges):
        while begin > 0 and ruler.measure(units, begin, end) < MIN_CHARS:
            if ruler.measure(units, begin - 1, end) > MAX_CHARS:
                break
            begin -= 1
        ranges[position] = (begin, end)

    return ranges


def chunk_document(slug: str, text: str) -> list[dict]:
    """Every chunk of one document, in reading order, ordinals from 0."""
    chunks: list[dict] = []
    for run in segments(slug, split_pages(text)):
        units = units_for(run)
        for start, end in pack(units):
            span = units[start:end]
            ordinal = len(chunks)
            chunks.append(
                {
                    "id": f"{slug}:{ordinal:04d}",
                    "document": slug,
                    "page_start": min(unit.page for unit in span),
                    "page_end": max(unit.page for unit in span),
                    "ordinal": ordinal,
                    "text": joined(span),
                }
            )
    return chunks


def summarise(per_document: list[tuple[str, list[dict]]]) -> None:
    every: list[int] = []
    print(f"{'document':<38} {'chunks':>7} {'min':>6} {'median':>7} {'max':>6}")
    for slug, chunks in per_document:
        sizes = sorted(len(chunk["text"]) for chunk in chunks)
        every.extend(sizes)
        if not sizes:
            print(f"{slug:<38} {0:>7} {'-':>6} {'-':>7} {'-':>6}")
            continue
        print(
            f"{slug:<38} {len(sizes):>7} {sizes[0]:>6} "
            f"{round(statistics.median(sizes)):>7} {sizes[-1]:>6}"
        )
    if every:
        every.sort()
        print(
            f"{'ALL':<38} {len(every):>7} {every[0]:>6} "
            f"{round(statistics.median(every)):>7} {every[-1]:>6}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--text", type=Path, default=TEXT, help="corpus/text directory")
    parser.add_argument("--out", type=Path, default=CHUNKS, help="output path, or - for stdout")
    args = parser.parse_args(argv)

    if not MANIFEST.exists():
        print(f"fatal: no manifest at {MANIFEST}", file=sys.stderr)
        return 2
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    per_document: list[tuple[str, list[dict]]] = []
    for document in manifest["documents"]:
        slug = document["slug"]
        path = args.text / f"{slug}.txt"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"fatal: {exc}. Run python3 scripts/fetch-corpus.py first.", file=sys.stderr)
            return 2
        per_document.append((slug, chunk_document(slug, text)))

    lines = [
        json.dumps(chunk, ensure_ascii=False)
        for _, chunks in per_document
        for chunk in chunks
    ]
    payload = "".join(f"{line}\n" for line in lines)

    if str(args.out) == "-":
        sys.stdout.write(payload)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # newline="" => identical bytes on any platform.
        with args.out.open("w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
        summarise(per_document)
        print(f"\nwrote {len(lines)} chunks to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
