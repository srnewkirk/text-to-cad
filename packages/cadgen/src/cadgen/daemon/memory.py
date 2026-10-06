"""Soft available-headroom admission estimates, without CAD or extra dependencies.

Available memory already excludes resident workers and other consumers. Only
unmaterialized starts and the next job's estimated increment need reservations.
These observations cannot bound a native operation's subsequent allocations.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from cadgen._internal.runtime_limits import KERNEL_WORKER_MEMORY_BYTES, WINDOWS_MEMORY_RESERVE_BYTES

MIB = 1024**2
LINUX_STARTUP_FLOOR_BYTES = 512 * MIB
WINDOWS_STARTUP_BYTES = 2 * 1024**3
WINDOWS_RESERVE_BYTES = 2 * 1024**3
OBSERVATION_MAX_AGE_SECONDS = 0.25


@dataclass(frozen=True)
class MemoryPolicy:
    startup_bytes: int
    reserve_bytes: int
    job_bytes: int = KERNEL_WORKER_MEMORY_BYTES
    spawn_job_increment_bytes: int = KERNEL_WORKER_MEMORY_BYTES

    @classmethod
    def for_platform(cls, platform: str | None = None) -> "MemoryPolicy":
        platform = sys.platform if platform is None else platform
        if platform == "win32":
            # Its incident-derived 2 GiB start reservation already covers work.
            return cls(WINDOWS_STARTUP_BYTES, WINDOWS_RESERVE_BYTES,
                       spawn_job_increment_bytes=0)
        return cls(LINUX_STARTUP_FLOOR_BYTES, WINDOWS_MEMORY_RESERVE_BYTES)


@dataclass(frozen=True)
class Headroom:
    available_bytes: int | None
    source: str
    error: str | None = None
    observed_at: float = field(default_factory=time.monotonic)
    enforced: bool = True


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _unescape_mount(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)


def _absolute(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid cgroup path")
    return path


def _cgroup_mounts(membership: str, mountinfo: str):
    """Return (version, mountpoint, mount root, leaf) for this process's memory hierarchy."""
    if not membership.strip():
        raise ValueError("cgroup membership unavailable")
    groups = {}
    for line in membership.splitlines():
        _, controllers, path = line.split(":", 2)
        if not controllers:
            groups["v2"] = _absolute(path)
        elif "memory" in controllers.split(","):
            groups["v1"] = _absolute(path)
    matches = []
    for line in mountinfo.splitlines():
        before, separator, after = line.partition(" - ")
        if not separator:
            raise ValueError("invalid mountinfo row")
        fields, filesystem = before.split(), after.split()
        if len(fields) < 6 or len(filesystem) < 3:
            raise ValueError("invalid mountinfo fields")
        version = "v2" if filesystem[0] == "cgroup2" else (
            "v1" if filesystem[0] == "cgroup" and "memory" in filesystem[2].split(",") else None)
        if version not in groups:
            continue
        root = _absolute(_unescape_mount(fields[3]))
        mount = _absolute(_unescape_mount(fields[4]))
        group = groups[version]
        # A namespace-relative membership can start at / while mountinfo still
        # names the host subtree root. Map that relative path within this mount.
        namespace_mapping = not group.is_relative_to(root)
        relative = group.relative_to(root) if not namespace_mapping else group.relative_to("/")
        matches.append((version, mount, root, mount / relative, namespace_mapping))
    if groups and not matches:
        raise ValueError("current memory cgroup has no accessible mount")
    return matches


def linux_headroom(read_text: Callable[[str], str] = _read) -> Headroom:
    """Host MemAvailable capped by finite, visible ancestor cgroup remaining bytes.

    Swap is not usable headroom here. Cgroup cache remains charged; no guessed
    cache reclaim is added. A hierarchy root may legitimately lack limit files.
    """
    observed_at = time.monotonic()
    try:
        values = {}
        for line in read_text("/proc/meminfo").splitlines():
            name, _, value = line.partition(":")
            if name == "MemAvailable":
                fields = value.split()
                if len(fields) != 2 or fields[1] != "kB":
                    raise ValueError("invalid MemAvailable")
                values[name] = int(fields[0]) * 1024
        if "MemAvailable" not in values or values["MemAvailable"] < 0:
            raise ValueError("MemAvailable unavailable")
        available = values["MemAvailable"]
        mounts = _cgroup_mounts(read_text("/proc/self/cgroup"), read_text("/proc/self/mountinfo"))
        finite = 0
        for version, mount, root, leaf, namespace_mapping in mounts:
            if namespace_mapping:
                # Do not guess that a namespace-relative path names this process.
                # A membership check disambiguates it from an unrelated subtree.
                members = {int(pid) for pid in read_text(str(leaf / "cgroup.procs")).split()}
                if os.getpid() not in members:
                    raise ValueError("namespace cgroup mount mapping does not contain this process")
            node = leaf
            while True:
                filename = "memory.max" if version == "v2" else "memory.limit_in_bytes"
                try:
                    raw = read_text(str(node / filename)).strip()
                except FileNotFoundError:
                    if node != mount or root != PurePosixPath("/"):
                        raise
                    raw = "max"  # the actual hierarchy root has no per-group maximum
                unlimited = raw == "max" if version == "v2" else False
                if not unlimited:
                    limit = int(raw)
                    if limit < 0:
                        raise ValueError("negative cgroup memory limit")
                    unlimited = version == "v1" and limit >= (1 << 60)
                    if not unlimited:
                        current_file = "memory.current" if version == "v2" else "memory.usage_in_bytes"
                        current = int(read_text(str(node / current_file)).strip())
                        if current < 0:
                            raise ValueError("negative cgroup memory usage")
                        available = min(available, max(0, limit - current))
                        finite += 1
                if node == mount:
                    break
                node = node.parent
        return Headroom(available, f"Linux MemAvailable; {finite} finite cgroup allowance(s)",
                        observed_at=observed_at)
    except (OSError, ValueError, IndexError) as exc:
        return Headroom(None, "Linux host/cgroup", f"memory headroom observation unavailable: {exc}",
                        observed_at=observed_at)


def observe_headroom() -> Headroom:
    if os.name == "nt":
        from cadgen._internal.runtime_limits import _windows_available_memory
        observed_at = time.monotonic()
        available = _windows_available_memory()
        return Headroom(available, "Windows RAM/commit", None if available is not None
                        else "memory headroom observation unavailable: Windows RAM/commit",
                        observed_at=observed_at)
    if sys.platform.startswith("linux"):
        return linux_headroom()
    # This patch implements only Windows/Linux observations. Other supported
    # daemon platforms retain their existing resident-cap policy explicitly.
    return Headroom(None, f"{sys.platform}: memory observations not implemented; resident cap only",
                    enforced=False)


def fresh_worker_bytes(pid: int) -> int | None:
    """Measure a fresh idle Linux worker; failure retains the policy startup seed."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        fields = Path(f"/proc/{pid}/statm").read_text().split()
        result = int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
        return result if result > 0 else None
    except (OSError, ValueError, IndexError):
        return None
