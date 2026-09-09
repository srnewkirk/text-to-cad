"""Written STEP bytes are a function of model content, not heap layout.

OCCT's STEPCAFControl_Writer registers each styled product's presentation
graph (MDGPR + styled items + colours) by iterating an ADDRESS-hashed shape
map, so an assembly with two or more styled products serializes its style
section in heap-address order — byte-different files for identical models,
varying per process and even per call. cadgen's package keys and export
records are content hashes of these bytes, so every wobble orphaned a store
package and dirtied committed fixtures.

``_style_tail_plan`` computes a content-derived order for that tail after
transfer (the same post-transfer canonicalization contract as
``_renumber_nauo_ids``). This test builds the same nested, multi-product,
per-occurrence-colored assembly from scratch repeatedly — fresh allocations
every time, exactly what flips the map order — and demands identical bytes.

The plan is applied in one of two places — to the written FILE (the tail
records alone in the normal path, the whole file when the fast path does not
recognize the file's shape) or to the MODEL via ``ChangeOrder`` (quadratic, the
backstop for a tail whose numbers straddle a digit-width boundary). Those two
must agree BYTE for byte, not merely semantically: the bytes are the store key,
so a formatting difference between them would re-key every package in every
store. ``test_both_appliers_write_identical_bytes`` is what makes deleting
neither path safe.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")


def _build_assembly(*, transparent_part: bool = False):
    """A minimal shape that hits the nondeterministic path: nested group
    products whose parts carry per-occurrence colors — several MDGPRs.

    ``transparent_part`` gives ONE part an alpha color. That is not a variation
    for its own sake: an alpha color makes OCCT append
    SURFACE_STYLE_RENDERING_WITH_PROPERTIES + SURFACE_STYLE_TRANSPARENT to the
    style tail, and the canonicalization only reorders a tail it fully
    recognizes — so a family list missing those two types silently disables
    canonicalization for every model with a transparent part, which is exactly
    what happened."""
    from build123d import Color, Compound
    from build123d.topology import Solid

    groups = []
    for group_index in range(3):
        parts = []
        for part_index in range(2):
            part = Solid.make_box(4 + group_index, 3 + part_index, 2)
            part = part.moved(part.location)  # fresh wrapper, shared TShape semantics
            part.label = f"part_{group_index}_{part_index}"
            if transparent_part and group_index == 0 and part_index == 0:
                part.color = Color(0.2, 0.9, 0.5, 0.4)
            else:
                part.color = Color(0.2 + 0.3 * group_index, 0.9 - 0.4 * part_index, 0.5)
            parts.append(part)
        group = Compound(children=parts)
        group.label = f"group_{group_index}"
        groups.append(group)
    root = Compound(children=groups)
    root.label = "determinism_rig"
    return root


class StepWriteDeterminismTest(unittest.TestCase):
    def test_repeated_fresh_builds_write_identical_bytes(self) -> None:
        from cadgen.step_export import export_build123d_step_file

        with tempfile.TemporaryDirectory(prefix="step-determinism-") as tmp:
            digests = set()
            for run in range(3):
                out = Path(tmp) / f"run{run}.step"
                export_build123d_step_file(_build_assembly(), out)
                digests.add(hashlib.sha256(out.read_bytes()).hexdigest())
            self.assertEqual(
                len(digests), 1,
                f"identical models wrote {len(digests)} distinct byte streams: {sorted(digests)}",
            )

    def test_transparent_part_still_writes_identical_bytes(self) -> None:
        """One alpha color must not switch canonicalization back off."""
        from cadgen.step_export import export_build123d_step_file

        with tempfile.TemporaryDirectory(prefix="step-determinism-alpha-") as tmp:
            digests = set()
            for run in range(4):
                out = Path(tmp) / f"run{run}.step"
                export_build123d_step_file(
                    _build_assembly(transparent_part=True), out
                )
                digests.add(hashlib.sha256(out.read_bytes()).hexdigest())
            self.assertEqual(
                len(digests), 1,
                "a model with one transparent part wrote "
                f"{len(digests)} distinct byte streams: {sorted(digests)}",
            )

    def test_transparent_part_emits_the_rendering_tail(self) -> None:
        """Guard the premise of the test above: if OCCT ever stops emitting
        these entities the alpha case would pass for the wrong reason."""
        from cadgen.step_export import export_build123d_step_file

        with tempfile.TemporaryDirectory(prefix="step-determinism-alpha-") as tmp:
            out = Path(tmp) / "alpha.step"
            export_build123d_step_file(_build_assembly(transparent_part=True), out)
            text = out.read_text(errors="replace")
            self.assertIn("SURFACE_STYLE_RENDERING_WITH_PROPERTIES(", text)
            self.assertIn("SURFACE_STYLE_TRANSPARENT(", text)

    def test_the_written_file_never_carries_platform_line_endings(self) -> None:
        """The written bytes are the store key, so they must not depend on the OS.

        The style-tail canonicalization reads the file OCCT wrote and writes it
        back. Doing that through ``Path.read_text``/``Path.write_text`` used the
        default universal-newline translation, which on Windows expanded every
        "\\n" OCCT emitted into "\\r\\n" -- so one model keyed two different
        packages depending on which machine built it, and the text applier
        disagreed byte-for-byte with the in-model applier (which never touches
        the file). Runs on every platform and only ever failed on one, which is
        the point: the assertion IS the cross-platform contract.
        """
        from cadgen.step_export import export_build123d_step_file

        with tempfile.TemporaryDirectory(prefix="step-eol-") as tmp:
            out = Path(tmp) / "eol.step"
            export_build123d_step_file(_build_assembly(), out)
            self.assertNotIn(b"\r\n", out.read_bytes())

    def _write_with(self, applier: str, path: Path, **rig_kwargs) -> bytes:
        """Export the rig with the style-tail permutation applied in `text` or
        in `model`."""
        import os

        from cadgen.step_export import export_build123d_step_file

        previous = os.environ.get("CADGEN_STEP_STYLE_REORDER")
        if applier == "model":
            os.environ["CADGEN_STEP_STYLE_REORDER"] = "model"
        else:
            os.environ.pop("CADGEN_STEP_STYLE_REORDER", None)
        try:
            export_build123d_step_file(_build_assembly(**rig_kwargs), path)
        finally:
            if previous is None:
                os.environ.pop("CADGEN_STEP_STYLE_REORDER", None)
            else:
                os.environ["CADGEN_STEP_STYLE_REORDER"] = previous
        return path.read_bytes()

    def test_both_appliers_write_identical_bytes(self) -> None:
        """The fast text path and the quadratic model path are the same file.

        This is the gate on the text rewrite: the written bytes are the
        content-addressed store key, so "equivalent STEP" is not good enough —
        a different line wrap would orphan every package built before it.
        """
        with tempfile.TemporaryDirectory(prefix="step-appliers-") as tmp:
            for label, rig_kwargs in (
                ("opaque", {}),
                ("transparent", {"transparent_part": True}),
            ):
                with self.subTest(rig=label):
                    in_text = self._write_with(
                        "text", Path(tmp) / f"{label}-text.step", **rig_kwargs
                    )
                    in_model = self._write_with(
                        "model", Path(tmp) / f"{label}-model.step", **rig_kwargs
                    )
                    self.assertEqual(
                        hashlib.sha256(in_text).hexdigest(),
                        hashlib.sha256(in_model).hexdigest(),
                        "text and model appliers disagree on the written bytes",
                    )

    def test_the_appliers_actually_reorder_something(self) -> None:
        """Control for the test above: both appliers must be doing work.

        If the rig ever stopped producing a permuted tail, the equality test
        would pass by comparing two untouched files. ``_style_tail_order`` is
        the one step both routes share, so its result is what gets captured."""
        import cadgen.step_export as step_export

        with tempfile.TemporaryDirectory(prefix="step-appliers-") as tmp:
            orders: list = []
            original = step_export._style_tail_order

            def capture(scan, targets):
                order = original(scan, targets)
                orders.append((scan, targets, order))
                return order

            step_export._style_tail_order = capture
            try:
                self._write_with("text", Path(tmp) / "probe.step")
            finally:
                step_export._style_tail_order = original

            # The scan's coverage probe runs first with empty targets; the
            # last call is the real order, with the targets read from the file.
            self.assertTrue(orders, "no style tail order was made")
            scan, targets, old_numbers = orders[-1]
            self.assertTrue(
                any(targets.values()), "the in-file applier read no styled targets"
            )
            self.assertIsNotNone(old_numbers)
            self.assertGreater(len(old_numbers), 1, "tail must hold several entities")
            self.assertNotEqual(
                old_numbers, list(range(scan.tail_start, scan.total + 1)),
                "the rig's tail was already in canonical order — this fixture no "
                "longer exercises the reorder",
            )

    def test_unrecognized_file_shape_falls_back_to_the_whole_file_pass(self) -> None:
        """When the in-place applier refuses the written file, the writer runs
        the whole-file pass with the full model plan — and that must be the
        same bytes the normal path produces."""
        import cadgen.step_export as step_export

        with tempfile.TemporaryDirectory(prefix="step-fallback-") as tmp:
            expected = self._write_with("text", Path(tmp) / "normal.step")
            original = step_export._canonicalize_style_tail_in_file
            calls: list = []

            def refuse(path, scan):
                calls.append(path)
                return False

            step_export._canonicalize_style_tail_in_file = refuse
            try:
                fallback = self._write_with("text", Path(tmp) / "fallback.step")
            finally:
                step_export._canonicalize_style_tail_in_file = original
            self.assertEqual(len(calls), 1, "the in-file applier was not consulted")
            self.assertEqual(
                hashlib.sha256(fallback).hexdigest(),
                hashlib.sha256(expected).hexdigest(),
                "the rewrite after a refused file shape wrote different bytes",
            )

    def test_in_file_applier_refuses_a_reference_into_the_tail_from_outside(self) -> None:
        """The in-place applier rewrites only the tail records, which is exact
        only while nothing outside the tail spells a tail number. A file that
        does — here a part name — must be refused, not partially renumbered."""
        import cadgen.step_export as step_export

        with tempfile.TemporaryDirectory(prefix="step-guard-") as tmp:
            scans: list = []
            original = step_export._style_tail_scan

            def capture(model):
                scan = original(model)
                scans.append(scan)
                return scan

            step_export._style_tail_scan = capture
            try:
                out = Path(tmp) / "guarded.step"
                self._write_with("text", out)
            finally:
                step_export._style_tail_scan = original
            scan = scans[-1]
            self.assertIsNotNone(scan)
            canonical = out.read_bytes()
            # Already canonical: applying again is a no-op and is accepted.
            self.assertTrue(step_export._canonicalize_style_tail_in_file(out, scan))
            self.assertEqual(out.read_bytes(), canonical)
            # A pre-tail string that mentions a tail number is refused, even
            # though a reference regex would never have rewritten it: the
            # guard is deliberately conservative.
            tampered = canonical.replace(
                b"PRODUCT('determinism_rig'",
                b"PRODUCT('rig #%d'" % scan.tail_start,
                1,
            )
            self.assertNotEqual(tampered, canonical, "the rig's product name moved")
            out.write_bytes(tampered)
            self.assertFalse(step_export._canonicalize_style_tail_in_file(out, scan))
            self.assertEqual(out.read_bytes(), tampered, "a refused file must be untouched")

    def test_tail_reference_pattern_matches_exactly_the_tail_numbers(self) -> None:
        """The pre-tail guard is one compiled range pattern over the whole
        file; it must match every tail number (leading zeros included, since
        ``int()`` would map those into the tail too) and nothing else."""
        from cadgen.step_export import _tail_reference_pattern

        for tail_start, total in ((39957, 44756), (100, 999), (5, 5), (1000, 1000), (2960431, 3012345)):
            pattern = _tail_reference_pattern(tail_start, total)
            width = len(str(total))
            low = max(1, tail_start - 1500)
            for value in list(range(low, min(total, tail_start + 1500))) + list(range(max(low, total - 1500), total + 1500)):
                expected = tail_start <= value <= total
                for spelling in (b"#%d" % value, b"#00%d" % value):
                    self.assertEqual(
                        pattern.fullmatch(spelling) is not None, expected,
                        f"{spelling!r} against [{tail_start}, {total}]",
                    )
            self.assertIsNone(pattern.search(b"#%d0" % tail_start), "a longer number is not a tail reference")
            self.assertIsNotNone(pattern.search(b"(#%d,#%d)" % (tail_start - 1, total)))
            self.assertEqual(len(str(tail_start)), width, "fixture ranges are same-width")

    def test_styled_item_target_reads_the_third_parameter(self) -> None:
        from cadgen.step_export import _styled_item_target

        self.assertEqual(_styled_item_target(b"#39958 = STYLED_ITEM('color',(#39959),#196);\n"), 196)
        # OCCT wraps long records at a fixed column with an indented continuation.
        self.assertEqual(
            _styled_item_target(
                b"#39966 = OVER_RIDING_STYLED_ITEM('overriding color',(#39967),#196,#39958\n  );\n"
            ),
            196,
        )
        self.assertEqual(
            _styled_item_target(b"#7 = OVER_RIDING_STYLED_ITEM('a, (b) #9',(#1,\n  #2),\n  #44678,#3);\n"),
            44678,
        )
        self.assertIsNone(_styled_item_target(b"#7 = STYLED_ITEM('',(#1),$);\n"))
        self.assertIsNone(_styled_item_target(b"#7 = STYLED_ITEM('',(#1));\n"))

    def test_canonicalization_is_a_pure_reorder(self) -> None:
        """The canonical file must carry the same entity population — sorted
        entity RECORD bodies (numbers stripped) are invariant across runs even
        without canonicalization, so equality here plus byte-equality above
        means reordering, not rewriting."""
        from cadgen.step_export import export_build123d_step_file

        import re

        with tempfile.TemporaryDirectory(prefix="step-determinism-") as tmp:
            out = Path(tmp) / "one.step"
            export_build123d_step_file(_build_assembly(), out)
            text = out.read_text()
            styled = re.findall(r"= (?:OVER_RIDING_)?STYLED_ITEM\(", text)
            colours = re.findall(r"COLOUR_RGB\('',([^)]*)\)", text)
            self.assertGreaterEqual(len(styled), 6, "rig must exercise the styled path")
            self.assertEqual(len(set(colours)), 6, "all six authored colors survive")


if __name__ == "__main__":
    unittest.main()
