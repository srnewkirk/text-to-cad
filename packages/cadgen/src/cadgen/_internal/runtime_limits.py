"""Platform-aware limits for native CAD work."""

from __future__ import annotations

import os

WINDOWS_MAX_KERNEL_WORKERS = 4
KERNEL_WORKER_MEMORY_BYTES = 450 * 1024 * 1024
WINDOWS_MEMORY_RESERVE_BYTES = 1024 * 1024 * 1024


def _windows_available_memory() -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPhys)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def kernel_worker_ceiling(
    *, cpu_count: int | None = None, platform: str | None = None,
    available_memory_bytes: int | None = None,
) -> int:
    """Default maximum for active native CAD processes; overrides stay external."""

    cores = max(1, cpu_count if cpu_count is not None else (os.cpu_count() or 1))
    platform_name = platform if platform is not None else os.name
    if platform_name != "nt":
        return cores
    available = available_memory_bytes if available_memory_bytes is not None else _windows_available_memory()
    memory_limit = WINDOWS_MAX_KERNEL_WORKERS
    if available is not None:
        usable = max(0, available - WINDOWS_MEMORY_RESERVE_BYTES)
        memory_limit = max(1, usable // KERNEL_WORKER_MEMORY_BYTES)
    return max(1, min(cores, WINDOWS_MAX_KERNEL_WORKERS, memory_limit))
