"""The installer's window.

Two screens in one: choose where and what, then watch it happen. Follows
EasyAI's setup/ui.py — the work runs on a QThread and only ever reaches the
window through signals, and the log is shown as it happens rather than at the
end, because a long download behind a frozen dialog is indistinguishable from a
crash.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from app import i18n
from app.i18n import t
from app.ui import theme
from app.ui.scroll import fit_to_screen, vertical_scroll
from setup import steps
from setup.download import Progress, format_eta

TITLE = "EasyMiniDirector Setup"


class _Worker(QThread):
    """Runs the installation off the interface thread."""

    said = Signal(str)
    progressed = Signal(object)      # setup.download.Progress
    done = Signal(object)            # steps.Report

    def __init__(self, choices: steps.Choices, parent=None):
        super().__init__(parent)
        self.choices = choices
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        installer = steps.Installer(
            self.choices, on_say=self.said.emit,
            on_progress=self.progressed.emit,
            should_stop=lambda: self._cancelled)
        try:
            self.done.emit(installer.run())
        except Exception as e:                  # never let a thread die silently
            import traceback
            traceback.print_exc()
            report = steps.Report()
            report.failed.append(str(e))
            self.done.emit(report)


class SetupWindow(QMainWindow):

    #: What the page needs to show everything at once, measured rather than
    #: guessed. Anything smaller scrolls, which is the point.
    #: 720, not 700: the language chooser in the header added 15 pixels, and a
    #: WANTED that is short of its own page means the window opens with a
    #: scrollbar it did not need. 730, not 720: renaming EasyMiniMax to
    #: EasyMiniDirector lengthened the blurb enough to wrap onto an extra
    #: line. Measured, not guessed - there is a test.
    WANTED = (780, 730)
    SMALLEST = (560, 320)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(TITLE)
        self._worker: _Worker | None = None
        self._finished = False
        self._build()
        fit_to_screen(self, self.WANTED, self.SMALLEST, margin=80)

    # ------------------------------------------------------------- layout
    def _build(self) -> None:
        """A scrolling page, with the progress and the buttons pinned below it.

        Everything used to be in one fixed column, which gave the window a hard
        minimum of 733 x 596 - it simply refused to be made smaller, so on a
        short screen the lower half was unreachable rather than merely
        off-screen. Now the page scrolls and the window can be any size.

        The progress bar and the buttons stay out of the scrolling area on
        purpose: they are what someone looks at while an install runs, and
        having to scroll to find out whether it is still going, or to reach
        Stop, would be the same fault in a different place.
        """
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(22, 20, 22, 14)
        column.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel(t("Install EasyMiniDirector"))
        title.setObjectName("Title")
        header.addWidget(title, 1)

        # A language chooser here and not only in the program's own Settings:
        # this window is the first thing anyone sees, and the Settings that
        # would let them change it do not exist until the install has finished.
        # i18n.start() already follows Windows' own language, so this is for
        # someone whose Windows is in one language and who wants the other.
        self.language = QComboBox()
        for code, name in i18n.available().items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(i18n.current())))
        self.language.currentIndexChanged.connect(self._on_language)
        header.addWidget(self.language, 0, Qt.AlignTop)
        column.addLayout(header)

        blurb = QLabel(t(
            "EasyMiniDirector goes into your EasyAI folder, so both programs sit "
            "together and share the same ComfyUI. Its own settings, workflows "
            "and videos are kept separately, in an EasyMiniDirector folder inside "
            "it — EasyAI's are never touched."))
        blurb.setObjectName("Hint")
        blurb.setWordWrap(True)
        column.addWidget(blurb)

        column.addWidget(self._folder_box())
        column.addWidget(self._options_box())

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        # Low enough that the whole page still fits a short screen. It has its
        # own scrollbar and grows into any spare height, so this is a floor and
        # not the size it usually is.
        self.log.setMinimumHeight(120)
        column.addWidget(self.log, 1)

        outer.addWidget(vertical_scroll(page), 1)

        pinned = QWidget()
        bottom = QVBoxLayout(pinned)
        bottom.setContentsMargins(22, 8, 22, 16)
        bottom.setSpacing(8)

        self.bar = QProgressBar()
        self.bar.setVisible(False)
        bottom.addWidget(self.bar)

        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        bottom.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_btn = QPushButton(t("Close"))
        self.close_btn.clicked.connect(self.close)
        buttons.addWidget(self.close_btn)

        self.install_btn = QPushButton(t("Install"))
        self.install_btn.setObjectName("Primary")
        self.install_btn.setMinimumHeight(38)
        self.install_btn.setMinimumWidth(150)
        self.install_btn.clicked.connect(self._start)
        buttons.addWidget(self.install_btn)
        bottom.addLayout(buttons)
        outer.addWidget(pinned)

        self.setCentralWidget(root)
        self._check_folder()

    def _on_language(self, _index: int) -> None:
        """Switch language, and rebuild the window in it.

        Rebuilt rather than retranslated: every label here is created in one
        place and none of them is worth keeping a reference to just for this.
        The chooser is disabled once an install starts, so there is never a log
        or a progress bar to lose.
        """
        code = self.language.currentData()
        if not code or code == i18n.current():
            return
        i18n.load(code)
        i18n.remember(code)

        folder = self.folder.text()
        wanted = (self.want_ollama.isChecked(), self.want_model.isChecked())
        self._build()
        self.folder.setText(folder)
        self.want_ollama.setChecked(wanted[0])
        self.want_model.setChecked(wanted[1])
        self._check_folder()

    def _folder_box(self) -> QWidget:
        box = QFrame()
        box.setObjectName("Panel")
        box.setStyleSheet(
            f"QFrame#Panel {{ background:{theme.SURFACE_2};"
            f" border:1px solid {theme.LINE}; border-radius:9px; }}")
        column = QVBoxLayout(box)
        column.setContentsMargins(12, 11, 12, 11)
        column.setSpacing(6)

        label = QLabel(t("Your EasyAI folder"))
        label.setObjectName("Heading")
        column.addWidget(label)

        row = QHBoxLayout()
        row.setSpacing(6)
        guess = steps.guess_easyai_dir()
        self.folder = QLineEdit(str(guess) if guess else "")
        self.folder.setPlaceholderText(t("The folder EasyAI.exe is in"))
        self.folder.textChanged.connect(self._check_folder)
        row.addWidget(self.folder, 1)
        browse = QPushButton(t("Browse…"))
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        column.addLayout(row)

        self.folder_hint = QLabel("")
        self.folder_hint.setWordWrap(True)
        column.addWidget(self.folder_hint)
        return box

    def _options_box(self) -> QWidget:
        box = QFrame()
        box.setObjectName("Panel")
        box.setStyleSheet(
            f"QFrame#Panel {{ background:{theme.SURFACE_2};"
            f" border:1px solid {theme.LINE}; border-radius:9px; }}")
        column = QVBoxLayout(box)
        column.setContentsMargins(12, 11, 12, 11)
        column.setSpacing(6)

        label = QLabel(t("The prompt helper  (optional)"))
        label.setObjectName("Heading")
        column.addWidget(label)

        self.want_ollama = QCheckBox(t("Install Ollama  (about 1.6 GB)"))
        self.want_ollama.setChecked(True)
        self.want_ollama.setToolTip(t(
            "The official installer, run quietly. It needs no administrator "
            "and installs into your own user folder."))
        column.addWidget(self.want_ollama)

        self.want_model = QCheckBox(
            t("Fetch the vision model {model}  (about 6 GB)", model=steps.MODEL))
        self.want_model.setChecked(True)
        column.addWidget(self.want_model)

        hint = QLabel(t(
            "These let the program describe your reference pictures and write "
            "descriptions for you. Everything else works without them — leave "
            "them unticked to save 7.6 GB, and the buttons simply stay hidden."))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        column.addWidget(hint)

        self.want_ollama.toggled.connect(
            lambda on: self.want_model.setEnabled(on) or
            (None if on else self.want_model.setChecked(False)))
        return box

    # -------------------------------------------------------------- checks
    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, t("Where is EasyAI?"), self.folder.text())
        if chosen:
            self.folder.setText(chosen)

    def _check_folder(self) -> None:
        """Say whether this is the right folder, before anything is written."""
        path = self.folder.text().strip()
        if not path:
            self.folder_hint.setText(t("Choose the folder EasyAI.exe is in."))
            self.folder_hint.setStyleSheet(f"color:{theme.MUTED};")
            self.install_btn.setEnabled(False)
            return

        if steps.looks_like_easyai(path):
            settings = steps.read_easyai_settings(Path(path))
            comfyui = settings.get("comfyui_dir")
            message = t("EasyAI found.")
            if comfyui:
                message += " " + t("ComfyUI is at {path}.", path=comfyui)
            else:
                message += " " + t("EasyAI has not been set up yet, so "
                                   "ComfyUI's location will be asked for on "
                                   "first run.")
            self.folder_hint.setText(message)
            self.folder_hint.setStyleSheet(f"color:{theme.OK};")
            self.install_btn.setEnabled(not self._finished)
        else:
            self.folder_hint.setText(t(
                "No EasyAI.exe in that folder. Nothing will be installed until "
                "this points at your EasyAI installation."))
            self.folder_hint.setStyleSheet(f"color:{theme.WARN};")
            self.install_btn.setEnabled(False)

    # ------------------------------------------------------------ running
    def _start(self) -> None:
        choices = steps.Choices(
            easyai_dir=Path(self.folder.text().strip()),
            install_ollama=self.want_ollama.isChecked(),
            install_model=self.want_model.isChecked(),
        )
        self.install_btn.setEnabled(False)
        self.folder.setEnabled(False)
        self.want_ollama.setEnabled(False)
        self.want_model.setEnabled(False)
        self.language.setEnabled(False)
        self.close_btn.setText(t("Stop"))
        self.log.setPlainText("")

        self._worker = _Worker(choices, self)
        self._worker.said.connect(self._say)
        self._worker.progressed.connect(self._on_progress)
        self._worker.done.connect(lambda report: self._on_done(report, choices))
        self._worker.start()

    def _say(self, message: str) -> None:
        self.log.appendPlainText(message)

    def _on_progress(self, progress: Progress) -> None:
        if not progress.total:
            self.bar.setVisible(False)
            self.status.setText(progress.name)
            return
        self.bar.setVisible(True)
        self.bar.setRange(0, 100)
        self.bar.setValue(progress.percent)
        self.status.setText(t(
            "{name}  ·  {done} of {total} GB  ·  {speed} MB/s  ·  {eta}",
            name=progress.name or "",
            done=f"{progress.done / 1e9:.1f}", total=f"{progress.total / 1e9:.1f}",
            speed=f"{progress.speed / 1e6:.1f}",
            eta=format_eta(progress.eta_seconds)))

    def _on_done(self, report: steps.Report, choices: steps.Choices) -> None:
        self._finished = True
        self.bar.setVisible(False)
        self.status.setText("")
        self.close_btn.setText(t("Close"))
        self._say("")
        self._say("=" * 58)
        self._say(steps.summarise(report, choices))

        self.install_btn.setText(t("Run it again"))
        self.install_btn.setEnabled(True)
        self.language.setEnabled(True)
        self.folder.setEnabled(True)
        self.want_ollama.setEnabled(True)
        self.want_model.setEnabled(self.want_ollama.isChecked())

        if report.ok:
            QMessageBox.information(self, TITLE, t(
                "EasyMiniDirector is installed.\n\nYou will find EasyMiniDirector.exe in "
                "{folder}, beside EasyAI.exe.", folder=choices.easyai_dir))
        else:
            QMessageBox.warning(self, TITLE, "\n".join(
                [t("Some of it did not work:"), ""] +
                [f"• {problem}" for problem in report.failed] +
                ["", t("The program will still start, and will offer to "
                       "finish the missing pieces itself.")]))

    # ------------------------------------------------------------ closing
    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            answer = QMessageBox.question(
                self, TITLE,
                t("The install is still going. Stop it?\n\nA part-finished "
                  "download is kept, so starting again carries on from where "
                  "it stopped."),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._worker.cancel()
            self._worker.wait(5000)
        super().closeEvent(event)
