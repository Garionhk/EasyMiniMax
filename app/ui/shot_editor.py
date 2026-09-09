"""The shot list: a timeline you can drag, and a prompt box per shot.

MiniMax H3 was trained on storyboard prompts with explicit time markers, and the
Director compiles the shots below into exactly that. So cutting a clip into
shots is not a power-user extra here - it is the model's own native way of being
told what happens when, and a five-second clip with three shots follows
direction far better than the same clip with one long paragraph.

Two halves that always agree:

* :class:`ShotTimeline` - the strip. Proportional blocks, draggable boundaries.
* :class:`ShotList`     - one prompt box per shot, in the same order.

Dragging a boundary moves frames from one shot to its neighbour, so the total
never changes. That is the invariant the whole editor is built around: the shots
tile the clip exactly, always, and no other code has to check.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from app.h3.presets import MIN_SHOT_FRAMES, MODEL_FPS
from app.h3.timeline import Shot
from app.i18n import t
from app.ui import theme

#: How close to a boundary the pointer has to be to grab it.
_GRAB_PX = 6
#: Above this many shots the strip stops being readable and the model stops
#: getting a useful storyboard - a shot per second is already very fast cutting.
MAX_SHOTS = 8


class ShotTimeline(QWidget):
    """The draggable strip. Owns no state - it draws the list it is given."""

    #: A boundary was dragged; the lengths changed.
    resized = Signal()
    #: A block was clicked. Carries the shot index.
    picked = Signal(int)
    #: Remove the shot at this index - right-click menu, or the Delete key.
    remove_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(58)
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)
        # Focusable so Delete can remove the highlighted shot once the strip
        # has been clicked. Scoped to the strip on purpose: a Delete shortcut
        # on the whole editor would eat the key inside the prompt boxes.
        self.setFocusPolicy(Qt.StrongFocus)
        self._shots: list[Shot] = []
        self._total = 1
        self._selected = 0
        self._dragging: int | None = None   # index of the shot left of the edge

    def set_shots(self, shots: list[Shot], total: int) -> None:
        self._shots = shots
        self._total = max(1, int(total))
        self.update()

    def set_selected(self, index: int) -> None:
        self._selected = index
        self.update()

    # -- geometry ----------------------------------------------------------
    def _edges(self) -> list[int]:
        """Pixel x of every boundary, including both ends."""
        if not self._shots:
            return [0, self.width()]
        width = self.width()
        out, running = [0], 0
        for shot in self._shots:
            running += shot.length
            out.append(int(round(width * running / self._total)))
        out[-1] = width
        return out

    def _boundary_at(self, x: int) -> int | None:
        """Which inner boundary the pointer is on, if any."""
        edges = self._edges()
        for index in range(1, len(edges) - 1):
            if abs(x - edges[index]) <= _GRAB_PX:
                return index - 1        # the shot to the left of the edge
        return None

    def _block_at(self, x: int) -> int:
        edges = self._edges()
        for index in range(len(edges) - 1):
            if edges[index] <= x < edges[index + 1]:
                return index
        return max(0, len(self._shots) - 1)

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.INPUT_BG))
        painter.drawRoundedRect(self.rect(), 8, 8)

        if not self._shots:
            painter.setPen(QPen(QColor(theme.DIM)))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             t("One shot for the whole clip"))
            painter.end()
            return

        edges = self._edges()
        for index, shot in enumerate(self._shots):
            left, right = edges[index], edges[index + 1]
            block = QRect(left + 1, 3, max(1, right - left - 2), self.height() - 6)
            chosen = index == self._selected

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.ACCENT if chosen else theme.SURFACE_3))
            painter.drawRoundedRect(block, 6, 6)

            # The number and the length, dropped as soon as they stop fitting -
            # a block with half a digit in it reads as a rendering fault.
            painter.setPen(QPen(QColor(
                theme.ACCENT_INK if chosen else theme.TEXT)))
            font = QFont(theme.UI_FONT)
            font.setPointSize(9)
            font.setBold(chosen)
            painter.setFont(font)
            label = str(index + 1)
            seconds = f"{shot.length / MODEL_FPS:.1f}s"
            if block.width() > 52:
                painter.drawText(block, Qt.AlignCenter, f"{label}   {seconds}")
            elif block.width() > 18:
                painter.drawText(block, Qt.AlignCenter, label)

        # The grab handles, drawn last so they sit over the blocks.
        painter.setPen(QPen(QColor(theme.BG), 2))
        for index in range(1, len(edges) - 1):
            x = edges[index]
            painter.drawLine(x, 6, x, self.height() - 6)

    # -- interaction -------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        x = int(event.position().x())
        if self._dragging is not None:
            self._drag_to(x)
            return
        self.setCursor(Qt.SplitHCursor if self._boundary_at(x) is not None
                       else Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        x = int(event.position().x())
        boundary = self._boundary_at(x)
        if boundary is not None:
            self._dragging = boundary
            return
        if self._shots:
            self.picked.emit(self._block_at(x))

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = None

    def contextMenuEvent(self, event) -> None:
        if not self._shots:
            return
        index = self._block_at(int(event.pos().x()))
        menu = QMenu(self)
        remove = menu.addAction(t("Remove shot {n}", n=index + 1))
        if menu.exec(event.globalPos()) is remove:
            self.remove_requested.emit(index)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._shots:
            self.remove_requested.emit(self._selected)
            return
        super().keyPressEvent(event)

    def leaveEvent(self, event) -> None:
        self.setCursor(Qt.ArrowCursor)

    def _drag_to(self, x: int) -> None:
        """Move one boundary, taking the frames off the neighbour.

        Frames are moved between the pair, never created or destroyed, so the
        shots still tile the clip exactly when the pointer is let go. Both sides
        stop at the minimum length rather than one shot swallowing another.
        """
        index = self._dragging
        if index is None or index + 1 >= len(self._shots):
            return
        left, right = self._shots[index], self._shots[index + 1]
        pair = left.length + right.length

        edges = self._edges()
        start_frames = int(round(edges[index] / max(1, self.width()) * self._total))
        wanted = int(round(x / max(1, self.width()) * self._total)) - start_frames
        wanted = max(MIN_SHOT_FRAMES, min(pair - MIN_SHOT_FRAMES, wanted))

        if wanted != left.length:
            left.length = wanted
            right.length = pair - wanted
            self.update()
            self.resized.emit()


class ShotRow(QFrame):
    """One shot's number, length and prompt."""

    changed = Signal()
    removed = Signal(object)      # self
    focused = Signal(object)      # self

    def __init__(self, index: int, shot: Shot, parent=None):
        super().__init__(parent)
        self.shot = shot
        self.setObjectName("Panel")
        self._selected = False

        row = QHBoxLayout(self)
        row.setContentsMargins(9, 8, 9, 8)
        row.setSpacing(9)

        column = QVBoxLayout()
        column.setSpacing(2)
        self.number = QLabel(str(index + 1))
        self.number.setAlignment(Qt.AlignCenter)
        self.number.setFixedSize(24, 24)
        column.addWidget(self.number)
        self.length = QLabel()
        self.length.setAlignment(Qt.AlignCenter)
        self.length.setStyleSheet(
            f"color:{theme.DIM}; font-family:'{theme.MONO_FONT}'; font-size:10px;")
        column.addWidget(self.length)
        column.addStretch(1)
        row.addLayout(column)

        self.prompt = QPlainTextEdit()
        self.prompt.setPlainText(shot.prompt)
        self.prompt.setPlaceholderText(
            t("What happens in this shot? e.g. cut to a close-up on her face"))
        self.prompt.setFixedHeight(56)
        self.prompt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.prompt.textChanged.connect(self._on_text)
        self.prompt.focusInEvent = self._wrap_focus(self.prompt.focusInEvent)
        row.addWidget(self.prompt, 1)

        self.remove_btn = QPushButton("✕")
        self.remove_btn.setObjectName("Icon")
        self.remove_btn.setFixedSize(26, 26)
        self.remove_btn.setToolTip(t(
            "Remove this shot. Its time is given to the shot before it, so the "
            "clip stays the same length."))
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.clicked.connect(lambda: self.removed.emit(self))
        row.addWidget(self.remove_btn, 0, Qt.AlignTop)

        self.set_index(index)
        self.refresh()

    def _wrap_focus(self, original):
        def handler(event):
            self.focused.emit(self)
            original(event)
        return handler

    def _on_text(self) -> None:
        self.shot.prompt = self.prompt.toPlainText()
        self.changed.emit()

    def set_index(self, index: int) -> None:
        self.number.setText(str(index + 1))

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.number.setStyleSheet(
            f"background:{theme.ACCENT if selected else theme.SURFACE_3};"
            f" color:{theme.ACCENT_INK if selected else theme.MUTED};"
            f" border-radius:12px; font-weight:700; font-size:11px;")
        self.setStyleSheet(
            f"QFrame#Panel {{ background:{theme.SURFACE_2};"
            f" border:1px solid {theme.ACCENT if selected else theme.LINE};"
            f" border-radius:9px; }}")

    def refresh(self) -> None:
        self.length.setText(f"{self.shot.length / MODEL_FPS:.1f}s")


