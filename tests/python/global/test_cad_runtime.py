from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.python.support.paths import repo_path


HELPER = repo_path("scripts/runtime/cad-runtime.py")
SPEC = importlib.util.spec_from_file_location("cad_runtime", HELPER)
cad_runtime = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(cad_runtime)


class CadRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "plugin"
        (self.root / ".codex-plugin").mkdir(parents=True)
        (self.root / "runtime").mkdir()
        (self.root / ".codex-plugin" / "plugin.json").write_text('{"version":"0.5.6"}', encoding="utf-8")
        self.wheel = self.root / "runtime" / "cadgen-0.5.6-py3-none-any.whl"
        self.wheel.write_bytes(b"managed wheel fixture")
        self.digest = hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        self.receipt = {"name": "cadgen", "version": "0.5.6", "wheel": self.wheel.name,
                        "sha256": self.digest, "python": "3.12.14"}
        (self.root / "runtime" / "cadgen-0.5.6.json").write_text(json.dumps(self.receipt), encoding="utf-8")
        self.workspace = Path(self.temp.name) / "CAD 零件 project"
        self.workspace.mkdir()

    def test_linux_library_paths_are_not_translated_to_windows_mounts(self):
        outputs = [
            subprocess.CompletedProcess([], 0, stdout="Ubuntu Running 2\n"),
            subprocess.CompletedProcess([], 0, stdout="/mnt/c/helper.py\n"),
            subprocess.CompletedProcess([], 0, stdout="/mnt/c/project\n"),
            subprocess.CompletedProcess([], 0),
        ]
        with mock.patch.object(cad_runtime.shutil, "which", return_value="wsl.exe"), \
             mock.patch.object(cad_runtime, "_run", side_effect=outputs) as run:
            cad_runtime._wsl_call("Ubuntu", ["--workspace", r"C:\project", "--browser-libs", "/home/user/libs", "doctor"])
        self.assertIn("/home/user/libs", run.call_args.args[0])
        self.assertEqual(run.call_count, 4)

    def test_browser_libraries_are_adopted_outside_the_consumer_project(self):
        libs = self.workspace / "libs"
        libs.mkdir()
        (libs / "lib-example.so").write_bytes(b"isolated library fixture")
        cache = Path(self.temp.name) / "runtime-cache" / "release-one"
        cad_runtime._adopt_browser_libraries(str(libs), cache)
        self.assertEqual((cache.parent / "browser-libs" / "lib-example.so").read_bytes(), b"isolated library fixture")
        # Another wheel release uses the same adopted OS dependency cache.
        cad_runtime._adopt_browser_libraries(str(libs), cache.parent / "release-two")

    def test_changed_model_dependencies_get_a_separate_derived_store(self):
        req = self.workspace / "requirements.txt"
        req.write_text("numpy==2.0.0\n", encoding="utf-8")
        args = mock.Mock(workspace=str(self.workspace), requirements=str(req))
        contract = cad_runtime._contract(self.root)
        workspace, cache, first_python = cad_runtime._paths(args, contract)
        first_store = cad_runtime._store_path(workspace, cache, first_python)
        self.assertEqual(first_store, cad_runtime._store_path(workspace, cache, first_python))
        req.write_text("numpy==2.1.0\n", encoding="utf-8")
        workspace, cache, second_python = cad_runtime._paths(args, contract)
        self.assertNotEqual(first_python, second_python)
        self.assertNotEqual(first_store, cad_runtime._store_path(workspace, cache, second_python))
        req.write_text("-r shared.txt\n", encoding="utf-8")
        with self.assertRaisesRegex(cad_runtime.RuntimeErrorMessage, "flat file"):
            cad_runtime._paths(args, contract)

    def test_adding_rendering_does_not_recreate_a_ready_environment(self):
        args = mock.Mock(requirements=None, render=True, browser_libs=None)
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "install-cadgen-runtime.py").touch()
        cache = Path(self.temp.name) / "cache"
        python = cache / "venv" / "bin" / "python"
        with mock.patch.object(cad_runtime, "_installed_identity", return_value=(True, "verified")), \
             mock.patch.object(cad_runtime, "_render_check", side_effect=[(False, "missing browser"), (True, "")]), \
             mock.patch.object(cad_runtime, "_uv_executable", return_value="uv"), \
             mock.patch.object(cad_runtime, "_identity", return_value={}), \
             mock.patch.object(cad_runtime, "_emit"), \
             mock.patch.object(cad_runtime, "_run") as run:
            cad_runtime._setup(args, self.root, self.receipt, "linux", None, self.workspace, cache, python)
        self.assertFalse(any("venv" in call.args[0] for call in run.call_args_list))
        self.assertTrue(any("--snapshot" in call.args[0] for call in run.call_args_list))

    def test_receipt_selects_matching_wheel_and_contract(self):
        contract = cad_runtime._contract(self.root)
        self.assertEqual(contract["sha256"], self.digest)
        self.assertEqual(contract["version"], "0.5.6")
        self.assertEqual(len(contract["contract"]), 20)

    def test_missing_and_tampered_wheels_fail_closed(self):
        self.wheel.write_bytes(b"tampered")
        with self.assertRaisesRegex(cad_runtime.RuntimeErrorMessage, "hash mismatch"):
            cad_runtime._contract(self.root)
        self.wheel.unlink()
        with self.assertRaisesRegex(cad_runtime.RuntimeErrorMessage, "missing or unreadable"):
            cad_runtime._contract(self.root)

    def test_docker_distribution_is_rejected(self):
        completed = subprocess.CompletedProcess([], 0, stdout="  NAME                   STATE           VERSION\n* Ubuntu                 Stopped         2\n  docker-desktop         Stopped         2\n", stderr="")
        with mock.patch.object(cad_runtime.shutil, "which", return_value="wsl.exe"), \
             mock.patch.object(cad_runtime, "_run", return_value=completed):
            with self.assertRaisesRegex(cad_runtime.RuntimeErrorMessage, "unavailable"):
                cad_runtime._wsl_call("docker-desktop", [])

    def test_wsl_config_requires_an_installed_wsl2_distribution(self):
        self.assertTrue(cad_runtime._valid_wsl_listing("* Ubuntu Running 2\n", "Ubuntu"))
        self.assertFalse(cad_runtime._valid_wsl_listing("Ubuntu Stopped 1\n", "Ubuntu"))
        self.assertFalse(cad_runtime._valid_wsl_listing("docker-desktop Stopped 2\n", "docker-desktop"))

    def test_wsl_reentry_preserves_unicode_and_spaced_script_arguments(self):
        outputs = [
            subprocess.CompletedProcess([], 0, stdout="Ubuntu Running 2\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="/mnt/c/helper.py\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="/mnt/c/a b\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="/mnt/c/plugin root\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="/mnt/c/a b/model 零件.py\n", stderr=""),
            subprocess.CompletedProcess([], 19, stdout="", stderr=""),
        ]
        with mock.patch.object(cad_runtime.shutil, "which", return_value="wsl.exe"), \
             mock.patch.object(cad_runtime, "_run", side_effect=outputs) as run:
            result = cad_runtime._wsl_call("Ubuntu", ["--workspace", r"C:\a b", "--plugin-root", r"C:\plugin root",
                                                       "python", "--", r"C:\a b\model 零件.py", "--label", "two words"], check=False)
        self.assertEqual(result.returncode, 19)
        final_argv = run.call_args.args[0]
        self.assertEqual(final_argv[-5:], ["python", "--", "/mnt/c/a b/model 零件.py", "--label", "two words"])
        self.assertIn("/mnt/c/a b/model 零件.py", final_argv)
        self.assertIn("two words", final_argv)

    def test_configured_windows_routes_to_selected_wsl_without_fallback(self):
        contract = cad_runtime._contract(self.root)
        with mock.patch.object(cad_runtime, "_plugin_root", return_value=self.root), \
             mock.patch.object(cad_runtime, "_contract", return_value=contract), \
             mock.patch.object(cad_runtime, "_backend", return_value=("wsl", "Ubuntu")), \
             mock.patch.object(cad_runtime, "_is_windows", return_value=True), \
             mock.patch.object(cad_runtime, "_wsl_call", return_value=subprocess.CompletedProcess([], 0)) as call:
            result = cad_runtime._dispatch(["--workspace", str(self.workspace), "--plugin-root", str(self.root), "python", "--", "model.py", "a b"])
        self.assertEqual(result, 0)
        call.assert_called_once()
        args, kwargs = call.call_args
        self.assertEqual(args[0], "Ubuntu")
        self.assertIn("model.py", args[1])
        self.assertIn("a b", args[1])

    def test_inherited_backend_marker_cannot_bypass_windows_detection(self):
        with mock.patch.object(cad_runtime.os, "name", "nt"), \
             mock.patch.dict(cad_runtime.os.environ, {cad_runtime.INTERNAL_BACKEND: "wsl:Ubuntu"}):
            self.assertTrue(cad_runtime._is_windows())

    def test_native_python_preserves_cwd_pythonpath_argv_and_exit_status(self):
        contract = cad_runtime._contract(self.root)
        req = self.workspace / "requirements.txt"
        req.write_text("build123d==0.11.1\n", encoding="utf-8")
        source = self.workspace / "src"
        source.mkdir()
        fake_python = Path(self.temp.name) / "venv" / "bin" / "python"
        identity = {"backend": "linux", "distribution": None, "python": str(fake_python),
                    "pythonVersion": "3.12.14", "cadgenVersion": "0.5.6", "wheelSha256": self.digest,
                    "runtimeContract": contract["contract"], "provenance": "verified managed wheel",
                    "workspace": str(self.workspace), "store": "store"}
        with mock.patch.object(cad_runtime, "_plugin_root", return_value=self.root), \
             mock.patch.object(cad_runtime, "_contract", return_value=contract), \
             mock.patch.object(cad_runtime, "_backend", return_value=("linux", None)), \
             mock.patch.object(cad_runtime, "_is_windows", return_value=False), \
             mock.patch.object(cad_runtime, "_paths", return_value=(self.workspace, Path(self.temp.name) / "cache", fake_python)), \
             mock.patch.object(cad_runtime, "_installed_identity", return_value=(True, "verified managed wheel")), \
             mock.patch.object(cad_runtime, "_identity", return_value=identity), \
             mock.patch.object(cad_runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 23)) as run, \
             mock.patch.object(cad_runtime, "_emit"):
            status = cad_runtime._dispatch(["--workspace", str(self.workspace), "--plugin-root", str(self.root),
                                            "--pythonpath", "src", "--requirements", str(req), "python", "--",
                                            "model.py", "--label", "two words"])
        self.assertEqual(status, 23)
        argv, options = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(argv, [str(fake_python), "model.py", "--label", "two words"])
        self.assertEqual(options["cwd"], self.workspace)
        self.assertIn(str(source), options["env"]["PYTHONPATH"])
        self.assertEqual(options["env"]["CADGEN_JOBS"], "1")
        self.assertEqual(options["env"]["CADGEN_DAEMON_SPARES"], "0")

    def test_import_failure_keeps_the_actual_import_error_diagnostic(self):
        failure = subprocess.CompletedProcess([], 1, stdout="", stderr="ImportError: libTKBRep.so: cannot open shared object file")
        fake_python = Path(self.temp.name) / "python"
        fake_python.touch()
        with mock.patch.object(cad_runtime, "_run", return_value=failure):
            ok, reason = cad_runtime._installed_identity(fake_python, self.receipt)
        self.assertFalse(ok)
        self.assertIn("ImportError: libTKBRep.so", reason)

    def test_installed_wheel_requires_matching_hash_and_venv_module_path(self):
        prefix = Path(self.temp.name) / "venv"
        fake_python = prefix / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        fake_python.touch()
        direct = json.dumps({"archive_info": {"hash": f"sha256={self.digest}"}})
        payload = {"version": "0.5.6", "python": "3.12.14", "direct": direct,
                   "module": str(prefix / "lib" / "python3.12" / "site-packages" / "cadgen" / "__init__.py"),
                   "prefix": str(prefix)}
        with mock.patch.object(cad_runtime, "_run", return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")):
            self.assertEqual(cad_runtime._installed_identity(fake_python, self.receipt), (True, "verified managed wheel"))
        payload["direct"] = json.dumps({"archive_info": {"hash": "sha256=" + "0" * 64}})
        with mock.patch.object(cad_runtime, "_run", return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")):
            ok, reason = cad_runtime._installed_identity(fake_python, self.receipt)
        self.assertFalse(ok)
        self.assertIn("marketplace-managed wheel hash", reason)
        payload["direct"] = json.dumps({"archive_info": {"hashes": {"sha256": self.digest}}})
        with mock.patch.object(cad_runtime, "_run", return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")):
            self.assertEqual(cad_runtime._installed_identity(fake_python, self.receipt), (True, "verified managed wheel"))
        payload["direct"] = direct
        payload["module"] = str(Path(self.temp.name) / "checkout" / "cadgen.py")
        with mock.patch.object(cad_runtime, "_run", return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")):
            ok, reason = cad_runtime._installed_identity(fake_python, self.receipt)
        self.assertFalse(ok)
        self.assertIn("inside the selected runtime", reason)

    def test_setup_seeds_pip_and_keeps_cadgen_out_of_model_index_resolution(self):
        workspace = self.workspace
        requirements = workspace / "requirements.txt"
        requirements.write_text("trimesh==5.1.0\ncadgen==0.5.6\n", encoding="utf-8")
        cache = Path(self.temp.name) / "cache"
        python = cache / "environments" / "fixture" / "venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.touch()
        adapter = self.root / "scripts" / "install-cadgen-runtime.py"
        adapter.parent.mkdir(parents=True)
        adapter.touch()
        args = SimpleNamespace(requirements=str(requirements), render=False, browser_libs=None)
        calls = []

        def record(argv, **kwargs):
            calls.append((list(map(str, argv)), kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with mock.patch.object(cad_runtime, "_installed_identity", side_effect=[(False, "missing"), (True, "verified managed wheel")]), \
             mock.patch.object(cad_runtime, "_uv_executable", return_value="/home/user/.local/bin/uv"), \
             mock.patch.object(cad_runtime, "_run", side_effect=record), \
             mock.patch.object(cad_runtime, "_identity", return_value={"backend": "linux"}), \
             mock.patch.object(cad_runtime, "_emit"):
            status = cad_runtime._setup(args, self.root, self.receipt, "linux", None, workspace, cache, python)
        self.assertEqual(status, 0)
        self.assertIn("--seed", calls[0][0])
        filtered_path = python.parent.parent / "model-requirements.txt"
        self.assertIn("trimesh==5.1.0", filtered_path.read_text(encoding="utf-8"))
        self.assertNotIn("cadgen==0.5.6", filtered_path.read_text(encoding="utf-8"))
        adapter_call = next(argv for argv, _ in calls if argv[1].endswith("install-cadgen-runtime.py"))
        self.assertNotIn("--no-deps", adapter_call)


if __name__ == "__main__":
    unittest.main()
