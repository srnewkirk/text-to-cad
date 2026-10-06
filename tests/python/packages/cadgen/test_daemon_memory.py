"""Headroom discovery against text fixtures; no OS configuration or CAD work."""

import os
import unittest
from unittest import mock

from cadgen.daemon import memory


class LinuxHeadroom(unittest.TestCase):
    def setUp(self):
        self.files = {
            "/proc/meminfo": "MemTotal: 4194304 kB\nMemAvailable: 3145728 kB\nSwapFree: 2097152 kB\n",
            "/proc/self/cgroup": "0::/parent/child\n",
            "/proc/self/mountinfo": "29 23 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n",
            "/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/parent/memory.max": "max",
            "/sys/fs/cgroup/parent/child/memory.max": "max",
        }

    def read(self, path):
        value = self.files.get(path, FileNotFoundError(path))
        if isinstance(value, Exception):
            raise value
        return value

    def observe(self):
        return memory.linux_headroom(self.read)

    def test_host_available_excludes_swap_and_total_capacity(self):
        result = self.observe()
        self.assertEqual(result.available_bytes, 3 * 1024**3)
        self.assertIsNone(result.error)

    def test_delayed_reader_preserves_observation_start_timestamp(self):
        now = [100.0]
        def delayed_read(path):
            value = self.read(path)
            if path == "/proc/meminfo":
                now[0] = 101.0
            return value
        with mock.patch.object(memory.time, "monotonic", side_effect=lambda: now[0]):
            result = memory.linux_headroom(delayed_read)
        self.assertEqual(result.observed_at, 100.0)
        self.assertGreater(now[0] - result.observed_at, memory.OBSERVATION_MAX_AGE_SECONDS)

    def test_smallest_finite_ancestor_remaining_allowance_wins(self):
        self.files.update({
            "/sys/fs/cgroup/parent/memory.max": str(2 * 1024**3),
            "/sys/fs/cgroup/parent/memory.current": str(1536 * memory.MIB),
            "/sys/fs/cgroup/parent/child/memory.max": str(1024 * memory.MIB),
            "/sys/fs/cgroup/parent/child/memory.current": str(256 * memory.MIB),
        })
        result = self.observe()
        self.assertEqual(result.available_bytes, 512 * memory.MIB)
        self.assertIn("2 finite", result.source)

    def test_over_limit_current_usage_is_zero_headroom(self):
        self.files.update({"/sys/fs/cgroup/parent/memory.max": "100",
                           "/sys/fs/cgroup/parent/memory.current": "101"})
        self.assertEqual(self.observe().available_bytes, 0)

    def test_mount_subtree_mapping_does_not_read_arbitrary_root(self):
        self.files["/proc/self/mountinfo"] = "29 23 0:26 /parent /cg rw - cgroup2 cgroup rw\n"
        self.files.update({"/cg/memory.max": "900", "/cg/memory.current": "200",
                           "/cg/child/memory.max": "max", "/cg/child/cgroup.procs": str(os.getpid())})
        self.assertEqual(self.observe().available_bytes, 700)

    def test_namespace_relative_membership_maps_within_mount(self):
        self.files["/proc/self/cgroup"] = "0::/child\n"
        self.files["/proc/self/mountinfo"] = "29 23 0:26 /host/container /cg rw - cgroup2 cgroup rw\n"
        self.files.update({"/cg/memory.max": "900", "/cg/memory.current": "200",
                           "/cg/child/memory.max": "max", "/cg/child/cgroup.procs": str(os.getpid())})
        self.assertEqual(self.observe().available_bytes, 700)

    def test_namespace_root_finite_limit_is_included(self):
        self.files["/proc/self/cgroup"] = "0::/\n"
        self.files["/proc/self/mountinfo"] = "29 23 0:26 /host/container /cg rw - cgroup2 cgroup rw\n"
        self.files.update({"/cg/memory.max": "900", "/cg/memory.current": "200",
                           "/cg/cgroup.procs": str(os.getpid())})
        self.assertEqual(self.observe().available_bytes, 700)

    def test_unprovable_namespace_mapping_does_not_use_arbitrary_root(self):
        self.files["/proc/self/cgroup"] = "0::/outside\n"
        self.files["/proc/self/mountinfo"] = "29 23 0:26 /parent /cg rw - cgroup2 cgroup rw\n"
        self.files.update({"/cg/memory.max": "900", "/cg/memory.current": "200",
                           "/cg/outside/memory.max": "max",
                           "/cg/outside/cgroup.procs": str(os.getpid() + 1)})
        result = self.observe()
        self.assertIsNone(result.available_bytes)
        self.assertIn("mapping", result.error)

    def test_actual_hierarchy_root_can_legitimately_lack_maximum(self):
        del self.files["/sys/fs/cgroup/memory.max"]
        self.assertEqual(self.observe().available_bytes, 3 * 1024**3)

    def test_missing_nonroot_limit_is_explicit_failure(self):
        del self.files["/sys/fs/cgroup/parent/memory.max"]
        self.assertIsNone(self.observe().available_bytes)
        self.assertIn("unavailable", self.observe().error)

    def test_inaccessible_root_is_not_treated_as_unlimited(self):
        self.files["/sys/fs/cgroup/memory.max"] = PermissionError("denied")
        self.assertIsNone(self.observe().available_bytes)

    def test_missing_usage_for_finite_limit_is_failure(self):
        self.files["/sys/fs/cgroup/parent/memory.max"] = "100"
        self.assertIsNone(self.observe().available_bytes)

    def test_malformed_observations_fail_explicitly(self):
        for path, value in (("/proc/meminfo", "MemFree: 123 kB"),
                            ("/proc/meminfo", "MemAvailable: -1 kB"),
                            ("/proc/meminfo", "MemAvailable: 12 MB"),
                            ("/proc/self/cgroup", "0::/../child"),
                            ("/proc/self/cgroup", ""),
                            ("/proc/self/mountinfo", "malformed"),
                            ("/sys/fs/cgroup/parent/memory.max", "broken")):
            with self.subTest(path=path, value=value), mock.patch.dict(self.files, {path: value}):
                self.assertIsNone(self.observe().available_bytes)

    def test_controller_without_accessible_mount_is_failure(self):
        self.files["/proc/self/mountinfo"] = "29 23 0:26 / /proc rw - proc proc rw\n"
        self.assertIsNone(self.observe().available_bytes)

    def test_mount_escapes_are_decoded(self):
        self.files["/proc/self/mountinfo"] = r"29 23 0:26 / /cg\040space rw - cgroup2 cgroup rw" + "\n"
        self.files.update({"/cg space/memory.max": "max", "/cg space/parent/memory.max": "100",
                           "/cg space/parent/memory.current": "10",
                           "/cg space/parent/child/memory.max": "max"})
        self.assertEqual(self.observe().available_bytes, 90)

    def test_v1_memory_controller_current_and_finite_ancestors(self):
        self.files["/proc/self/cgroup"] = "4:cpu:/other\n5:memory:/parent/child\n"
        self.files["/proc/self/mountinfo"] = "29 23 0:26 / /cg rw - cgroup cgroup rw,memory\n"
        self.files.update({
            "/cg/memory.limit_in_bytes": str((1 << 63) - 4096),
            "/cg/parent/memory.limit_in_bytes": "1000",
            "/cg/parent/memory.usage_in_bytes": "300",
            "/cg/parent/child/memory.limit_in_bytes": "900",
            "/cg/parent/child/memory.usage_in_bytes": "400",
        })
        self.assertEqual(self.observe().available_bytes, 500)

    def test_v1_namespace_subtree_mount_mapping(self):
        self.files["/proc/self/cgroup"] = "5:memory:/\n"
        self.files["/proc/self/mountinfo"] = "29 23 0:26 /host/container /cg rw - cgroup cgroup rw,memory\n"
        self.files.update({"/cg/memory.limit_in_bytes": "1000", "/cg/memory.usage_in_bytes": "300",
                           "/cg/cgroup.procs": str(os.getpid())})
        self.assertEqual(self.observe().available_bytes, 700)


