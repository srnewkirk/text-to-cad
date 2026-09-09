import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { resolveViewerPython } from "./pythonExecutable.mjs";

test("explicit VIEWER_PYTHON wins", () => {
  assert.deepEqual(
    resolveViewerPython({ env: { VIEWER_PYTHON: "C:\\CAD\\python.exe" }, platform: "win32" }),
    { executable: "C:\\CAD\\python.exe", source: "VIEWER_PYTHON" },
  );
});

test("Windows discovers the launch-root virtual environment", () => {
  const root = "C:\\project";
  const expected = path.resolve(root, ".venv", "Scripts", "python.exe");
  assert.deepEqual(
    resolveViewerPython({
      env: { INIT_CWD: root },
      platform: "win32",
      appRoot: "C:\\repo\\apps\\viewer",
      exists: (candidate) => candidate === expected,
    }),
    { executable: expected, source: "project virtual environment" },
  );
});

test("Windows PATH fallback is python rather than python3", () => {
  assert.deepEqual(
    resolveViewerPython({ env: {}, platform: "win32", exists: () => false }),
    { executable: "python", source: "PATH fallback" },
  );
});

test("POSIX retains python3 as its PATH fallback", () => {
  assert.deepEqual(
    resolveViewerPython({ env: {}, platform: "linux", exists: () => false }),
    { executable: "python3", source: "PATH fallback" },
  );
});
