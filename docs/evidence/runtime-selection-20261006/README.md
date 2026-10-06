# Plugin-owned runtime selection candidate

The reusable WSL correction belongs to `srnewkirk/text-to-cad`, distributed as
`cad@homelab-plugins`. Camera workspace configuration is a consumer input.
This record covers source/candidate verification on 2026-10-06. It does not
record a new release or installed-plugin acceptance.

## Implementation and package provenance

Core source commit: `866f6d5c9ac6a96dc349b826c733b13c3908d1d8`, on
`codex/managed-wsl-runtime`. The commit containing this record additionally
shares adopted browser libraries across wheel upgrades and isolates derived
stores by workspace and dependency environment. The canonical `VERSION`
remains 0.5.6 during development.

The plugin owns WSL selection, environment setup, invocation and diagnostics.
Windows enters its configured WSL2 distro without a Windows fallback; native
Linux/macOS selection remains native. Setup uses a receipt-verified private
wheel. Snapshot jobs receive the current client's library environment, and
Playwright import errors distinguish a missing package from a broken dependency.

The isolated delivery fixture was never registered or installed as a plugin.
Its complete `cadgen-0.5.6-py3-none-any.whl` contains all 179 Python files matching
canonical source, plus freshly bundled Viewer assets. Wheel SHA-256:
`b03920c98d15c46258e342842a6ce5e6d60cc7d7ff9005de35a02d415aadca62`.
The fixture receipt identifies the core source commit above and marks the
package as a candidate. Later bootstrap-only changes do not change wheel bytes.

## Checks and observed results

- 37 focused Python tests passed: runtime bootstrap, package boundaries, skill
  containment, daemon client/environment and snapshot import diagnostics.
- Official skill validation passed for cad, cad-viewer, dxf, urdf, srdf and sdf.
- All 369 JS/TS/JSON/lock/build-script inputs matched the existing Linux build
  cache after line-ending normalization. The one bundle entry point rebuilt
  assets there, and its freshness check passed.
- Actual backend: Ubuntu WSL2, Linux kernel
  `6.18.40.1-microsoft-standard-WSL2`, Python 3.12.14. The interpreter was
  `/home/srnew/.cache/cad-runtime/6ea0eaf81cb65c04bca6/environments/base/venv/bin/python`.
- Rendering setup installed Playwright and matching Chromium in the managed
  candidate environment; a real Chromium launch passed. Existing isolated
  browser libraries were adopted into
  `/home/srnew/.cache/cad-runtime/browser-libs`. No OS packages were installed.
- The tiny `models/examples/runtime-selection/coupon.py` fixture built STEP
  and STL, then returned `current` on repeat. Its geometry tree stayed
  `b49f0526e6e7cc3468ee8e651e958608acb011d585fc4ddc2f9ad75bfecf3b2b`.
  Final workspace/environment store key: `f13e7849ac326a16`.
- Tiny STEP validation had zero failures. Snapshot rendering succeeded with
  an already warm daemon, including after the final cache-scope adjustments.
- The camera project's existing `STEP/design/camera_display/display_holder.step`
  passed read-only validation with zero failures and rendered successfully.
  The candidate did not build or edit camera model source in this wave.
- Candidate Viewers launched for both workspaces on ports 3246 and 3247;
  both responded HTTP 200. These were verification sessions, not durable links.
  The user's existing Viewer on port 3245 was left alone.

Local detailed logs are ignored under source `tmp/managed-runtime-*`; generated
snapshots and tiny fixture outputs are under `models/tmp/runtime-selection-fixture`.
Native Linux/macOS selection is covered by unit tests; no live macOS acceptance
was performed. Existing bounded admission and worker defaults were preserved.

## Remaining release boundary

The installed marketplace package 0.5.6 remains unchanged. The working camera
project prototype and its pending model edits are preserved until a managed
replacement passes normal installed-plugin acceptance.

A new user-selected version, reviewed marketplace promotion, release closure
and fresh-session checks through the installed helper remain required. Those
checks must omit the candidate `--plugin-root` override and exercise the tiny
fixture and camera workspace sequentially. Consumer adapter cleanup follows
successful installed verification. Physical camera fit, strength and cable
checks remain physical checks; this runtime correction does not establish them.