class ShotEditor(QWidget):
    """The strip, the rows, and the buttons that add and remove shots."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shots: list[Shot] = []
        self._total = 124
        self._selected = 0
        self._rows: list[ShotRow] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(9)

        heading = QLabel(t("Shots"))
        heading.setObjectName("Heading")
        root.addWidget(heading)

        hint = QLabel(t(
            "Cut your clip into shots and describe each one. Drag the dividers "
            "to change how long each lasts, and press ✕ to remove one. Leave "
            "this empty to describe the whole clip in one go."))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.timeline = ShotTimeline()
        self.timeline.resized.connect(self._on_timeline_resized)
        self.timeline.picked.connect(self.select)
        self.timeline.remove_requested.connect(self.delete_shot)
        root.addWidget(self.timeline)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        self._rows_layout = QVBoxLayout(holder)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(7)
        self._rows_layout.addStretch(1)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.add_btn = QPushButton(t("＋  Add a shot"))
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setToolTip(t(
            "The first one covers the whole clip. After that, each press "
            "splits the highlighted shot in two."))
        self.add_btn.clicked.connect(self.add_shot)
        buttons.addWidget(self.add_btn)
        self.clear_btn = QPushButton(t("Clear shots"))
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear)
        buttons.addWidget(self.clear_btn)
        buttons.addStretch(1)
        root.addLayout(buttons)

        self._sync()

    # -- what the window asks for -----------------------------------------
    def shots(self) -> list[Shot]:
        return self._shots

    def set_shots(self, shots: list[Shot], total: int) -> None:
        self._shots = shots
        self._total = total
        self._selected = min(self._selected, max(0, len(shots) - 1))
        self._rebuild_rows()
        self._sync()

    def set_total(self, total: int) -> None:
        """The clip got longer or shorter; rescale the shots to fit it."""
        total = max(1, int(total))
        if total == self._total:
            return
        if self._shots:
            scale = total / max(1, sum(s.length for s in self._shots))
            running = 0
            for shot in self._shots[:-1]:
                shot.length = max(MIN_SHOT_FRAMES, int(round(shot.length * scale)))
                running += shot.length
            self._shots[-1].length = max(MIN_SHOT_FRAMES, total - running)
        self._total = total
        self._sync()
        self.changed.emit()

    # -- editing -----------------------------------------------------------
    def add_shot(self) -> None:
        """Name the first shot, or split the chosen one in two.

        The first press adds a single shot across the whole clip - the shot that
        was already implied by an empty list - so pressing "Add a shot" once
        gives you one shot to describe, not two. Someone laying out a video
        starts by describing what is there.

        After that, splitting rather than appending is what keeps the total
        fixed: appending would either stretch the clip, changing a length the
        user did not touch, or squeeze every other shot to make room.
        """
        if len(self._shots) >= MAX_SHOTS:
            return

        if not self._shots:
            self._shots.append(Shot("", self._total))
            self._selected = 0
        else:
            index = min(self._selected, len(self._shots) - 1)
            victim = self._shots[index]
            if victim.length < MIN_SHOT_FRAMES * 2:
                # Too short to divide. Take the room from the longest shot
                # instead, so the button still does something. Found by
                # position for the same reason remove_row is - equal-valued
                # shots are the normal case here, not an edge one.
                index = max(range(len(self._shots)),
                            key=lambda i: self._shots[i].length)
                victim = self._shots[index]
                if victim.length < MIN_SHOT_FRAMES * 2:
                    return
            # Halve it and give the remainder to the new shot, so the two
            # together are exactly what the one was and the total is untouched.
            half = victim.length // 2
            rest = victim.length - half
            victim.length = half
            self._shots.insert(index + 1, Shot("", rest))
            self._selected = index + 1

        self._rebuild_rows()
        self._sync()
        self.changed.emit()

    def delete_shot(self, index: int) -> None:
        """Remove the shot at ``index``, handing its frames to a neighbour.

        The single place a shot is removed - the ✕ button, the timeline's
        right-click menu and the Delete key all come through here, so there is
        one implementation of "and where do its frames go?" rather than three
        that can drift apart.

        The frames go to the shot before it, or to the one after when the first
        shot is deleted. Never to the clip length: removing a shot must not
        quietly shorten the video.
        """
        if not (0 <= index < len(self._shots)):
            return

        length = self._shots[index].length
        self._shots.pop(index)
        if self._shots:
            neighbour = self._shots[index - 1] if index > 0 else self._shots[0]
            neighbour.length += length

        self._selected = min(self._selected, max(0, len(self._shots) - 1))
        self._rebuild_rows()
        self._sync()
        self.changed.emit()

    def delete_selected(self) -> None:
        """Remove whichever shot is currently highlighted."""
        self.delete_shot(self._selected)

    def remove_row(self, row: ShotRow) -> None:
        """The ✕ on one row, resolved to that row's position.

        By the row's position in the list, never by looking its Shot up with
        ``index()``. Shot is a dataclass, so two shots with the same words and
        the same length compare equal - which is exactly what you have a moment
        after splitting one - and ``index()`` would return the first of them.
        Pressing ✕ on the third shot would quietly delete the second.
        """
        if row in self._rows:
            self.delete_shot(self._rows.index(row))

    def clear(self) -> None:
        self._shots.clear()
        self._selected = 0
        self._rebuild_rows()
        self._sync()
        self.changed.emit()

    def select(self, index: int) -> None:
        self._selected = index
        self._sync_selection()

    # -- keeping the two halves in step ------------------------------------
    def _on_row_focused(self, row: ShotRow) -> None:
        """Typing in a prompt box lights the matching block on the strip."""
        if row in self._rows:
            self.select(self._rows.index(row))

    def _on_timeline_resized(self) -> None:
        for row in self._rows:
            row.refresh()
        self.changed.emit()

    def _rebuild_rows(self) -> None:
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows = []

        for index, shot in enumerate(self._shots):
            row = ShotRow(index, shot)
            row.changed.connect(self.changed.emit)
            row.removed.connect(self.remove_row)
            row.focused.connect(self._on_row_focused)
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._rows.append(row)

    def _sync(self) -> None:
        self.timeline.set_shots(self._shots, self._total)
        for index, row in enumerate(self._rows):
            row.set_index(index)
            row.refresh()
        self.add_btn.setEnabled(len(self._shots) < MAX_SHOTS)
        self.clear_btn.setEnabled(bool(self._shots))
        self._sync_selection()

    def _sync_selection(self) -> None:
        self.timeline.set_selected(self._selected)
        for index, row in enumerate(self._rows):
            row.set_selected(index == self._selected)
