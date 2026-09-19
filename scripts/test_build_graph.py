#!/usr/bin/env python3
"""Fixture tests for build_graph.py: the normalisation, the guards that stop a
merge running away, and the promise that the same extractions produce the same
bytes. No corpus, no network, stdlib only.

Run: python3 scripts/test_build_graph.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_graph  # noqa: E402


def entity(name: str, kind: str = "organisation", **overrides: object) -> dict:
    row = {"name": name, "type": kind, "description": f"{name}.", "aliases": []}
    row.update(overrides)
    return row


def relation(source: str, target: str, **overrides: object) -> dict:
    row = {"source": source, "target": target, "relation": "built", "evidence": "in the passage"}
    row.update(overrides)
    return row


def record(chunk: str, entities: list[dict], relations: list[dict] | None = None, **overrides: object) -> dict:
    row = {
        "chunk": chunk,
        "model": "test-model",
        "status": "ok",
        "stop_reason": "tool_use",
        "extraction": {"entities": entities, "relations": relations or []},
        "repairs": {},
    }
    row.update(overrides)
    return row


class Normalisation(unittest.TestCase):
    """The key two names have to share to become one node."""

    def test_case_punctuation_and_articles_fall_away(self) -> None:
        self.assertEqual(build_graph.normalise("The Genesis Project."), "genesis project")
        self.assertEqual(build_graph.normalise("GENESIS  project"), "genesis project")

    def test_a_hyphen_and_a_space_are_the_same_thing(self) -> None:
        self.assertEqual(
            build_graph.normalise("Mars-Polar-Lander"), build_graph.normalise("Mars Polar Lander")
        )
        self.assertEqual(build_graph.normalise("N-PRIME"), build_graph.normalise("N PRIME"))

    def test_a_corporate_suffix_is_not_part_of_the_name(self) -> None:
        self.assertEqual(
            build_graph.normalise("Lockheed Martin Astronautics, Inc."),
            build_graph.normalise("Lockheed Martin Astronautics"),
        )

    def test_accents_fold(self) -> None:
        self.assertEqual(build_graph.normalise("Ariáne"), build_graph.normalise("Ariane"))

    def test_an_article_in_the_middle_stays(self) -> None:
        self.assertEqual(build_graph.normalise("Report on the Loss"), "report on the loss")


class Guards(unittest.TestCase):
    """Which keys are allowed to pull two mentions together."""

    def test_a_generic_word_never_merges(self) -> None:
        for word in ("board", "the Board", "project", "spacecraft", "team", "the contractor"):
            self.assertFalse(
                build_graph.mergeable(build_graph.normalise(word)), f"{word!r} must not merge"
            )

    def test_a_very_short_alias_never_merges(self) -> None:
        self.assertFalse(build_graph.mergeable(build_graph.normalise("DS")))
        self.assertFalse(build_graph.mergeable(build_graph.normalise("2")))

    def test_a_real_acronym_does_merge(self) -> None:
        self.assertTrue(build_graph.mergeable(build_graph.normalise("LMA")))
        self.assertTrue(build_graph.mergeable(build_graph.normalise("MCO")))


class Building(unittest.TestCase):
    def build(self, records: list[dict]) -> dict:
        mentions, relations, _ = build_graph.collect(records)
        graph, _ = build_graph.build(mentions, relations)
        return graph

    def node(self, graph: dict, name: str) -> dict:
        found = [node for node in graph["nodes"] if node["name"] == name]
        self.assertEqual(len(found), 1, f"expected one node named {name!r}, got {len(found)}")
        return found[0]

    def test_an_alias_merges_two_mentions_into_one_node(self) -> None:
        graph = self.build(
            [
                record("a:0000", [entity("Lockheed Martin Astronautics", aliases=["LMA"])]),
                record("a:0001", [entity("LMA")]),
                record("b:0002", [entity("Lockheed Martin Astronautics")]),
            ]
        )
        self.assertEqual(len(graph["nodes"]), 1)
        node = graph["nodes"][0]
        self.assertEqual(node["name"], "Lockheed Martin Astronautics")
        self.assertIn("LMA", node["aliases"])
        self.assertEqual(node["documents"], ["a", "b"])
        self.assertEqual(len(graph["node_chunks"]), 3)

    def test_a_generic_alias_does_not_drag_two_boards_together(self) -> None:
        graph = self.build(
            [
                record("a:0000", [entity("First Mishap Investigation Board", "board", aliases=["the Board"])]),
                record("b:0000", [entity("Second Mishap Investigation Board", "board", aliases=["the Board"])]),
            ]
        )
        self.assertEqual(len(graph["nodes"]), 2)

    def test_a_generic_name_can_still_be_a_node(self) -> None:
        graph = self.build([record("a:0000", [entity("Board", "board")])])
        self.assertEqual(len(graph["nodes"]), 1)

    def test_the_most_frequent_surface_form_names_the_node(self) -> None:
        graph = self.build(
            [
                record("a:0000", [entity("Mars Climate Orbiter", "spacecraft", aliases=["MCO"])]),
                record("a:0001", [entity("Mars Climate Orbiter", "spacecraft")]),
                record("a:0002", [entity("MCO", "spacecraft")]),
            ]
        )
        self.assertEqual(graph["nodes"][0]["name"], "Mars Climate Orbiter")
        self.assertEqual(graph["nodes"][0]["id"], "mars-climate-orbiter")

    def test_the_most_frequent_type_wins_a_conflict(self) -> None:
        graph = self.build(
            [
                record("a:0000", [entity("Genesis", "mission")]),
                record("a:0001", [entity("Genesis", "spacecraft")]),
                record("a:0002", [entity("Genesis", "mission")]),
            ]
        )
        self.assertEqual(graph["nodes"][0]["type"], "mission")

    def test_the_longest_description_is_kept_and_capped(self) -> None:
        graph = self.build(
            [
                record("a:0000", [entity("Thing", description="Short.")]),
                record("a:0001", [entity("Thing", description="x" * 400)]),
            ]
        )
        self.assertEqual(len(graph["nodes"][0]["description"]), build_graph.MAX_DESCRIPTION)

    def test_an_edge_keeps_the_chunk_that_stated_it(self) -> None:
        graph = self.build(
            [
                record(
                    "a:0000",
                    [entity("Builder"), entity("Entry Vehicle", "spacecraft")],
                    [relation("Builder", "Entry Vehicle")],
                )
            ]
        )
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["chunk_id"], "a:0000")
        self.assertEqual(graph["edges"][0]["source"], "builder")
        self.assertEqual(graph["edges"][0]["target"], "entry-vehicle")

    def test_the_same_edge_in_the_same_chunk_is_stored_once(self) -> None:
        pair = [entity("Builder"), entity("Entry Vehicle", "spacecraft")]
        graph = self.build(
            [
                record("a:0000", pair, [relation("Builder", "Entry Vehicle")] * 2),
                record("a:0001", pair, [relation("Builder", "Entry Vehicle")]),
            ]
        )
        self.assertEqual(len(graph["edges"]), 2, "once per chunk, because the chunk is the evidence")
        self.assertEqual({edge["chunk_id"] for edge in graph["edges"]}, {"a:0000", "a:0001"})

    def test_a_relation_whose_ends_merged_is_not_an_edge(self) -> None:
        graph = self.build(
            [
                record(
                    "a:0000",
                    [entity("Lockheed Martin Astronautics", aliases=["LMA"]), entity("LMA")],
                    [relation("Lockheed Martin Astronautics", "LMA")],
                )
            ]
        )
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(graph["edges"], [])

    def test_a_failed_chunk_contributes_nothing(self) -> None:
        graph = self.build(
            [
                record("a:0000", [], status="failed"),
                record("a:0001", [entity("Thing")]),
            ]
        )
        self.assertEqual(len(graph["nodes"]), 1)

    def test_two_nodes_that_slug_the_same_still_get_their_own_id(self) -> None:
        graph = self.build(
            [
                record("a:0000", [entity("Re-entry")]),
                record("a:0001", [entity("Re entry!")]),
            ]
        )
        ids = sorted(node["id"] for node in graph["nodes"])
        self.assertEqual(len(set(ids)), len(ids))


class Determinism(unittest.TestCase):
    """Same extractions in, byte-identical graph.json out."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.records = [
            record(
                "a:0000",
                [entity("Lockheed Martin Astronautics", aliases=["LMA"]), entity("Orbiter", "spacecraft")],
                [relation("Lockheed Martin Astronautics", "Orbiter")],
            ),
            record("b:0000", [entity("LMA"), entity("Board", "board")]),
            record("b:0001", [entity("lockheed martin astronautics, inc.")]),
        ]

    def write(self, records: list[dict], name: str) -> Path:
        path = self.root / name
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8"
        )
        return path

    def build_to(self, records: list[dict], name: str) -> bytes:
        source = self.write(records, f"{name}.jsonl")
        out = self.root / f"{name}.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build_graph.main(["--extractions", str(source), "--out", str(out)]), 0)
        return out.read_bytes()

    def test_the_same_input_produces_the_same_bytes(self) -> None:
        self.assertEqual(self.build_to(self.records, "first"), self.build_to(self.records, "second"))

    def test_the_order_the_lines_were_written_in_does_not_matter(self) -> None:
        forwards = self.build_to(self.records, "forwards")
        backwards = self.build_to(list(reversed(self.records)), "backwards")
        self.assertEqual(forwards, backwards)

    def test_a_rerun_of_one_chunk_replaces_its_earlier_line(self) -> None:
        corrected = [*self.records, record("b:0000", [entity("LMA")])]
        graph = json.loads(self.build_to(corrected, "corrected").decode("utf-8"))
        self.assertNotIn("Board", [node["name"] for node in graph["nodes"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
