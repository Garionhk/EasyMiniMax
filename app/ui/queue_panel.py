"""What is lined up, what is running, and a way to take one back out.

Pressing Create while something is already being made adds to the queue rather
than being ignored. That only works if the queue can be seen: a press that
appears to do nothing is indistinguishable from a broken button, and a batch
set up for the evening is worth nothing if one mistyped shot cannot be pulled
back out of it.

So this is small and always honest - one line per item, in the order they will
run, and it hides itself completely when there is nothing queued.

Adapted from EasyAI's Queue tab. That one is a whole tab with its own gallery,
because three modes share one queue there. Here there is one screen and one
kind of job, so it is a strip in the results column instead. It is built from
plain rows rather than a QListWidget on purpose: this column already scrolls,
and a list inside a scroll area gives two scrollbars for one list.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app.i18n import plural, t
from app.queue import LABELS, State
from app.ui import theme

#: A mark per state, so the list can be read at a glance rather than studied.
MARKS = {
    State.WAITING: "⏳",
    State.RUNNING: "▶",
    State.DONE: "✓",
    State.FAILED: "!",
    State.CANCELLED: "✕",
}

COLOURS = {
    State.WAITING: theme.MUTED,
    State.RUNNING: theme.ACCENT,
    State.DONE: theme.OK,
    State.FAILED: theme.BAD,
    State.CANCELLED: theme.DIM,
}


class Elided(QLabel):
    """A label that shortens with an ellipsis instead of clipping.

    This column can be dragged narrow, and a prompt is longer than any width
    it will ever have. Plain clipping cuts a word in half and looks like a
    rendering fault; an ellipsis looks like a decision.

    The two size hints matter as much as the ellipsis does. A plain QLabel
    reports the width of its text as its *minimum*, which Qt then enforces on
    everything containing it - one long prompt would give this whole column a
    floor it could never be dragged under, and a column that cannot shrink is
    a column that gets clipped. So the minimum is whatever it was told to
    accept, and the preferred width is measured from the full text rather than
    from the shortened text currently on screen, which would otherwise ratchet
    down and never come back.
    """

    def __init__(self, floor: int = 40, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(floor)

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(QFontMetrics(self.font()).horizontalAdvance(self._full) + 2)
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(self.minimumWidth())
        return hint

    def set_full_text(self, text: str) -> None:
        self._full = text or ""
        self.setToolTip(self._full if len(self._full) > 20 else "")
        self._apply()

    def full_text(self) -> str:
        return self._full

    def _apply(self) -> None:
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(
            self._full, Qt.ElideRight, max(self.width() - 2, 20)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply()


class QueueRow(QFrame):
    """One item: what it is, how far along it is, and a way to stop it."""

    cancel_requested = Signal(object)

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("QueueRow")
        self.setStyleSheet(
            f"QFrame#QueueRow {{ background:{theme.SURFACE_2};"
            f" border:1px solid {theme.LINE}; border-radius:8px; }}")

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 5, 6, 5)
        row.setSpacing(8)

        self.mark = QLabel()
        self.mark.setFixedWidth(14)
        row.addWidget(self.mark)

        self.name = Elided(floor=36)
        self.name.setObjectName("Hint")
        row.addWidget(self.name)

        self.detail = Elided()
        self.detail.setObjectName("Hint")
        row.addWidget(self.detail, 1)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setFixedWidth(70)
        self.bar.setFixedHeight(8)
        self.bar.setTextVisible(False)
        row.addWidget(self.bar)

        self.stop = QPushButton("✕")
        self.stop.setObjectName("Icon")
        self.stop.setFixedSize(26, 24)
        self.stop.setCursor(Qt.PointingHandCursor)
        self.stop.clicked.connect(lambda: self.cancel_requested.emit(self.item))
        row.addWidget(self.stop)

        # The bar and the ✕ come and go with the state, and a row that changes
        # height as it finishes makes the whole list jump under the pointer.
        # A floor, not a fixed size, so a larger system font still fits.
        self.setMinimumHeight(24 + row.contentsMargins().top()
                              + row.contentsMargins().bottom())

        self.refresh()

    def refresh(self) -> None:
        item = self.item
        self.mark.setText(MARKS.get(item.state, ""))
        self.mark.setStyleSheet(f"color:{COLOURS.get(item.state, theme.MUTED)};")
        self.name.set_full_text(item.workflow_name)
        self.detail.set_full_text(item.summary())

        running = item.state is State.RUNNING
        self.bar.setVisible(running)
        self.bar.setValue(item.percent)
        # A finished item has nothing left to stop. Its row stays, because
        # what failed and why is the reason to keep looking at this list.
        self.stop.setVisible(not item.state.finished)
        self.stop.setToolTip(t("Stop this one") if running
                             else t("Take this out of the queue"))
        self.setToolTip(f"{t(LABELS[item.state])}\n{item.prompt[:300]}")


class QueuePanel(QWidget):
    """The whole strip: a heading, a row per item, and Clear finished."""

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._rows: dict[int, QueueRow] = {}

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.heading = QLabel(t("Queue"))
        self.heading.setObjectName("Title")
        head.addWidget(self.heading)

        # No floor at all: on a narrow column the count is the first thing
        # that can go, and the rows underneath say the same in more detail.
        self.count = Elided(floor=0)
        self.count.setObjectName("Hint")
        head.addWidget(self.count)
        head.addStretch(1)

        self.clear_btn = QPushButton(t("Clear finished"))
        self.clear_btn.setToolTip(t(
            "Take the finished ones off this list. No video is deleted."))
        self.clear_btn.clicked.connect(manager.clear_finished)
        head.addWidget(self.clear_btn)
        column.addLayout(head)

        self.rows = QWidget()
        self._rows_layout = QVBoxLayout(self.rows)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        column.addWidget(self.rows)

        manager.changed.connect(self.rebuild)
        manager.item_changed.connect(self._on_item_changed)
        self.rebuild()

    # -- the list ----------------------------------------------------------
    def rebuild(self) -> None:
        """Redraw every row.

        The list is short - a queue is an evening's work, not a database - so
        rebuilding it whole is less code than working out what moved, and
        progress goes straight to the row it belongs to rather than through
        here.
        """
        while self._rows_layout.count():
            widget = self._rows_layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows.clear()

        for item in self.manager.items:
            row = QueueRow(item)
            row.cancel_requested.connect(self.manager.cancel)
            self._rows_layout.addWidget(row)
            self._rows[item.id] = row

        self._refresh_count()
        # Nothing queued is the normal state, and an empty box with a heading
        # would take room from the preview to report no news at all.
        self.setVisible(bool(self.manager.items))

    def _refresh_count(self) -> None:
        waiting = len(self.manager.waiting())
        bits = []
        if self.manager.running is not None:
            bits.append(t("1 running"))
        if waiting:
            bits.append(plural(waiting, "1 waiting", "{n} waiting"))
        self.count.set_full_text("  ·  ".join(bits))
        self.clear_btn.setVisible(
            any(i.state.finished for i in self.manager.items))

    def _on_item_changed(self, item) -> None:
        row = self._rows.get(item.id)
        if row is not None:
            row.refresh()
        self._refresh_count()

    def retranslate(self) -> None:
        self.heading.setText(t("Queue"))
        self.clear_btn.setText(t("Clear finished"))
        self.clear_btn.setToolTip(t(
            "Take the finished ones off this list. No video is deleted."))
        self.rebuild()
