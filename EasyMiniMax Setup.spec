# -*- mode: python ; coding: utf-8 -*-
"""EasyMiniMax Setup - the installer, with the program inside it.

Built by "Build EXE.bat", *after* EasyMiniMax.exe, because it carries that exe
as one of its own data files and writes it out into the EasyAI folder. One file
to hand somebody, and the program can never arrive half-copied.
"""
import os
import sys
import time

sys.path.insert(0, SPECPATH)
from app import __version__ as VERSION
from build_common import (
    EXCLUDES, SETUP_ONLY_EXCLUDES, drop_from_binaries, newest_source_time,
    strip_unused, version_file,
)

APP_NAME = 'EasyMiniMax Setup'
APP_EXE = os.path.join(SPECPATH, 'dist', 'EasyMiniMax.exe')

# Built first by Build EXE.bat. Running this spec on its own before the app has
# been built would produce an installer with nothing to install, so say so
# rather than shipping one.
if not os.path.isfile(APP_EXE):
    raise SystemExit(
        "dist\\EasyMiniMax.exe is not there, so the installer would have "
        "nothing to install.\n"
        'Run "Build EXE.bat", which builds the program first.')

# And refuse a *stale* one. Building only the installer after changing the
# program would quietly ship the previous build inside it: an installer that
# looks new, installs an old program, and gives no sign of either.
app_built = os.path.getmtime(APP_EXE)
newest = newest_source_time(SPECPATH)
if app_built < newest:
    raise SystemExit(
        "dist\\EasyMiniMax.exe is older than the code it was built from "
        f"(built {time.strftime('%H:%M:%S', time.localtime(app_built))}, "
        f"code changed {time.strftime('%H:%M:%S', time.localtime(newest))}).\n"
        "Building the installer now would put the *previous* program inside "
        'it.\nRun "Build EXE.bat", which builds both, in the right order.')

a = Analysis(
    ['EasyMiniMaxSetup.py'],
    pathex=[],
    binaries=[],
    # The exe goes in at the top level of the bundle, where steps.py looks for
    # it with resource_dir() / APP_EXE.
    datas=[(APP_EXE, '.'),
           ('workflows', 'workflows'),
           ('assets/icons', 'assets/icons'),
           ('lang', 'lang')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The installer shows a log and a progress bar. It has no video preview and
    # opens no pictures, so Qt Multimedia - and the 14 MB of avcodec behind it -
    # and Pillow are dead weight here, even though the program needs both.
    excludes=EXCLUDES + SETUP_ONLY_EXCLUDES,
    noarchive=False,
    optimize=0,
)
a.binaries = strip_unused(a.binaries)
# The software-OpenGL fallback is 20 MB and exists for machines with no usable
# graphics driver. The program keeps it - it draws a video preview. The
# installer draws a log and a progress bar, and Qt falls back to plain software
# rendering for those without it.
a.binaries = drop_from_binaries(a.binaries, ['opengl32sw.dll'])

# NOTE: EasyMiniMax.exe is in datas above and must NOT be dropped from
# binaries. PyInstaller's dependency scan also files it under binaries, and its
# TOC is keyed by name - the binaries entry wins and the datas one is discarded.
# Dropping the binary therefore removes the *only* copy, and produces an
# installer with no program inside it that looks perfectly healthy otherwise.

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
                         'Installs EasyMiniMax into your EasyAI folder',
                         VERSION),
)
