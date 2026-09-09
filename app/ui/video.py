"""First frames for video results.

A finished video used to show only its filename, so comparing three takes meant
opening three files in an external player. This grabs the opening frame of each
one, which is enough to tell them apart at a glance.

Decoding is done by PySide6's own bundled FFmpeg backend through QtMultimedia -
no extra dependency, and nothing for a viewer to install.

The work is event-driven rather than blocking: a grab can take up to a second,
and the results list must stay usable while one is happening. Requests queue up
and answers arrive by signal.
"""
from __future__ import annotations

import os
from collections import OrderedDict, deque
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

#: Qt's FFmpeg backend prints the file's format, streams and metadata to stderr
#: every time it opens something. Harmless in a windowed build, but it buries
#: everything else when running from source.
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.ffmpeg.*=false")

#: How long to wait for one file before giving up. A frame normally arrives in
#: well under a second; anything slower is a file we cannot read.
TIMEOUT_MS = 8000

#: Grabbed frames, newest last. Small because these are only ever shown as
#: thumbnails, and the window keeps its own scaled copies anyway.
_CACHE_LIMIT = 64


class VideoInfo:
    """What the first frame told us about the file."""

    def __init__(self, width: int = 0, height: int = 0, seconds: float = 0.0):
        self.width = width
        self.height = height
        self.seconds = seconds

    def __bool__(self) -> bool:
        return bool(self.width and self.height)

    def summary(self) -> str:
        """e.g. "864 × 480 · 2.3s" - shown under the preview."""
        if not self:
            return ""
        if self.seconds:
            return f"{self.width} × {self.height} · {self.seconds:.1f}s"
        return f"{self.width} × {self.height}"


class FrameGrabber(QObject):
    """Reads the opening frame of one video at a time.

    One player is reused for every request. Qt's media objects want to live on
    the thread with the event loop, so this deliberately stays on the interface
    thread and never blocks it - each grab is a few signals, not a wait.
    """

    ready = Signal(str, QImage, object)      # path, frame, VideoInfo
    failed = Signal(str)                     # path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: OrderedDict[str, tuple[QImage, VideoInfo]] = OrderedDict()
        self._waiting: deque[str] = deque()
        self._current: str | None = None
        #: The frame usually arrives before the duration is known, so it is
        #: held here until both are in - otherwise half the videos report no
        #: length, and the other half report the previous file's.
        self._frame: QImage | None = None
        self._duration_ms = 0

        self._sink = QVideoSink(self)
        self._player = QMediaPlayer(self)
        self._player.setVideoSink(self._sink)
        self._player.setAudioOutput(None)        # never make a noise for a thumbnail
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._player.durationChanged.connect(self._on_duration)
        self._player.errorOccurred.connect(
            lambda *_: QTimer.singleShot(0, self._give_up))

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(TIMEOUT_MS)
        self._timeout.timeout.connect(self._give_up)

        #: Backstop for a file whose duration never arrives: publish the frame
        #: on its own rather than wait for something that is not coming.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(400)
        self._settle.timeout.connect(self._publish)

    # -- asking -------------------------------------------------------------
    def cached(self, path: str | Path) -> tuple[QImage, VideoInfo] | None:
        return self._cache.get(str(path))

    def request(self, path: str | Path) -> None:
        """Ask for a frame. ``ready`` fires now if it is already known."""
        key = str(path)
        found = self._cache.get(key)
        if found is not None:
            self.ready.emit(key, found[0], found[1])
            return
        if key == self._current or key in self._waiting:
            return
        self._waiting.append(key)
        self._start_next()

    # -- doing --------------------------------------------------------------
    def _start_next(self) -> None:
        if self._current is not None or not self._waiting:
            return
        self._current = self._waiting.popleft()
        self._frame = None
        self._duration_ms = 0
        if not Path(self._current).is_file():
            self._give_up()
            return
        self._timeout.start()
        self._player.setSource(QUrl.fromLocalFile(str(Path(self._current).resolve())))
        # Playing is what makes the decoder produce a frame; it is stopped the
        # moment one arrives, so nothing is ever really played.
        self._player.play()

    def _on_frame(self, frame) -> None:
        if self._current is None or self._frame is not None or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        self._frame = image.copy()               # Qt reuses the frame buffer
        if self._duration_ms:
            self._later()
        else:
            self._settle.start()

    def _on_duration(self, milliseconds: int) -> None:
        if self._current is None or milliseconds <= 0:
            return
        self._duration_ms = milliseconds
        if self._frame is not None:
            self._later()

    def _later(self) -> None:
        """Publish once this callback has unwound.

        Stopping the player and pointing it at the next file from inside its
        own videoFrameChanged handler re-enters the decoder and crashes it -
        found the hard way, as a segmentation fault part-way through a batch.
        """
        QTimer.singleShot(0, self._publish)

    def _publish(self) -> None:
        """Hand over the frame once its length is known, or known not to be."""
        if self._current is None or self._frame is None:
            return
        path, image = self._current, self._frame
        info = VideoInfo(image.width(), image.height(), self._duration_ms / 1000.0)
        self._current, self._frame = None, None
        self._finish()

        self._cache[path] = (image, info)
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)

        self.ready.emit(path, image, info)
        self._start_next()

    def _give_up(self) -> None:
        """A file we cannot read is not an error worth showing anyone."""
        if self._frame is not None:          # a frame but never a duration
            self._publish()
            return
        path, self._current = self._current, None
        self._finish()
        if path:
            self.failed.emit(path)
        self._start_next()

    def _finish(self) -> None:
        self._timeout.stop()
        self._settle.stop()
        self._player.stop()
        self._player.setSource(QUrl())
        self._duration_ms = 0


_shared: FrameGrabber | None = None


def grabber() -> FrameGrabber:
    """The one grabber, shared so a file is never decoded twice."""
    global _shared
    if _shared is None:
        _shared = FrameGrabber()
    return _shared
