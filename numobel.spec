# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the NUMOBEL desktop app.

Builds a single-file executable from ``run.py``. Build with:

    pyinstaller numobel.spec

The resulting binary lands in ``dist/`` (``dist/numobel.exe`` on Windows,
``dist/numobel`` on Linux/macOS). PyInstaller does NOT cross-compile: build the
Windows ``.exe`` on Windows, the Linux binary on Linux, etc.

Note: ``numobel.db`` and ``images/`` are intentionally NOT bundled. At runtime
the app anchors to the executable's own directory (see ``numobel.db.base_dir``),
so ship ``numobel.db`` (produced by ``python -m numobel.importer.run_import``)
alongside the executable; ``images/`` is created next to it on first photo add.
"""

block_cipher = None


a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="numobel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
