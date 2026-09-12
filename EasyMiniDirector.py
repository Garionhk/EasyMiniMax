"""EasyMiniDirector - a plain front end for the MiniMax H3 Director workflow.

Launched with pythonw, so there is no console to print a traceback into. Any
failure before the window exists has to reach the user as a message box, or the
program simply appears not to start.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _make_console_utf8_safe() -> None:
    """Stop a stray emoji in a print() from killing the program.

    Windows consoles default to a codepage that cannot encode the characters
    used all through this interface, and print() raises rather than dropping
    them. Under pythonw the streams may not exist at all.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _report(problem: str) -> None:
    """Say what went wrong, however little of Qt is working."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "EasyMiniDirector", problem)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, problem, "EasyMiniDirector", 0x10)
        except Exception:
            print(problem)


def main() -> int:
    _make_console_utf8_safe()

    from PySide6.QtWidgets import QApplication

    from app import i18n
    from app.config import Config, ensure_folders
    from app.ui import theme
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    i18n.start()                    # before any window is built
    theme.apply(app)

    cfg = Config()
    ensure_folders(cfg)

    window = MainWindow(cfg)
    window.show()
    window.start()                  # engine bring-up behind the splash
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        _report("EasyMiniDirector could not start:\n\n" + traceback.format_exc())
        sys.exit(1)
