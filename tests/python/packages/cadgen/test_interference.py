"""cadgen.interference: pairwise part-vs-part clash detection."""

import unittest

from build123d import Box, Pos

from cadgen.interference import Occurrence, _shape_bbox, find_clashes


def occurrence(ref, name, shape):
    return Occurrence(ref=ref, name=name, shape=shape.wrapped, bbox=_shape_bbox(shape.wrapped))


class FindClashesTest(unittest.TestCase):
    def setUp(self):
        self.a = occurrence("o1.1", "block_a", Pos(0, 0, 0) * Box(100, 100, 100))
        # overlaps a by 20 mm across a 100x100 face -> 200000 mm^3
        self.overlapping = occurrence("o1.2", "block_b", Pos(80, 0, 0) * Box(100, 100, 100))
        self.far = occurrence("o1.3", "block_c", Pos(0, 400, 0) * Box(100, 100, 100))
        self.touching = occurrence("o1.4", "block_d", Pos(0, 100, 0) * Box(100, 100, 100))

    def test_reports_a_real_overlap_with_its_volume(self):
        clashes, _stats = find_clashes([self.a, self.overlapping])
        self.assertEqual(len(clashes), 1)
        self.assertAlmostEqual(clashes[0].volume, 200000.0, delta=1.0)
        self.assertEqual({clashes[0].a_ref, clashes[0].b_ref}, {"o1.1", "o1.2"})

    def test_separated_parts_are_rejected_by_bbox_without_a_boolean(self):
        clashes, stats = find_clashes([self.a, self.far])
        self.assertEqual(clashes, [])
        self.assertEqual(stats["pairs_skipped_bbox"], 1)
        self.assertEqual(stats["pairs_tested"], 0)

    def test_touching_faces_are_contact_not_a_clash(self):
        # Neighbouring panels share a face by design; a sliver must not be a clash.
        clashes, stats = find_clashes([self.a, self.touching])
        self.assertEqual(clashes, [], "coincident faces are contact, not interpenetration")
        self.assertGreaterEqual(stats["pairs_tested"], 1, "and it must actually be tested")

    def test_tolerance_can_be_raised_to_ignore_small_overlaps(self):
        clashes, _stats = find_clashes([self.a, self.overlapping], tolerance=500000.0)
        self.assertEqual(clashes, [])

    def test_stats_distinguish_nothing_overlapped_from_nothing_tested(self):
        _clashes, stats = find_clashes([self.a, self.overlapping, self.far, self.touching])
        self.assertEqual(stats["occurrences"], 4)
        self.assertEqual(stats["pairs_total"], 6)
        self.assertGreater(stats["pairs_tested"], 0)

    def test_max_pairs_truncation_is_reported(self):
        _clashes, stats = find_clashes(
            [self.a, self.overlapping, self.touching], max_pairs=0
        )
        self.assertEqual(stats["pairs_tested"], 0)
        self.assertGreater(stats["pairs_truncated"], 0, "silent truncation would fake a pass")

    def test_clashes_are_sorted_worst_first(self):
        small = occurrence("o1.5", "small", Pos(0, 0, 98) * Box(100, 100, 100))
        clashes, _stats = find_clashes([self.a, self.overlapping, small])
        self.assertGreaterEqual(len(clashes), 2)
        volumes = [clash.volume for clash in clashes]
        self.assertEqual(volumes, sorted(volumes, reverse=True))


