#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: wsl-build.sh --source PATH --commit SHA --mirror PATH --mode check|build

Maintains an isolated WSL-native mirror of a Windows text-to-cad checkout,
installs locked dependencies, and checks or rebuilds the packaged CAD runtime.
It never pushes, publishes, promotes, or installs a Codex plugin.
EOF
}

SOURCE=""
COMMIT=""
MIRROR=""
MODE="check"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="${2:?missing --source value}"; shift 2 ;;
    --commit) COMMIT="${2:?missing --commit value}"; shift 2 ;;
    --mirror) MIRROR="${2:?missing --mirror value}"; shift 2 ;;
    --mode) MODE="${2:?missing --mode value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$SOURCE" && -d "$SOURCE/.git" ]] || { echo "Source repository is unavailable: $SOURCE" >&2; exit 2; }
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "Commit must be a full Git SHA." >&2; exit 2; }
[[ -n "$MIRROR" && "$MIRROR" == "/home/$USER/"* ]] || { echo "Mirror must be below /home/$USER." >&2; exit 2; }
[[ "$MODE" == "check" || "$MODE" == "build" ]] || { echo "Mode must be check or build." >&2; exit 2; }
grep -qi microsoft /proc/sys/kernel/osrelease || { echo "This driver requires WSL 2." >&2; exit 2; }

export PATH="$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
MARKER="$MIRROR/.git/codex-wsl-build-mirror"

if [[ ! -d "$MIRROR/.git" ]]; then
  [[ ! -e "$MIRROR" ]] || { echo "Refusing to replace non-mirror path: $MIRROR" >&2; exit 2; }
  mkdir -p "$(dirname "$MIRROR")"
  git -c "safe.directory=$SOURCE/.git" clone --no-hardlinks "$SOURCE" "$MIRROR"
  printf '%s\n' "Managed only by scripts/dev/wsl-build.sh" > "$MARKER"
fi
[[ -f "$MARKER" ]] || { echo "Refusing to modify an unmarked checkout: $MIRROR" >&2; exit 2; }

git -C "$MIRROR" -c "safe.directory=$SOURCE/.git" fetch --no-tags "$SOURCE" "$COMMIT"
git -C "$MIRROR" checkout --detach --force FETCH_HEAD

cd "$MIRROR"
[[ "$(node --version)" == v22.* ]] || { echo "Node 22 is required." >&2; exit 2; }
command -v npm >/dev/null || { echo "npm is required." >&2; exit 2; }
command -v uv >/dev/null || { echo "uv is required." >&2; exit 2; }

uv venv --clear --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
npm ci --prefix packages/cadgen-js
npm ci --prefix apps/viewer
npm ci --prefix apps/docs

export PATH="$MIRROR/.venv/bin:$PATH"
if [[ "$MODE" == "check" ]]; then
  scripts/bundle/bundle.sh --check
else
  scripts/bundle/bundle.sh --clean
  echo "Bundle rebuilt only in WSL mirror: $MIRROR"
  echo "Review generated changes there; this driver does not copy them back."
fi

git status --short
