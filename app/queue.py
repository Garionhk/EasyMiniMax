"""One queue for the whole program.

Pressing Create used to start work, and pressing it again while something was
running did nothing at all. Now every press adds an item, and items run in turn
- so three ideas can be set up in a minute and left to make themselves.

One at a time, because there is one graphics card. Queuing here rather than
pushing everything into ComfyUI's own queue keeps cancelling exact: a waiting
item is simply removed from a list, where ComfyUI's /interrupt is global and
its queue is shared with anything else connected to the same server.

Copied from EasyAI unchanged apart from what a queue item carries: this app has
one workflow and no aspect-ratio node, so an item holds the graph and the
storyboard request instead of a Workflow and a list of ratio options.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from app.i18n import N, t
from app.jobs import JobResult, JobWorker


class State(Enum):
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (State.DONE, State.FAILED, State.CANCELLED)


#: Shown in the queue list. Marked for translation here and translated where
#: they are displayed, so a language change re-labels existing items.
LABELS = {
    State.WAITING: N("Waiting"),
    State.RUNNING: N("Running"),
    State.DONE: N("Done"),
    State.FAILED: N("Failed"),
    State.CANCELLED: N("Cancelled"),
}

_ids = itertools.count(1)


@dataclass
class QueueItem:
    """One press of Create, with everything it needs to run later.

    The request is built when Create is pressed - a deep copy of the storyboard
    as it was at that moment - so editing the shots for the next render cannot
    disturb one already waiting its turn.
    """
    graph: dict                       # the bundled workflow
    request: object                   # app.jobs.GenerationRequest
    output_dir: Path
    timeout: int = 5400

    id: int = field(default_factory=lambda: next(_ids))
    state: State = State.WAITING
    percent: int = 0
    message: str = ""
    result: JobResult | None = None
    error: str = ""

    @property
    def workflow_name(self) -> str:
        """Which path made it, for the queue list: Turbo or Quality."""
        profile = getattr(self.request, "profile", None)
        return t(getattr(profile, "label", "")) if profile else ""

    @property
    def prompt(self) -> str:
        return (getattr(self.request, "prompt", "") or "").strip()

    def summary(self) -> str:
        """The one-line description shown in the queue."""
        if self.state is State.DONE and self.result:
            bits = []
            if self.result.width:
                bits.append(f"{self.result.width} × {self.result.height}")
            if self.result.frames:
                bits.append(f"{self.result.frames} frames")
            if self.result.elapsed:
                bits.append(t("{n}s", n=f"{self.result.elapsed:.0f}"))
            if bits:
                return " · ".join(bits)
        if self.state is State.FAILED:
            return self.error.splitlines()[0] if self.error else t("Failed")
        if self.state is State.RUNNING:
            return self.message
        first_line = self.prompt.splitlines()[0] if self.prompt else ""
        return first_line[:70]


class QueueManager(QObject):
    """Holds the items and runs them one at a time."""

    #: Something about the list changed - added, removed, or a state moved on.
    changed = Signal()
    #: One item's progress or result moved. Carries the item.
    item_changed = Signal(object)
    #: A file was written. Carries (item, path), so galleries can fill in.
    file_ready = Signal(object, str)
    #: ComfyUI's live preview for the running item.
    preview = Signal(object, bytes)
    #: The last outstanding item finished and nothing is left to run.
    emptied = Signal()

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.items: list[QueueItem] = []
        self._worker: JobWorker | None = None
        self._running: QueueItem | None = None

    # -- what is in it -----------------------------------------------------
    @property
    def running(self) -> QueueItem | None:
        return self._running

    def unfinished(self) -> list[QueueItem]:
        return [i for i in self.items if not i.state.finished]

    def waiting(self) -> list[QueueItem]:
        return [i for i in self.items if i.state is State.WAITING]

    def is_busy(self) -> bool:
        return bool(self.unfinished())

    # -- putting work in ---------------------------------------------------
    def add(self, item: QueueItem) -> QueueItem:
        self.items.append(item)
        self.changed.emit()
        self._start_next()
        return item

    def cancel(self, item: QueueItem) -> None:
        """Stop one item, whether it is waiting its turn or already running."""
        if item.state.finished:
            return
        if item is self._running and self._worker is not None:
            # The existing path: asks ComfyUI to stop, and the worker reports
            # back as cancelled, which moves the queue on.
            self._worker.cancel()
            return
        item.state = State.CANCELLED
        item.message = ""
        self.item_changed.emit(item)
        self.changed.emit()
        self._check_empty()

    def cancel_all(self) -> None:
        for item in list(self.items):
            if not item.state.finished:
                self.cancel(item)

    def clear_finished(self) -> None:
        before = len(self.items)
        self.items = [i for i in self.items if not i.state.finished]
        if len(self.items) != before:
            self.changed.emit()

    # -- running it --------------------------------------------------------
    def _start_next(self) -> None:
        if self._running is not None:
            return
        waiting = self.waiting()
        if not waiting:
            return

        item = waiting[0]
        self._running = item
        item.state = State.RUNNING
        item.percent = 0
        item.message = t("Starting…")
        self.item_changed.emit(item)
        self.changed.emit()

        worker = JobWorker(
            client=self.client,
            graph=item.graph,
            request=item.request,
            output_dir=item.output_dir,
            timeout=item.timeout,
        )
        worker.progress.connect(lambda p, m, i=item: self._on_progress(i, p, m))
        worker.preview.connect(lambda data, i=item: self.preview.emit(i, data))
        worker.file_ready.connect(lambda path, i=item: self.file_ready.emit(i, path))
        worker.finished_ok.connect(lambda result, i=item: self._on_done(i, result))
        worker.failed.connect(lambda message, i=item: self._on_failed(i, message))
        self._worker = worker
        worker.start()

    def _on_progress(self, item: QueueItem, percent: int, message: str) -> None:
        item.percent = percent
        item.message = message
        self.item_changed.emit(item)

    def _on_done(self, item: QueueItem, result: JobResult) -> None:
        item.state = State.DONE
        item.result = result
        item.percent = 100
        item.message = ""
        self._finish(item)

    def _on_failed(self, item: QueueItem, message: str) -> None:
        # A run the user stopped is not a failure, and should not be coloured
        # like one in the list.
        cancelled = message == t(JobWorker.CANCELLED)
        item.state = State.CANCELLED if cancelled else State.FAILED
        item.error = "" if cancelled else message
        item.message = ""
        self._finish(item)

    def _finish(self, item: QueueItem) -> None:
        self._running = None
        self._worker = None
        self.item_changed.emit(item)
        self.changed.emit()
        # One item failing must not stop the rest: the next one starts either
        # way, and the list records what went wrong.
        self._start_next()
        self._check_empty()

    def _check_empty(self) -> None:
        if not self.unfinished():
            self.emptied.emit()

    def stop_everything(self) -> None:
        """Used when the window is closing for good."""
        self.cancel_all()
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(4000)
