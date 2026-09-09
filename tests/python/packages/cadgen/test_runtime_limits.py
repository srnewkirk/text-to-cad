from __future__ import annotations

import unittest

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen._internal import runtime_limits  # noqa: E402


class KernelWorkerCeilingTests(unittest.TestCase):
    def test_posix_tracks_the_cpu_count(self):
        self.assertEqual(
            runtime_limits.kernel_worker_ceiling(cpu_count=12, platform="posix"),
            12,
        )

    def test_windows_caps_a_large_machine_at_four(self):
        self.assertEqual(
            runtime_limits.kernel_worker_ceiling(
                cpu_count=32,
                platform="nt",
                available_memory_bytes=16 * 1024**3,
            ),
            4,
        )

    def test_windows_reduces_the_limit_when_memory_is_tight(self):
        available = runtime_limits.WINDOWS_MEMORY_RESERVE_BYTES + 2 * runtime_limits.KERNEL_WORKER_MEMORY_BYTES
        self.assertEqual(
            runtime_limits.kernel_worker_ceiling(
                cpu_count=16,
                platform="nt",
                available_memory_bytes=available,
            ),
            2,
        )

    def test_windows_always_allows_one_worker(self):
        self.assertEqual(
            runtime_limits.kernel_worker_ceiling(
                cpu_count=16,
                platform="nt",
                available_memory_bytes=0,
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
