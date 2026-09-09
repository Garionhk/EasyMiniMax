"""The dialog that offers to install the missing add-ons.

This writes into the user's ComfyUI folder, so it is deliberately a dialog and
not a silent step at startup. It names each add-on, where it comes from, and the
exact folder it will be written to, and does nothing until the button is
pressed. A program that quietly clones repositories into someone else's
installation has overstepped, however convenient that would be.

The install itself runs on a worker thread so the window keeps painting, and the
log is shown as it happens rather than after - a long git clone with a frozen
dialog in front of it is indistinguishable from a crash.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from app.i18n import t
from app.setup import nodes
from app.ui import theme
from app.ui.scroll import breakable, fit_to_screen, vertical_scroll

#: What this dialog would like, and the least it can be without clipping. It
#: grows as the install log fills, so it needs a floor that is not its content.
WANTED = (660, 560)
SMALLEST = (580, 300)


class _InstallWorker(QThread):
    """Runs the clones off the interface thread."""

    said = Signal(str)
    done = Signal(object)         # nodes.InstallReport

    def __init__(self, statuses, nodes_dir: Path, python: Path | None, parent=None):
        super().__init__(parent)
        self._statuses = statuses
        self._nodes_dir = nodes_dir
        self._python = python
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        installer = nodes.Installer(
            self._nodes_dir, self._python,
            on_say=self.said.emit, should_stop=lambda: self._cancelled)
        try:
            self.done.emit(installer.run(self._statuses))
        except Exception as e:                  # never let a thread die silently
            report = nodes.InstallReport()
            report.failed.append(str(e))
            self.done.emit(report)


class AddOnDialog(QDialog):
    """What is missing, what will be done about it, and then a restart offer."""

    #: The user asked for ComfyUI to be restarted so the new nodes load.
    restart_requested = Signal()

    def __init__(self, statuses, nodes_dir: Path, python: Path | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("Add-ons needed"))
        self._statuses = [s for s in statuses if s.needs_install]
        self._nodes_dir = Path(nodes_dir)
        self._python = python
        self._worker: _InstallWorker | None = None
        self.report: nodes.InstallReport | None = None

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(18, 16, 18, 10)
        column.setSpacing(12)

        title = QLabel(t("This workflow needs one or two ComfyUI add-ons"))
        title.setObjectName("Title")
        title.setWordWrap(True)
        column.addWidget(title)

        column.addWidget(self._packs_panel())

        target = QLabel(t("They will be installed into:\n{path}",
                          path=self._nodes_dir))
        target.setObjectName("Hint")
        target.setWordWrap(True)
        target.setTextInteractionFlags(Qt.TextSelectableByMouse)
        column.addWidget(target)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setVisible(False)
        self.log.setFixedHeight(120)
        column.addWidget(self.log)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)               # indeterminate: git gives no total
        self.bar.setVisible(False)

        self.buttons = QDialogButtonBox()
        self.install_btn = QPushButton(t("Install them"))
        self.install_btn.setObjectName("Primary")
        self.install_btn.clicked.connect(self._start)
        self.buttons.addButton(self.install_btn, QDialogButtonBox.AcceptRole)

        # Short, because three buttons side by side set the width of the whole
        # dialog, and "Show me how to do it myself" alone was 381 pixels of it.
        self.manual_btn = QPushButton(t("Do it by hand"))
        self.manual_btn.setToolTip(t(
            "Show the commands to install these yourself, for when the "
            "download will not work."))
        self.manual_btn.clicked.connect(self._show_manual)
        self.buttons.addButton(self.manual_btn, QDialogButtonBox.HelpRole)

        self.later_btn = QPushButton(t("Not now"))
        self.later_btn.clicked.connect(self.reject)
        self.buttons.addButton(self.later_btn, QDialogButtonBox.RejectRole)

        shell.addWidget(vertical_scroll(page), 1)

        # The progress bar and the buttons stay put while the log scrolls.
        footer = QWidget()
        footer_column = QVBoxLayout(footer)
        footer_column.setContentsMargins(18, 8, 18, 14)
        footer_column.setSpacing(8)
        footer_column.addWidget(self.bar)
        footer_column.addWidget(self.buttons)
        shell.addWidget(footer)

        fit_to_screen(self, WANTED, SMALLEST)

        if not nodes.git_available():
            self.install_btn.setEnabled(False)
            self.install_btn.setToolTip(t(
                "Git is not installed, so this cannot be done automatically."))
            self._show_manual()

    # -- layout ------------------------------------------------------------
    def _packs_panel(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(9)

        for status in self._statuses:
            pack = status.pack
            row = QVBoxLayout()
            row.setSpacing(1)

            name = QLabel(pack.name if pack.required
                          else t("{name}  (optional)", name=pack.name))
            name.setObjectName("Heading")
            name.setWordWrap(True)
            row.addWidget(name)

            why = QLabel(t(pack.why))
            why.setObjectName("Hint")
            why.setWordWrap(True)
            row.addWidget(why)

            url = QLabel(breakable(pack.url))
            url.setWordWrap(True)
            # The clean address, for copying and for anyone who wants to read
            # it without the invisible break points.
            url.setToolTip(pack.url)
            url.setStyleSheet(f"color:{theme.DIM}; font-size:11px;")
            url.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(url)

            column.addLayout(row)
        return panel

    def _show_manual(self) -> None:
        self.log.setVisible(True)
        self.log.setPlainText(
            nodes.manual_instructions(self._statuses, self._nodes_dir))

    # -- the install -------------------------------------------------------
    def _start(self) -> None:
        self.install_btn.setEnabled(False)
        self.manual_btn.setEnabled(False)
        self.later_btn.setText(t("Stop"))
        self.log.setVisible(True)
        self.log.setPlainText("")
        self.bar.setVisible(True)

        self._worker = _InstallWorker(self._statuses, self._nodes_dir, self._python)
        self._worker.said.connect(self._say)
        self._worker.done.connect(self._finished)
        self._worker.start()

    def _say(self, message: str) -> None:
        self.log.appendPlainText(message)

    def _finished(self, report: nodes.InstallReport) -> None:
        self.report = report
        self.bar.setVisible(False)
        self.later_btn.setText(t("Close"))
        self.manual_btn.setEnabled(True)

        if report.failed:
            self._say("")
            for problem in report.failed:
                self._say("• " + problem)
            self._say("")
            self._say(t("You can still install them by hand:"))
            self._say(nodes.manual_instructions(self._statuses, self._nodes_dir))
            self.install_btn.setText(t("Try again"))
            self.install_btn.setEnabled(nodes.git_available())
            return

        # Custom nodes are only registered when ComfyUI starts, so an install
        # that works is still invisible until it restarts. Saying so - and
        # offering to do it - is the difference between "it installed" and "it
        # installed and now works".
        self._say("")
        self._say(t("Installed. ComfyUI has to restart before it can see them."))
        restart = QPushButton(t("Restart the AI engine now"))
        restart.setObjectName("Primary")
        restart.clicked.connect(self._on_restart)
        self.buttons.addButton(restart, QDialogButtonBox.ApplyRole)
        self.install_btn.setVisible(False)

    def _on_restart(self) -> None:
        self.restart_requested.emit()
        self.accept()

    def reject(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        super().reject()


def offer(parent, statuses, nodes_dir: Path, python: Path | None) -> AddOnDialog | None:
    """Show the dialog if anything is missing. Returns it, or None."""
    if not any(s.needs_install for s in statuses):
        return None
    dialog = AddOnDialog(statuses, nodes_dir, python, parent)
    return dialog
