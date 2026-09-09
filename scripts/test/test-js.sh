#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=scripts/test/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

cd "$REPO_ROOT"

section "cadgen-js tests"
npm --prefix packages/cadgen-js test

section "CAD Viewer tests"
npm --prefix apps/viewer run test
