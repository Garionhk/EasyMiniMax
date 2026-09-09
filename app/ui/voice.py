"""The reference voice: one clip, and whose voice it is.

H3 generates its own sound, so this is not a soundtrack to lay over the video —
it is a reference for how the speaking should sound. Attach a clip of someone
talking and the model follows its voice and timbre.

The panel is deliberately blunt about two things a beginner would otherwise
discover the hard way:

* A voice clip switches the render onto the reference model, exactly as a
  reference picture does. That is a 21 GB checkpoint swap and a noticeably
  slower first render.
* Binding the voice to a person who has no picture attached writes a reference
  to an empty slot. It does not fail — it just quietly means less than it looks
  like it means.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from app.h3.timeline import SUBJECT_SLOTS, Storyboard, Voice
from app.i18n import t
from app.ui import theme
from app.ui.widgets import DropZone

_AUDIO_EXT = {".flac", ".mp3", ".wav", ".ogg", ".m4a"}


class VoicePanel(QWidget):
    """One audio drop zone, a description, and a "whose voice?" chooser."""

    changed = Signal()

    def __init__(self, storyboard: Storyboard, parent=None):
        super().__init__(parent)
        self._storyboard = storyboard

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(7)

        heading = QLabel(t("Use someone's voice  (optional)"))
        heading.setObjectName("Heading")
        heading.setWordWrap(True)
        column.addWidget(heading)

        card = QFrame()
        card.setObjectName("Panel")
        card.setStyleSheet(
            f"QFrame#Panel {{ background:{theme.SURFACE_2};"
            f" border:1px solid {theme.LINE}; border-radius:9px; }}")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(9, 9, 9, 9)
        inner.setSpacing(7)

        self.drop = DropZone(t("Voice clip"), _AUDIO_EXT, compact=True)
        self.drop.changed.connect(self._on_clip)
        inner.addWidget(self.drop)

        self.description = QLineEdit()
        self.description.setMinimumWidth(120)
        self.description.setPlaceholderText(
            t("What it sounds like, e.g. a low, calm woman's voice"))
        self.description.textChanged.connect(self._on_description)
        inner.addWidget(self.description)

        row = QHBoxLayout()
        row.setSpacing(6)
        label = QLabel(t("Whose voice?"))
        label.setObjectName("Hint")
        row.addWidget(label)

        self.subject = QComboBox()
        self.subject.setMinimumContentsLength(10)
        self.subject.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.subject.addItem(t("Nobody in particular"), None)
        for slot in range(1, SUBJECT_SLOTS + 1):
            self.subject.addItem(t("Person {n}", n=slot), slot)
        self.subject.setToolTip(t(
            "Tie this voice to one of the pictures above, so the model knows "
            "which face it belongs to."))
        self.subject.currentIndexChanged.connect(self._on_subject)
        row.addWidget(self.subject, 1)
        inner.addLayout(row)

        column.addWidget(card)

        self.note = QLabel()
        self.note.setObjectName("Hint")
        self.note.setWordWrap(True)
        column.addWidget(self.note)

        self.refresh()

    # -- state -------------------------------------------------------------
    @property
    def voice(self) -> Voice:
        return self._storyboard.voice

    def _on_clip(self, path: str) -> None:
        self.voice.audio = Path(path) if path else None
        self._changed()

    def _on_description(self, text: str) -> None:
        self.voice.description = text
        self._changed()

    def _on_subject(self, _index: int) -> None:
        self.voice.subject = self.subject.currentData()
        self._changed()

    def _changed(self) -> None:
        self.refresh()
        self.changed.emit()

    def load_from(self, voice: Voice) -> None:
        """Put a loaded recipe's voice back into the controls."""
        self._storyboard.voice = voice
        blockers = (self.drop, self.description, self.subject)
        for widget in blockers:
            widget.blockSignals(True)
        try:
            self.drop.set_path(str(voice.audio) if voice.audio else "")
            self.description.setText(voice.description)
            position = self.subject.findData(voice.subject)
            self.subject.setCurrentIndex(max(0, position))
        finally:
            for widget in blockers:
                widget.blockSignals(False)
        self.refresh()

    # -- what it says ------------------------------------------------------
    def refresh(self) -> None:
        active = self.voice.active
        for widget in (self.description, self.subject):
            widget.setEnabled(active)

        if not active:
            self.note.setText(t(
                "Attach a clip of someone talking to steer how the voices in "
                "your video sound. Leave it empty and the model invents them."))
            self.note.setStyleSheet(f"color:{theme.DIM}; font-size:11px;")
            return

        # Bound to a slot with no picture in it: allowed, but worth saying.
        slot = self.voice.subject
        if slot and not self._storyboard.subjects[slot - 1].active:
            self.note.setText(t(
                "Person {n} has no picture, so this voice is tied to an empty "
                "slot. Add a picture there, or set this to “Nobody in "
                "particular”.", n=slot))
            self.note.setStyleSheet(f"color:{theme.WARN}; font-size:11px;")
            return

        self.note.setText(t(
            "A voice clip switches to the second, larger model, the same as a "
            "reference picture does — so the first render after adding one "
            "takes noticeably longer."))
        self.note.setStyleSheet(f"color:{theme.WARN}; font-size:11px;")
