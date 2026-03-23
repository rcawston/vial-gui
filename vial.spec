# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


ROOT = Path.cwd()
SRC = ROOT / "src" / "main" / "python"
HIDDENIMPORTS = ["hid"]
if sys.platform.startswith("linux"):
    HIDDENIMPORTS.append("hidraw")
DATAS = [
    (str(ROOT / "src" / "main" / "resources" / "base"), "resources"),
    (str(ROOT / "src" / "build" / "settings" / "base.json"), "."),
    (str(ROOT / "src" / "main" / "icons"), "icons"),
]

block_cipher = None

a = Analysis(
    [str(SRC / "main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["multiprocessing"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name="Vial-bin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    exclude_binaries=True,
)

if sys.platform == "darwin":
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        name="Vial-dist",
    )
    app = BUNDLE(
        coll,
        name="Vial.app",
        icon=str(ROOT / "src" / "main" / "icons" / "mac" / "256.png"),
        bundle_identifier="today.vial",
    )
else:
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        name="Vial",
    )
