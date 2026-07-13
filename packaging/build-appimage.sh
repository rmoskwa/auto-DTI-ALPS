#!/usr/bin/env bash
#
# Build a portable AppImage for DTI-ALPS.
#
#   1. PyInstaller freezes the app into a onedir bundle (dist/dti-alps/).
#   2. That bundle is assembled into an AppDir alongside the .desktop entry,
#      icon, and AppRun launcher.
#   3. appimagetool squashes the AppDir into a single dti-alps-<ver>.AppImage.
#
# Usage:
#   packaging/build-appimage.sh [VERSION]
#
# VERSION defaults to `git describe` (tag-based) and falls back to "dev".
# Run from the repository root. Requires: python with the [build,gui] extras
# installed, plus curl (to fetch appimagetool on first run).
#
# NOTE: MRtrix3 and FSL are NOT bundled -- users install those separately.
set -euo pipefail

ARCH="${ARCH:-x86_64}"
VERSION="${1:-$(git describe --tags --always 2>/dev/null || echo dev)}"
VERSION="${VERSION#v}" # strip a leading v (v0.1.0 -> 0.1.0)

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

APPDIR="build/AppDir"
OUT="dist/dti-alps-${VERSION}-${ARCH}.AppImage"

echo "==> Building DTI-ALPS AppImage (version=${VERSION}, arch=${ARCH})"

# 1. Freeze with PyInstaller.
rm -rf build/dti-alps dist/dti-alps "$APPDIR"
python -m PyInstaller --noconfirm --clean dti-alps.spec

# 2. Assemble the AppDir.
mkdir -p "$APPDIR/usr/bin"
cp -a dist/dti-alps/. "$APPDIR/usr/bin/"

install -m 0755 packaging/AppRun "$APPDIR/AppRun"
cp packaging/dti-alps.desktop "$APPDIR/dti-alps.desktop"
cp packaging/dti-alps.png "$APPDIR/dti-alps.png"

# Also install into the standard hicolor location so desktop integration
# (menu entry) works if the user opts in via appimaged/AppImageLauncher.
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp packaging/dti-alps.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/dti-alps.png"
mkdir -p "$APPDIR/usr/share/applications"
cp packaging/dti-alps.desktop "$APPDIR/usr/share/applications/dti-alps.desktop"

# 3. Fetch appimagetool if needed and squash the AppDir.
TOOL="build/appimagetool-${ARCH}.AppImage"
if [ ! -x "$TOOL" ]; then
    echo "==> Downloading appimagetool"
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
    chmod +x "$TOOL"
fi

mkdir -p dist
# APPIMAGE_EXTRACT_AND_RUN lets appimagetool run without FUSE (e.g. in CI).
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" "$APPDIR" "$OUT"

echo "==> Done: $OUT"
