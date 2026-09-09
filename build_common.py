"""What the three builds leave out, and why.

Read by EasyMiniMax.spec and EasyMiniMax Setup.spec, so the answer lives in one
place rather than drifting between the two. Copied from EasyAI, whose builds
have the same problem and the same shared Python underneath them.

The problem this solves: PyInstaller works out what to include by following
imports, and this machine's Python is a shared one carrying torch, transformers,
opencv and the rest. Anything those pull in that is merely *importable* can end
up inside a viewer's download. Pillow arriving for the Read tab is what made it
obvious - it dragged in numpy, and numpy dragged in 6 MB of OpenBLAS, for code
that never runs.

Everything named here was checked rather than assumed:

  * the Python packages by loading the real window, reading a PNG's prompt,
    grabbing a video frame and calling the engine, then asking sys.modules what
    had actually been imported;
  * the Qt libraries by reading the import tables of the DLLs that are used -
    Qt6Multimedia, Qt6Widgets and Qt6Gui name only Core, Gui and Network
    between them, never Quick, Qml or Pdf.

If a program ever does start using one of these, delete the line. Leaving a
needed module in this list produces an ImportError at run time, in a windowed
build where nobody can see it - so treat additions carefully and rebuild.
"""
from __future__ import annotations

#: Never imported by any of the three programs, on any path.
UNUSED_PACKAGES = [
    # Pulled in through Pillow's optional array interop. Never called, and the
    # single most expensive passenger: numpy itself plus the OpenBLAS it ships.
    "numpy",
    # urllib3 talks TLS through the standard library's ssl module. This arrives
    # only because it is installed, and costs 3.4 MB of compiled Rust.
    "cryptography",
    "yaml",
    # Nothing here draws a chart or opens a Tk window, but both are large and
    # both are easy for a stray import to reach.
    "matplotlib",
    "tkinter",
]

#: Qt modules with no counterpart in this codebase. Everything is QtWidgets;
#: there is no QML, no 3D and no PDF anywhere.
UNUSED_QT = [
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtOpenGLWidgets",
]

#: Image formats nothing reads. app/prompts.py accepts PNG, WEBP and JPEG, so
#: those plugins stay; AVIF alone is 4 MB of decoder for a format no result is
#: ever saved in.
UNUSED_IMAGE_FORMATS = [
    "PIL.AvifImagePlugin",
]

EXCLUDES = UNUSED_PACKAGES + UNUSED_QT + UNUSED_IMAGE_FORMATS

#: Qt libraries PySide6's PyInstaller hook copies in wholesale, whether or not
#: anything imports the matching Python module - so excluding the module above
#: is not enough to stop the DLL travelling. Matched case-insensitively against
#: the file name.
UNUSED_QT_LIBRARIES = (
    "qt6quick",
    "qt6qml",
    "qt6pdf",
)

#: Deliberately *not* removed, though it looks like an easy 7 MB: Qt falls back
#: to opengl32sw.dll when a machine has no usable OpenGL driver, and this is
#: shipped to viewers whose machines cannot be checked first. A build that
#: cannot draw its own window is a worse outcome than a larger download.
KEPT_ON_PURPOSE = ("opengl32sw.dll",)


def strip_unused(binaries):
    """Drop the collected-but-unreferenced Qt libraries from a build.

    Takes and returns PyInstaller's list of (name, path, kind) tuples.
    """
    kept = []
    for entry in binaries:
        name = str(entry[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
        if any(name.startswith(unused) for unused in UNUSED_QT_LIBRARIES):
            continue
        kept.append(entry)
    removed = len(binaries) - len(kept)
    print(f"[build_common] left out {removed} unused Qt libraries")
    return kept


# -- telling one build from another ---------------------------------------

#: Extra libraries the *installer* has no use for. The program shows video and
#: reads pictures; the installer shows a log and a progress bar. Qt Multimedia
#: alone drags in avcodec, which is 14 MB of video decoder for a window that
#: never plays anything.
SETUP_ONLY_EXCLUDES = [
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PIL", "PIL.Image",
]


def version_file(build_dir, app_name, description, version):
    """Write a Windows version resource and return its path.

    Without one, every build looks identical in Explorer: same size, same icon,
    no way to tell whether the exe in a folder is the one just built or one from
    a fortnight ago. With it, right-click -> Properties -> Details shows the
    version and the build time.
    """
    import datetime
    import os

    parts = [int(p) for p in str(version).split(".")[:3]] + [0, 0, 0]
    quad = tuple(parts[:4])
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    os.makedirs(build_dir, exist_ok=True)
    path = os.path.join(build_dir, f"version_{app_name.replace(' ', '_')}.txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={quad}, prodvers={quad},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('FileDescription', '{description}'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', '{app_name}'),
        StringStruct('OriginalFilename', '{app_name}.exe'),
        StringStruct('ProductName', '{app_name}'),
        StringStruct('ProductVersion', '{version}  (built {stamp})'),
        StringStruct('Comments', 'Built {stamp}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""")
    return path


def drop_from_binaries(binaries, names):
    """Remove entries whose name matches any of `names`, case-insensitively.

    Used for libraries a particular build has no use for and that PyInstaller
    collects anyway.

    Not for a file that is also in `datas`: PyInstaller's TOC is keyed by name,
    the binaries entry wins, and the datas one is quietly discarded - so
    dropping the binary drops the only copy. That mistake produced an installer
    with no program inside it and no other sign that anything was wrong.
    """
    wanted = {n.lower() for n in names}
    kept, dropped = [], 0
    for entry in binaries:
        name = str(entry[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name in wanted:
            dropped += 1
            continue
        kept.append(entry)
    if dropped:
        print(f"[build_common] dropped {dropped} binary copy of "
              f"{', '.join(sorted(wanted))} (already carried as data)")
    return kept


#: Folders whose contents are the program's source, for the staleness check.
#: dist/ and build/ are outputs, __pycache__ is derived, and the .venv is not
#: ours - including any of them would make every build look stale.
_SOURCE_DIRS = ("app", "setup", "workflows", "assets")
_SOURCE_FILES = ("EasyMiniMax.py", "EasyMiniMaxSetup.py", "build_common.py",
                 "EasyMiniMax.spec", "EasyMiniMax Setup.spec")


def newest_source_time(project_dir):
    """When the program's source was last touched.

    Used to catch a built exe that is older than the code in front of it, which
    is the one build mistake that produces something that looks right and is
    not: an installer carrying last week's program.
    """
    import os

    newest = 0.0
    for name in _SOURCE_FILES:
        path = os.path.join(project_dir, name)
        if os.path.isfile(path):
            newest = max(newest, os.path.getmtime(path))

    for folder in _SOURCE_DIRS:
        root_dir = os.path.join(project_dir, folder)
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for file_name in files:
                if file_name.endswith(".pyc"):
                    continue
                try:
                    newest = max(newest, os.path.getmtime(
                        os.path.join(root, file_name)))
                except OSError:
                    continue
    return newest
