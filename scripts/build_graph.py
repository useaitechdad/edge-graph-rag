#!/usr/bin/env python3
"""Turn the per-chunk extractions into one graph: corpus/graph.json.

Offline and deterministic. Reads runs/tmp/extractions.jsonl — what
`scripts/extract.py` paid for — and writes resolved `nodes`, `edges` and
`node_chunks`, the three tables migrations/0001_init.sql defines.
`scripts/load_graph.py` is what puts them in D1.

The whole job is entity resolution: the extractor sees one passage at a time, so
the same organisation arrives as "Lockheed Martin Astronautics", "Lockheed Martin
Astronautics (LMA)" and "LMA" from three different pages, and unless those become
one node there is no graph — just seven disconnected documents with vocabulary.

The resolution is deliberately simple, so that what it did can be read off the
screen and argued with:

  1. A normalised key per name: casefold, accents folded, hyphens and slashes to
     spaces, punctuation dropped, leading articles and trailing corporate
     suffixes removed, whitespace collapsed. Two names with the same key are the
     same node.
  2. Alias merging, with union-find: if one mention lists the other's name as an
     alias, or two mentions share an alias, their keys join.
  3. Guards, because alias merging is where a resolver runs amok. A key of two
     characters or fewer, or a generic word the reports use for everything —
     board, project, spacecraft, team, contractor — never causes a merge. It can
     still be a node; it just cannot drag another node into itself.

Same extractions.jsonl in, byte-identical graph.json out: every set is sorted
before it is written, and nothing in the file is a timestamp.

Dependencies: Python standard library only.

Usage:
    python3 scripts/build_graph.py
    python3 scripts/build_graph.py --extractions runs/tmp/extractions.jsonl --out corpus/graph.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract  # noqa: E402  — the schema and the repair rule belong to the extractor

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTIONS = REPO_ROOT / "runs" / "tmp" / "extractions.jsonl"
GRAPH = REPO_ROOT / "corpus" / "graph.json"

# A description is a label on a node, not a summary of it.
MAX_DESCRIPTION = 300
# Node ids are a primary key in D1 and a thing to read in a query plan.
MAX_ID = 72

SEPARATORS = re.compile(r"[-‐-―_/\\]+")
NOT_WORD = re.compile(r"[^\w\s]", re.UNICODE)
NOT_SLUG = re.compile(r"[^a-z0-9]+")

ARTICLES = {"the", "a", "an"}
SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "llc",
    "ltd",
    "limited",
    "plc",
    "gmbh",
    "ag",
    "nv",
    "sa",
    "lp",
    "llp",
}

# Words these reports use for a different thing on every page. A mention may be
# named one of these; it may never merge another mention by way of one. Without
# this guard a single passage that calls its subject "the Board" welds every
# investigation board in the corpus into one node, and the cross-document count —
# the number this whole milestone exists to produce — becomes a lie.
GENERIC = {
    "board",
    "project",
    "program",
    "programme",
    "spacecraft",
    "team",
    "contractor",
    "subcontractor",
    "mission",
    "lander",
    "orbiter",
    "probe",
    "vehicle",
    "system",
    "subsystem",
    "component",
    "instrument",
    "software",
    "hardware",
    "report",
    "panel",
    "committee",
    "review",
    "office",
    "agency",
    "center",
    "centre",
    "group",
    "laboratory",
    "lab",
    "management",
    "engineering",
    "operations",
    "staff",
    "project office",
    "project team",
    "review board",
    "investigation board",
    "mishap investigation board",
    "principal investigator",
    "root cause",
    "launch vehicle",
    "ground system",
    "flight software",
}


def fold(text: str) -> str:
    """Accents off, so a name typed two ways compares as one."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def normalise(name: str) -> str:
    """The key two names have to share to be the same node."""
    text = NOT_WORD.sub(" ", SEPARATORS.sub(" ", fold(name).casefold()))
    words = text.split()
    while words and words[0] in ARTICLES:
        words.pop(0)
    while words and words[-1] in SUFFIXES:
        words.pop()
    return " ".join(words)


def mergeable(key: str) -> bool:
    """Whether a key is specific enough to pull two mentions together."""
    return len(key) > 2 and key not in GENERIC and not key.isdigit()


def slug(name: str) -> str:
    return NOT_SLUG.sub("-", fold(name).casefold()).strip("-")[:MAX_ID].strip("-") or "node"


