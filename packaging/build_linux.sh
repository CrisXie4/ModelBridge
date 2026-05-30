#!/usr/bin/env bash
# ============================================================================
#  ModelBridge — Linux build script
# ----------------------------------------------------------------------------
#  Like build_macos.sh, this MUST be run on Linux (PyInstaller can't
#  cross-compile). The output is a tarball — far simpler than RPM/DEB
#  packaging and works on any modern distro.
#
#  Build on the OLDEST glibc you want to support. PyInstaller links
#  against the host's glibc, so a binary built on Ubuntu 24.04 won't
#  run on CentOS 7. For widest compatibility, build inside an
#  ``manylinux2014`` container (see packaging/README.md).
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="$(grep -E '^version' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
ARCH="$(uname -m)"  # x86_64 / aarch64
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
./dist/mbridge/mbridge version

echo "=== [2/2] Packaging as tar.gz ==="
TAR_NAME="mbridge-${VERSION}-linux-${ARCH}.tar.gz"
tar -C dist -czf "${OUT_DIR}/${TAR_NAME}" mbridge

echo
echo "=== Done ==="
echo "Tarball: ${OUT_DIR}/${TAR_NAME}"
echo "Install for a single user:"
echo "  tar xzf ${TAR_NAME} -C \$HOME/.local"
echo "  ln -sf \$HOME/.local/mbridge/mbridge \$HOME/.local/bin/mbridge"
