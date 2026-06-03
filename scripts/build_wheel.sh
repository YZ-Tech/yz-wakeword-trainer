#!/usr/bin/env bash
# Build a wheel that includes the bundled SPA assets.
#
# Why a wrapper script: `python -m build` alone won't run npm — but the
# wheel needs yz_wakeword_trainer/static/ populated for the
# `pip install`-and-go promise to hold. PEP 517 build hooks for "run npm
# first" exist (setuptools cmdclass, hatchling hooks, custom backends)
# but they all add real complexity. A shell wrapper is honest, explicit,
# and trivial to read. Use this instead of `python -m build` directly.
#
# Usage (from satellites/yz-wakeword-trainer/):
#     bash scripts/build_wheel.sh
#
# Output: dist/yz_wakeword_trainer-{ver}-py3-none-any.whl with the SPA inside.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

echo "── Step 1/3: install UI deps (idempotent)"
cd "$ROOT/ui"
npm install --no-audit --no-fund

echo "── Step 2/3: build SPA → yz_wakeword_trainer/static/"
npm run build:pages

# Sanity check — the wheel needs these files present.
if [[ ! -f "$ROOT/yz_wakeword_trainer/static/index.html" ]]; then
    echo "✗ SPA build did not produce yz_wakeword_trainer/static/index.html — aborting" >&2
    exit 1
fi

echo "── Step 3/3: build Python wheel"
cd "$ROOT"
# `python -m build` requires `pip install build` — install if missing.
python -m build --wheel --no-isolation

echo
echo "✓ wheel built. Contents include the SPA — verify with:"
echo "    unzip -l dist/yz_wakeword_trainer-*.whl | grep -E 'static/'"
echo
echo "Install test:"
echo "    pip install dist/yz_wakeword_trainer-*.whl"
echo "    python -m yz_wakeword_trainer  # → http://127.0.0.1:9001/ should serve the UI"
