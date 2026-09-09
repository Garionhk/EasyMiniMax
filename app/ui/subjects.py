"""Reference pictures: keep this person, this animal, this outfit consistent.

Attaching a picture here does more than add a hint. It flips the Director's
reference mode on, which switches the whole render onto a different 21 GB
checkpoint (ref2va instead of fl2va). That is a real cost in time and memory, so
the panel says so plainly rather than letting a beginner wonder why the render
suddenly got slower.

Each slot needs two things beyond the picture, and both matter:

* **What it shows** - the compiled prompt says "Subject 1 is the person shown in
  Picture 1", and without a description the model is told a picture exists but
  nothing about what to keep from it.
* **What to call it** - so a shot prompt can say "the rider leans into the turn"
  and have that land on this face.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from app.h3.timeline import SUBJECT_KINDS, SUBJECT_SLOTS, Subject
from app.i18n import N, t
from app.ui import theme
from app.ui.widgets import DropZone

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

#: Plain words for the Director's vocabulary. The value written into the
#: timeline has to be one of its fixed terms or it is silently clamped back to
#: "person", so the list is offered rather than typed.
#:
#: N() and not t(): a module-level t() runs when the module is first imported,
#: which is before any language has been chosen - so these stayed English in
#: every language. Marked here, translated where they are shown.
KIND_LABELS = {
    "person": N("A person"),
    "animal": N("An animal"),
    "object": N("An object"),
    "environment": N("A place"),
    "clothing": N("An outfit"),
    "prop": N("A prop"),
    "interface": N("A screen or interface"),
    "effect": N("A visual effect"),
    "style": N("A look or style"),
    "action": N("An action"),
    "expression": N("An expression"),
    "pose": N("A pose"),
}


class SubjectSlot(QFrame):
    """One reference picture and what to say about it."""

    changed = Signal()
    #: The ✨ button was pressed. Carries this slot, for the window to run the
    #: prompt helper against - the panel itself knows nothing about Ollama.
    analyze_asked = Signal(object)

    def __init__(self, index: int, subject: Subject, parent=None):
        super().__init__(parent)
        self.subject = subject
        self.setObjectName("Panel")
        self._analyzer = False
        self.setStyleSheet(
            f"QFrame#Panel {{ background:{theme.SURFACE_2};"
            f" border:1px solid {theme.LINE}; border-radius:9px; }}")

        column = QVBoxLayout(self)
        column.setContentsMargins(9, 9, 9, 9)
        column.setSpacing(7)

        self.drop = DropZone(t("Picture {n}", n=index + 1), _IMAGE_EXT, compact=True)
        self.drop.changed.connect(self._on_picture)
        column.addWidget(self.drop)

        self.description = QLineEdit(subject.description)
        # Qt sizes a line edit to its placeholder by default, and these
        # placeholders are sentences - which was pushing the whole left column
        # wider than the window and giving it a sideways scrollbar.
        self.description.setMinimumWidth(120)
        self.description.setPlaceholderText(
            t("What it shows, e.g. a woman in a red jacket"))
        self.description.textChanged.connect(self._on_description)

        described = QHBoxLayout()
        described.setSpacing(6)
        described.addWidget(self.description, 1)

        # Hidden unless a prompt helper is actually configured and answering.
        # A greyed button with no explanation is worse than no button.
        self.analyze_btn = QPushButton("✨")
        self.analyze_btn.setFixedWidth(34)
        self.analyze_btn.setCursor(Qt.PointingHandCursor)
        self.analyze_btn.setToolTip(t(
            "Look at this picture and write the description for me."))
        self.analyze_btn.setVisible(False)
        self.analyze_btn.clicked.connect(lambda: self.analyze_asked.emit(self))
        described.addWidget(self.analyze_btn)
        column.addLayout(described)

        row = QHBoxLayout()
        row.setSpacing(6)

        self.short_name = QLineEdit(subject.short_name)
        self.short_name.setMinimumWidth(100)
        self.short_name.setPlaceholderText(t("Call it… e.g. the rider"))
        self.short_name.setToolTip(t(
            "The name your shot descriptions use for this picture, so "
            "“the rider turns to camera” lands on this face."))
        self.short_name.textChanged.connect(self._on_short_name)
        row.addWidget(self.short_name, 1)

        self.kind = QComboBox()
        self.kind.setMinimumContentsLength(8)
        self.kind.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        for key in SUBJECT_KINDS:
            self.kind.addItem(t(KIND_LABELS.get(key, key)), key)
        self.kind.setCurrentIndex(max(0, list(SUBJECT_KINDS).index(subject.kind)))
        self.kind.currentIndexChanged.connect(self._on_kind)
        row.addWidget(self.kind)
        column.addLayout(row)

        self._sync_enabled()

    # -- edits -------------------------------------------------------------
    def _on_picture(self, path: str) -> None:
        self.subject.image = Path(path) if path else None
        self._sync_enabled()
        self.changed.emit()

    def _on_description(self, text: str) -> None:
        self.subject.description = text
        self.changed.emit()

    def _on_short_name(self, text: str) -> None:
        self.subject.short_name = text
        self.changed.emit()

    def _on_kind(self, _index: int) -> None:
        self.subject.kind = self.kind.currentData() or "person"
        self.changed.emit()

    def set_analyzer_available(self, available: bool) -> None:
        self._analyzer = available
        self.analyze_btn.setVisible(available)
        self._sync_enabled()

    def set_analyzing(self, busy: bool) -> None:
        """Show the wait, so a slow model does not look like a dead button."""
        self.analyze_btn.setEnabled(not busy and self.subject.active)
        self.analyze_btn.setText("…" if busy else "✨")

    def _sync_enabled(self) -> None:
        """The text fields only mean anything once there is a picture."""
        active = self.subject.active
        for widget in (self.description, self.short_name, self.kind):
            widget.setEnabled(active)
        # Nothing to look at without a picture.
        self.analyze_btn.setEnabled(active)
        # An attached picture with nothing said about it is the one case worth
        # nudging: the render will work and quietly ignore the reference.
        needs_text = active and not self.subject.description.strip()
        self.description.setStyleSheet(
            f"border:1px solid {theme.WARN};" if needs_text else "")

    def refresh(self) -> None:
        self._sync_enabled()


class SubjectPanel(QWidget):
    """The three slots, and one honest line about what using them costs."""

    changed = Signal()
    #: Bubbled up from whichever slot's ✨ was pressed.
    analyze_asked = Signal(object)

    def __init__(self, subjects: list[Subject], parent=None):
        super().__init__(parent)
        self._subjects = subjects

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(7)

        heading = QLabel(t("Keep someone consistent  (optional)"))
        heading.setObjectName("Heading")
        heading.setWordWrap(True)
        column.addWidget(heading)

        self.slots: list[SubjectSlot] = []
        for index in range(SUBJECT_SLOTS):
            slot = SubjectSlot(index, subjects[index])
            slot.changed.connect(self._on_changed)
            slot.analyze_asked.connect(self.analyze_asked.emit)
            column.addWidget(slot)
            self.slots.append(slot)

        self.note = QLabel()
        self.note.setObjectName("Hint")
        self.note.setWordWrap(True)
        column.addWidget(self.note)

        self._refresh_note()

    def set_analyzer_available(self, available: bool) -> None:
        for slot in self.slots:
            slot.set_analyzer_available(available)

    def _on_changed(self) -> None:
        self._refresh_note()
        self.changed.emit()

    def _refresh_note(self) -> None:
        for slot in self.slots:
            slot.refresh()
        if any(s.active for s in self._subjects):
            self.note.setText(t(
                "Using pictures switches to a second, larger model, so the "
                "first render after a change takes noticeably longer."))
            self.note.setStyleSheet(f"color:{theme.WARN}; font-size:11px;")
        else:
            self.note.setText(t(
                "Add a picture to keep the same person, animal or outfit "
                "across the whole clip. Leave empty to invent everything."))
            self.note.setStyleSheet(f"color:{theme.DIM}; font-size:11px;")