class PolicyAndFailure(unittest.TestCase):
    def test_estimates_are_explicit_and_platform_specific(self):
        linux = memory.MemoryPolicy.for_platform("linux")
        windows = memory.MemoryPolicy.for_platform("win32")
        self.assertEqual((linux.startup_bytes, linux.reserve_bytes, linux.job_bytes),
                         (512 * memory.MIB, 1024 * memory.MIB, 450 * memory.MIB))
        self.assertEqual((windows.startup_bytes, windows.reserve_bytes), (2 * 1024**3, 2 * 1024**3))

    def test_windows_missing_observation_is_explicit(self):
        from cadgen._internal import runtime_limits
        with mock.patch.object(memory.os, "name", "nt"), mock.patch.object(runtime_limits, "_windows_available_memory", return_value=None):
            result = memory.observe_headroom()
        self.assertIsNone(result.available_bytes)
        self.assertIn("unavailable", result.error)

    def test_windows_delayed_observation_is_stamped_before_read(self):
        from cadgen._internal import runtime_limits
        now = [100.0]
        def delayed():
            now[0] = 101.0
            return 8 * 1024**3
        with mock.patch.object(memory.os, "name", "nt"), \
                mock.patch.object(memory.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(runtime_limits, "_windows_available_memory", side_effect=delayed):
            result = memory.observe_headroom()
        self.assertEqual(result.observed_at, 100.0)

    def test_other_platform_retains_explicit_resident_only_policy(self):
        with mock.patch.object(memory.os, "name", "posix"), mock.patch.object(memory.sys, "platform", "darwin"):
            result = memory.observe_headroom()
        self.assertFalse(result.enforced)
        self.assertIn("resident cap only", result.source)

    def test_baseline_read_failure_returns_no_measurement(self):
        with mock.patch.object(memory.sys, "platform", "linux"), mock.patch.object(memory.Path, "read_text", side_effect=OSError("gone")):
            self.assertIsNone(memory.fresh_worker_bytes(123))


if __name__ == "__main__":
    unittest.main()