class InstanceTreeOccurrenceTest(unittest.TestCase):
    """A compound_from_instances assembly places instances at the OCCT level,
    so the in-memory scene's XCAF walk sees ONE childless leaf: a 26-instance
    arm reported 0 pairs tested and PASSed green. The scene now carries the
    explicit occurrence tree, and interference walks THAT — the same o1.N
    namespace `inspect refs` resolves."""

    def _scene(self):
        import tempfile
        from pathlib import Path

        from build123d import Location

        from cadgen.instances import compound_from_instances
        from cadgen.step_export import build_build123d_step_scene

        proto = Box(10, 10, 10)
        proto.label = "cube"
        compound = compound_from_instances(
            "pair",
            [
                (proto, Location((0, 0, 0)), "a"),
                (proto, Location((5, 0, 0)), "b"),  # 5 mm overlap with a
                (proto, Location((100, 0, 0)), "c"),
            ],
        )
        with tempfile.TemporaryDirectory(prefix="cadgen-interfere-") as tempdir:
            return build_build123d_step_scene(compound, Path(tempdir) / "pair.step")

    def test_scene_carries_the_instance_tree(self):
        scene = self._scene()
        self.assertIsNotNone(scene.instance_occurrence_tree)

    def test_instanced_assembly_flattens_to_placed_leaves(self):
        from cadgen.interference import occurrences_from_scene

        occurrences = occurrences_from_scene(self._scene())
        self.assertEqual(
            ["o1.1", "o1.2", "o1.3"], [occurrence.ref for occurrence in occurrences]
        )
        self.assertEqual(["a", "b", "c"], [occurrence.name for occurrence in occurrences])
        # Placements must be WORLD placements: c sits at x=100.
        self.assertAlmostEqual(occurrences[2].bbox[0], 95.0, delta=1e-6)

    def test_instanced_assembly_clash_is_found(self):
        from cadgen.interference import occurrences_from_scene

        occurrences = occurrences_from_scene(self._scene())
        clashes, stats = find_clashes(occurrences, tolerance=0.01)
        self.assertEqual(stats["occurrences"], 3)
        self.assertGreater(stats["pairs_tested"], 0, "zero pairs tested was the silent pass")
        self.assertEqual(len(clashes), 1)
        self.assertAlmostEqual(clashes[0].volume, 500.0, delta=1.0)
        self.assertEqual({clashes[0].a_ref, clashes[0].b_ref}, {"o1.1", "o1.2"})

    def test_label_rows_come_from_the_same_tree(self):
        from cadgen.interference import scene_label_rows

        rows = {row["id"]: row["name"] for row in scene_label_rows(self._scene())}
        self.assertEqual(rows.get("o1.2"), "b")


class PartAssignmentTest(unittest.TestCase):
    """The verdict's unit is the PART: a direct component of the selection's
    root. A vendor servo is a sub-assembly whose motor sits inside its case by
    construction, and a STEP document cannot tell that sub-assembly from an
    authored one — so the boundary is structural, and selection-relative."""

    def test_no_selection_makes_the_document_root_the_root(self):
        from cadgen.interference import selection_root

        bodies = [occurrence(ref, ref, Box(1, 1, 1)) for ref in ("o1.1.1", "o1.1.2", "o1.2")]
        self.assertEqual(selection_root([], bodies), "o1")

    def test_several_document_roots_make_each_root_a_part(self):
        from cadgen.interference import part_of, selection_root

        bodies = [occurrence(ref, ref, Box(1, 1, 1)) for ref in ("o1.1", "o2.1")]
        self.assertEqual(selection_root([], bodies), "")
        self.assertEqual(part_of("o1.1", ""), "o1")
        self.assertEqual(part_of("o2.1.3", ""), "o2")

    def test_one_named_ref_is_the_root_so_its_components_are_the_parts(self):
        from cadgen.interference import part_of, selection_root

        self.assertEqual(selection_root(["o1.7"], []), "o1.7")
        self.assertEqual(part_of("o1.7.1", "o1.7"), "o1.7.1")
        self.assertEqual(part_of("o1.7.2.4", "o1.7"), "o1.7.2")

    def test_several_named_refs_share_their_common_ancestor(self):
        from cadgen.interference import selection_root

        self.assertEqual(selection_root(["o1.3", "o1.9"], []), "o1")
        self.assertEqual(selection_root(["o1.7.1", "o1.7.2"], []), "o1.7")
        self.assertEqual(selection_root(["o1.7.1", "o1.7.1"], []), "o1.7.1")

    def test_bodies_map_to_the_component_of_the_root_above_them(self):
        from cadgen.interference import part_assignments

        bodies = [occurrence(ref, ref, Box(1, 1, 1)) for ref in ("o1.1.1", "o1.1.2", "o1.2", "o1.3.1.1")]
        self.assertEqual(
            part_assignments(bodies, "o1"),
            {"o1.1.1": "o1.1", "o1.1.2": "o1.1", "o1.2": "o1.2", "o1.3.1.1": "o1.3"},
        )
        # The root itself, or a body outside the root, is its own part.
        self.assertEqual(part_assignments(bodies, "o1.1")["o1.2"], "o1.2")


