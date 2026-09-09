"""Lining up more than one render, and getting one back out again.

Pressing Create used to be a single-shot affair: while a video was being made
the button was hidden, so a second idea had to wait for a human to be sitting
there when the first one finished. The queue existed underneath the whole time
- nothing could reach it.

What these guard is the behaviour that makes an unattended batch trustworthy:
one item at a time on the one graphics card, the next one starting whatever
happened to the last, and nothing - not a failure, not a message box - quietly
stopping the rest while nobody is watching.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal                     # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox        # noqa: E402

import app.queue as queue_module                               # noqa: E402
from app.config import Config                                  # noqa: E402
from app.i18n import t                                         # noqa: E402
from app.jobs import JobResult, JobWorker                      # noqa: E402
from app.queue import QueueItem, QueueManager, State           # noqa: E402
from app.ui import theme                                       # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


class FakeWorker(QObject):
    """A JobWorker that never touches ComfyUI, driven by the test instead.

    Same signals and the same three methods the manager uses, so the manager
    runs its real code path - what is replaced is only the part that would
    need a graphics card.
    """

    progress = Signal(int, str)
    preview = Signal(bytes)
    file_ready = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    #: The manager compares a failure message against this to tell a run the
    #: user stopped from one that broke, so it has to keep the same value.
    CANCELLED = JobWorker.CANCELLED

    made: list["FakeWorker"] = []

    def __init__(self, **kwargs):
        super().__init__()
        self.kwargs = kwargs
        self.started = False
        FakeWorker.made.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.failed.emit(t(self.CANCELLED))

    def isRunning(self) -> bool:              # noqa: N802  (Qt spelling)
        return self.started

    def wait(self, msecs: int = 0) -> bool:
        return True


@pytest.fixture
def fake_workers(monkeypatch):
    FakeWorker.made = []
    monkeypatch.setattr(queue_module, "JobWorker", FakeWorker)
    return FakeWorker.made


@pytest.fixture
def manager(qt_app, fake_workers, tmp_path):
    return QueueManager(client=None)


def add(manager, tmp_path, prompt: str = "a fox") -> QueueItem:
    """One item, with just enough of a request to be described in the list."""
    from app.h3.presets import TURBO
    from app.h3.timeline import Storyboard
    from app.jobs import GenerationRequest

    board = Storyboard()
    board.global_prompt = prompt
    return manager.add(QueueItem(
        graph={}, request=GenerationRequest(storyboard=board, profile=TURBO),
        output_dir=tmp_path))


# -- one at a time ---------------------------------------------------------

def test_the_first_one_starts_and_the_rest_wait(manager, tmp_path, fake_workers):
    """There is one graphics card, so two at once would only be slower."""
    first = add(manager, tmp_path, "one")
    second = add(manager, tmp_path, "two")
    third = add(manager, tmp_path, "three")

    assert first.state is State.RUNNING
    assert second.state is third.state is State.WAITING
    assert len(fake_workers) == 1, "more than one render was started at once"


def test_the_next_one_starts_when_the_first_finishes(manager, tmp_path, fake_workers):
    first = add(manager, tmp_path, "one")
    second = add(manager, tmp_path, "two")

    fake_workers[0].finished_ok.emit(JobResult(elapsed=1.0))

    assert first.state is State.DONE
    assert second.state is State.RUNNING
    assert len(fake_workers) == 2


def test_one_failure_does_not_stop_the_rest(manager, tmp_path, fake_workers):
    """The whole point of leaving a batch running is that it keeps going."""
    first = add(manager, tmp_path, "one")
    second = add(manager, tmp_path, "two")

    fake_workers[0].failed.emit("out of memory")

    assert first.state is State.FAILED
    assert first.error == "out of memory"
    assert second.state is State.RUNNING


def test_a_run_the_user_stopped_is_not_recorded_as_a_failure(
        manager, tmp_path, fake_workers):
    item = add(manager, tmp_path)
    manager.cancel(item)
    assert item.state is State.CANCELLED
    assert item.error == ""


# -- taking things back out ------------------------------------------------

def test_a_waiting_item_can_be_removed_without_disturbing_the_running_one(
        manager, tmp_path, fake_workers):
    first = add(manager, tmp_path, "one")
    second = add(manager, tmp_path, "two")

    manager.cancel(second)

    assert second.state is State.CANCELLED
    assert first.state is State.RUNNING, "removing a waiting item stopped the render"
    assert len(fake_workers) == 1


def test_stopping_the_running_one_lets_the_next_start(manager, tmp_path, fake_workers):
    first = add(manager, tmp_path, "one")
    second = add(manager, tmp_path, "two")

    manager.cancel(first)

    assert first.state is State.CANCELLED
    assert second.state is State.RUNNING


def test_clear_finished_keeps_what_is_still_to_come(manager, tmp_path, fake_workers):
    first = add(manager, tmp_path, "one")
    second = add(manager, tmp_path, "two")
    fake_workers[0].finished_ok.emit(JobResult())

    manager.clear_finished()

    assert manager.items == [second]
    assert first not in manager.items


def test_emptied_fires_once_there_is_nothing_left(manager, tmp_path, fake_workers):
    fired = []
    manager.emptied.connect(lambda: fired.append(True))

    add(manager, tmp_path, "one")
    add(manager, tmp_path, "two")
    fake_workers[0].finished_ok.emit(JobResult())
    assert not fired, "it announced an empty queue with one still to run"

    fake_workers[1].finished_ok.emit(JobResult())
    assert fired


# -- the window ------------------------------------------------------------

@pytest.fixture
def window(qt_app, fake_workers, tmp_path, monkeypatch):
    import app.config
    from app.ui.main_window import MainWindow

    monkeypatch.setattr(app.config, "SIBLING_SETTINGS", tmp_path / "nothing.json")
    cfg = Config(tmp_path / "settings.json")
    cfg.set("workflow_dir", str(Path(__file__).resolve().parent.parent / "workflows"))
    cfg.set("output_dir", str(tmp_path / "out"))
    cfg.set("auto_launch", False)

    win = MainWindow(cfg)
    yield win
    win.queue.stop_everything()
    win.deleteLater()


def press_create(window, prompt: str) -> None:
    window.prompt.setPlainText(prompt)
    window._on_create()


def test_pressing_create_twice_queues_two(window):
    press_create(window, "the first idea")
    press_create(window, "the second idea")

    assert len(window.queue.items) == 2
    assert [i.prompt for i in window.queue.items] == \
        ["the first idea", "the second idea"]


def test_create_stays_available_while_something_is_being_made(window):
    """Hiding it was the one thing stopping a second one being lined up."""
    press_create(window, "the first idea")

    assert window.queue.running is not None
    assert not window.create_btn.isHidden(), "Create disappeared while working"
    assert window.create_btn.isEnabled()
    assert not window.cancel_btn.isHidden(), "there was no way to stop it"


def test_the_line_under_create_invites_a_second_press(window):
    """Before there is a count, nobody knows a second press does anything."""
    press_create(window, "the first idea")
    assert window.queue_label.text() == t("Press Create again to line up another.")

    press_create(window, "the second idea")
    assert "1" in window.queue_label.text()


def test_the_queue_strip_shows_one_row_per_item(window):
    assert window.queue_panel.isHidden() or not window.queue.items

    press_create(window, "one")
    press_create(window, "two")

    assert len(window.queue_panel._rows) == 2
    assert not window.queue_panel.isHidden()


def test_a_row_says_which_idea_it_is(window):
    """Four identical grey lines are no more use than no list at all."""
    from app.h3.presets import TURBO

    press_create(window, "a desert chase at golden hour")
    press_create(window, "rain on a neon street")
    second = window.queue_panel._rows[window.queue.items[1].id]

    assert second.detail.full_text() == "rain on a neon street"
    assert second.name.full_text() == t(TURBO.label)


def test_removing_a_row_takes_it_out_of_the_queue(window):
    press_create(window, "one")
    press_create(window, "two")
    second = window.queue.items[1]

    window.queue_panel._rows[second.id].stop.click()

    assert second.state is State.CANCELLED
    assert window.queue.items[0].state is State.RUNNING


def test_a_failure_with_more_waiting_does_not_open_a_message_box(
        window, monkeypatch, fake_workers):
    """A modal box runs its own event loop.

    Left overnight, the first failure would sit there with a dialog open and
    nothing else would ever run. The reason still has to be said - it goes to
    the notes panel and the queue row instead.
    """
    boxes = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: boxes.append(a))

    press_create(window, "one")
    press_create(window, "two")
    fake_workers[0].failed.emit("out of memory")

    assert not boxes, "a message box stopped the queue"
    assert "out of memory" in window.notes.text()
    assert window.queue.items[1].state is State.RUNNING


def test_the_last_failure_is_still_said_out_loud(window, monkeypatch, fake_workers):
    """With nothing behind it there is no batch to hold up, so it is a box."""
    boxes = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: boxes.append(a))

    press_create(window, "the only one")
    fake_workers[0].failed.emit("out of memory")

    assert boxes, "the failure was never shown"


def test_removing_a_waiting_item_leaves_the_running_one_on_show(
        window, fake_workers):
    """The bar belongs to whatever is running, not to whatever last changed.

    Taking a waiting item out is a change to that item, and reading the state
    off it took the progress bar and Stop away from the render still going -
    which reads as the render having quietly stopped.
    """
    press_create(window, "one")
    press_create(window, "two")
    fake_workers[0].progress.emit(42, "working")

    window.queue_panel._rows[window.queue.items[1].id].stop.click()

    assert not window.progress.isHidden(), "the running render lost its bar"
    assert not window.cancel_btn.isHidden(), "the running render lost its Stop"
    assert window.progress.value() == 42


def test_what_is_in_the_queue_does_not_set_the_width_of_the_column(
        window, qt_app):
    """A wide minimum does not widen a column here - it clips it.

    None of the three columns scroll sideways, so whatever they cannot fit is
    cut off rather than reachable. Every label reports the width of its own
    text as a minimum unless it is told not to, so one long prompt, or the
    count line growing from "1 running" to "1 running · 11 waiting", would put
    a floor under the results column that dragging could not get past.
    """
    window.resize(1500, 940)
    window.show()
    press_create(window, "short")
    qt_app.processEvents()
    floor = window.queue_panel.layout().totalMinimumSize().width()

    for _ in range(11):
        press_create(window, "a very long description of a shot " * 12)
    qt_app.processEvents()
    window.queue_panel.layout().invalidate()

    assert window.queue_panel.layout().totalMinimumSize().width() <= floor, (
        "the results column will not shrink now that there is a queue in it")


def test_the_remove_button_actually_draws_its_glyph(window, qt_app):
    """It rendered as an empty square, and an empty square is not a button.

    The stylesheet gives every QPushButton 14px of padding on each side. On a
    26px-wide button that is wider than the button, so Qt drops the label and
    draws the frame alone - no error, no warning, just a control nobody can
    guess the purpose of. Comparing what is drawn with and without the text is
    the only way to notice from a test.
    """
    press_create(window, "one")
    window.show()
    qt_app.processEvents()
    button = window.queue_panel._rows[window.queue.items[0].id].stop

    with_glyph = button.grab().toImage()
    button.setText("")
    blank = button.grab().toImage()

    assert with_glyph != blank, (
        "the ✕ is not being drawn - the button is a blank square")
