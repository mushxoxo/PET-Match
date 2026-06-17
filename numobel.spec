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
cache, and TLS certs are missed by PyInstaller's static analysis.

OAuth client: if a ``google_client.json`` (a *Desktop app* client downloaded
from the Google Cloud Console) sits next to this spec at build time, it is
bundled so end users can just click "Connect Google…" and authorize — no
pasting. A Desktop-app client secret is not confidential (security comes from
the loopback redirect), so bundling it is safe. The file is gitignored and the
bundling is optional: a clean checkout without it still builds, and users can
instead drop ``google_client.json`` next to the executable or set the
``NUMOBEL_GOOGLE_CLIENT_ID`` / ``NUMOBEL_GOOGLE_CLIENT_SECRET`` env vars.
"""

import os

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None


hidden = (
    collect_submodules("googleapiclient")
    + collect_submodules("google")
    + ["httplib2", "uritemplate"]
)
datas = collect_data_files("googleapiclient") + collect_data_files("certifi")

# Bundle the pre-configured OAuth client only if present (it's gitignored).
if os.path.exists("google_client.json"):
    datas += [("google_client.json", ".")]


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
