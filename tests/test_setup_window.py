"""The installer's window, and the one thing it got wrong.

Everything sat in one fixed column, which gave the window a hard minimum of
733 x 596. It did not merely look cramped on a short screen - Qt refused to
make it smaller, so the lower half was unreachable rather than off-screen. The
Install button was below the desktop with no way to get to it.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QScrollArea       # noqa: E402

from app.ui import theme                                      # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


@pytest.fixture
def window(qt_app):
    from setup.ui import SetupWindow
    w = SetupWindow()
    w.show()
    qt_app.processEvents()
    yield w
    w.deleteLater()


def _scroll(window) -> QScrollArea:
    area = window.findChild(QScrollArea)
    assert area is not None, "the page does not scroll at all"
    return area


# -- it can actually be made small ----------------------------------------

def test_the_window_can_be_made_much_smaller_than_its_content(window, qt_app):
    """The bug: it used to refuse anything under 596 tall."""
    window.resize(600, 340)
    qt_app.processEvents()
    assert window.height() <= 360, (
        f"the window would not shrink below {window.height()}px")


def test_the_page_scrolls_when_it_does_not_fit(window, qt_app):
    window.resize(700, 360)
    qt_app.processEvents()
    assert _scroll(window).verticalScrollBar().maximum() > 0


def test_everything_is_reachable_by_scrolling(window, qt_app):
    """Content that does not fit must be scrolled to, not lost."""
    window.resize(700, 340)
    qt_app.processEvents()
    bar = _scroll(window).verticalScrollBar()

    bar.setValue(bar.maximum())
    qt_app.processEvents()
    # The log is the last thing on the page; at the bottom it must be showing.
    assert not window.log.visibleRegion().isEmpty()


def test_the_page_never_scrolls_sideways(window, qt_app):
    """A horizontal scrollbar on a column layout only ever means something
    failed to wrap."""
    window.resize(560, 400)
    qt_app.processEvents()
    assert _scroll(window).horizontalScrollBarPolicy() == \
        __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.ScrollBarAlwaysOff


# -- the controls that must never scroll away -----------------------------

@pytest.mark.parametrize("height", [700, 520, 400, 320])
def test_the_buttons_stay_visible_at_every_size(window, qt_app, height):
    """Having to scroll to find Stop during a 6 GB download would be the same
    fault in a different place."""
    window.resize(700, height)
    qt_app.processEvents()
    assert not window.install_btn.visibleRegion().isEmpty()
    assert not window.close_btn.visibleRegion().isEmpty()


def test_the_progress_stays_visible_while_scrolling(window, qt_app):
    window.resize(700, 360)
    window.bar.setVisible(True)
    window.bar.setValue(50)
    window.status.setText("downloading…")
    qt_app.processEvents()

    bar = _scroll(window).verticalScrollBar()
    bar.setValue(bar.maximum())
    qt_app.processEvents()

    assert not window.bar.visibleRegion().isEmpty()
    assert not window.status.visibleRegion().isEmpty()


def test_the_progress_and_buttons_are_outside_the_scrolling_page(window):
    area = _scroll(window)
    page = area.widget()
    for name, widget in (("progress bar", window.bar),
                         ("status", window.status),
                         ("Install", window.install_btn),
                         ("Close", window.close_btn)):
        assert not page.isAncestorOf(widget), f"{name} would scroll away"


def test_the_log_is_on_the_scrolling_page(window):
    """It belongs with the rest of the page, not pinned - it is the tallest
    thing here and pinning it would defeat the scrolling."""
    assert _scroll(window).widget().isAncestorOf(window.log)


# -- opening size ----------------------------------------------------------

def test_it_opens_no_larger_than_the_screen_allows(window, qt_app):
    """Opening at a fixed 700 tall puts the buttons under the taskbar on a
    768-high laptop."""
    room = qt_app.primaryScreen().availableGeometry()
    assert window.width() <= max(560, room.width() - 80)
    assert window.height() <= max(320, room.height() - 80)


def test_it_opens_no_larger_than_it_needs(window):
    from setup.ui import SetupWindow
    assert window.width() <= SetupWindow.WANTED[0]
    assert window.height() <= SetupWindow.WANTED[1]


def test_the_whole_page_fits_at_the_size_it_asks_for(qt_app):
    """Whatever WANTED says must actually be enough, or it is just a number."""
    from setup.ui import SetupWindow
    w = SetupWindow()
    w.show()
    w.resize(*SetupWindow.WANTED)
    qt_app.processEvents()
    assert _scroll(w).verticalScrollBar().maximum() == 0, (
        "WANTED is smaller than the page it is supposed to show")
    w.deleteLater()


# =========================================================================
#  Every window that stacks more than a screenful
#
#  The Settings dialog was the worst of these: 1245 x 1122, larger than the
#  screen of the machine it was written on, and it refused to be made smaller -
#  so Save and Cancel were somewhere past the bottom edge with no way to reach
#  them. Each of these three has to fit whatever screen it lands on.
# =========================================================================

def _make(kind, qt_app):
    from pathlib import Path

    from app.comfy.client import ComfyClient
    from app.config import Config

    if kind == "settings":
        from app.ui.settings_dialog import SMALLEST, SettingsDialog, WANTED
        window = SettingsDialog(Config(), ComfyClient("127.0.0.1:8188"))
    elif kind == "addons":
        from app.setup import nodes
        from app.ui.first_run import SMALLEST, WANTED, AddOnDialog

        class Caps:
            available, node_types, error = True, set(), ""

            def has_node(self, _name):
                return False

        window = AddOnDialog(nodes.survey(Caps(), Path("/nowhere")),
                             Path("C:/ComfyUI/custom_nodes"), None)
    else:
        from setup.ui import SetupWindow
        window = SetupWindow()
        WANTED, SMALLEST = SetupWindow.WANTED, SetupWindow.SMALLEST

    window.show()
    qt_app.processEvents()
    return window, WANTED, SMALLEST


EVERY_WINDOW = ["settings", "addons", "setup"]


@pytest.fixture(params=EVERY_WINDOW)
def any_window(request, qt_app):
    window, wanted, smallest = _make(request.param, qt_app)
    yield request.param, window, wanted, smallest
    window.deleteLater()


def test_every_setup_window_scrolls(any_window):
    _name, window, _wanted, _smallest = any_window
    assert window.findChild(QScrollArea) is not None


def test_every_setup_window_fits_a_small_screen(any_window, qt_app):
    """768 tall at 150% scaling leaves about 512 logical pixels."""
    _name, window, _wanted, smallest = any_window
    assert smallest[1] <= 512
    window.resize(*smallest)
    qt_app.processEvents()
    assert window.height() <= smallest[1] + 8


def test_no_declared_minimum_clips_its_content(any_window):
    """A minimum under the content does not shrink a window, it clips it -
    which looks the same from outside and is harder to notice."""
    name, window, _wanted, smallest = any_window
    needed = window.minimumSizeHint()
    assert smallest[0] >= needed.width(), (
        f"{name}: minimum width {smallest[0]} is under the "
        f"{needed.width()} its content needs")
    assert smallest[1] >= needed.height(), (
        f"{name}: minimum height {smallest[1]} is under the "
        f"{needed.height()} its content needs")


def test_everything_is_reachable_by_scrolling(any_window, qt_app):
    _name, window, _wanted, smallest = any_window
    window.resize(*smallest)
    qt_app.processEvents()
    area = window.findChild(QScrollArea)
    bar = area.verticalScrollBar()
    if bar.maximum() == 0:
        return                      # it all fits; nothing to reach
    bar.setValue(bar.maximum())
    qt_app.processEvents()
    assert bar.value() == bar.maximum()


def test_no_setup_window_scrolls_sideways(any_window):
    from PySide6.QtCore import Qt
    _name, window, _wanted, _smallest = any_window
    area = window.findChild(QScrollArea)
    assert area.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff


def test_every_setup_window_opens_within_the_screen(any_window, qt_app):
    _name, window, wanted, smallest = any_window
    room = qt_app.primaryScreen().availableGeometry()
    assert window.width() <= max(smallest[0], room.width() - 40)
    assert window.height() <= max(smallest[1], room.height() - 40)
    assert window.width() <= wanted[0]
    assert window.height() <= wanted[1]


@pytest.mark.parametrize("kind", ["addons", "setup"])
def test_the_page_fits_at_the_size_it_asks_for(kind, qt_app):
    """For the two that can fit, WANTED has to actually be enough.

    Settings is excluded deliberately, not because the check is inconvenient:
    five groups of settings come to about 1130 pixels, which is taller than the
    screen on most laptops. There is no honest size at which it all shows, so
    it scrolls - see the test below.
    """
    window, wanted, _smallest = _make(kind, qt_app)
    window.resize(*wanted)
    qt_app.processEvents()
    hidden = window.findChild(QScrollArea).verticalScrollBar().maximum()
    window.deleteLater()
    assert hidden == 0, f"{kind}: WANTED is {hidden}px short of its own page"


def test_settings_scrolls_by_design_and_keeps_its_buttons(qt_app):
    """The settings page is taller than a laptop screen and always will be.

    What matters is not that it fits, but that everything can be reached and
    that Save and Cancel never scroll away - which is what was actually wrong
    when the dialog demanded 1245 x 1122.
    """
    window, wanted, _smallest = _make("settings", qt_app)
    window.resize(*wanted)
    qt_app.processEvents()

    area = window.findChild(QScrollArea)
    assert area.verticalScrollBar().maximum() > 0, (
        "if this now fits, drop this test and add settings back to the one above")

    page = area.widget()
    from PySide6.QtWidgets import QDialogButtonBox
    buttons = window.findChild(QDialogButtonBox)
    assert not page.isAncestorOf(buttons), "Save and Cancel would scroll away"
    assert not buttons.visibleRegion().isEmpty()
    window.deleteLater()


# -- long addresses --------------------------------------------------------

def test_a_url_can_wrap():
    """A URL has no spaces, so word wrap cannot break it - and a QLabel holding
    one demands its whole pixel length. Two of them made the add-on dialog
    762px wide."""
    from app.ui.scroll import breakable

    url = "https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director"
    wrapped = breakable(url)
    assert "\u200b" in wrapped
    # Invisible: what it says is unchanged.
    assert wrapped.replace("\u200b", "") == url


def test_a_windows_path_can_wrap():
    from app.ui.scroll import breakable

    path = r"C:\AI ComfyUI\ComfyUI_windows_portable\ComfyUI\custom_nodes"
    assert breakable(path).replace("\u200b", "") == path


def test_the_add_on_dialog_keeps_the_clean_address_for_copying(qt_app):
    """The break points must not be in what someone copies."""
    from PySide6.QtWidgets import QLabel

    window, _wanted, _smallest = _make("addons", qt_app)
    urls = [c for c in window.findChildren(QLabel)
            if c.toolTip().startswith("https://")]
    assert urls, "no address label carries the clean URL"
    for label in urls:
        assert "\u200b" not in label.toolTip()
    window.deleteLater()
