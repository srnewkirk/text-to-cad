import assert from "node:assert/strict";
import test from "node:test";

import { pythonLaunchFailureMessage } from "./cad-python.mjs";

test("Python launch failures name the selected interpreter and supported override", () => {
  const message = pythonLaunchFailureMessage("python3", new Error("spawn python3 ENOENT"));

  assert.match(message, /with python3/);
  assert.match(message, /spawn python3 ENOENT/);
  assert.match(message, /VIEWER_CAD_PYTHON/);
  assert.match(message, /CADGEN_PYTHON does not select/);
});

test("Python launch failure formatting accepts non-Error failures", () => {
  assert.match(pythonLaunchFailureMessage("custom-python", "unavailable"), /unavailable/);
});
