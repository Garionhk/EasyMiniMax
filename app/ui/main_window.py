"""The one window: describe it, cut it into shots, press Create.

Three columns, left to right in the order someone actually thinks in:

    ┌─ your video ──────────┬─ shots ────────┬─ result ─────┐
    │ what happens          │ the strip      │ live preview │
    │ reference pictures    │ a box per shot │ what you made│
    │ Turbo / Quality       │                │              │
    │ shape · length · seed │                │              │
    │ [ Create ]            │                │              │
    └───────────────────────┴────────────────┴──────────────┘

The engine bring-up, the status pill, the queue and the walk-away close are all
lifted from EasyAI, because they were the right answers there and the problems
have not changed. What is new is the middle column and the storyboard behind it.
"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter,
    QVBoxLayout, QWidget,
)

from app import __version__
from app.comfy import objectinfo
from app.comfy.client import ComfyClient
from app.comfy.launcher import ComfyLauncher
from app.config import Config
from app.h3 import graph as h3graph
from app.h3 import preflight, presets, recipe
from app.h3.timeline import Shot, Storyboard, describe
from app.i18n import plural, t
from app.jobs import EngineWorker, FreeLlm, GenerationRequest, JobResult
from app.llm import prompts as llm_prompts
from app.llm.ollama import Ollama, detect as detect_llm
from app.llm.worker import AnalyzeWorker, FreeVramWorker
from app.queue import QueueItem, QueueManager, State
from app.setup import nodes as node_setup
from app.ui import controls, theme
from app.ui.first_run import AddOnDialog
from app.ui.queue_panel import QueuePanel
from app.ui.settings_dialog import SettingsDialog
from app.ui.shot_editor import ShotEditor
from app.ui.subjects import SubjectPanel
from app.ui.voice import VoicePanel
from app.ui.widgets import PreviewPane, ResultGallery, Switch, open_folder

APP_TITLE = "EasyMiniDirector"

#: The smallest this window may be made with all three columns showing.
#:
#: Measured, not chosen: two 280px side columns plus the shot editor's own
#: 380px - which is simply the width of its two buttons - plus the splitter
#: handles and margins. Setting anything smaller does not make it smaller, it
#: makes it clipped. A column can still be dragged shut to go narrower than
#: this, which is the honest way to use a narrow screen.
MIN_WIDTH = 990
MIN_HEIGHT = 430


class StartupSplash(QDialog):
    """Shown while ComfyUI is being found or started."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(APP_TITLE)
        self.setModal(True)
        self.setFixedSize(440, 190)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel(APP_TITLE)
        title.setObjectName("Big")
        layout.addWidget(title)

        self.message = QLabel(t("Starting up…"))
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        bar = QProgressBar()
        bar.setRange(0, 0)          # indeterminate
        layout.addWidget(bar)
        layout.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.skip_btn = QPushButton(t("Carry on without it"))
        self.skip_btn.setToolTip(t(
            "Open the program anyway. You will not be able to make anything "
            "until ComfyUI is running."))
        row.addWidget(self.skip_btn)
        layout.addLayout(row)

    def set_message(self, text: str) -> None:
        self.message.setText(text)


