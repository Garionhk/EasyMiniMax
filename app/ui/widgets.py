"""Reusable pieces of the make-a-video screen.

Copied from EasyAI, minus the parts that only make sense for a program with
many workflows and a megapixel budget:

* :class:`FlowLayout`    - wrapping layout Qt does not ship with.
* :class:`Switch`        - a checkbox drawn as a toggle.
* :class:`DropZone`      - click-or-drag picture input, with a thumbnail.
* :class:`PreviewPane`   - live preview during a run, final frame after it.
* :class:`ResultGallery` - thumbnails and player for everything made today.

The choosers this app needs instead - resolution, duration, Turbo/Quality -
live in app/ui/controls.py, because H3 takes a fixed canvas from a short list
rather than any width and height you care to name.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QListWidget,
    QListWidgetItem, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from app.i18n import t
from app.ui import theme

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
_VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
_AUDIO_EXT = {".flac", ".mp3", ".wav", ".ogg", ".m4a"}


def open_in_explorer(path: str | Path) -> None:
    """Open the file manager with this file selected.

    Explorer wants the switch and the path as a single argument -
    ``/select,C:\\pictures\\a.png``. Passing them as two arguments makes it
    ignore both and open Documents instead. If the file has gone, fall back to
    showing the folder rather than opening the wrong window.
    """
    path = Path(path)
    if not path.exists():
        open_folder(path.parent)
        return
    try:
        if sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{path}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError:
        open_folder(path.parent)


def open_folder(path: str | Path) -> None:
    """Open a folder itself, rather than selecting something inside it."""
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        if sys.platform == "win32":
            subprocess.Popen(f'explorer "{path}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


def _square(pixmap: QPixmap, side: int) -> QPixmap:
    """Centre a picture on a transparent square of the given size."""
    scaled = pixmap.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    canvas = QPixmap(side, side)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap((side - scaled.width()) // 2,
                       (side - scaled.height()) // 2, scaled)
    painter.end()
    return canvas


class FlowLayout(QLayout):
    """Lays widgets left to right, wrapping onto a new line when out of room.

    Qt has no such layout. The shape chips need one: nine of them do not fit
    across the middle column, and a horizontal layout would either squash them
    or force a scrollbar.
    """

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect, apply: bool) -> int:
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


# --------------------------------------------------------------------------
class Switch(QCheckBox):
    """A tick box drawn as a sliding switch.

    Still a QCheckBox underneath - isChecked, setChecked and toggled all behave
    normally - so nothing that uses one has to know it looks different.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"QCheckBox{{color:{theme.MUTED};font-size:13px;spacing:10px;}}"
            f"QCheckBox:hover{{color:{theme.TEXT};}}"
            f"QCheckBox::indicator{{width:34px;height:19px;border-radius:10px;"
            f"border:1px solid {theme.LINE_2};background:{theme.SURFACE_2};}}"
            f"QCheckBox::indicator:checked{{background:{theme.ACCENT};"
            f"border-color:{theme.ACCENT};}}")


