"""The choosers this app needs, which EasyAI's widget set does not have.

MiniMax H3 does not take a width, a height and a step count the way an image
model does. It takes a canvas off a short list, a frame count off a grid, and a
sampling setup that only makes sense as a whole. So these are pickers over fixed
options rather than spin boxes over ranges - which is also the friendlier
shape: there is no wrong value to type.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QSlider, QVBoxLayout, QWidget,
)

from app.h3 import presets
from app.h3.presets import PROFILES, QUALITY, RESOLUTIONS, TURBO, Profile
from app.i18n import t
from app.ui import theme
from app.ui.widgets import FlowLayout


class _Chip(QPushButton):
    """A small pill that stays lit when chosen."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("Chip")
        self.setStyleSheet(f"""
            QPushButton#Chip {{
                background:{theme.SURFACE_3}; color:{theme.TEXT};
                border:1px solid {theme.LINE}; border-radius:8px;
                padding:7px 14px; font-size:12px;
            }}
            QPushButton#Chip:hover {{ border-color:{theme.ACCENT_2}; }}
            QPushButton#Chip:checked {{
                background:{theme.ACCENT}; color:{theme.ACCENT_INK};
                border-color:{theme.ACCENT}; font-weight:600;
            }}
        """)


class ProfileSwitch(QWidget):
    """Turbo or Quality, and an honest account of what that changes.

    The four values behind this switch move together - LoRA strength, step
    count, the Spectrum accelerator and the attention backend - and setting any
    one of them alone produces a worse result than either end. That is why it is
    one control.

    It is still not a black box: the "What this changes" line lists the four
    values, because a beginner who later reads a forum post about step counts
    should be able to find that number here rather than concluding the program
    is hiding things from them.
    """

    changed = Signal(str)     # profile key

    def __init__(self, current: str = "turbo", parent=None):
        super().__init__(parent)
        self._key = current if current in PROFILES else "turbo"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._buttons: dict[str, QPushButton] = {}
        for profile in (TURBO, QUALITY):
            button = QPushButton(t(profile.label))
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(38)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setStyleSheet(f"""
                QPushButton {{
                    background:{theme.SURFACE_3}; color:{theme.TEXT};
                    border:1px solid {theme.LINE}; border-radius:9px;
                    font-size:13px; padding:0 10px;
                }}
                QPushButton:hover {{ border-color:{theme.ACCENT_2}; }}
                QPushButton:checked {{
                    background:{theme.ACCENT}; color:{theme.ACCENT_INK};
                    border-color:{theme.ACCENT}; font-weight:700;
                }}
            """)
            button.clicked.connect(lambda _=False, k=profile.key: self.set_profile(k))
            row.addWidget(button)
            self._buttons[profile.key] = button
        root.addLayout(row)

        self.blurb = QLabel()
        self.blurb.setObjectName("Hint")
        self.blurb.setWordWrap(True)
        root.addWidget(self.blurb)

        self.detail_btn = QPushButton(t("What this changes"))
        self.detail_btn.setCheckable(True)
        self.detail_btn.setCursor(Qt.PointingHandCursor)
        self.detail_btn.setFlat(True)
        self.detail_btn.setStyleSheet(
            f"QPushButton {{ border:none; color:{theme.DIM}; font-size:11px;"
            f" text-align:left; padding:0; }}"
            f"QPushButton:hover {{ color:{theme.ACCENT_2}; }}")
        self.detail_btn.toggled.connect(self._on_toggle_detail)
        root.addWidget(self.detail_btn, 0, Qt.AlignLeft)

        self.detail = QLabel()
        self.detail.setVisible(False)
        self.detail.setStyleSheet(
            f"color:{theme.MUTED}; font-family:'{theme.MONO_FONT}';"
            f" font-size:11px; background:{theme.INPUT_BG};"
            f" border:1px solid {theme.LINE}; border-radius:7px; padding:8px;")
        root.addWidget(self.detail)

        self._refresh()

    # -- state -------------------------------------------------------------
    def profile(self) -> Profile:
        return presets.profile(self._key)

    def key(self) -> str:
        return self._key

    def set_profile(self, key: str) -> None:
        key = key if key in PROFILES else "turbo"
        changed = key != self._key
        self._key = key
        self._refresh()
        if changed:
            self.changed.emit(key)

    def _on_toggle_detail(self, shown: bool) -> None:
        self.detail.setVisible(shown)
        self.detail_btn.setText(
            t("Hide what this changes") if shown else t("What this changes"))

    def _refresh(self) -> None:
        profile = self.profile()
        for key, button in self._buttons.items():
            button.setChecked(key == self._key)
        self.blurb.setText(t(profile.blurb))
        self.detail.setText("\n".join(
            f"{t(name):<22}{value}" for name, value in profile.describe()))


