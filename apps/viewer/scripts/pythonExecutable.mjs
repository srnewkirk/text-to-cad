import fs from "node:fs";
import path from "node:path";

function firstExistingFile(candidates, exists = fs.existsSync) {
  return candidates.find((candidate) => candidate && exists(candidate)) || "";
}

export function resolveViewerPython({
  env = process.env,
  platform = process.platform,
  appRoot,
  cwd = process.cwd(),
  exists = fs.existsSync,
} = {}) {
  const configured = String(env.VIEWER_PYTHON || "").trim();
  if (configured) {
    return { executable: configured, source: "VIEWER_PYTHON" };
  }

  const roots = [
    String(env.INIT_CWD || "").trim(),
    appRoot ? path.resolve(appRoot, "..", "..") : "",
    cwd,
  ].filter(Boolean);
  const relativeCandidates = platform === "win32"
    ? [path.join(".venv", "Scripts", "python.exe")]
    : [path.join(".venv", "bin", "python")];
  const discovered = firstExistingFile(
    roots.flatMap((root) => relativeCandidates.map((relative) => path.resolve(root, relative))),
    exists,
  );
  if (discovered) {
    return { executable: discovered, source: "project virtual environment" };
  }
  return {
    executable: platform === "win32" ? "python" : "python3",
    source: "PATH fallback",
  };
}
