# -*- mode: python ; coding: utf-8 -*-
"""EasyMiniMax - the program itself.

Built by "Build EXE.bat". A spec rather than a command line because the unused
Qt libraries can only be dropped after PyInstaller has finished collecting
them - see build_common.py for what is left out and why.
"""
import os
import sys

sys.path.insert(0, SPECPATH)
from app import __version__ as VERSION
from build_common import EXCLUDES, strip_unused, version_file

APP_NAME = 'EasyMiniMax'

a = Analysis(
    ['EasyMiniMax.py'],
    pathex=[],
    binaries=[],
    # The layout has to match what app/paths.py resolve_dir() expects: the
    # workflow is read from resource_dir()/workflows at run time.
    # lang/ holds the translation catalogues; app/i18n.py reads them from
    # resource_dir()/lang, so the name here has to match.
    datas=[('workflows', 'workflows'), ('assets/icons', 'assets/icons'),
           ('lang', 'lang')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES + [],
    noarchive=False,
    optimize=0,
)
a.binaries = strip_unused(a.binaries)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icons/EasyMiniMax.ico'],
    # So Explorer can tell one build from another: Properties -> Details shows
    # the version and when it was built.
    version=version_file(os.path.join(SPECPATH, 'build'), APP_NAME,
                         'Make short videos with MiniMax H3', VERSION),
)
