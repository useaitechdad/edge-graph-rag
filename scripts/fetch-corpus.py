#!/usr/bin/env python3
"""Deterministically rebuild the Graph RAG corpus from MANIFEST.json.

For every document in corpus/MANIFEST.json this script will:
  1. download <url> into corpus/raw/<slug>.pdf (skipped if the file is already
     present and already hashes correctly),
  2. verify the SHA-256 of the PDF against the manifest,
  3. re-extract the text layer into corpus/text/<slug>.txt with PyMuPDF,
     using exactly the page-marker convention the corpus was built with.

Exit status is non-zero on ANY hash mismatch, download failure or extraction
mismatch, so this doubles as an integrity check in CI.

Dependencies: Python standard library + PyMuPDF (``import fitz``).

Usage:
    python3 scripts/fetch-corpus.py            # fetch + verify + extract
    python3 scripts/fetch-corpus.py --check    # verify only, never download
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import fitz  # PyMuPDF

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "corpus"
MANIFEST = CORPUS / "MANIFEST.json"
RAW = CORPUS / "raw"
TEXT = CORPUS / "text"

# Page-break marker. A form feed, then a human-readable page line, then EOL.
# Keep this byte-identical forever: the manifest's `characters` field and every
# downstream chunk offset depend on it.
PAGE_MARKER = "\f--- page {n} ---\n"

USER_AGENT = "edge-graph-rag-corpus-fetcher/1.0 (+https://github.com/)"
TIMEOUT = 180


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_text(pdf_path: Path) -> str:
    """Extract the PDF text layer, one `\\f--- page N ---` marker per page.

    No OCR, no reflow, no normalisation: whatever PyMuPDF's default "text"
    extraction returns for the page is what lands in the file. Every page's
    block ends with exactly one newline so page N+1's marker starts a line.
    """
    doc = fitz.open(pdf_path)
    try:
        out: list[str] = []
        for index in range(doc.page_count):
            out.append(PAGE_MARKER.format(n=index + 1))
            page_text = doc[index].get_text()
            if not page_text.endswith("\n"):
                page_text += "\n"
            out.append(page_text)
        return "".join(out)
    finally:
        doc.close()


def write_text(path: Path, content: str) -> None:
    # newline="" => no platform translation, so the bytes are identical on any OS.
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(content)


def download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = response.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)


def process(doc: dict, check_only: bool) -> list[str]:
    """Return a list of error strings (empty means this document is fine)."""
    errors: list[str] = []
    slug = doc["slug"]
    pdf_path = RAW / f"{slug}.pdf"
    txt_path = TEXT / f"{slug}.txt"

    if not pdf_path.exists():
        if check_only:
            return [f"{slug}: missing {pdf_path} (--check will not download)"]
        print(f"  downloading {slug} <- {doc['url']}")
        try:
            download(doc["url"], pdf_path)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            return [f"{slug}: download failed: {exc}"]

    digest = sha256_file(pdf_path)
    if digest != doc["sha256"]:
        return [
            f"{slug}: SHA-256 MISMATCH\n"
            f"    expected {doc['sha256']}\n"
            f"    actual   {digest}\n"
            f"    file     {pdf_path}"
        ]

    text = extract_text(pdf_path)

    with fitz.open(pdf_path) as probe:
        pages = probe.page_count
    if pages != doc["pages"]:
        errors.append(f"{slug}: page count {pages} != manifest {doc['pages']}")
    if len(text) != doc["characters"]:
        errors.append(
            f"{slug}: extracted {len(text)} characters != manifest {doc['characters']}"
        )

    if check_only:
        if not txt_path.exists():
            errors.append(f"{slug}: missing {txt_path}")
        elif txt_path.read_text(encoding="utf-8") != text:
            errors.append(f"{slug}: {txt_path} differs from a fresh extraction")
    else:
        TEXT.mkdir(parents=True, exist_ok=True)
        write_text(txt_path, text)

    if not errors:
        print(f"  ok {slug}: {pages} pages, {len(text)} chars, sha256 verified")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify hashes and text files without downloading or rewriting",
    )
    args = parser.parse_args()

    if not MANIFEST.exists():
        print(f"fatal: no manifest at {MANIFEST}", file=sys.stderr)
        return 2

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    documents = manifest["documents"]
    RAW.mkdir(parents=True, exist_ok=True)
    TEXT.mkdir(parents=True, exist_ok=True)

    print(f"{len(documents)} documents in {MANIFEST}")
    failures: list[str] = []
    for doc in documents:
        failures.extend(process(doc, args.check))

    if failures:
        print("\nFAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"\nall {len(documents)} documents verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
