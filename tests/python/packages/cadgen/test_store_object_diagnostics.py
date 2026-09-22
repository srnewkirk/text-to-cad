"""Object-store failures name the failed stage and the cache location."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen.store import objects
from cadgen.store.paths import StoreUnwritableError, StoreWriteError


def denied() -> PermissionError:
    error = PermissionError(13, "Access is denied")
    error.winerror = 5
    return error


class ObjectStoreDiagnosticTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="cadgen-object-diagnostic-")
        self.addCleanup(self.tmp.cleanup)
        self.previous = os.environ.get("CADGEN_CACHE_DIR")
        os.environ["CADGEN_CACHE_DIR"] = str(Path(self.tmp.name) / "store")

        def restore() -> None:
            if self.previous is None:
                os.environ.pop("CADGEN_CACHE_DIR", None)
            else:
                os.environ["CADGEN_CACHE_DIR"] = self.previous

        self.addCleanup(restore)

    def assert_context(self, error: StoreWriteError, stage: str, target: Path) -> None:
        message = str(error)
        self.assertIn(f"during {stage}", message)
        self.assertIn(f"cache root={os.environ['CADGEN_CACHE_DIR']}", message)
        self.assertIn(f"target={target}", message)
        self.assertIn("errno=13", message)
        self.assertIn("winerror=5", message)

    def test_directory_creation_names_its_stage(self) -> None:
        target = Path(self.tmp.name) / "store" / "objects" / "ab"
        with mock.patch.object(Path, "mkdir", side_effect=denied()):
            with self.assertRaises(StoreUnwritableError) as raised:
                objects._mkdir(target)
        message = str(raised.exception)
        self.assertIn("during create object shard directory", message)
        self.assertIn(f"the store at {os.environ['CADGEN_CACHE_DIR']}", message)
        self.assertIn(str(target), message)
        self.assertIn("errno=13", message)
        self.assertIn("winerror=5", message)

    def test_temp_creation_names_its_stage(self) -> None:
        target = Path(self.tmp.name) / "object.tmp"
        with mock.patch("builtins.open", side_effect=denied()):
            with self.assertRaises(StoreWriteError) as raised:
                objects._write_temp(target, b"payload")
        self.assert_context(raised.exception, "create temporary object", target)

    def test_temp_write_names_its_stage(self) -> None:
        target = Path(self.tmp.name) / "object.tmp"
        handle = mock.MagicMock()
        handle.write.side_effect = denied()
        with mock.patch("builtins.open", return_value=handle):
            with self.assertRaises(StoreWriteError) as raised:
                objects._write_temp(target, b"payload")
        self.assert_context(raised.exception, "write temporary object", target)

    def test_copy_and_publish_name_their_distinct_stages(self) -> None:
        source = Path(self.tmp.name) / "source.step"
        temporary = Path(self.tmp.name) / "object.tmp"
        target = Path(self.tmp.name) / "store" / "objects" / "ab" / "digest"

        with mock.patch.object(objects.shutil, "copyfile", side_effect=denied()):
            with self.assertRaises(StoreWriteError) as copied:
                objects._copy_temp(source, temporary)
        self.assert_context(copied.exception, f"copy object source {source}", temporary)

        with mock.patch.object(objects, "replace_atomic", side_effect=denied()):
            with self.assertRaises(StoreWriteError) as published:
                objects._publish_temp(temporary, target)
        self.assert_context(
            published.exception, f"publish temporary object {temporary}", target
        )


if __name__ == "__main__":
    unittest.main()