class IntraPartOverlapTest(unittest.TestCase):
    """Bodies of one part are still tested, but an overlap between them is
    tagged with the part and stays off the verdict."""

    def setUp(self):
        self.case = occurrence("o1.1.1", "case", Box(20, 20, 20))
        self.motor = occurrence("o1.1.2", "motor", Pos(0, 0, 5) * Box(10, 10, 20))
        self.bracket = occurrence("o1.2", "bracket", Pos(60, 0, 0) * Box(10, 10, 10))
        self.rival = occurrence("o1.3", "rival", Pos(5, 0, 0) * Box(20, 20, 20))
        self.parts = {"o1.1.1": "o1.1", "o1.1.2": "o1.1", "o1.2": "o1.2", "o1.3": "o1.3"}

    def test_an_overlap_inside_one_part_carries_the_part(self):
        clashes, stats = find_clashes([self.case, self.motor, self.bracket], tolerance=0.01, parts=self.parts)
        self.assertEqual(stats["parts"], 2)
        self.assertEqual(stats["pairs_intra_part"], 1)
        self.assertEqual([clash.part for clash in clashes], ["o1.1"])

    def test_a_cross_part_clash_carries_no_part(self):
        clashes, _stats = find_clashes([self.case, self.motor, self.rival], tolerance=0.01, parts=self.parts)
        cross = [clash for clash in clashes if clash.part is None]
        self.assertEqual(len(cross), 2, "the rival overlaps both of the servo's bodies")
        self.assertTrue(all({c.a_ref, c.b_ref} & {"o1.3"} for c in cross))

    def test_without_a_part_map_every_body_is_its_own_part(self):
        clashes, stats = find_clashes([self.case, self.motor], tolerance=0.01)
        self.assertEqual(stats["parts"], 2)
        self.assertEqual([clash.part for clash in clashes], [None])

    def test_the_pair_budget_is_spent_on_cross_part_pairs_first(self):
        # One boolean allowed: it must go to case x rival (can fail the check),
        # not to case x motor (cannot).
        clashes, stats = find_clashes([self.case, self.motor, self.rival], tolerance=0.01, parts=self.parts, max_pairs=1)
        self.assertEqual(stats["pairs_tested"], 1)
        self.assertEqual(stats["pairs_truncated"], 2)
        self.assertEqual([clash.part for clash in clashes], [None])


