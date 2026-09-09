"""Where ComfyUI lives, where files go, and whether to start the engine.

Copied from EasyAI. Wrong paths are the most common way this sort of app
breaks, so the ComfyUI section tests itself: pick a folder, and it immediately
says whether it found a start file and whether a server is answering.

What changed is the bottom half. EasyAI offers an aspect ratio to start with;
here there is nothing to choose that the main window does not already show, so
that group holds the add-on controls instead.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from app import i18n
from app.comfy.client import ComfyClient
from app.comfy.launcher import ComfyLauncher
from app.i18n import t
from app.ui import theme
from app.ui.scroll import fit_to_screen, vertical_scroll

#: What this dialog would like, and the least it can be without clipping.
#: Measured: the five groups of settings are simply tall, and on a laptop the
#: page has to scroll rather than the window growing past the screen.
WANTED = (720, 780)
SMALLEST = (560, 320)


class PathRow(QWidget):
    """A read-only path box with a Browse button."""

    def __init__(self, value: str, caption: str, parent=None):
        super().__init__(parent)
        self.caption = caption
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QLineEdit(value)
        layout.addWidget(self.edit, 1)
        browse = QPushButton(t("Browse…"))
        browse.clicked.connect(self._browse)
        layout.addWidget(browse)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, self.caption, self.edit.text())
        if chosen:
            self.edit.setText(chosen)

    def value(self) -> str:
        return self.edit.text().strip()


class SettingsDialog(QDialog):
    def __init__(self, cfg, client: ComfyClient, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.client = client
        self.setWindowTitle(t("Settings"))
        self._build()
        # After _build, so the floor is set against real content. This dialog
        # was 1245 x 1122 and refused to be smaller - bigger than the screen it
        # was written on, with Save and Cancel somewhere past the bottom edge.
        fit_to_screen(self, WANTED, SMALLEST)
        self._refresh_engine_hint()

    def _build(self) -> None:
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 16, 16, 10)
        outer.setSpacing(12)

        # --- engine ------------------------------------------------------
        engine_box = QGroupBox(t("The AI engine (ComfyUI)"))
        form = QFormLayout(engine_box)

        self.dir_row = PathRow(str(self.cfg.get("comfyui_dir")),
                               t("Where is ComfyUI installed?"))
        self.dir_row.edit.textChanged.connect(self._on_dir_changed)
        form.addRow(t("ComfyUI folder"), self.dir_row)

        self.launcher_combo = QComboBox()
        self.launcher_combo.setEditable(True)
        self.launcher_combo.setToolTip(t(
            "The .bat file that starts ComfyUI. On a portable install this is "
            "usually run_nvidia_gpu.bat."))
        form.addRow(t("Start file"), self.launcher_combo)

        self.server_edit = QLineEdit(self.cfg.server)
        self.server_edit.setToolTip(t(
            "Leave this alone unless ComfyUI runs on another PC or a different "
            "port."))
        self.server_edit.textChanged.connect(self._refresh_engine_hint)
        form.addRow(t("Address"), self.server_edit)

        self.auto_launch = QCheckBox(
            t("Start ComfyUI automatically when EasyMiniMax opens"))
        self.auto_launch.setChecked(bool(self.cfg.get("auto_launch")))
        form.addRow("", self.auto_launch)

        self.stop_on_exit = QCheckBox(t("Close ComfyUI when EasyMiniMax closes"))
        self.stop_on_exit.setToolTip(t(
            "Leaving it running keeps hold of the graphics card.\n\n"
            "Only ever closes a ComfyUI that EasyMiniMax started. One you opened "
            "yourself is left alone."))
        self.stop_on_exit.setChecked(bool(self.cfg.get("stop_engine_on_exit")))
        form.addRow("", self.stop_on_exit)

        check_row = QHBoxLayout()
        self.engine_hint = QLabel("")
        self.engine_hint.setWordWrap(True)
        check_row.addWidget(self.engine_hint, 1)
        test_btn = QPushButton(t("Test now"))
        test_btn.clicked.connect(self._refresh_engine_hint)
        check_row.addWidget(test_btn)
        form.addRow("", self._wrap(check_row))
        outer.addWidget(engine_box)

        # --- folders -----------------------------------------------------
        folder_box = QGroupBox(t("Folders"))
        folder_form = QFormLayout(folder_box)
        self.workflow_row = PathRow(str(self.cfg.get("workflow_dir")),
                                    t("Where are the workflow files?"))
        folder_form.addRow(t("Workflows"), self.workflow_row)
        self.output_row = PathRow(str(self.cfg.get("output_dir")),
                                  t("Where should results be saved?"))
        folder_form.addRow(t("Results"), self.output_row)
        outer.addWidget(folder_box)

        # --- generation --------------------------------------------------
        gen_box = QGroupBox(t("Creating"))
        gen_form = QFormLayout(gen_box)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(60, 14400)
        self.timeout_spin.setSingleStep(60)
        self.timeout_spin.setSuffix(t(" seconds"))
        self.timeout_spin.setValue(int(self.cfg.get("job_timeout") or 5400))
        self.timeout_spin.setToolTip(t(
            "Give up on a render that takes longer than this. Video is slow: "
            "a long clip on the Quality setting can take most of an hour."))
        gen_form.addRow(t("Give up after"), self.timeout_spin)

        self.launch_timeout_spin = QSpinBox()
        self.launch_timeout_spin.setRange(30, 1800)
        self.launch_timeout_spin.setSingleStep(30)
        self.launch_timeout_spin.setSuffix(t(" seconds"))
        self.launch_timeout_spin.setValue(int(self.cfg.get("launch_timeout") or 300))
        gen_form.addRow(t("Wait for the engine up to"), self.launch_timeout_spin)

        # Language lives here rather than in its own group: it is a one-line
        # choice, and burying it deeper would be unkind to anyone who has
        # landed in a language they cannot read.
        self.language_combo = QComboBox()
        for code, label in i18n.available().items():
            self.language_combo.addItem(label, code)
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(i18n.current())))
        self.language_combo.setToolTip(
            t("Changes the whole window as soon as you press Save."))
        gen_form.addRow(t("Language"), self.language_combo)
        outer.addWidget(gen_box)

        outer.addWidget(self._helper_box())
        outer.addWidget(self._addons_box())
        outer.addStretch(1)

        shell.addWidget(vertical_scroll(page), 1)

        # Save and Cancel stay out of the scrolling page. Having to scroll to
        # find the button that closes a dialog is the fault this whole change
        # is about.
        footer = QWidget()
        footer_row = QVBoxLayout(footer)
        footer_row.setContentsMargins(16, 8, 16, 14)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        footer_row.addWidget(buttons)
        shell.addWidget(footer)

        self._on_dir_changed()

    def _helper_box(self) -> QWidget:
        """The optional local language model.

        Off by default and silent about itself when off. Probing for a server
        that is not there costs a real fraction of a second on a refused
        connection, so nothing here touches the network until the box is
        ticked and Test is pressed.
        """
        from app.llm.ollama import DEFAULT_URL, SUGGESTED_MODEL

        box = QGroupBox(t("Prompt helper (optional)"))
        form = QFormLayout(box)

        self.llm_enabled = QCheckBox(t(
            "Use a local language model to help write descriptions"))
        self.llm_enabled.setChecked(bool(self.cfg.get("llm_enabled")))
        self.llm_enabled.setToolTip(t(
            "Needs Ollama running on this machine with a vision model "
            "pulled, for example:  ollama pull " + SUGGESTED_MODEL))
        self.llm_enabled.toggled.connect(self._on_helper_toggled)
        form.addRow("", self.llm_enabled)

        self.llm_url = QLineEdit(str(self.cfg.get("llm_url") or DEFAULT_URL))
        self.llm_url.setToolTip(t(
            "Where Ollama is listening. Leave this alone unless you changed "
            "its port or it runs on another PC."))
        form.addRow(t("Address"), self.llm_url)

        model_row = QHBoxLayout()
        self.llm_model = QComboBox()
        self.llm_model.setEditable(True)
        current = str(self.cfg.get("llm_model") or "")
        if current:
            self.llm_model.addItem(current)
        model_row.addWidget(self.llm_model, 1)
        refresh = QPushButton(t("Find models"))
        refresh.clicked.connect(self._refresh_models)
        model_row.addWidget(refresh)
        form.addRow(t("Model"), self._wrap(model_row))

        self.llm_free = QCheckBox(t(
            "Free the graphics memory before each video"))
        self.llm_free.setChecked(bool(self.cfg.get("llm_free_before_render")))
        self.llm_free.setToolTip(t(
            "The language model and the video model cannot both fit on one "
            "graphics card. This asks the language model to leave, and waits "
            "until it actually has, before the video starts.\n\n"
            "There is very little reason to turn this off."))
        form.addRow("", self.llm_free)

        check_row = QHBoxLayout()
        self.llm_hint = QLabel("")
        self.llm_hint.setWordWrap(True)
        check_row.addWidget(self.llm_hint, 1)
        test = QPushButton(t("Test now"))
        test.clicked.connect(self._test_helper)
        check_row.addWidget(test)
        form.addRow("", self._wrap(check_row))

        self._on_helper_toggled(self.llm_enabled.isChecked())
        return box

    def _on_helper_toggled(self, on: bool) -> None:
        for widget in (self.llm_url, self.llm_model, self.llm_free):
            widget.setEnabled(on)
        if not on:
            self.llm_hint.setText("")

    def _helper_client(self):
        from app.llm.ollama import Ollama
        return Ollama(self.llm_url.text(),
                      timeout=int(self.cfg.get("llm_timeout") or 120))

    def _refresh_models(self) -> None:
        from app.llm.ollama import LlmError

        try:
            names = self._helper_client().models()
        except LlmError as e:
            self.llm_hint.setText(str(e))
            self.llm_hint.setStyleSheet(f"color:{theme.WARN};")
            return

        if not names:
            self.llm_hint.setText(t(
                "That server has no models yet. Pull a vision one first, for "
                "example:  ollama pull qwen2.5vl:7b"))
            self.llm_hint.setStyleSheet(f"color:{theme.WARN};")
            return

        current = self.llm_model.currentText().strip()
        self.llm_model.clear()
        self.llm_model.addItems(names)
        if current in names:
            self.llm_model.setCurrentText(current)
        else:
            # Prefer something that can actually look at a picture.
            vision = [n for n in names
                      if any(hint in n.lower()
                             for hint in ("vl", "vision", "llava", "moondream"))]
            self.llm_model.setCurrentText(vision[0] if vision else names[0])
        self.llm_hint.setText(t("Found {n} models.", n=len(names)))
        self.llm_hint.setStyleSheet(f"color:{theme.OK};")

    def _test_helper(self) -> None:
        client = self._helper_client()
        if not client.is_alive(fresh=True):
            self.llm_hint.setText(t(
                "Nothing is answering at {url}. Is Ollama running?",
                url=client.url))
            self.llm_hint.setStyleSheet(f"color:{theme.WARN};")
            return

        resident = client.loaded()
        held = (t(" It currently holds {n} GB.",
                  n=f"{sum(m.vram for m in resident) / 1e9:.1f}")
                if resident else t(" Nothing is loaded right now."))
        self.llm_hint.setText(t("Ollama is answering at {url}.", url=client.url) + held)
        self.llm_hint.setStyleSheet(f"color:{theme.OK};")

    def _addons_box(self) -> QWidget:
        """Where the custom nodes ended up, and a way to update them.

        Worth showing even when everything is fine: these are the two pieces
        that are not part of ComfyUI, so when something stops working this is
        the first place to look, and a user who cannot find them has no way to
        tell a broken add-on from a broken program.
        """
        from app.setup import nodes as node_setup

        box = QGroupBox(t("Add-ons"))
        form = QFormLayout(box)
        nodes_dir = self.cfg.custom_nodes_dir()

        for pack in node_setup.REQUIRED:
            folder = node_setup.folder_for(pack, nodes_dir)
            row = QHBoxLayout()
            state = QLabel(str(folder) if folder else t("not installed"))
            state.setWordWrap(True)
            state.setStyleSheet(
                f"color:{theme.OK if folder else theme.WARN}; font-size:11px;")
            row.addWidget(state, 1)
            if folder is not None:
                update = QPushButton(t("Update"))
                update.clicked.connect(
                    lambda _=False, name=pack.name: self._update_pack(name))
                row.addWidget(update)
            form.addRow(pack.name, self._wrap(row))
        return box

    def _update_pack(self, pack_name: str) -> None:
        from app.setup import nodes as node_setup
        message = node_setup.update(self.cfg.custom_nodes_dir(), pack_name)
        QMessageBox.information(self, t("Add-ons"), message)

    @staticmethod
    def _wrap(layout) -> QWidget:
        holder = QWidget()
        holder.setLayout(layout)
        return holder

    # ------------------------------------------------------------- helpers
    def _on_dir_changed(self) -> None:
        """Repopulate the start-file list for whatever folder is selected."""
        current = self.launcher_combo.currentText() or str(self.cfg.get("comfyui_launcher"))
        probe = ComfyLauncher(self.dir_row.value(), current, self.client)
        found = probe.find_launchers()

        self.launcher_combo.blockSignals(True)
        self.launcher_combo.clear()
        self.launcher_combo.addItems(found)
        if current:
            index = self.launcher_combo.findText(current)
            if index >= 0:
                self.launcher_combo.setCurrentIndex(index)
            else:
                self.launcher_combo.setEditText(current)
        self.launcher_combo.blockSignals(False)
        self._refresh_engine_hint()

    def _refresh_engine_hint(self) -> None:
        messages: list[str] = []
        colour = theme.OK

        probe = ComfyLauncher(self.dir_row.value(),
                              self.launcher_combo.currentText(), self.client)
        problem = probe.validate()
        if problem:
            messages.append(problem.splitlines()[0])
            colour = theme.WARN

        server = self.server_edit.text().strip() or self.cfg.server
        if ComfyClient(server).is_alive(timeout=1.5):
            messages.append(t("ComfyUI is answering at {server}.", server=server))
        else:
            messages.append(
                t("Nothing is answering at {server} right now.", server=server))
            if not problem:
                colour = theme.WARN

        self.engine_hint.setText("  ".join(messages))
        self.engine_hint.setStyleSheet(f"color:{colour};")

    # -------------------------------------------------------------- saving
    def _save(self) -> None:
        self.cfg.set("comfyui_dir", self.dir_row.value())
        self.cfg.set("comfyui_launcher", self.launcher_combo.currentText().strip())
        self.cfg.set("comfyui_server", self.server_edit.text().strip() or "127.0.0.1:8188")
        self.cfg.set("auto_launch", self.auto_launch.isChecked())
        self.cfg.set("stop_engine_on_exit", self.stop_on_exit.isChecked())
        self.cfg.set("workflow_dir", self.workflow_row.value())
        self.cfg.set("output_dir", self.output_row.value())
        self.cfg.set("job_timeout", self.timeout_spin.value())
        self.cfg.set("launch_timeout", self.launch_timeout_spin.value())
        self.cfg.set("llm_enabled", self.llm_enabled.isChecked())
        self.cfg.set("llm_url", self.llm_url.text().strip())
        self.cfg.set("llm_model", self.llm_model.currentText().strip())
        self.cfg.set("llm_free_before_render", self.llm_free.isChecked())
        self.cfg.set("first_run_done", True)

        # Remembered outside EasyAI's own settings, so EasyAI Setup opens in
        # the same language without either program reaching into the other's.
        # The window retranslates itself once this dialog closes, so there is
        # nothing to announce and no restart to ask for.
        chosen = self.language_combo.currentData()
        if chosen and chosen != i18n.current():
            i18n.load(chosen)
            i18n.remember(chosen)

        self.cfg.save()
        self.accept()
