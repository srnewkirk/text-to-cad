from __future__ import annotations

import builtins
from pathlib import Path
import sys
import unittest
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen import snapshot_core


class SnapshotImportDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_missing_package_and_broken_dependencies_are_distinguished(self):
        failures = (
            (ModuleNotFoundError("No module named 'playwright'", name="playwright"), True),
            (ModuleNotFoundError("No module named 'greenlet'", name="greenlet"), False),
            (ImportError("DLL load failed while importing _greenlet: Access is denied."), False),
        )
        real_import = builtins.__import__
        for failure, missing_package in failures:
            with self.subTest(error=str(failure)):
                def fail_playwright(name, *args, **kwargs):
                    if name == "playwright.async_api":
                        raise failure
                    return real_import(name, *args, **kwargs)

                renderer = snapshot_core.BatchSnapshotRenderer(Path("unused-runtime"))
                with mock.patch("builtins.__import__", side_effect=fail_playwright), \
                     mock.patch.object(snapshot_core, "SnapshotAssetServer", side_effect=OSError), \
                     self.assertRaises(snapshot_core.SnapshotError) as caught:
                    await renderer.start()
                message = str(caught.exception)
                self.assertIs(caught.exception.__cause__, failure)
                self.assertFalse(renderer.started)
                self.assertIsNone(renderer.asset_server)
                if missing_package:
                    self.assertIn("requires the Python playwright package", message)
                else:
                    self.assertIn(str(failure), message)
                    self.assertIn(sys.executable, message)
                    self.assertNotIn("requires the Python playwright package", message)


if __name__ == "__main__":
    unittest.main()
