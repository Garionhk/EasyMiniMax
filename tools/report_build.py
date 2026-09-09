"""What the build actually produced, printed at the end of Build EXE.bat.

"Done" is not evidence. This says the version, the size and the build time of
each exe, and - the part that matters - checks each one against the newest
source file, so a stale build cannot slip through unnoticed.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import __version__                       # noqa: E402
from build_common import newest_source_time       # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent
EXES = ("EasyMiniMax.exe", "EasyMiniMax Setup.exe")


def main() -> int:
    newest = newest_source_time(str(PROJECT))
    print(f"  version      {__version__}")
    print(f"  code changed {time.strftime('%H:%M:%S', time.localtime(newest))}")
    print()

    stale = []
    for name in EXES:
        path = PROJECT / "dist" / name
        if not path.is_file():
            print(f"  MISSING      {name}")
            stale.append(name)
            continue
        built = path.stat().st_mtime
        mark = "ok " if built >= newest else "OLD"
        print(f"  [{mark}] {name:<24} {path.stat().st_size / 1048576:6.1f} MB   "
              f"built {time.strftime('%H:%M:%S', time.localtime(built))}")
        if built < newest:
            stale.append(name)

    # The installer carries the program inside it, so it can never be smaller.
    # This is here because dropping the wrong TOC entry once produced a healthy
    # looking installer with no program in it, and nothing else noticed.
    app = PROJECT / "dist" / "EasyMiniMax.exe"
    setup = PROJECT / "dist" / "EasyMiniMax Setup.exe"
    if app.is_file() and setup.is_file():
        if setup.stat().st_size <= app.stat().st_size:
            print()
            print("  !! The installer is smaller than the program it carries.")
            print("     It is missing the program - do not ship it.")
            return 1

    if stale:
        print()
        print("  !! These are older than the code they were built from:")
        for name in stale:
            print(f"       {name}")
        print("     Something went wrong - do not ship these.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