class DropZone(QFrame):
    """A click-or-drop target for one input file."""

    changed = Signal(str)   # empty string when cleared

    def __init__(self, label: str, kinds: set[str], compact: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setAcceptDrops(True)
        self._label = label
        self._kinds = kinds
        self._path: str = ""
        self._compact = compact

        # Compact zones let a workflow that wants ten reference pictures still
        # show several at once without pushing the Create button off screen.
        # Two lines of text plus padding; the earlier heights cropped the
        # "click to choose" caption once the theme grew its type.
        thumb_size = 42 if compact else 60
        self.setFixedHeight(72 if compact else 96)

        layout = QHBoxLayout(self)
        margin = 6 if compact else 10
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(8 if compact else 10)

        self.thumb = QLabel()
        self.thumb.setFixedSize(thumb_size, thumb_size)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(
            f"background:{theme.BG_INPUT};border-radius:6px;color:{theme.TEXT_DIM};")
        layout.addWidget(self.thumb)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.title = QLabel(label)
        self.title.setObjectName("Heading")
        self.title.setMinimumWidth(60)
        self.caption = QLabel(t("Click to choose, or drag one here"))
        self.caption.setObjectName("Hint")
        # Wraps even when compact. Without this the caption is a sentence that
        # demands its full width, and a column of these sets the minimum width
        # of everything containing them.
        self.caption.setWordWrap(True)
        self.caption.setMinimumWidth(60)
        text_col.addWidget(self.title)
        text_col.addWidget(self.caption)
        layout.addLayout(text_col, 1)

        self.clear_btn = QPushButton(t("Remove"))
        self.clear_btn.setVisible(False)
        self.clear_btn.clicked.connect(lambda: self.set_path(""))
        layout.addWidget(self.clear_btn)

        self._reset_thumb()

    # -- state -------------------------------------------------------------
    def path(self) -> str:
        return self._path

    def set_path(self, path: str) -> None:
        self._path = path or ""
        if self._path:
            name = Path(self._path).name
            self.caption.setText(name if len(name) <= 42 else name[:20] + "…" + name[-18:])
            self.caption.setToolTip(self._path)
            self.clear_btn.setVisible(True)
            self._show_thumb(self._path)
        else:
            self.caption.setText(t("Click to choose, or drag one here"))
            self.caption.setToolTip("")
            self.clear_btn.setVisible(False)
            self._reset_thumb()
        self.changed.emit(self._path)

    def retranslate(self) -> None:
        self.clear_btn.setText(t("Remove"))
        if not self._path:
            self.caption.setText(t("Click to choose, or drag one here"))

    def _reset_thumb(self) -> None:
        icon = "🖼" if _IMAGE_EXT & self._kinds else ("🎵" if _AUDIO_EXT & self._kinds else "🎬")
        self.thumb.setPixmap(QPixmap())
        self.thumb.setText(icon)

    def _show_thumb(self, path: str) -> None:
        if Path(path).suffix.lower() in _IMAGE_EXT:
            pix = QPixmap(path)
            if not pix.isNull():
                side = self.thumb.width()
                self.thumb.setText("")
                self.thumb.setPixmap(pix.scaled(side, side, Qt.KeepAspectRatio,
                                                Qt.SmoothTransformation))
                return
        self._reset_thumb()

    # -- interaction -------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        patterns = " ".join(f"*{e}" for e in sorted(self._kinds))
        path, _ = QFileDialog.getOpenFileName(
            self, f"Choose a {self._label.lower()}", "", f"Files ({patterns})")
        if path:
            self.set_path(path)

    def dragEnterEvent(self, event) -> None:
        if self._first_accepted(event):
            event.acceptProposedAction()
            self.setStyleSheet(f"QFrame#Panel {{ border: 2px solid {theme.ACCENT}; }}")

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet("")

    def dropEvent(self, event) -> None:
        self.setStyleSheet("")
        path = self._first_accepted(event)
        if path:
            self.set_path(path)
            event.acceptProposedAction()

    def _first_accepted(self, event) -> str:
        if not event.mimeData().hasUrls():
            return ""
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local and Path(local).suffix.lower() in self._kinds:
                return local
        return ""


# --------------------------------------------------------------------------
class PreviewPane(QLabel):
    """Live preview while the render runs, then the finished video."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        # A floor, not the size it usually is: it takes all the spare height
        # in its column. Low enough that the window can be made short without
        # the preview alone holding it open.
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            f"background:{theme.BG_INPUT};border:1px solid {theme.BORDER};"
            f"border-radius:10px;color:{theme.TEXT_DIM};")
        self._pixmap: QPixmap | None = None
        #: The video whose frame we are waiting for. Guards against a slow
        #: grab landing after the user has moved on to something else.
        self._awaiting: str | None = None
        self.clear_preview(t("Your video will appear here"))

    def clear_preview(self, message: str = "") -> None:
        self._pixmap = None
        self.setText(message or t("Your video will appear here"))

    def show_bytes(self, data: bytes) -> None:
        image = QImage.fromData(data)
        if not image.isNull():
            self.show_pixmap(QPixmap.fromImage(image))

    def show_file(self, path: str | Path) -> None:
        pix = QPixmap(str(path))
        if not pix.isNull():
            self.show_pixmap(pix)
        else:
            self.clear_preview(f"Saved:\n{Path(path).name}")

    def show_video(self, path: str | Path) -> None:
        """Show a video's opening frame, so results are comparable at a glance.

        Until it arrives the filename is shown, which is what this pane used to
        say permanently. Decoding is asynchronous, so a slow or unreadable file
        never holds up the window.
        """
        from app.ui.video import grabber

        path = str(path)
        self._awaiting = path
        found = grabber().cached(path)
        if found is not None:
            self._show_frame(path, *found)
            return

        self.clear_preview(t("Saved {name}", name=Path(path).name))
        source = grabber()
        source.ready.connect(self._on_frame_ready)
        source.request(path)

    def _on_frame_ready(self, path: str, image: QImage, info) -> None:
        if path == getattr(self, "_awaiting", None):
            self._show_frame(path, image, info)

    def _show_frame(self, path: str, image: QImage, info) -> None:
        self.show_pixmap(QPixmap.fromImage(image))
        self.setToolTip(f"{Path(path).name}\n{info.summary()}")

    def show_pixmap(self, pix: QPixmap) -> None:
        self._pixmap = pix
        self._awaiting = None
        self.setText("")
        self._rescale()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap and not self._pixmap.isNull():
            super().setPixmap(self._pixmap.scaled(
                self.size() - QSize(8, 8), Qt.KeepAspectRatio, Qt.SmoothTransformation))


# --------------------------------------------------------------------------
class ResultGallery(QWidget):
    """Everything made this session, newest first. Click to open."""

    selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.title = QLabel(t("Your results"))
        self.title.setObjectName("Heading")
        header.addWidget(self.title)
        header.addStretch(1)
        self.open_folder_btn = QPushButton(t("Open folder"))
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self._open_folder)
        header.addWidget(self.open_folder_btn)
        layout.addLayout(header)

        self.list = QListWidget()
        self.list.setIconSize(QSize(52, 52))
        self.list.itemClicked.connect(self._on_click)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.list, 1)

        self.empty_hint = QLabel(
            t("Nothing yet — press Create to make something."))
        self.empty_hint.setObjectName("Hint")
        self.empty_hint.setWordWrap(True)
        layout.addWidget(self.empty_hint)

        self._last_dir: Path | None = None
        self._folder: Path | None = None
        self._listening = False

    def add(self, path: str | Path) -> None:
        path = Path(path)
        item = QListWidgetItem(path.name)
        item.setData(Qt.UserRole, str(path))
        item.setSizeHint(QSize(0, 62))
        if path.suffix.lower() in _IMAGE_EXT:
            pix = QPixmap(str(path))
            if not pix.isNull():
                # Pad to a square so every row's text starts at the same place;
                # a tall 2:3 picture is otherwise much narrower than a wide one
                # and its filename crowds the thumbnail.
                item.setIcon(_square(pix, self.list.iconSize().width()))
        elif path.suffix.lower() in _VIDEO_EXT:
            # A real thumbnail rather than a symbol: this list is where several
            # takes get compared, so telling them apart matters most here.
            from app.ui.video import grabber

            item.setText("🎬  " + path.name)
            source = grabber()
            found = source.cached(str(path))
            if found is not None:
                self._set_frame(str(path), found[0], found[1])
            else:
                # Once only: this list is rebuilt whenever the Read tab is
                # opened, and a fresh connection per video would leave the
                # handler running once per rebuild.
                if not self._listening:
                    source.ready.connect(self._on_frame_ready)
                    self._listening = True
                source.request(str(path))
        elif path.suffix.lower() in _AUDIO_EXT:
            item.setText("🎵  " + path.name)
        else:
            # Prompt Helper saves its result as .txt, which is neither a
            # picture nor a sound - a music note in front of a written prompt
            # says nothing true about it.
            item.setText("📝  " + path.name)
        item.setToolTip(f"{path}\n\n" + t("Double-click to open it"))
        self.list.insertItem(0, item)
        self.list.setCurrentRow(0)
        self._last_dir = path.parent
        self.open_folder_btn.setEnabled(True)
        self.empty_hint.setVisible(False)

    def clear(self) -> None:
        """Empty the list so it can be rebuilt from a folder.

        Used by the Read tab, which shows what is on disk rather than what this
        session made, and so has to redraw when the folder changes.
        """
        self.list.clear()
        self._last_dir = None
        self.empty_hint.setVisible(True)

    def _on_frame_ready(self, path: str, image: QImage, info) -> None:
        self._set_frame(path, image, info)

    def _set_frame(self, path: str, image: QImage, info) -> None:
        """Fill in a video's thumbnail once it has been decoded."""
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.UserRole) != path:
                continue
            item.setIcon(_square(QPixmap.fromImage(image),
                                 self.list.iconSize().width()))
            item.setText(Path(path).name)          # the symbol is now redundant
            if info and info.summary():
                item.setToolTip(f"{path}\n{info.summary()}\n\n"
                                + t("Double-click to open it"))
            return

    def retranslate(self) -> None:
        self.title.setText(t("Your results"))
        self.open_folder_btn.setText(t("Open folder"))
        self.empty_hint.setText(t("Nothing yet — press Create to make something."))

    def _on_click(self, item: QListWidgetItem) -> None:
        self.selected.emit(item.data(Qt.UserRole))

    def _on_double_click(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", path])
        except OSError:
            open_in_explorer(path)

    def set_folder(self, folder: str | Path) -> None:
        """Where this tab saves its results.

        Set up front so the button works before anything has been made, which
        is when someone is most likely to go looking for the folder.
        """
        self._folder = Path(folder)
        self.open_folder_btn.setEnabled(True)
        self.open_folder_btn.setToolTip(str(self._folder))

    def _open_folder(self) -> None:
        target = self._last_dir or getattr(self, "_folder", None)
        if target:
            open_folder(target)


# --------------------------------------------------------------------------
