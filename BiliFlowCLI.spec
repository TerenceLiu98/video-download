# -*- mode: python ; coding: utf-8 -*-
# Single-file console build of the pure CLI (biliflow-cli). No Qt, no Textual.

a = Analysis(
    ['bilibili_downloader/cli/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=['packaging_hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'PySide6', 'shiboken6', 'textual', 'tkinter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='biliflow-cli',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