class PartVerdictOnDocumentTest(unittest.TestCase):
    """End to end on a STEP the way the CLI takes it — the DOCUMENT, whose XCAF
    tree is all `interfere` ever sees. A purchased part with two overlapping
    bodies and a clean neighbour must PASS; naming the part tests inside it."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path

        import build123d as bd

        cls._tmp = tempfile.TemporaryDirectory(prefix="cadgen-interfere-doc-")
        case = bd.Box(20, 20, 20)
        case.label = "case"
        motor = bd.Pos(0, 0, 5) * bd.Box(10, 10, 20)
        motor.label = "motor"
        servo = bd.Compound(children=[case, motor], label="servo")
        bracket = bd.Pos(60, 0, 0) * bd.Box(10, 10, 10)
        bracket.label = "bracket"
        root = bd.Compound(children=[servo, bracket], label="arm")
        cls.document = Path(cls._tmp.name) / "arm.step"
        bd.export_step(root, cls.document)
        # A document whose only top-level component is the servo: one part.
        cls.single = Path(cls._tmp.name) / "servo_only.step"
        bd.export_step(bd.Compound(children=[servo], label="rig"), cls.single)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _run(self, document, **kwargs):
        import os

        from cadgen.interference import inspect_interference

        # Target resolution anchors on the process cwd; the document is a
        # foreign path there, exactly as the CLI sees a vendor file.
        previous = os.getcwd()
        os.chdir(self._tmp.name)
        try:
            return inspect_interference(str(document), tolerance=0.01, **kwargs)
        finally:
            os.chdir(previous)

    def test_a_vendor_parts_own_bodies_do_not_fail_the_assembly(self):
        result = self._run(self.document)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["conclusive"])
        self.assertEqual(result["clashCount"], 0)
        self.assertEqual(result["intraPartOverlapCount"], 1)
        (overlap,) = result["intraPartOverlaps"]
        self.assertEqual(overlap["part"], {"ref": "o1.1", "name": "servo"})
        self.assertEqual({overlap["a"]["ref"], overlap["b"]["ref"]}, {"o1.1.1", "o1.1.2"})
        self.assertEqual(result["root"], {"ref": "o1", "name": "arm"})
        self.assertEqual(
            [(part["ref"], part["bodies"]) for part in result["parts"]], [("o1.1", 2), ("o1.2", 1)]
        )

    def test_naming_the_part_tests_its_bodies_against_each_other(self):
        result = self._run(self.document, refs=["o1.1"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["clashCount"], 1)
        self.assertEqual(result["intraPartOverlapCount"], 0)
        self.assertEqual(result["root"]["ref"], "o1.1")

    def test_naming_two_parts_compares_them_as_wholes(self):
        result = self._run(self.document, refs=["servo", "bracket"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["intraPartOverlapCount"], 1)

    def test_a_single_part_is_inconclusive_and_says_how_to_look_inside(self):
        result = self._run(self.single)
        self.assertFalse(result["ok"])
        self.assertFalse(result["conclusive"])
        self.assertIn("--refs o1.1", result["inconclusiveReason"])
        self.assertEqual(result["intraPartOverlapCount"], 1, "the overlap is still reported")


class InconclusiveVerdictTest(unittest.TestCase):
    """`pairs tested == 0` because fewer than two occurrences were selected is
    not a pass — inspect_interference reports conclusive=False and ok=False so
    text (INCONCLUSIVE) and exit codes cannot render it as green."""

    def test_inconclusive_text_never_says_pass(self):
        from cadgen.cli.step_inspect.cli import _format_interfere_text

        text = _format_interfere_text(
            {
                "ok": False,
                "entry": "tom",
                "tolerance": 0.01,
                "stats": {
                    "occurrences": 1,
                    "pairs_total": 0,
                    "pairs_tested": 0,
                    "pairs_skipped_bbox": 0,
                    "pairs_truncated": 0,
                },
                "conclusive": False,
                "inconclusiveReason": "the document presents 1 leaf occurrence(s); "
                "at least two are needed to test a pair",
                "clashCount": 0,
                "clashes": [],
                "errors": [],
            }
        )
        self.assertIn("INCONCLUSIVE", text)
        self.assertNotIn("PASS", text)

    def test_conclusive_clean_run_still_passes(self):
        from cadgen.cli.step_inspect.cli import _format_interfere_text

        text = _format_interfere_text(
            {
                "ok": True,
                "entry": "tom",
                "tolerance": 0.01,
                "stats": {
                    "occurrences": 2,
                    "pairs_total": 1,
                    "pairs_tested": 0,
                    "pairs_skipped_bbox": 1,
                    "pairs_truncated": 0,
                },
                "conclusive": True,
                "clashCount": 0,
                "clashes": [],
                "errors": [],
            }
        )
        self.assertIn("PASS", text)

    def test_intra_part_overlaps_are_summarised_per_part_under_a_pass(self):
        from cadgen.cli.step_inspect.cli import _format_interfere_text

        overlap = {
            "a": {"ref": "o1.18.1", "name": "ZK_122:1"},
            "b": {"ref": "o1.18.4", "name": "MOTOR-1723_3:1"},
            "volume": 2917.0,
            "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]},
            "part": {"ref": "o1.18", "name": "servo_18"},
        }
        text = _format_interfere_text(
            {
                "ok": True,
                "entry": "tom",
                "tolerance": 0.01,
                "root": {"ref": "o1", "name": "tom"},
                "parts": [],
                "stats": {
                    "occurrences": 76,
                    "parts": 26,
                    "pairs_total": 2850,
                    "pairs_intra_part": 300,
                    "pairs_tested": 188,
                    "pairs_skipped_bbox": 2662,
                    "pairs_truncated": 0,
                },
                "conclusive": True,
                "clashCount": 0,
                "clashes": [],
                "intraPartOverlapCount": 2,
                "intraPartOverlaps": [overlap, dict(overlap, volume=10.0)],
                "errors": [],
            }
        )
        self.assertIn("PASS", text)
        self.assertNotIn("FAIL", text)
        self.assertIn("parts     : 26 components of tom [o1]", text)
        self.assertIn("intra-part: 2 overlap(s)", text)
        self.assertIn("servo_18 [o1.18]: 2 overlap(s), largest 2917.0 mm^3", text)
        # Per-part summary, not a record per pair.
        self.assertNotIn("ZK_122:1", text)


if __name__ == "__main__":
    unittest.main()
