"""The CAD skill's snapshot: STEP models and their generators, plus direct meshes.

Everything about rendering — arguments, job schema, theme, display, the headless browser —
is `cadgen.snapshot_cli`, shared with every other skill that renders. What is local is this
file: which input kinds this skill accepts, and where its own bundled browser runtime lives.

`.implicit.js` and `.urdf`/`.srdf`/`.sdf` used to resolve here too. They belong to the
implicit-cad and urdf/srdf/sdf skills now; handing one to this CLI names the skill that
renders it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_WORKER_ENV = "CADGEN_SNAPSHOT_WORKER"

RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
KINDS = ("step", "stp", "3mf", "glb", "stl")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    scripts_dir = Path(__file__).resolve().parents[1]

    # The daemon sets CADGEN_DAEMON_CHILD so it never recurses. Keep this and all
    # cadgen/OCP imports in the worker so a native import failure cannot kill the
    # stdlib-only Windows launcher.
    if os.environ.get("CADGEN_WARM") == "1" and not os.environ.get("CADGEN_DAEMON_CHILD"):
        daemon_scripts_dir = str(scripts_dir)
        if daemon_scripts_dir not in sys.path:
            sys.path.insert(0, daemon_scripts_dir)
        from cadgen_daemon.client import run_via_daemon

        warm_exit = run_via_daemon("snapshot", args, os.getcwd())
        if warm_exit is not None:
            return warm_exit

    for runtime_path in (
        scripts_dir,
        scripts_dir / "packages",
        scripts_dir / "packages" / "cadgen" / "src",
    ):
        text = str(runtime_path)
        if runtime_path.is_dir() and text not in sys.path:
            sys.path.insert(0, text)

    from cadgen.snapshot_cli import run_snapshot_cli

    return run_snapshot_cli(
        args,
        kinds=KINDS,
        runtime_dir=RUNTIME_DIR,
    )


def _windows_status(returncode: int) -> int | None:
    """Return an NTSTATUS-shaped exit code, including Python's signed form."""
    status = returncode & 0xFFFFFFFF
    return status if status >= 0x80000000 else None


def _run_isolated(argv: list[str]) -> int:
    """Keep the stdlib-only launcher alive if native CAD/graphics code terminates."""
    worker_env = os.environ.copy()
    worker_env[_WORKER_ENV] = "1"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent), *argv],
        env=worker_env,
        check=False,
    )
    status = _windows_status(result.returncode)
    if status is None:
        return result.returncode

    label = "access violation" if status == 0xC0000005 else "native process failure"
    print(
        "snapshot worker terminated abnormally "
        f"(Windows status 0x{status:08X}: {label}). "
        "No snapshot was produced. Check Windows Error Reporting for the faulting "
        "module, then update or change the implicated graphics driver/runtime before retrying.",
        file=sys.stderr,
    )
    return 1


def entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if os.name == "nt" and os.environ.get(_WORKER_ENV) != "1":
        return _run_isolated(args)
    return main(args)


if __name__ == "__main__":
    raise SystemExit(entrypoint())