class MainWindow(QMainWindow):

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.client = ComfyClient(cfg.server)
        self.launcher = ComfyLauncher(
            cfg.get("comfyui_dir"), cfg.get("comfyui_launcher"), self.client,
            timeout=int(cfg.get("launch_timeout") or 300))
        self.queue = QueueManager(self.client, self)

        #: The one thing being edited. Every control writes into this object,
        #: and a copy of it is what gets queued - so changing the shots for the
        #: next render cannot disturb one already waiting.
        self.storyboard = Storyboard()
        self.graph: dict = {}
        self.caps = objectinfo.Capabilities()
        self.preflight: preflight.Preflight | None = None
        self.llm = Ollama(str(cfg.get("llm_url")),
                          timeout=int(cfg.get("llm_timeout") or 120))
        self._analyze_worker: AnalyzeWorker | None = None
        self._free_worker: FreeVramWorker | None = None
        self._prompt_undo: str = ""
        self._engine_worker: EngineWorker | None = None
        self._splash: StartupSplash | None = None
        self._mine: set[int] = set()

        self.setWindowTitle(f"{APP_TITLE}  ·  MiniMax H3")
        self._build()
        self.resize(*self._opening_size())
        self._load_workflow()
        self._restore()

        self.queue.item_changed.connect(self._on_queue_item)
        self.queue.file_ready.connect(self._on_queue_file)
        self.queue.preview.connect(self._on_queue_preview)
        self.queue.emptied.connect(self._on_queue_empty)

        QShortcut(QKeySequence("Ctrl+Return"), self, self._on_create)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self._on_create)
        QShortcut(QKeySequence("F5"), self, self._check_engine)

    #: The size this window wants when there is room for it.
    WANTED = (1500, 940)

    def _opening_size(self) -> tuple[int, int]:
        """As much of the window as this screen will actually show.

        Opening at a fixed 1500 x 940 is right on a desktop and wrong on a
        laptop: on a 1366 x 768 screen it opens with a third of itself past the
        edge, and the parts that matter - Create, the result - are the parts off
        screen. So it opens to fit, and every column scrolls for the rest.
        """
        wanted_width, wanted_height = self.WANTED
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return wanted_width, wanted_height
        room = screen.availableGeometry()
        return (min(wanted_width, max(MIN_WIDTH, room.width() - 60)),
                min(wanted_height, max(MIN_HEIGHT, room.height() - 60)))

    # ------------------------------------------------------------- layout
    def _build(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        self.status_strip = controls.StatusStrip()
        self.status_strip.action.connect(self._on_status_action)
        outer.addWidget(self.status_strip)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_middle())
        splitter.addWidget(self._build_right())
        splitter.setSizes([480, 480, 500])
        # Collapsible on purpose. On a narrow screen the honest answer is not
        # three columns squeezed to 250px each - it is dragging one shut while
        # you work in the others, and dragging it back when you want it.
        splitter.setChildrenCollapsible(True)
        outer.addWidget(splitter, 1)

        self.setCentralWidget(root)
        # Small enough to fit any screen this could run on, now that every
        # column scrolls and any of them can be dragged shut.
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self._build_menu()
        self._build_status_bar()

    def _scroll(self, inner: QWidget) -> QWidget:
        from PySide6.QtWidgets import QScrollArea
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        # Never sideways. A column that scrolls in both directions hides half
        # its own controls, and every widget in here is happy to be narrower.
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Narrow enough that three of these fit a 1366-wide laptop with room to
        # spare, wide enough that the controls still read as controls.
        area.setMinimumWidth(280)
        area.setWidget(inner)
        return area

    def _build_left(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(14)

        heading = QLabel(t("Your video"))
        heading.setObjectName("Title")
        column.addWidget(heading)

        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText(t(
            "Describe the whole video: the look, the place, the mood.\n\n"
            "e.g. Cinematic desert chase at golden hour, warm sand against a "
            "deep blue sky, handheld camera.\n"
            "Audio: a low engine rumble, wind across the microphone."))
        self.prompt.setMinimumHeight(150)
        self.prompt.textChanged.connect(self._on_prompt_changed)
        column.addWidget(controls.labelled(
            t("What happens"), self.prompt,
            t("H3 makes its own sound too, so it is worth saying what you "
              "want to hear as well as see.")))

        helper = QHBoxLayout()
        helper.setSpacing(6)
        self.improve_btn = QPushButton(t("✨  Write this out for me"))
        self.improve_btn.setCursor(Qt.PointingHandCursor)
        self.improve_btn.setToolTip(t(
            "Turn a rough idea into a full description, including the sound."))
        self.improve_btn.setVisible(False)
        self.improve_btn.clicked.connect(self._improve_prompt)
        helper.addWidget(self.improve_btn, 1)

        self.undo_btn = QPushButton(t("Undo"))
        self.undo_btn.setVisible(False)
        self.undo_btn.setToolTip(t("Put back what you had written."))
        self.undo_btn.clicked.connect(self._undo_improve)
        helper.addWidget(self.undo_btn)
        column.addLayout(helper)

        self.subjects = SubjectPanel(self.storyboard.subjects)
        self.subjects.changed.connect(self._on_changed)
        self.subjects.analyze_asked.connect(self._describe_picture)
        column.addWidget(self.subjects)

        self.voice = VoicePanel(self.storyboard)
        self.voice.changed.connect(self._on_changed)
        column.addWidget(self.voice)

        self.profile = controls.ProfileSwitch(str(self.cfg.get("profile")))
        self.profile.changed.connect(self._on_profile_changed)
        column.addWidget(controls.labelled(t("Speed"), self.profile))

        self.resolution = controls.ResolutionPicker(str(self.cfg.get("resolution")))
        self.resolution.changed.connect(self._on_resolution_changed)
        column.addWidget(controls.labelled(t("Shape"), self.resolution))

        self.duration = controls.DurationPicker(
            float(self.cfg.get("duration_seconds") or 5.0))
        self.duration.changed.connect(self._on_duration_changed)
        column.addWidget(controls.labelled(t("How long"), self.duration))

        column.addWidget(self._seed_row())
        column.addStretch(1)
        return self._scroll(panel)

    def _seed_row(self) -> QWidget:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)

        inner = QWidget()
        row = QHBoxLayout(inner)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        # Short, because a QCheckBox will not wrap its label and a long one
        # would set the minimum width of the whole column. The explanation
        # goes underneath, where it can wrap.
        self.lock_seed = Switch(t("Same starting number"))
        self.lock_seed.setToolTip(t(
            "Every video starts from a random number. Tick this to keep that "
            "number the same, so you can change the words and see what the "
            "words did rather than what the luck did."))
        self.lock_seed.setChecked(bool(self.cfg.get("lock_seed")))
        self.lock_seed.toggled.connect(self._on_lock_seed)
        row.addWidget(self.lock_seed, 1)

        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2 ** 31 - 1)
        self.seed_spin.setValue(int(self.cfg.get("locked_seed") or 0))
        self.seed_spin.setEnabled(self.lock_seed.isChecked())
        self.seed_spin.setFixedWidth(120)
        row.addWidget(self.seed_spin)
        column.addWidget(inner)

        caption = QLabel(t(
            "Keeps the luck fixed, so changing the words shows what the words "
            "did rather than what the random number did."))
        caption.setObjectName("Hint")
        caption.setWordWrap(True)
        column.addWidget(caption)
        return holder

    def _build_middle(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(8, 0, 8, 0)
        column.setSpacing(10)

        self.shots = ShotEditor()
        self.shots.changed.connect(self._on_changed)
        column.addWidget(self.shots, 1)

        column.addWidget(self._create_box())
        return panel

    def _create_box(self) -> QWidget:
        box = QFrame()
        box.setObjectName("Panel")
        box.setStyleSheet(
            f"QFrame#Panel {{ background:{theme.SURFACE_2};"
            f" border:1px solid {theme.LINE}; border-radius:10px; }}")
        column = QVBoxLayout(box)
        column.setContentsMargins(12, 11, 12, 11)
        column.setSpacing(8)

        self.summary = QLabel()
        self.summary.setStyleSheet(
            f"color:{theme.MUTED}; font-family:'{theme.MONO_FONT}'; font-size:11px;")
        self.summary.setWordWrap(True)
        column.addWidget(self.summary)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        column.addWidget(self.progress)

        self.progress_text = QLabel()
        self.progress_text.setObjectName("Hint")
        self.progress_text.setVisible(False)
        column.addWidget(self.progress_text)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.create_btn = QPushButton(t("Create"))
        self.create_btn.setObjectName("Primary")
        self.create_btn.setMinimumHeight(44)
        self.create_btn.setCursor(Qt.PointingHandCursor)
        self.create_btn.setToolTip(t("Ctrl+Enter"))
        self.create_btn.clicked.connect(self._on_create)
        row.addWidget(self.create_btn, 1)

        self.cancel_btn = QPushButton(t("Stop"))
        self.cancel_btn.setToolTip(t(
            "Stop the one being made now. Anything else in the queue carries "
            "on - use the ✕ beside it to take that out too."))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setMinimumHeight(44)
        self.cancel_btn.clicked.connect(self._on_cancel)
        row.addWidget(self.cancel_btn)
        column.addLayout(row)

        self.queue_label = QLabel()
        self.queue_label.setObjectName("Hint")
        self.queue_label.setVisible(False)
        column.addWidget(self.queue_label)
        return box

    def _build_right(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(8, 0, 0, 0)
        column.setSpacing(10)

        heading = QLabel(t("Result"))
        heading.setObjectName("Title")
        column.addWidget(heading)

        self.preview = PreviewPane()
        column.addWidget(self.preview, 1)

        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setVisible(False)
        self.notes.setStyleSheet(
            f"color:{theme.WARN}; font-size:11px; background:{theme.SURFACE_2};"
            f" border:1px solid {theme.LINE}; border-radius:8px; padding:8px;")
        column.addWidget(self.notes)

        # Between what is playing and what has been made: what is still to
        # come. It hides itself when the queue is empty, which is most of the
        # time, so it costs the preview nothing until it has something to say.
        self.queue_panel = QueuePanel(self.queue)
        column.addWidget(self.queue_panel)

        self.gallery = ResultGallery()
        self.gallery.selected.connect(self._on_gallery_pick)
        self.gallery.set_folder(self.cfg.output_dir())
        column.addWidget(self.gallery)
        return self._scroll(panel)

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu(t("&File"))
        self._add_action(file_menu, t("Load settings from a video…"),
                         self._load_recipe, "Ctrl+O")
        self._add_action(file_menu, t("Open the results folder"),
                         lambda: open_folder(self.cfg.output_dir()))
        file_menu.addSeparator()
        self._add_action(file_menu, t("Settings…"), self._open_settings, "Ctrl+,")
        file_menu.addSeparator()
        self._add_action(file_menu, t("Close"), self.close, "Ctrl+Q")

        engine_menu = bar.addMenu(t("&AI engine"))
        self._add_action(engine_menu, t("Check it now"), self._check_engine, "F5")
        self._add_action(engine_menu, t("Start it"), self._start_engine)
        self._add_action(engine_menu, t("Restart it"), self._restart_engine)
        engine_menu.addSeparator()
        self._add_action(engine_menu, t("Free the graphics memory now"),
                         self._free_vram_now)
        self._add_action(engine_menu, t("Check the add-ons…"), self._check_addons)

        help_menu = bar.addMenu(t("&Help"))
        self._add_action(help_menu, t("How do I use this?"), self._show_help)
        self._add_action(help_menu, t("About"), self._show_about)

    def _add_action(self, menu, text, slot, shortcut=None) -> None:
        action = menu.addAction(text)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.engine_dot = QLabel("●")
        self.engine_text = QLabel(t("Checking…"))
        bar.addPermanentWidget(self.engine_dot)
        bar.addPermanentWidget(self.engine_text)

        self._vram_timer = QTimer(self)
        self._vram_timer.setInterval(6000)
        self._vram_timer.timeout.connect(self._refresh_vram)
        self._vram_timer.start()

    # ------------------------------------------------------- loading state
    def _load_workflow(self) -> None:
        import json
        path = self.cfg.workflow_path
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                self.graph = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.graph = {}
            QMessageBox.critical(self, APP_TITLE, t(
                "The workflow file could not be read:\n{path}\n\n{problem}",
                path=path, problem=e))

    def _restore(self) -> None:
        self.storyboard.frames = self.duration.frames()
        resolution = self.resolution.resolution()
        self.storyboard.width, self.storyboard.height = resolution.width, resolution.height
        self.shots.set_shots(self.storyboard.shots, self.storyboard.frames)
        geometry = self.cfg.get("window_geometry")
        if geometry:
            try:
                self.restoreGeometry(bytes.fromhex(geometry))
            except ValueError:
                pass
        self._refresh_summary()

    # ------------------------------------------------------------- edits
    def _on_prompt_changed(self) -> None:
        self.storyboard.global_prompt = self.prompt.toPlainText()
        self._refresh_summary()

    def _on_changed(self) -> None:
        self._refresh_summary()

    def _on_profile_changed(self, key: str) -> None:
        self.cfg.set("profile", key)
        self.cfg.save()
        self._refresh_summary()

    def _on_resolution_changed(self, key: str) -> None:
        resolution = presets.resolution(key)
        self.storyboard.width = resolution.width
        self.storyboard.height = resolution.height
        self.cfg.set("resolution", key)
        self.cfg.save()
        self._refresh_summary()

    def _on_duration_changed(self, frames: int) -> None:
        self.storyboard.frames = frames
        # The shots keep their proportions and are rescaled to the new length,
        # rather than the last one being stretched or the list being cleared.
        self.shots.set_total(frames)
        self.cfg.set("duration_seconds", presets.seconds_for(frames))
        self.cfg.save()
        self._refresh_summary()

    def _on_lock_seed(self, locked: bool) -> None:
        self.seed_spin.setEnabled(locked)
        self.cfg.set("lock_seed", locked)
        self.cfg.save()

    def _refresh_summary(self) -> None:
        self.storyboard.shots = self.shots.shots()
        self.summary.setText(describe(self.storyboard))

    # ------------------------------------------------------------- engine
    def start(self) -> None:
        """Bring ComfyUI up behind a splash, then check what it can do."""
        self._splash = StartupSplash(self)
        self._splash.skip_btn.clicked.connect(self._skip_startup)
        self._engine_worker = EngineWorker(
            self.launcher, bool(self.cfg.get("auto_launch")), self)
        self._engine_worker.status.connect(self._splash.set_message)
        self._engine_worker.done.connect(self._on_engine_ready)
        self._engine_worker.start()
        self._splash.exec()

    def _skip_startup(self) -> None:
        if self._engine_worker is not None:
            self._engine_worker.cancel()
        if self._splash is not None:
            self._splash.accept()
            self._splash = None

    def _on_engine_ready(self, ok: bool, message: str) -> None:
        if self._splash is not None:
            self._splash.accept()
            self._splash = None
        if not ok:
            print(f"[engine] {message}")
        self._check_engine()

    def _check_engine(self) -> None:
        """Ask the engine what it can do, then say whether we can render."""
        alive = self.client.is_alive(timeout=2.0)
        self._set_engine_indicator(alive)
        self.caps = objectinfo.fetch(self.client, refresh=True) if alive \
            else objectinfo.Capabilities()

        if not self.graph:
            self.status_strip.show_state(
                "bad", t("The workflow file is missing."), "")
            self.create_btn.setEnabled(False)
            return

        statuses = node_setup.survey(self.caps, self.cfg.custom_nodes_dir())
        self._statuses = statuses

        if not alive:
            self.status_strip.show_state(
                "bad", t("The AI engine is not running."), t("Start it"))
            self._status_action = self._start_engine
            self.create_btn.setEnabled(False)
            return

        needs_install = node_setup.missing(statuses)
        if needs_install:
            names = ", ".join(s.pack.name for s in needs_install)
            self.status_strip.show_state(
                "warn", t("Missing add-on: {names}", names=names),
                t("Install it"))
            self._status_action = self._check_addons
            self.create_btn.setEnabled(not node_setup.blocking(statuses))
            return

        restarting = [s for s in statuses if s.needs_restart]
        if restarting:
            names = ", ".join(s.pack.name for s in restarting)
            self.status_strip.show_state(
                "warn",
                t("{names} is installed but ComfyUI has not picked it up yet.",
                  names=names),
                t("Restart the engine"))
            self._status_action = self._restart_engine
            self.create_btn.setEnabled(False)
            return

        self.preflight = preflight.run(
            self.graph, self.caps, dict(self.cfg.get("model_overrides") or {}))
        problem = preflight.summarise(self.preflight)
        if problem:
            self.status_strip.show_state("warn", problem, t("What do I do?"))
            self._status_action = self._explain_models
            self.create_btn.setEnabled(False)
            return

        self.status_strip.show_state("ok", t("Ready to make a video."), "")
        self._status_action = None
        self.create_btn.setEnabled(True)
        self._detect_llm_once()
        self._refresh_llm()

    def _on_status_action(self) -> None:
        action = getattr(self, "_status_action", None)
        if action:
            action()

    def _set_engine_indicator(self, alive: bool) -> None:
        self.engine_dot.setStyleSheet(
            f"color:{theme.OK if alive else theme.BAD};")
        self.engine_text.setText(
            t("Engine running") if alive else t("Engine off"))

    def _refresh_vram(self) -> None:
        if not self.client.is_alive(timeout=1.0):
            self._set_engine_indicator(False)
            return
        self._set_engine_indicator(True)
        try:
            stats = self.client.system_stats() or {}
        except Exception:
            return
        devices = stats.get("devices") or []
        if not devices:
            return
        free = devices[0].get("vram_free", 0) / 1e9
        total = devices[0].get("vram_total", 0) / 1e9
        if total:
            self.engine_text.setText(
                t("Engine running · {free} of {total} GB free",
                  free=f"{free:.1f}", total=f"{total:.0f}"))

    def _start_engine(self) -> None:
        self.start()

    def _restart_engine(self) -> None:
        """Stop and start, so newly installed add-ons are registered."""
        self.statusBar().showMessage(t("Restarting the AI engine…"))
        self.launcher.stop()
        self.start()

    # ------------------------------------------------------------ add-ons
    def _check_addons(self) -> None:
        statuses = node_setup.survey(self.caps, self.cfg.custom_nodes_dir())
        if not any(s.needs_install for s in statuses):
            QMessageBox.information(self, t("Add-ons"), t(
                "Both add-ons are already installed."))
            self._check_engine()
            return
        dialog = AddOnDialog(statuses, self.cfg.custom_nodes_dir(),
                             self.cfg.embedded_python(), self)
        dialog.restart_requested.connect(self._restart_engine)
        dialog.exec()
        self._check_engine()

    def _explain_models(self) -> None:
        """Name the missing files, and where they come from. Never fetch them.

        These are twenty-gigabyte downloads. Offering to pull one behind a
        progress bar would hide a decision the user should make with the size in
        front of them, so this only ever tells them what is missing.
        """
        if not self.preflight or not self.preflight.missing_models:
            return
        lines = [t("This ComfyUI cannot see these model files:"), ""]
        for missing in self.preflight.missing_models:
            lines.append(f"  • {missing.label}")
            lines.append(f"    {missing.filename}")
            if missing.alternatives:
                lines.append(t("    You do have: {names}",
                               names=", ".join(missing.alternatives[:3])))
            lines.append("")
        lines.append(t("They can be downloaded from:"))
        lines.append(f"  {preflight.MODEL_SOURCE}")
        lines.append("")
        lines.append(t(
            "Put each one in the folder the workflow names, then press F5."))
        QMessageBox.information(self, t("Missing models"), "\n".join(lines))

    # ------------------------------------------------------------ running
    def _on_create(self) -> None:
        if not self.create_btn.isEnabled():
            return
        self.storyboard.shots = self.shots.shots()
        self.storyboard.global_prompt = self.prompt.toPlainText()

        complaints = h3graph.problems(self.storyboard)
        if complaints:
            QMessageBox.information(self, APP_TITLE, "\n\n".join(complaints))
            return

        seed = self.seed_spin.value() if self.lock_seed.isChecked() else None
        if self.lock_seed.isChecked():
            self.cfg.set("locked_seed", seed)
            self.cfg.save()

        request = GenerationRequest(
            # A copy, so editing the shots for the next one cannot reach into
            # a render that is already queued.
            storyboard=copy.deepcopy(self.storyboard),
            profile=self.profile.profile(),
            seed=seed,
            overrides=dict(self.cfg.get("model_overrides") or {}),
            attention_options=(self.preflight.attention_options
                               if self.preflight else None),
            can_save_last_frame=self.caps.has_node(h3graph.LASTFRAME),
            free_llm=self._free_llm_request(),
        )
        item = QueueItem(
            graph=self.graph,
            request=request,
            output_dir=self.cfg.output_dir(),
            timeout=int(self.cfg.get("job_timeout") or 5400),
        )
        self._mine.add(item.id)
        self.queue.add(item)
        self._refresh_queue_label()

    def _on_cancel(self) -> None:
        running = self.queue.running
        if running is not None:
            self.queue.cancel(running)

    def _on_queue_item(self, item) -> None:
        if item.id not in self._mine:
            return
        # Create never goes away. Hiding it while something was being made was
        # the one thing stopping a second idea from being lined up, and lining
        # the next one up while this one works is the whole point of a queue.
        # Stop and the progress bar simply appear alongside it.
        #
        # The bar follows whatever is running now rather than the item that
        # just sent this signal, so a finished item does not blank the bar of
        # the one that took its place.
        running = self.queue.running
        self.cancel_btn.setVisible(running is not None)
        self.progress.setVisible(running is not None)
        self.progress_text.setVisible(running is not None)
        if running is not None:
            self.progress.setValue(running.percent)
            self.progress_text.setText(running.message)

        if item.state is State.DONE and item.result is not None:
            self._on_success(item.result)
        elif item.state is State.FAILED:
            # Anything still waiting is about to start, and a modal box here
            # would hold the whole queue open until somebody clicked it.
            self._on_failure(item.error, quiet=bool(self.queue.waiting()))
        self._refresh_queue_label()

    def _on_queue_file(self, item, path: str) -> None:
        if item.id in self._mine:
            self.gallery.add(path)

    def _on_queue_preview(self, item, data: bytes) -> None:
        if item.id in self._mine:
            self.preview.show_bytes(data)

    def _refresh_queue_label(self) -> None:
        """The line under Create: what is queued, or that queueing exists.

        The count answers "did my press do anything?". Before there is a
        count - the first press while something is running - the invitation
        answers it instead, because nobody presses a button twice to find out.
        """
        waiting = len(self.queue.waiting())
        if waiting:
            text = plural(waiting, "1 more waiting", "{n} more waiting")
        elif self.queue.running is not None:
            text = t("Press Create again to line up another.")
        else:
            text = ""
        self.queue_label.setText(text)
        self.queue_label.setVisible(bool(text))

    def _on_success(self, result: JobResult) -> None:
        if result.files:
            self.preview.show_file(str(result.files[-1]))
        bits = [t("Done in {n}s", n=f"{result.elapsed:.0f}")]
        if result.frames:
            bits.append(presets.format_length(result.frames))
        if result.seed is not None:
            bits.append(t("number {seed}", seed=result.seed))
        if result.last_frame:
            bits.append(t("last frame saved"))
        self.statusBar().showMessage(" · ".join(bits), 12000)
        self._show_notes(result.notes)

    def _on_failure(self, message: str, quiet: bool = False) -> None:
        """One run did not work.

        Quiet when there is more work behind it: a message box runs its own
        event loop, so a batch left going overnight would stop at the first
        failure and sit there showing a dialog nobody is awake to close. The
        row in the queue keeps the state and the reason either way.
        """
        if not message:
            return
        if quiet:
            self._show_notes([message])
            self.statusBar().showMessage(message.splitlines()[0], 12000)
            return
        self.preview.clear_preview(t("That did not work."))
        QMessageBox.warning(self, APP_TITLE, message)

    def _show_notes(self, notes: list[str]) -> None:
        self.notes.setVisible(bool(notes))
        self.notes.setText("\n\n".join(notes))

    def _on_gallery_pick(self, path: str) -> None:
        self.preview.show_file(path)

    def _on_queue_empty(self) -> None:
        """Nothing left to run. Create is already there; the rest goes away."""
        self.cancel_btn.setVisible(False)
        self.progress.setVisible(False)
        self.progress_text.setVisible(False)
        self._refresh_queue_label()

    # ------------------------------------------------------------- dialogs
    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.cfg, self.client, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.client = ComfyClient(self.cfg.server)
        self.queue.client = self.client
        previous, self.launcher = self.launcher, ComfyLauncher(
            self.cfg.get("comfyui_dir"), self.cfg.get("comfyui_launcher"),
            self.client, timeout=int(self.cfg.get("launch_timeout") or 300))
        # Hand over the running process, so changing a setting does not orphan
        # a ComfyUI this program started.
        self.launcher.adopt(previous)
        self._refresh_llm()
        self._check_engine()

    # -------------------------------------------------------- prompt helper
    def _llm_ready(self) -> bool:
        """Is there a prompt helper to offer?

        Three things have to be true, and the first is the important one: the
        feature is off until asked for, so a machine with no Ollama - which is
        most of them - never touches the network for this at all.
        """
        if not self.cfg.get("llm_enabled") or not self.cfg.get("llm_model"):
            return False
        return self.llm.is_alive()

    def _detect_llm_once(self) -> None:
        """Look for a prompt helper the first time, and only the first time.

        Without this the feature is invisible until somebody goes looking for a
        setting they have no reason to know exists - which is exactly what
        happened: a machine with Ollama running and a vision model pulled still
        showed no button, because the switch defaults to off.

        So: probe once, take the answer, and record that we looked. A machine
        with no Ollama pays one refused connection, once, ever.
        """
        if self.cfg.get("llm_checked"):
            return
        self.cfg.set("llm_checked", True)

        model = detect_llm(self.llm)
        if model:
            self.cfg.set("llm_enabled", True)
            self.cfg.set("llm_model", model)
            self.statusBar().showMessage(
                t("Found a prompt helper: {model}. The ✨ buttons are ready.",
                  model=model), 12000)
            print(f"[llm] found {model} at {self.llm.url}")
        self.cfg.save()

    def _refresh_llm(self) -> None:
        """Show or hide everything to do with the prompt helper."""
        self.llm = Ollama(str(self.cfg.get("llm_url")),
                          timeout=int(self.cfg.get("llm_timeout") or 120))
        available = self._llm_ready()
        self.subjects.set_analyzer_available(available)
        self.improve_btn.setVisible(available)
        if not available:
            self.undo_btn.setVisible(False)

    def _analyzing(self, busy: bool) -> None:
        self.improve_btn.setEnabled(not busy)
        self.improve_btn.setText(t("Writing…") if busy
                                 else t("✨  Write this out for me"))

    def _start_analyze(self, system: str, prompt: str, image=None,
                       max_words: int = 0, on_done=None) -> None:
        if self._analyze_worker is not None and self._analyze_worker.isRunning():
            return
        worker = AnalyzeWorker(self.llm, str(self.cfg.get("llm_model")),
                               system, prompt, image=image, max_words=max_words,
                               parent=self)
        worker.finished_ok.connect(on_done)
        worker.failed.connect(self._on_analyze_failed)
        self._analyze_worker = worker
        worker.start()

    def _on_analyze_failed(self, message: str) -> None:
        self._analyzing(False)
        for slot in self.subjects.slots:
            slot.set_analyzing(False)
        QMessageBox.warning(self, t("Prompt helper"), message)

    def _describe_picture(self, slot) -> None:
        """Fill in one subject slot's description from its picture."""
        if not slot.subject.active:
            return
        slot.set_analyzing(True)
        system, prompt = llm_prompts.describe_picture(slot.subject.kind)

        def done(text: str) -> None:
            slot.set_analyzing(False)
            # Written into the field, not straight into the storyboard, so the
            # user reads it and can edit it before it reaches the model.
            slot.description.setText(text)

        self._start_analyze(system, prompt, image=slot.subject.image,
                            max_words=60, on_done=done)

    def _improve_prompt(self) -> None:
        """Expand a rough idea into a full description."""
        idea = self.prompt.toPlainText().strip()
        if not idea:
            QMessageBox.information(self, t("Prompt helper"), t(
                "Type a rough idea first — even a few words is enough."))
            return

        self._analyzing(True)
        words = int(self.cfg.get("llm_max_words") or 160)
        system, prompt = llm_prompts.expand_idea(
            idea, presets.seconds_for(self.storyboard.frames), words)

        def done(text: str) -> None:
            self._analyzing(False)
            # Keep what they had for exactly one Undo. Replacing someone's
            # words with a machine's and offering no way back is not a trade
            # anyone agreed to.
            self._prompt_undo = idea
            self.undo_btn.setVisible(True)
            self.prompt.setPlainText(text)

        self._start_analyze(system, prompt, max_words=words, on_done=done)

    def _undo_improve(self) -> None:
        if self._prompt_undo:
            self.prompt.setPlainText(self._prompt_undo)
        self._prompt_undo = ""
        self.undo_btn.setVisible(False)

    # ------------------------------------------------------- graphics memory
    def _free_llm_request(self) -> FreeLlm | None:
        """What the worker should free before this render, if anything."""
        if not self.cfg.get("llm_enabled"):
            return None
        if not self.cfg.get("llm_free_before_render"):
            return None
        return FreeLlm(url=str(self.cfg.get("llm_url")),
                       wait=int(self.cfg.get("llm_free_wait") or 30))

    def _free_vram_now(self) -> None:
        """The menu item. Same work the render does, on demand."""
        if self._free_worker is not None and self._free_worker.isRunning():
            return
        if not self.cfg.get("llm_enabled"):
            QMessageBox.information(self, t("Graphics memory"), t(
                "The prompt helper is switched off, so nothing of this "
                "program's is holding the graphics card."))
            return

        self.statusBar().showMessage(t("Freeing graphics memory…"))
        worker = FreeVramWorker(self.llm, int(self.cfg.get("llm_free_wait") or 30),
                                parent=self)
        worker.status.connect(lambda m: self.statusBar().showMessage(m))
        worker.done.connect(self._on_freed)
        self._free_worker = worker
        worker.start()

    def _on_freed(self, freed: bool, note: str) -> None:
        self._refresh_vram()
        if freed:
            self.statusBar().showMessage(t("The graphics card is clear."), 8000)
        else:
            self.statusBar().clearMessage()
            QMessageBox.warning(self, t("Graphics memory"), note)

    # ------------------------------------------------------ saved settings
    def _load_recipe(self) -> None:
        """Put every control back where it was for an earlier video.

        The file sits beside the video it made, so this is really "open that
        one again" - which is how most good results get used: as a starting
        point for the next attempt rather than as a finished thing.
        """
        from PySide6.QtWidgets import QFileDialog

        chosen, _ = QFileDialog.getOpenFileName(
            self, t("Load settings from a video"),
            str(self.cfg.output_dir()),
            t("EasyMiniDirector settings (*.json)"))
        if not chosen:
            return
        self.apply_recipe(recipe.load(chosen))

    def apply_recipe(self, loaded: recipe.Loaded) -> None:
        """Write a loaded recipe into the controls, and say what would not go.

        Order matters: the length goes in before the shots, because setting the
        length rescales whatever shots are already there - so doing it the
        other way round would stretch the very lengths just restored.
        """
        board = loaded.storyboard

        self.duration.set_frames(board.frames)
        self.storyboard.frames = board.frames
        if loaded.resolution_key:
            self.resolution.set_key(loaded.resolution_key)
        self.storyboard.width, self.storyboard.height = board.width, board.height

        self.prompt.setPlainText(board.global_prompt)

        self.storyboard.shots = [Shot(s.prompt, s.length) for s in board.shots]
        self.shots.set_shots(self.storyboard.shots, board.frames)

        for index, subject in enumerate(board.subjects):
            if index >= len(self.storyboard.subjects):
                break
            self.storyboard.subjects[index] = subject
            slot = self.subjects.slots[index]
            slot.subject = subject
            slot.drop.set_path(str(subject.image) if subject.image else "")
            slot.description.setText(subject.description)
            slot.short_name.setText(subject.short_name)
            position = self.subjects.slots[index].kind.findData(subject.kind)
            slot.kind.setCurrentIndex(max(0, position))
            slot.refresh()

        self.voice.load_from(board.voice)
        self.storyboard.voice = board.voice

        if loaded.profile is not None:
            self.profile.set_profile(loaded.profile.key)
        if loaded.seed is not None:
            self.lock_seed.setChecked(True)
            self.seed_spin.setValue(int(loaded.seed))

        self._refresh_summary()
        self._show_notes(loaded.notes)
        self.statusBar().showMessage(t("Settings loaded."), 8000)

    def _show_help(self) -> None:
        QMessageBox.information(self, t("How do I use this?"), t(
            "1.  Describe your video in the box on the left. Say what you want "
            "to hear as well as see — this model makes its own sound.\n\n"
            "2.  Optionally cut it into shots in the middle, and describe each "
            "one. Drag the dividers to change how long each shot lasts.\n\n"
            "3.  Optionally add a picture of a person or thing to keep "
            "consistent right through the clip.\n\n"
            "4.  Leave it on Turbo while you are trying ideas out. Switch to "
            "Quality once you have something worth waiting for.\n\n"
            "5.  Press Create. The first render after starting up is always "
            "the slowest — the model is being read off disk.\n\n"
            "6.  You can press Create again while one is being made. They "
            "queue up and run in turn, so an evening’s work can be set up "
            "in a few minutes and left to make itself."))

    def _show_about(self) -> None:
        QMessageBox.about(self, t("About"), t(
            "{app} {version}\n\nA plain front end for the MiniMax H3 Director "
            "workflow in ComfyUI.\n\nBuilt on the same foundations as EasyAI.",
            app=APP_TITLE, version=__version__))

    # -------------------------------------------------------------- closing
    def closeEvent(self, event) -> None:
        outstanding = len(self.queue.unfinished())
        if outstanding:
            # Named, because closing on a queue of eight throws away eight
            # things, and "something" reads like one.
            answer = QMessageBox.question(
                self, APP_TITLE,
                plural(outstanding,
                       "Something is still being made. Close anyway?",
                       "{n} are still in the queue. Close anyway?"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return

        self.cfg.set("window_geometry", bytes(self.saveGeometry()).hex())
        self.cfg.set("first_run_done", True)
        self.cfg.save()

        self.queue.stop_everything()
        if self.cfg.get("stop_engine_on_exit"):
            # Only ever stops one this program started. Leaving it running
            # holds the whole graphics card, and a beginner has no idea there
            # is a second program to close.
            self.launcher.stop()
        super().closeEvent(event)
