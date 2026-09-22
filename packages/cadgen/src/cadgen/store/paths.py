"""Where the store lives — ONE resolution rule, honored by both languages.

1. ``$CADGEN_CACHE_DIR`` when set — the explicit override.
2. ``$XDG_CACHE_HOME/cadgen`` on POSIX / ``%LOCALAPPDATA%\\cadgen`` on Windows when set.
3. ``~/.cache/cadgen`` otherwise.

The JS mirror is ``cadgenCacheRootDir`` in
``packages/cadgen-js/src/lib/surf/tessellationCacheFs.mjs``;
``tests/python/global/test_cache_root_sync.py`` pins the two together.
"""

from __future__ import annotations

import os
from pathlib import Path

# Mirror of TESSELLATION_VERSION in packages/cadgen-js/src/lib/surf/tessellate.js
# (sync-tested). It is part of the MESH index key, not a store salt.
MESH_TESSELLATION_VERSION = 1

# "document" is the ARTIFACT side (sha256 of a file's bytes → its tree); every
# other kind is the code/dependency side. STORE.md §2, the law.
INDEX_KINDS = ("model", "document", "output", "component", "op", "mesh")


def store_root() -> Path:
    override = os.environ.get("CADGEN_CACHE_DIR", "").strip()
    if override:
        # Absolutized ONCE, against the cwd of the process that first reads it,
        # and written back so every later reader -- this process after a chdir,
        # a worker it spawns -- sees the same folder. A relative value is otherwise
        # a different store from every directory.
        root = Path(override).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
            os.environ["CADGEN_CACHE_DIR"] = str(root)
        return root
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data) / "cadgen"
    else:
        xdg_cache_home = os.environ.get("XDG_CACHE_HOME", "").strip()
        if xdg_cache_home:
            return Path(xdg_cache_home) / "cadgen"
    return Path.home() / ".cache" / "cadgen"


class StoreUnwritableError(RuntimeError):
    """The store's folder cannot be written. One sentence, no frames."""


class StoreWriteError(RuntimeError):
    """A store write failed at a named operation boundary."""


def write_failure(exc: OSError, operation: str, target: Path | str) -> StoreWriteError:
    """Describe a store write failure without changing its retry policy.

    The operation name distinguishes directory creation, temp-file creation,
    payload writing/copying, and publication. errno and WinError are retained
    because the same ``PermissionError`` text can mean different things on a
    local disk, Windows, or an SMB-backed cache.
    """
    details = []
    if exc.errno is not None:
        details.append(f"errno={exc.errno}")
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        details.append(f"winerror={winerror}")
    code = f"; {', '.join(details)}" if details else ""
    reason = exc.strerror or str(exc) or type(exc).__name__
    return StoreWriteError(
        f"object store write failed during {operation}; cache root={store_root()}; "
        f"target={Path(target)}; {reason}{code}"
    )


def unwritable(
    exc: OSError, target: Path | str, *, operation: str | None = None
) -> StoreUnwritableError:
    root = store_root()
    stage = f" during {operation}" if operation else ""
    details = []
    if exc.errno is not None:
        details.append(f"errno={exc.errno}")
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        details.append(f"winerror={winerror}")
    codes = f"; {', '.join(details)}" if details else ""
    return StoreUnwritableError(
        f"the store at {root} is not writable{stage} "
        f"({exc.strerror or type(exc).__name__} writing {Path(target)}{codes}); "
        "point CADGEN_CACHE_DIR at a folder this user can write"
    )


def objects_dir() -> Path:
    return store_root() / "objects"


def index_dir(kind: str) -> Path:
    if kind not in INDEX_KINDS:
        raise ValueError(f"unknown index kind {kind!r}; one of {INDEX_KINDS}")
    return store_root() / "index" / kind

