#!/usr/bin/env python3
"""Select and manage the marketplace-backed Python runtime for CAD workspaces.

This file intentionally uses only the standard library: it is also the bootstrap
that runs before cadgen has been installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Sequence


APP = "cad-runtime"
INTERNAL_BACKEND = "CAD_RUNTIME_BACKEND"


class RuntimeErrorMessage(Exception):
    pass


def _run(argv: Sequence[str], *, check: bool = True, capture: bool = False, **kw):
    try:
        return subprocess.run(list(map(str, argv)), check=check, text=True, encoding="utf-8",
                             errors="replace",
                             capture_output=capture, **kw)
    except FileNotFoundError as exc:
        raise RuntimeErrorMessage(f"Required executable is unavailable: {argv[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeErrorMessage(f"Command failed ({exc.returncode}): {argv[0]}" + (f"\n{detail}" if detail else "")) from exc


def _plugin_root(value: str | None) -> Path:
    root = Path(value).expanduser().resolve() if value else Path(__file__).resolve().parents[2]
    manifest = root / ".codex-plugin" / "plugin.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeErrorMessage(f"Cannot read plugin manifest at {manifest}: {exc}") from exc
    version = data.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeErrorMessage(f"Plugin manifest has no valid version: {manifest}")
    return root


def _contract(root: Path) -> dict:
    manifest = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    receipt = root / "runtime" / f"cadgen-{version}.json"
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeErrorMessage(f"Missing or invalid managed runtime receipt {receipt}: {exc}") from exc
    if data.get("name") != "cadgen" or data.get("version") != version:
        raise RuntimeErrorMessage(f"Runtime receipt does not match plugin version {version}: {receipt}")
    wheel_name = data.get("wheel")
    digest = data.get("sha256")
    if not isinstance(wheel_name, str) or Path(wheel_name).name != wheel_name or not re.fullmatch(r"cadgen-[\w.]+-py3-none-any\.whl", wheel_name):
        raise RuntimeErrorMessage(f"Unsafe wheel name in runtime receipt: {wheel_name!r}")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeErrorMessage(f"Invalid wheel SHA-256 in runtime receipt: {receipt}")
    wheel = (root / "runtime" / wheel_name).resolve()
    runtime_dir = (root / "runtime").resolve()
    if wheel.parent != runtime_dir:
        raise RuntimeErrorMessage("Managed wheel path escapes the plugin runtime directory.")
    try:
        actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeErrorMessage(f"Managed wheel is missing or unreadable: {wheel}: {exc}") from exc
    if actual != digest:
        raise RuntimeErrorMessage(f"Managed wheel hash mismatch: expected {digest}, got {actual}: {wheel}")
    data["wheelPath"] = str(wheel)
    data["contract"] = hashlib.sha256(f"{version}:{wheel_name}:{digest}".encode()).hexdigest()[:20]
    return data


def _cache_root(contract: dict) -> Path:
    configured = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(configured).expanduser() if configured else Path.home() / ".cache"
    return base / APP / contract["contract"]


def _uv_executable() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "uv"
    return str(local) if local.is_file() and os.access(local, os.X_OK) else None


def _config_path() -> Path:
    if os.name == "nt":
        configured = os.environ.get("APPDATA", "").strip()
        return (Path(configured).expanduser() if configured else Path.home() / "AppData" / "Roaming") / APP / "config.json"
    configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(configured).expanduser() if configured else Path.home() / ".config") / APP / "config.json"


def _wsl_distro() -> str:
    explicit = os.environ.get("CAD_RUNTIME_WSL_DISTRO", "").strip()
    config = _config_path()
    if explicit:
        return explicit
    try:
        value = json.loads(config.read_text(encoding="utf-8")).get("wslDistro")
    except FileNotFoundError:
        value = None
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeErrorMessage(f"Cannot read WSL configuration {config}: {exc}") from exc
    if not isinstance(value, str) or not value.strip():
        raise RuntimeErrorMessage(f"No WSL distribution configured. Choose one once with: python {Path(__file__).resolve()} configure-wsl Ubuntu")
    return value.strip()


def _is_windows() -> bool:
    return os.name == "nt"


def _wsl_call(distro: str, argv: Sequence[str], *, capture: bool = False, check: bool = True):
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if not wsl:
        raise RuntimeErrorMessage("WSL is configured but wsl.exe is unavailable. Repair/enable WSL2; CAD will not fall back to Windows Python.")
    listed = _run([wsl, "--list", "--verbose"], check=False, capture=True)
    if not _valid_wsl_listing(listed.stdout or "", distro, listed.returncode):
        raise RuntimeErrorMessage(f"Configured WSL distribution {distro!r} is unavailable. Check `wsl.exe --list --verbose`, then run `python {Path(__file__).resolve()} configure-wsl <installed-distro>`.")
    def linux_path(path: str) -> str:
        # Explicit Linux paths (notably isolated browser libraries) are already
        # in the selected distro. wslpath would treat /home as C:\home when
        # invoked by a Windows host and redirect it to /mnt/c/home.
        if path.startswith("/") or path.startswith("~/"):
            return path
        result = _run([wsl, "-d", distro, "--exec", "wslpath", "-a", "-u", path], capture=True)
        return result.stdout.strip()
    helper = linux_path(str(Path(__file__).resolve()))
    forwarded = ["--backend", f"wsl:{distro}", *argv]
    # Convert explicit host filesystem arguments while preserving all model argv.
    for i, arg in enumerate(forwarded):
        if arg in ("--workspace", "--plugin-root", "--requirements", "--browser-libs", "--pythonpath") and i + 1 < len(forwarded):
            value = forwarded[i + 1]
            if arg != "--pythonpath" or re.match(r"^[A-Za-z]:[\\/]", value):
                forwarded[i + 1] = linux_path(value)
    try:
        python_index = forwarded.index("python")
        script_index = python_index + 1
        if forwarded[script_index] == "--":
            script_index += 1
        if script_index < len(forwarded) and re.match(r"^[A-Za-z]:[\\/]", forwarded[script_index]):
            forwarded[script_index] = linux_path(forwarded[script_index])
    except (ValueError, IndexError):
        pass
    return _run([wsl, "-d", distro, "--exec", "python3", helper, *forwarded], capture=capture, check=check)


def _valid_wsl_listing(output: str, distro: str, returncode: int = 0) -> bool:
    if returncode or distro.lower().startswith("docker-"):
        return False
    for line in output.replace("\x00", "").splitlines():
        match = re.match(r"^\s*\*?\s*(.+?)\s+(?:Running|Stopped)\s+([12])\s*$", line)
        if match and match.group(1).strip() == distro:
            return match.group(2) == "2"
    return False


def _check_wsl_distribution(distro: str) -> None:
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if not wsl:
        raise RuntimeErrorMessage("WSL is unavailable; repair WSL2 before configuring the CAD runtime.")
    listed = _run([wsl, "--list", "--verbose"], check=False, capture=True)
    if not _valid_wsl_listing(listed.stdout or "", distro, listed.returncode):
        raise RuntimeErrorMessage(f"{distro!r} is not an installed WSL2 distribution (Docker distributions are not supported). Check `wsl.exe --list --verbose`.")


def _backend() -> tuple[str, str | None]:
    selected = os.environ.get(INTERNAL_BACKEND, "")
    if os.name == "nt":
        return "wsl", _wsl_distro()
    if selected.startswith("wsl:"):
        distro = selected[4:]
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8", errors="replace").lower()
        if not os.environ.get("WSL_DISTRO_NAME") or "microsoft" not in release:
            raise RuntimeErrorMessage("WSL handoff did not enter a WSL2 Linux kernel; refusing native fallback.")
        if os.environ["WSL_DISTRO_NAME"] != distro:
            raise RuntimeErrorMessage(f"WSL handoff selected {distro!r}, but this process runs in {os.environ['WSL_DISTRO_NAME']!r}.")
        return "wsl", distro
    if sys.platform.startswith("linux"):
        return "linux", None
    if sys.platform == "darwin":
        return "macos", None
    raise RuntimeErrorMessage(f"Unsupported CAD runtime host: {sys.platform}")


def _installed_identity(python: Path, contract: dict) -> tuple[bool, str]:
    if not python.is_file():
        return False, f"runtime interpreter is missing at {python}"
    probe = "import importlib.metadata as m,json,sys,pathlib,cadgen; d=m.distribution('cadgen'); print(json.dumps({'version':d.version,'python':'.'.join(map(str,sys.version_info[:3])),'direct':d.read_text('direct_url.json'),'module':str(pathlib.Path(cadgen.__file__).resolve()),'prefix':str(pathlib.Path(sys.prefix).resolve())}))"
    result = _run([python, "-c", probe], check=False, capture=True)
    if result.returncode:
        detail = (result.stderr or "").strip().splitlines()
        detail = detail[-1] if detail else "no import diagnostic"
        return False, f"cadgen import failed under {python}: {detail}"
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, "cadgen metadata could not be read"
    if data.get("version") != contract["version"]:
        return False, f"cadgen version {data.get('version')} does not match managed {contract['version']}"
    if data.get("python") != contract.get("python"):
        return False, f"runtime Python {data.get('python')} does not match managed {contract.get('python')}"
    try:
        Path(data["module"]).relative_to(Path(data["prefix"]))
    except (KeyError, ValueError):
        return False, "cadgen module does not resolve inside the selected runtime environment"
    direct = data.get("direct")
    if not direct:
        return False, "cadgen has no direct wheel provenance metadata"
    try:
        info = json.loads(direct)
        archive = info.get("archive_info", {})
        archive_hash = archive.get("hash", "") or ("sha256=" + archive.get("hashes", {}).get("sha256", ""))
    except (TypeError, json.JSONDecodeError):
        return False, "cadgen wheel provenance metadata is invalid"
    if archive_hash not in (f"sha256={contract['sha256']}", contract["sha256"]):
        return False, "installed cadgen was not installed from the marketplace-managed wheel hash"
    return True, "verified managed wheel"


def _paths(args, contract: dict) -> tuple[Path, Path, Path]:
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise RuntimeErrorMessage(f"CAD workspace does not exist or is not a directory: {workspace}")
    cache = _cache_root(contract)
    dependency_key = "base"
    if args.requirements:
        requirements = Path(args.requirements).expanduser().resolve()
        try:
            dependency_key = hashlib.sha256(requirements.read_bytes()).hexdigest()[:16]
        except OSError as exc:
            raise RuntimeErrorMessage(f"Cannot read model requirements {requirements}: {exc}") from exc
    venv = cache / "environments" / dependency_key / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return workspace, cache, python


def _identity(backend: str, distro: str | None, python: Path, contract: dict, workspace: Path, cache: Path) -> dict:
    py = (_run([python, "-c", "import sys;print(sys.executable);print('.'.join(map(str,sys.version_info[:3])))"], check=False, capture=True)
          if python.is_file() else subprocess.CompletedProcess([], 127, stdout="", stderr="runtime interpreter is missing"))
    version, pyver = (py.stdout.strip().splitlines() + ["unknown", "unknown"])[:2]
    ok, provenance = _installed_identity(python, contract) if py.returncode == 0 else (False, "runtime interpreter is missing")
    return {"backend": backend, "distribution": distro, "python": version, "pythonVersion": pyver,
            "cadgenVersion": contract["version"] if ok else None, "wheelSha256": contract["sha256"],
            "platform": platform.system(), "kernel": platform.release(),
            "browserLibraries": str(cache / "browser-libs") if (cache / "browser-libs").is_dir() else None,
            "runtimeContract": contract["contract"], "provenance": provenance, "workspace": str(workspace),
            "store": str(cache / "store" / hashlib.sha256(str(workspace).encode()).hexdigest()[:16])}


def _emit(info: dict) -> None:
    print(json.dumps(info, ensure_ascii=False, sort_keys=True), file=sys.stderr)


def _adopt_browser_libraries(browser_libs: str | None, cache: Path) -> None:
    if browser_libs and not (cache / "browser-libs").exists():
        # Keep reusable dependencies in the plugin cache, outside consumers.
        shutil.copytree(Path(browser_libs).expanduser().resolve(), cache / "browser-libs")


def _setup(args, root: Path, contract: dict, backend: str, distro: str | None, workspace: Path, cache: Path, python: Path) -> int:
    cache.mkdir(parents=True, exist_ok=True)
    ready, _ = _installed_identity(python, contract)
    if ready and (not args.render or _render_check(python, args.browser_libs)[0]):
        if args.render:
            _adopt_browser_libraries(args.browser_libs, cache)
        _emit(_identity(backend, distro, python, contract, workspace, cache))
        return 0
    expected_python = contract.get("python")
    uv = _uv_executable()
    setup_env = os.environ.copy()
    if uv:
        setup_env["PATH"] = str(Path(uv).parent) + os.pathsep + setup_env.get("PATH", "")
    base_python = expected_python and shutil.which(f"python{expected_python}")
    if not ready:
        if uv:
            _run([uv, "venv", "--seed", "--python", expected_python or "3", python.parent.parent], env=setup_env)
        elif base_python:
            _run([base_python, "-m", "venv", python.parent.parent])
        else:
            raise RuntimeErrorMessage(f"Cannot create managed runtime: Python {expected_python} and uv are unavailable. Install uv or Python {expected_python}, then rerun: python {Path(__file__).resolve()} --workspace {workspace} setup")
    if args.requirements:
        req = Path(args.requirements).resolve()
        if not req.is_file():
            raise RuntimeErrorMessage(f"Model requirements file is missing: {req}")
        # cadgen is installed only from the verified receipt wheel, never from an index.
        lines = req.read_text(encoding="utf-8").splitlines()
        filtered = [line for line in lines if not re.match(r"\s*cadgen(?:\s|[<=>!~;\[]|$)", line, re.I)]
        filtered_path = cache / "model-requirements.txt"
        filtered_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")
        if uv:
            _run([uv, "pip", "install", "--python", python, "-r", filtered_path], env=setup_env)
        else:
            _run([python, "-m", "pip", "install", "-r", filtered_path])
    adapter = root / "scripts" / "install-cadgen-runtime.py"
    if not adapter.is_file():
        raise RuntimeErrorMessage(f"Marketplace verified-wheel adapter is missing: {adapter}")
    # Seeded pip lets the marketplace adapter record standard PEP 610 wheel
    # provenance. It also resolves cadgen's required dependencies from this
    # verified wheel after project pins have been installed; cadgen itself is
    # never looked up by name on an index.
    install_args = [python, adapter]
    if args.render:
        install_args.append("--snapshot")
    _run(install_args, env=setup_env)
    if args.render:
        _run([python, "-m", "playwright", "install", "chromium"])
        render_ok, render_detail = _render_check(python, args.browser_libs)
        if not render_ok:
            detail = render_detail[-6000:] if render_detail else "unknown Chromium startup error"
            raise RuntimeErrorMessage(f"Playwright/Chromium setup did not pass a launch check: {detail}. Check browser shared libraries or pass --browser-libs with an isolated library directory.")
        _adopt_browser_libraries(args.browser_libs, cache)
    ok, reason = _installed_identity(python, contract)
    if not ok:
        raise RuntimeErrorMessage(f"Managed runtime setup did not establish wheel provenance: {reason}")
    _emit(_identity(backend, distro, python, contract, workspace, cache))
    return 0


def _render_check(python: Path, browser_libs: str | None) -> tuple[bool, str]:
    env = os.environ.copy()
    if browser_libs:
        env["LD_LIBRARY_PATH"] = str(Path(browser_libs).expanduser().resolve()) + (os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    check = "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()"
    result = _run([python, "-c", check], check=False, capture=True, env=env)
    return (result.returncode == 0, (result.stderr or "").strip())


def _dispatch(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="CAD project directory that owns the artifacts")
    parser.add_argument("--backend", help=argparse.SUPPRESS)
    parser.add_argument("--plugin-root", help="Installed marketplace plugin root (development/testing override)")
    parser.add_argument("--pythonpath", help="Project-relative source directory added to PYTHONPATH")
    parser.add_argument("--requirements", help="Optional model-specific Python requirements file")
    parser.add_argument("--browser-libs", help="Optional isolated browser shared-library directory")
    parser.add_argument("--render", action="store_true", help="Install Playwright and its matching Chromium browser")
    parser.add_argument("mode", choices=("configure-wsl", "setup", "status", "doctor", "python"))
    parser.add_argument("mode_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.backend:
        os.environ[INTERNAL_BACKEND] = args.backend
    if args.mode == "configure-wsl":
        distro = args.mode_args[0] if args.mode_args else ""
        if not distro or len(args.mode_args) != 1:
            raise RuntimeErrorMessage("Usage: cad-runtime.py configure-wsl <installed-wsl2-distro>")
        if os.name == "nt":
            _check_wsl_distribution(distro)
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"wslDistro": distro}, indent=2) + "\n", encoding="utf-8")
        print(f"Configured WSL distribution {distro!r} in {path}")
        return 0
    if not args.workspace:
        raise RuntimeErrorMessage("--workspace is required for setup, status, doctor, and python modes.")
    workspace_arg = str(Path(args.workspace).expanduser().resolve())
    args.workspace = workspace_arg
    root = _plugin_root(args.plugin_root)
    contract = _contract(root)
    backend, distro = _backend()
    forwarded = ["--workspace", workspace_arg, "--plugin-root", str(root)]
    for flag in ("--pythonpath", "--requirements", "--browser-libs"):
        value = getattr(args, flag[2:].replace("-", "_"))
        if value:
            if flag == "--requirements":
                req = Path(value).expanduser()
                value = str((Path(workspace_arg) / req).resolve() if not req.is_absolute() else req.resolve())
            elif flag == "--pythonpath" and Path(value).is_absolute():
                value = str(Path(value).expanduser().resolve())
            forwarded.extend((flag, value))
            setattr(args, flag[2:].replace("-", "_"), value)
    if args.render:
        forwarded.append("--render")
    forwarded.append(args.mode)
    if args.mode_args:
        forwarded.extend(args.mode_args)
    # Re-enter this same stdlib helper under Linux for all Windows requests.
    if _is_windows():
        result = _wsl_call(distro, forwarded, check=False)
        return result.returncode
    workspace, cache, python = _paths(args, contract)
    if not args.browser_libs and (cache / "browser-libs").is_dir():
        args.browser_libs = str(cache / "browser-libs")
    cache.mkdir(parents=True, exist_ok=True)
    store = cache / "store" / hashlib.sha256(str(workspace).encode()).hexdigest()[:16]
    store.mkdir(parents=True, exist_ok=True)
    if args.mode == "setup":
        return _setup(args, root, contract, backend, distro, workspace, cache, python)
    info = _identity(backend, distro, python, contract, workspace, cache)
    _emit(info)
    if args.mode in ("status", "doctor"):
        ok, reason = _installed_identity(python, contract)
        if not ok:
            raise RuntimeErrorMessage(f"Managed CAD runtime is not ready ({reason}). Run: python {Path(__file__).resolve()} --workspace {workspace} --plugin-root {root} setup")
        if args.mode == "doctor" and args.render:
            render_env = os.environ.copy()
            if args.browser_libs:
                render_env["LD_LIBRARY_PATH"] = str(Path(args.browser_libs).expanduser().resolve()) + (os.pathsep + render_env["LD_LIBRARY_PATH"] if render_env.get("LD_LIBRARY_PATH") else "")
            browser = _run([python, "-c", "from pathlib import Path; import sys; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); x=Path(p.chromium.executable_path); print(x); sys.exit(3) if not x.is_file() else None; b=p.chromium.launch(headless=True); b.close(); p.stop()"], check=False, capture=True, env=render_env)
            if browser.returncode == 3:
                raise RuntimeErrorMessage("Playwright is installed but its matching Chromium browser is missing. Run setup with --render.")
            if browser.returncode:
                detail = (browser.stderr or "").strip()[-6000:] or "unknown browser startup error"
                if "No module named 'playwright'" in detail or 'No module named "playwright"' in detail:
                    raise RuntimeErrorMessage("Playwright is missing. Run setup with --render to install Playwright and matching Chromium.")
                raise RuntimeErrorMessage(f"Playwright/Chromium or a required shared library is unavailable: {detail}. Install the matching browser with setup --render; provide isolated libraries with --browser-libs when needed.")
        return 0
    if args.mode == "python":
        command = args.mode_args
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            raise RuntimeErrorMessage("python mode requires a script or module after --")
        ok, reason = _installed_identity(python, contract)
        if not ok:
            raise RuntimeErrorMessage(f"Managed CAD runtime is not ready ({reason}). Run: python {Path(__file__).resolve()} --workspace {workspace} --plugin-root {root} setup")
        env = os.environ.copy()
        env["CADGEN_CACHE_DIR"] = str(store)
        env["CADGEN_DAEMON"] = "1"
        env["CADGEN_JOBS"] = "1"
        env["CADGEN_COMPONENT_WORKERS"] = "1"
        env["CADGEN_VALIDATE_WORKERS"] = "1"
        env["CADGEN_DAEMON_SPARES"] = "0"
        env["CADGEN_DAEMON_MAX_WORKERS"] = "4"
        if args.pythonpath:
            source = Path(args.pythonpath).expanduser()
            source = source.resolve() if source.is_absolute() else (workspace / source).resolve()
            env["PYTHONPATH"] = str(source) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        if args.browser_libs:
            env["LD_LIBRARY_PATH"] = str(Path(args.browser_libs).resolve()) + (os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        return subprocess.run([str(python), *command], cwd=workspace, env=env).returncode
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _dispatch(argv)
    except RuntimeErrorMessage as exc:
        print(f"cad-runtime: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
