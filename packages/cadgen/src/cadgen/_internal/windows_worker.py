"""Windows policy applied only inside disposable native CAD workers."""

from __future__ import annotations

import os

SEM_FAILCRITICALERRORS = 0x0001
SEM_NOGPFAULTERRORBOX = 0x0002
SEM_NOOPENFILEERRORBOX = 0x8000
WORKER_ERROR_MODE = SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX


def suppress_native_crash_dialogs() -> bool:
    """Disable interactive OS error boxes for this process, preserving exit status."""

    if os.name != "nt":
        return False
    try:
        import ctypes

        ctypes.windll.kernel32.SetErrorMode(WORKER_ERROR_MODE)
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False
