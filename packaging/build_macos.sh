#!/usr/bin/env bash
# ============================================================================
#  ModelBridge — macOS build script
# ----------------------------------------------------------------------------
#  PyInstaller can't cross-compile, so this MUST be run on macOS. It
#  produces a portable directory at ``dist/mbridge/`` (with a unix
#  ``mbridge`` binary) and packages it as a tar.gz under
#  ``packaging/Output/``.
#
#  We deliberately do NOT build a ``.app`` bundle or a ``.pkg`` —
#  ``mbridge`` is a CLI, not a GUI app, and Mac users on the command
#  line want the binary on PATH, not in /Applications. The README walks
#  them through extracting the tarball and adding the bin dir to PATH.
#
#  Notarisation is left as a TODO — see packaging/README.md.
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

REPO_ROOT="$(pwd)"
VERSION="$(grep -E '^version' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
ARCH="$(uname -m)"   # arm64 or x86_64
OUT_DIR="packaging/Output"
mkdir -p "$OUT_DIR"

echo "=== [1/2] PyInstaller ==="
python3 -m pip install --upgrade pip pyinstaller >/dev/null
python3 -m PyInstaller packaging/mbridge.spec --clean --noconfirm

if [ ! -x "dist/mbridge/mbridge" ]; then
    echo "PyInstaller did not produce dist/mbridge/mbridge — aborting." >&2
    exit 1
fi

echo "Quick smoke test:"
./dist/mbridge/mbridge --version

echo "=== [2/2] Packaging as tar.gz ==="
TAR_NAME="mbridge-${VERSION}-macos-${ARCH}.tar.gz"
# -C dist lets the archive expand cleanly: ``tar xzf ...`` → ``mbridge/``.
tar -C dist -czf "${OUT_DIR}/${TAR_NAME}" mbridge

echo
echo "=== Done ==="
echo "Tarball: ${OUT_DIR}/${TAR_NAME}"
echo "Smoke-test it elsewhere with:"
echo "  tar xzf ${TAR_NAME} && ./mbridge/mbridge --version"
