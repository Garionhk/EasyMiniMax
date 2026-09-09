"""EasyMiniMax Setup - installs EasyMiniMax into an existing EasyAI folder.

Start it with:   python EasyMiniMaxSetup.py

A separate program from EasyMiniMax itself. It writes into the EasyAI folder you
choose and nowhere else, and it never modifies EasyAI's own settings - it only
reads them, to find out where ComfyUI is.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from EasyMiniMax import _make_console_utf8_safe   # noqa: E402  (shared helper)

TITLE = "EasyMiniMax Setup"


def _report(problem: str) -> None:
    """Say what went wrong, however little of Qt is working.

    The launcher uses pythonw, so there is no console for a traceback to land
    in: without this, a failure before the window exists looks like the program
    simply not starting.
    """
    print(problem, file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        box = QMessageBox(QMessageBox.Critical, TITLE,
                          f"{TITLE} could not start.")
        box.setDetailedText(problem)
        box.exec()
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, problem, TITLE, 0x10)
        except Exception:
            pass


def main() -> int:
    _make_console_utf8_safe()

    from PySide6.QtWidgets import QApplication

    from app import i18n
    from app.ui import theme
    from setup.ui import SetupWindow

    app = QApplication(sys.argv)
    i18n.start()
    theme.apply(app)

    window = SetupWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        _report(traceback.format_exc())
        sys.exit(1)
