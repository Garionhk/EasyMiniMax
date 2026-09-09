"""Making a window fit whatever screen it lands on.

A column of controls has a natural height, and Qt turns that height into a hard
minimum: the window refuses to be made smaller, so on a short screen the lower
half is not merely off-screen, it is unreachable. The Settings dialog was the
worst of these at 1245 x 1122 - larger than the screen of the machine it was
written on, with no way to reach the buttons.

Two functions, used by every window that stacks more than a screenful:

* :func:`vertical_scroll` - the page scrolls, and never sideways.
* :func:`fit_to_screen`   - it opens at what the screen has room for.

What must *not* go inside the scrolling page is anything the user needs while
they wait or before they can leave: the progress bar, and Save / Cancel /
Install. Having to scroll to find out whether a download is still going, or to
reach the button that dismisses the window, is the same fault in a new place.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea, QWidget


def vertical_scroll(page: QWidget, minimum_width: int = 0) -> QScrollArea:
    """Put a widget in a scrolling area that only ever scrolls vertically.

    Horizontal scrolling is off on purpose: these pages are columns, and a
    sideways scrollbar on a column only ever means something has failed to
    wrap. Turning it off makes the content fit the width instead.
    """
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    if minimum_width:
        area.setMinimumWidth(minimum_width)
    area.setWidget(page)
    return area


def fit_to_screen(window, wanted: tuple[int, int], minimum: tuple[int, int],
                  margin: int = 60) -> None:
    """Set the floor, then open at as much of `wanted` as the screen allows.

    `minimum` has to be measured rather than chosen. A minimum smaller than the
    content does not make a window smaller - it makes it clipped, which looks
    like the same bug from the outside and is harder to notice.

    `margin` leaves room for the title bar and taskbar; availableGeometry
    already excludes the taskbar, but not the window's own frame.
    """
    min_width, min_height = minimum
    window.setMinimumSize(min_width, min_height)

    wanted_width, wanted_height = wanted
    screen = window.screen() or QApplication.primaryScreen()
    if screen is None:
        window.resize(wanted_width, wanted_height)
        return

    room = screen.availableGeometry()
    window.resize(
        min(wanted_width, max(min_width, room.width() - margin)),
        min(wanted_height, max(min_height, room.height() - margin)),
    )


#: Where a long address may be broken across lines. Not a hyphen anywhere:
#: a hyphen inserted into a URL or a path reads as part of it.
_BREAK_AFTER = ("/", "\\", "_", "-", ".")


def breakable(text: str) -> str:
    """Let a URL or a file path wrap, without changing what it says.

    Word wrap breaks at spaces, and neither a URL nor a Windows path has any -
    so a QLabel holding one reports a minimum width equal to its whole length
    and drags the window wider than the screen. That is what made the add-on
    dialog demand 762 pixels: two GitHub addresses.

    A zero-width space after each separator gives the layout somewhere to
    break. It is invisible, and it does not change the line as read - but it
    does travel with the text if it is copied, so anything meant to be copied
    (the commands in the install log, for instance) is left alone and only the
    label is treated this way. Callers should keep the clean text as a tooltip.
    """
    out = []
    for character in str(text):
        out.append(character)
        if character in _BREAK_AFTER:
            out.append("\u200b")
    return "".join(out)