class Union:
    """Union-find over normalised keys, by size, with a deterministic root."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.merges = 0

    def add(self, key: str) -> None:
        self.parent.setdefault(key, key)

    def find(self, key: str) -> str:
        self.add(key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, left: str, right: str) -> None:
        first, second = self.find(left), self.find(right)
        if first == second:
            return
        # The smaller key becomes the root: which one is arbitrary, but it has to
        # be the same one every run, or the output stops being byte-identical.
        low, high = sorted((first, second))
        self.parent[high] = low
        self.merges += 1


def read_extractions(path: Path) -> list[dict]:
    """One record per chunk, the last line for a chunk id winning.

    `extract.py --only` reruns a chunk and appends a second line for it; taking
    the last is what makes that a correction rather than a duplicate.
    """
    if not path.exists():
        raise SystemExit(f"No {path}. Run ./scripts/extract.sh first.")
    latest: dict[str, dict] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                latest[record["chunk"]] = record
    return [latest[chunk] for chunk in sorted(latest)]


def document_of(chunk_id: str) -> str:
    """The slug of the document a chunk came from. scripts/chunk.py builds chunk
    ids as `<document slug>:<ordinal>`, and a slug never contains a colon."""
    return chunk_id.rsplit(":", 1)[0]


class Mention:
    """One entity as one passage named it."""

    __slots__ = ("name", "type", "description", "aliases", "chunk", "key")

    def __init__(self, entity: dict, chunk: str) -> None:
        self.name = entity["name"]
        self.type = entity["type"]
        self.description = entity["description"]
        self.aliases = entity["aliases"]
        self.chunk = chunk
        self.key = normalise(self.name)


def collect(records: list[dict]) -> tuple[list[Mention], list[dict], Counter]:
    """Every mention and every relation the extractions hold, repaired."""
    mentions: list[Mention] = []
    relations: list[dict] = []
    counts: Counter[str] = Counter()
    for record in records:
        counts[record.get("status", "ok")] += 1
        if record.get("status") == "failed":
            continue
        clean, repairs = extract.repair(record.get("extraction") or {})
        for key, value in repairs.items():
            counts[key] += value
        chunk = record["chunk"]
        for entity in clean["entities"]:
            mention = Mention(entity, chunk)
            if mention.key:
                mentions.append(mention)
            else:
                counts["unnameable_entities"] += 1
        for relation in clean["relations"]:
            relations.append({**relation, "chunk": chunk})
    return mentions, relations, counts


def resolve(mentions: list[Mention]) -> tuple[Union, dict[str, list[Mention]]]:
    """Group the mentions that are the same thing. Key -> group root."""
    union = Union()
    for mention in mentions:
        union.add(mention.key)
    for mention in mentions:
        if not mergeable(mention.key):
            continue
        for alias in mention.aliases:
            alias_key = normalise(alias)
            if alias_key and alias_key != mention.key and mergeable(alias_key):
                union.union(mention.key, alias_key)
    groups: dict[str, list[Mention]] = defaultdict(list)
    for mention in mentions:
        groups[union.find(mention.key)].append(mention)
    return union, groups


def pick_name(group: list[Mention]) -> str:
    """The surface form the corpus used most. Ties go to the alphabet, so that
    the same extractions always name the node the same way."""
    counts = Counter(mention.name for mention in group)
    return min(counts, key=lambda name: (-counts[name], name))


def pick_type(group: list[Mention]) -> str:
    """The most frequent type. A tie goes to whichever comes first in the enum,
    which runs from the most concrete kind of entity to the least."""
    counts = Counter(mention.type for mention in group)
    return min(counts, key=lambda kind: (-counts[kind], extract.ENTITY_TYPES.index(kind)))


def pick_description(group: list[Mention]) -> str:
    """The longest one seen: the extractor writes one sentence per passage, and
    the longest is usually the passage that said the most about the entity."""
    descriptions = sorted({mention.description for mention in group if mention.description})
    if not descriptions:
        return ""
    # Sorted first, so max() returning the first of the equally long ones makes
    # the tie-break alphabetical rather than a property of the input order.
    return max(descriptions, key=len)[:MAX_DESCRIPTION].strip()


def build(mentions: list[Mention], relations: list[dict]) -> tuple[dict, dict[str, str]]:
    """The graph, and the key -> node id map the edges are resolved through."""
    union, groups = resolve(mentions)

    nodes: list[dict] = []
    by_key: dict[str, str] = {}
    taken: set[str] = set()
    merged: list[dict] = []
    for root in sorted(groups):
        group = groups[root]
        name = pick_name(group)
        keys = sorted({mention.key for mention in group})
        if len(keys) > 1:
            # A group built from more than one normalised key is a merge that
            # actually happened, as opposed to an alias that was only recorded.
            merged.append({"name": name, "names": sorted({mention.name for mention in group}), "keys": keys})
        node_id = slug(name)
        if node_id in taken:
            ordinal = 2
            while f"{node_id}-{ordinal}" in taken:
                ordinal += 1
            node_id = f"{node_id}-{ordinal}"
        taken.add(node_id)
        chunks = sorted({mention.chunk for mention in group})
        surfaces = {mention.name for mention in group} - {name}
        aliases = surfaces | {alias for mention in group for alias in mention.aliases}
        nodes.append(
            {
                "id": node_id,
                "name": name,
                "type": pick_type(group),
                "description": pick_description(group),
                "aliases": sorted(alias for alias in aliases if alias != name),
                "documents": sorted({document_of(chunk) for chunk in chunks}),
                "mentions": len(group),
                "chunks": chunks,
            }
        )
        for mention in group:
            by_key[mention.key] = node_id

    edges: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    dropped = 0
    for relation in relations:
        source = by_key.get(normalise(relation["source"]))
        target = by_key.get(normalise(relation["target"]))
        if not source or not target or source == target:
            # Endpoints that resolved to the same node are not an error: two
            # names in one passage that turned out to be one thing.
            dropped += 1
            continue
        identity = (source, target, relation["relation"], relation["chunk"])
        if identity in seen:
            continue
        seen.add(identity)
        edges.append(
            {
                "id": hashlib.sha256("|".join(identity).encode("utf-8")).hexdigest()[:16],
                "source": source,
                "target": target,
                "relation": relation["relation"],
                "chunk_id": relation["chunk"],
            }
        )
    edges.sort(key=lambda edge: (edge["source"], edge["target"], edge["relation"], edge["chunk_id"]))

    node_chunks = sorted(
        {(node["id"], chunk) for node in nodes for chunk in node["chunks"]}
    )
    graph = {
        "source": "runs/tmp/extractions.jsonl",
        "nodes": nodes,
        "edges": edges,
        "node_chunks": [{"node_id": node, "chunk_id": chunk} for node, chunk in node_chunks],
    }
    merged.sort(key=lambda group: (-len(group["keys"]), group["name"]))
    return graph, {"merges": union.merges, "collapsed_relations": dropped, "merged": merged}


def summarise(graph: dict, counts: Counter, stats: dict) -> None:
    """What the build did, for the terminal and for anyone watching it."""
    nodes = graph["nodes"]
    edges = graph["edges"]
    by_id = {node["id"]: node for node in nodes}

    print("\n==> nodes by type")
    types = Counter(node["type"] for node in nodes)
    for kind in extract.ENTITY_TYPES:
        if types[kind]:
            print(f"    {kind:<16} {types[kind]:>6}")

    degree: Counter[str] = Counter()
    for edge in edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1

    print("\n==> top 15 nodes by degree")
    print(f"    {'degree':>6}  {'docs':>4}  node")
    for node_id, count in sorted(degree.items(), key=lambda item: (-item[1], item[0]))[:15]:
        node = by_id[node_id]
        print(f"    {count:>6}  {len(node['documents']):>4}  {node['name']}  [{node['type']}]")

    merged = stats["merged"]
    print(f"\n==> {stats['merges']} merges: {len(merged)} nodes were assembled from more than one name")
    for group in merged[:10]:
        others = [name for name in group["names"] if name != group["name"]]
        print(f"    {group['name']}  <-  {', '.join(others[:6])}")

    crossing = [node for node in nodes if len(node["documents"]) > 1]
    crossing.sort(key=lambda node: (-len(node["documents"]), -node["mentions"], node["id"]))

    print("\n==> totals")
    print(f"    chunks read              {sum(counts[state] for state in ('ok', 'truncated', 'failed')):>6}")
    print(f"    nodes                    {len(nodes):>6}")
    print(f"    edges                    {len(edges):>6}")
    print(f"    node_chunks              {len(graph['node_chunks']):>6}")
    print(f"    nodes with no edge       {sum(1 for node in nodes if not degree[node['id']]):>6}")
    print(f"    relations that collapsed {stats['collapsed_relations']:>6}  (both ends resolved to one node)")

    # The number this milestone exists to produce, boxed so it is readable from
    # across a room: an entity that only ever appears in one report joins nothing.
    width = 70
    print()
    print("╔" + "═" * width + "╗")
    for line in (
        f"NODES THAT APPEAR IN MORE THAN ONE DOCUMENT: {len(crossing)} of {len(nodes)}",
        "the joins a single vector lookup has no way to make",
        "",
        *(f"  {len(node['documents'])} docs   {node['name']}  [{node['type']}]" for node in crossing[:10]),
    ):
        print("║ " + line[: width - 2].ljust(width - 2) + " ║")
    print("╚" + "═" * width + "╝")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--extractions", type=Path, default=EXTRACTIONS, help="the jsonl extract.py wrote")
    parser.add_argument("--out", type=Path, default=GRAPH, help="where to write the graph")
    parser.add_argument("--quiet", action="store_true", help="write the graph, print nothing but the totals")
    args = parser.parse_args(argv)

    records = read_extractions(args.extractions)
    mentions, relations, counts = collect(records)
    graph, stats = build(mentions, relations)

    payload = json.dumps(graph, indent="\t", ensure_ascii=False) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # newline="" => identical bytes on any platform.
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        handle.write(payload)

    if not args.quiet:
        summarise(graph, counts, stats)
    print(
        f"\nwrote {len(graph['nodes'])} nodes, {len(graph['edges'])} edges and "
        f"{len(graph['node_chunks'])} node_chunks to {args.out}"
    )
    if counts["failed"]:
        print(f"note: {counts['failed']} chunks failed extraction and contributed nothing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
