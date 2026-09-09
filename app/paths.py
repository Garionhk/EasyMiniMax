"""Where things live, whether running from source or from a built .exe.

Two different questions get two different answers, and conflating them is the
classic way to lose a user's work:

* **Files shipped with the program** - the language catalogues, the install
  catalogue, the licences, the icon. Read-only. Inside a one-file build these
  are unpacked into a temporary folder that Windows deletes on exit.

* **Files the user creates and keeps** - settings.json, their workflows, every
  picture they make. These must sit next to the .exe, because the temporary
  folder is gone the moment the program closes.

Running from source both are the project folder, which is why the distinction
never came up until the first build.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: The checked-out project, used whenever this is not a frozen build.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: The folder our own files live in, inside the EasyAI installation.
#:
#: A built copy sits *beside* EasyAI.exe, so that both programs are in one place.
#: That is also why this exists: both compute settings.json from data_dir(), and
#: returning the exe's own folder would hand the two programs the same file to
#: read and write. They would trade window positions, and each would carry the
#: other's settings around for ever.
APP_FOLDER = "EasyMiniMax"


def frozen() -> bool:
    """True when running from a PyInstaller build rather than from source."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Read-only files that shipped inside the program."""
    if frozen():
        # _MEIPASS is the unpacked one-file bundle; for a one-folder build it
        # is absent and everything sits beside the executable instead.
        return Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).parent)
    return PROJECT_ROOT


def install_dir() -> Path:
    """The folder holding the program itself.

    In a built copy this is EasyAI's own folder, which we are a guest in: it
    holds EasyAI.exe, EasyAI's settings.json, and now our two exes as well.
    Nothing of ours is ever written directly into it.
    """
    if frozen():
        return Path(sys.executable).parent
    return PROJECT_ROOT


def data_dir() -> Path:
    """Files the user creates and expects to still be there tomorrow.

    One level below the exe in a built copy - see APP_FOLDER. From a source
    checkout the project folder is already ours alone, so it is used as-is.
    """
    if frozen():
        return install_dir() / APP_FOLDER
    return PROJECT_ROOT
