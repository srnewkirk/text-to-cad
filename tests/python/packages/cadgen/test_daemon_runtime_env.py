from __future__ import annotations

import os
import unittest
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen.daemon import client, worker


class RuntimeEnvironmentForwarding(unittest.TestCase):
    def test_warm_worker_uses_current_browser_libraries_and_clears_previous_ones(self):
        with mock.patch.dict(os.environ):
            os.environ["LD_LIBRARY_PATH"] = "/libs/current runtime"
            request_env = client.forwarded_env()
            os.environ["LD_LIBRARY_PATH"] = "/libs/previous daemon"
            worker._apply_request_env({"env": request_env})
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/libs/current runtime")

            del os.environ["LD_LIBRARY_PATH"]
            next_env = client.forwarded_env()
            os.environ["LD_LIBRARY_PATH"] = "/libs/previous job"
            worker._apply_request_env({"env": next_env})
            self.assertNotIn("LD_LIBRARY_PATH", os.environ)


if __name__ == "__main__":
    unittest.main()
