# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the DTI-ALPS desktop app.

Produces a onedir bundle under ``dist/dti-alps/`` which the AppImage build
(``packaging/build-appimage.sh``) wraps into a single portable executable.

Only the app itself is bundled. The external neuroimaging suites (MRtrix3,
FSL) are NOT bundled -- they must be installed separately and on PATH.
"""

import glob

# Ship the JHU ROI templates (repo-root templates/*.nii.gz). The test_shapes/
# fixtures live in a subdir and are intentionally excluded.
datas = [(path, "templates") for path in glob.glob("templates/*.nii.gz")]

a = Analysis(
    ["packaging/dti_alps_entry.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dti-alps",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="dti-alps",
)
