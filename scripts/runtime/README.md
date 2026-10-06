# Installed personal CAD runtime

`cad-runtime.py` is the standard-library bootstrap for `cad@homelab-plugins`.
The source repository owns selection, setup, invocation and diagnostics. The
personal marketplace supplies `runtime/cadgen-<version>.json`, the matching
hashed wheel, and `scripts/install-cadgen-runtime.py`. Installing the plugin
from this source checkout is not supported.

Resolve the installed skill path to find its plugin root. On Windows, use an
available host Python interpreter (the project's existing interpreter or the
app's bundled Python can bootstrap it). Select the installed WSL2 distro once:

```text
python <plugin-root>/scripts/runtime/cad-runtime.py configure-wsl Ubuntu
```

`CAD_RUNTIME_WSL_DISTRO` overrides that selection for a single invocation.
Windows requests always enter the selected WSL2 distro, with no native Windows
fallback. Native Linux/macOS requests stay native. The helper neither changes
WSL resource settings nor installs OS packages.

For each workspace:

```text
python <plugin-root>/scripts/runtime/cad-runtime.py --workspace <workspace> --render setup
python <plugin-root>/scripts/runtime/cad-runtime.py --workspace <workspace> --render doctor
python <plugin-root>/scripts/runtime/cad-runtime.py --workspace <workspace> python -- src/part.py
python <plugin-root>/scripts/runtime/cad-runtime.py --workspace <workspace> python -- -m cadgen.cli step inspect validate STEP/part.step
python <plugin-root>/scripts/runtime/cad-runtime.py --workspace <workspace> python -- -m cadgen.viewer --host 127.0.0.1 --json
```

Use `--pythonpath src` for project imports and `--requirements <file>` for
model-specific dependencies in a flat requirements file, consistently for setup
and later commands. Nested requirement/constraint files are rejected because
they would defeat the content key and could resolve cadgen from an index. Paths
resolve from the declared workspace. Cadgen is installed only from the verified
private wheel. Its environment is keyed by wheel identity and requirements
content; each workspace/environment pair has a separate derived store. Model
scripts are ordinary Python programs. The helper does not introduce a model
build command.

Rendering includes Playwright plus matching Chromium. On a minimal Linux host,
`--browser-libs <directory>` can adopt an existing isolated library set during
rendering setup; the helper copies it into its own shared cache for subsequent
projects and wheel upgrades.
Doctor actually launches Chromium and preserves import and shared-library
diagnostics. Missing browser binaries, missing Playwright and broken dependency
imports are distinct failures. Snapshot workers receive the current client's
library environment, even when the daemon was warmed before rendering setup.

Viewer announces readiness and then serves in the foreground. Start it in a
managed background tool session and read its returned JSON URL; subsequent
launches reuse it. Do not queue another command after a newly started server.

The stderr JSON receipt records the backend, distro, Python, kernel, verified
wheel SHA, workspace and store. Resource defaults are one geometry/component/
validation job, zero speculative spares and four resident workers; the existing
cancellable admission policy remains authoritative.

For source development, `--plugin-root` accepts a verified delivery fixture.
Candidate helper/wheel tests are not installed-plugin acceptance. Final checks
must use the normal helper shipped in a managed marketplace installation,
without this override, in a fresh session.