class ResolutionPicker(QWidget):
    """Shape and size, as the Director's own preset menu.

    A list rather than a row of chips, because there are twenty-seven of them.
    The pixel sizes are the editor's table verbatim - a 768 px short edge with
    the long edge capped at 1344, or 480 for the fast band - so a shape chosen
    here is the same shape by the same name anywhere else this model is used.

    The three bands are separated by headings that cannot be selected, and the
    two that cost something say so underneath.
    """

    changed = Signal(str)     # resolution key

    def __init__(self, current: str = "", parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self.combo = QComboBox()
        self.combo.setMinimumWidth(150)
        self._build_items()
        self.combo.currentIndexChanged.connect(self._on_pick)
        root.addWidget(self.combo)

        self.caption = QLabel()
        self.caption.setStyleSheet(
            f"color:{theme.DIM}; font-family:'{theme.MONO_FONT}'; font-size:11px;")
        root.addWidget(self.caption)

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setVisible(False)
        self.warning.setStyleSheet(f"color:{theme.WARN}; font-size:11px;")
        root.addWidget(self.warning)

        self.set_key(current or presets.DEFAULT_RESOLUTION)

    def _build_items(self) -> None:
        """One heading per band, then its shapes."""
        model = self.combo.model()
        for group in presets.GROUP_ORDER:
            members = [r for r in RESOLUTIONS if r.group == group]
            if not members:
                continue
            self.combo.addItem(t(group))
            # A heading is a label, not a choice. Qt has no group header, so
            # the row is added and then made unselectable.
            item = model.item(self.combo.count() - 1)
            item.setEnabled(False)
            item.setSelectable(False)
            for res in members:
                self.combo.addItem("    " + t(res.label), res.key)
                self.combo.setItemData(
                    self.combo.count() - 1,
                    t("{pixels} pixels", pixels=res.pixels), Qt.ToolTipRole)

    # -- state -------------------------------------------------------------
    def key(self) -> str:
        return self.combo.currentData() or presets.DEFAULT_RESOLUTION

    def resolution(self):
        return presets.resolution(self.key())

    def set_key(self, key: str) -> None:
        index = self.combo.findData(key)
        if index < 0:
            index = self.combo.findData(presets.DEFAULT_RESOLUTION)
        if index >= 0 and index != self.combo.currentIndex():
            self.combo.setCurrentIndex(index)
        else:
            self._refresh()

    def _on_pick(self, _index: int) -> None:
        # A heading cannot be reached with the mouse, but the keyboard walks
        # straight through one, so a landing on a heading steps past it.
        if self.combo.currentData() is None:
            step = 1 if self.combo.currentIndex() + 1 < self.combo.count() else -1
            self.combo.setCurrentIndex(self.combo.currentIndex() + step)
            return
        self._refresh()
        self.changed.emit(self.key())

    def _refresh(self) -> None:
        res = self.resolution()
        self.caption.setText(res.pixels)
        message = presets.resolution_warning(res)
        self.warning.setText(t(message) if message else "")
        self.warning.setVisible(bool(message))


class DurationPicker(QWidget):
    """How long the clip is, in seconds - showing the frames it will really be.

    H3 only renders frame counts on a 17k+5 grid, so asking for 5 seconds gets
    124 frames, which is 5.17 seconds. The snapped figure is shown rather than
    the request: a program that says 5 and produces 5.17 looks broken, where one
    that says 5.17 up front simply looks precise.
    """

    changed = Signal(int)     # frames

    #: The slider works in tenths, because Qt sliders are integers only.
    _STEPS = 10

    def __init__(self, seconds: float = 5.0, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(int(presets.MIN_SECONDS * self._STEPS))
        self.slider.setMaximum(int(presets.MAX_SECONDS * self._STEPS))
        self.slider.setSingleStep(self._STEPS // 2)
        self.slider.setPageStep(self._STEPS)
        self.slider.setValue(int(round(seconds * self._STEPS)))
        self.slider.valueChanged.connect(self._on_slide)
        root.addWidget(self.slider)

        self.caption = QLabel()
        self.caption.setStyleSheet(
            f"color:{theme.TEXT}; font-family:'{theme.MONO_FONT}'; font-size:12px;")
        root.addWidget(self.caption)

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setVisible(False)
        self.warning.setStyleSheet(f"color:{theme.WARN}; font-size:11px;")
        root.addWidget(self.warning)

        self._refresh()

    def frames(self) -> int:
        return presets.frames_for(self.slider.value() / self._STEPS)

    def set_frames(self, frames: int) -> None:
        """Move the slider to the position that produces exactly this length.

        Not by arithmetic. Going frames -> seconds -> slider overshoots, because
        the grid is coarser than the slider: 124 frames is 5.17 s, the nearest
        slider stop is 5.2 s, and 5.2 s snaps up to the *next* grid value at 141
        frames. Loading a saved length would quietly make the clip longer than
        the file it came from.
        """
        frames = presets.align_frame_count(frames)
        positions = range(self.slider.minimum(), self.slider.maximum() + 1)
        for value in positions:
            if presets.frames_for(value / self._STEPS) == frames:
                self.slider.setValue(value)
                return
        # Out of this version's range - a hand-edited file, or an older one.
        # Take the closest the slider can offer rather than refusing.
        self.slider.setValue(min(
            positions,
            key=lambda v: abs(presets.frames_for(v / self._STEPS) - frames)))

    def _on_slide(self, _value: int) -> None:
        self._refresh()
        self.changed.emit(self.frames())

    def _refresh(self) -> None:
        frames = self.frames()
        self.caption.setText(presets.format_length(frames))
        message = presets.length_warning(frames)
        self.warning.setText(t(message) if message else "")
        self.warning.setVisible(bool(message))


class StatusStrip(QFrame):
    """The line that says whether anything is stopping a render.

    Green when ready, amber when something is off, with a button when there is
    something the program can actually do about it.
    """

    action = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setStyleSheet(
            f"QFrame#Panel {{ background:{theme.SURFACE_2};"
            f" border:1px solid {theme.LINE}; border-radius:9px; }}")

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)

        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color:{theme.DIM}; font-size:14px;")
        row.addWidget(self.dot)

        self.text = QLabel(t("Checking the AI engine…"))
        self.text.setWordWrap(True)
        row.addWidget(self.text, 1)

        self.button = QPushButton()
        self.button.setVisible(False)
        self.button.clicked.connect(self.action.emit)
        row.addWidget(self.button)

    def show_state(self, level: str, message: str, action: str = "") -> None:
        colour = {"ok": theme.OK, "warn": theme.WARN,
                  "bad": theme.BAD}.get(level, theme.DIM)
        self.dot.setStyleSheet(f"color:{colour}; font-size:14px;")
        self.text.setText(message)
        self.button.setText(action)
        self.button.setVisible(bool(action))


def labelled(title: str, widget: QWidget, hint: str = "") -> QWidget:
    """A control under a small caption, the layout used all down the panel."""
    box = QWidget()
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(5)

    label = QLabel(title)
    label.setObjectName("Heading")
    column.addWidget(label)
    if hint:
        caption = QLabel(hint)
        caption.setObjectName("Hint")
        caption.setWordWrap(True)
        column.addWidget(caption)
    column.addWidget(widget)
    return box
