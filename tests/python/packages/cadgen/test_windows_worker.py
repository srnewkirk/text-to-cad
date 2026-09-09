from __future__ import annotations

import ctypes
import unittest
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen._internal import windows_worker  # noqa: E402


class WindowsWorkerPolicyTests(unittest.TestCase):
    def test_non_windows_process_is_unchanged(self):
        with mock.patch.object(windows_worker.os, "name", "posix"):
            self.assertFalse(windows_worker.suppress_native_crash_dialogs())

    def test_windows_worker_disables_interactive_native_error_boxes(self):
        set_error_mode = mock.Mock()
        kernel32 = mock.Mock(SetErrorMode=set_error_mode)
        with mock.patch.object(windows_worker.os, "name", "nt"), mock.patch.object(
            ctypes, "windll", mock.Mock(kernel32=kernel32), create=True
        ):
            self.assertTrue(windows_worker.suppress_native_crash_dialogs())
        set_error_mode.assert_called_once_with(windows_worker.WORKER_ERROR_MODE)


if __name__ == "__main__":
    unittest.main()
