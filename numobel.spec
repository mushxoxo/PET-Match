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

The Google sync libraries (Sheets/Drive) are bundled here via collected
submodules + data files, because their dynamic imports, bundled API-discovery
cache, and TLS certs are missed by PyInstaller's static analysis. NO OAuth
client id/secret is embedded in the binary: the user enters those at runtime in
the "Connect Google…" dialog and they are stored in the local ``numobel.db``
settings table, so the frozen binary contains no secrets.
"""

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None


hidden = (
    collect_submodules("googleapiclient")
    + collect_submodules("google")
    + ["httplib2", "uritemplate"]
)
datas = collect_data_files("googleapiclient") + collect_data_files("certifi")


a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
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
