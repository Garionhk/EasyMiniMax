"""The wiring between the controls and the storyboard.

These are the mistakes that unit tests on the domain layer cannot catch: a
control that edits a copy nobody reads, a queued render that changes under the
user's feet, a duration change that silently throws the shot list away.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication          # noqa: E402

from app.config import Config                       # noqa: E402
from app.h3.presets import MIN_SHOT_FRAMES          # noqa: E402
from app.ui import theme                            # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    """A window pointed at a throwaway settings file and output folder."""
    import app.config
    from app.ui.main_window import MainWindow

    # SIBLING_SETTINGS is a module constant, so it has to be patched there.
    # Without this the tests quietly read the real EasyAI settings file and
    # would start passing or failing depending on the machine.
    monkeypatch.setattr(app.config, "SIBLING_SETTINGS", tmp_path / "nothing.json")
    cfg = Config(tmp_path / "settings.json")
    cfg.set("workflow_dir", str(
        __import__("pathlib").Path(__file__).resolve().parent.parent / "workflows"))
    cfg.set("output_dir", str(tmp_path / "out"))
    cfg.set("auto_launch", False)

    win = MainWindow(cfg)
    yield win
    win.queue.stop_everything()
    win.deleteLater()


# -- the controls reach the storyboard -------------------------------------

def test_typing_a_prompt_reaches_the_storyboard(window):
    window.prompt.setPlainText("a fox in the snow")
    assert window.storyboard.global_prompt == "a fox in the snow"


def test_choosing_a_shape_reaches_the_storyboard(window):
    window.resolution.set_key("768x1344")
    assert (window.storyboard.width, window.storyboard.height) == (768, 1344)


def test_the_duration_control_writes_a_legal_frame_count(window):
    window.duration.set_frames(120)          # not on the grid
    window._on_duration_changed(window.duration.frames())
    assert window.storyboard.frames % 17 == 5


def test_changing_the_length_rescales_the_shots_rather_than_losing_them(window):
    window.shots.add_shot()          # the first press names one shot
    window.shots.add_shot()          # the second splits it
    before = len(window.shots.shots())
    assert before == 2

    window.duration.set_frames(243)
    window._on_duration_changed(243)

    assert len(window.shots.shots()) == before
    assert sum(s.length for s in window.shots.shots()) == window.storyboard.frames


def test_a_subject_slot_writes_into_the_storyboard(window):
    slot = window.subjects.slots[0]
    slot.description.setText("a woman in a red jacket")
    slot.short_name.setText("the rider")
    assert window.storyboard.subjects[0].description == "a woman in a red jacket"
    assert window.storyboard.subjects[0].short_name == "the rider"


def test_the_summary_line_tracks_what_will_be_made(window):
    window.duration.set_frames(56)
    window._on_duration_changed(56)
    window.resolution.set_key("992x992")          # the Director's native 1:1
    assert "56 frames" in window.summary.text()
    assert "992 × 992" in window.summary.text()


# -- queueing --------------------------------------------------------------

def test_create_refuses_an_empty_prompt(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: shown.append(a[-1]))
    window.create_btn.setEnabled(True)
    window._on_create()
    assert shown, "an empty prompt should be refused, not queued"
    assert not window.queue.items


def test_a_queued_render_is_not_disturbed_by_later_edits(window, monkeypatch):
    """The whole reason the request carries a deep copy.

    Setting up the next render while one is waiting its turn must not reach
    into the one already queued - otherwise a batch left running overnight
    quietly renders the last prompt three times.
    """
    monkeypatch.setattr(window.queue, "_start_next", lambda: None)
    window.create_btn.setEnabled(True)
    window.prompt.setPlainText("the first idea")
    window.shots.add_shot()
    window._on_create()

    assert len(window.queue.items) == 1
    queued = window.queue.items[0].request

    window.prompt.setPlainText("a completely different idea")
    window.shots.clear()
    window.resolution.set_key("768x768")

    assert queued.storyboard.global_prompt == "the first idea"
    assert len(queued.storyboard.shots) == 1
    assert queued.storyboard.width == 1344


def test_the_queued_request_carries_the_chosen_profile(window, monkeypatch):
    monkeypatch.setattr(window.queue, "_start_next", lambda: None)
    window.create_btn.setEnabled(True)
    window.prompt.setPlainText("a fox")
    window.profile.set_profile("quality")
    window._on_create()
    request = window.queue.items[0].request
    assert request.profile.key == "quality"
    assert request.profile.steps == 20


def test_locking_the_seed_pins_it_on_the_request(window, monkeypatch):
    monkeypatch.setattr(window.queue, "_start_next", lambda: None)
    window.create_btn.setEnabled(True)
    window.prompt.setPlainText("a fox")
    window.lock_seed.setChecked(True)
    window.seed_spin.setValue(4242)
    window._on_create()
    assert window.queue.items[0].request.seed == 4242


def test_an_unlocked_seed_is_left_for_the_patcher_to_randomise(window, monkeypatch):
    monkeypatch.setattr(window.queue, "_start_next", lambda: None)
    window.create_btn.setEnabled(True)
    window.prompt.setPlainText("a fox")
    window.lock_seed.setChecked(False)
    window._on_create()
    assert window.queue.items[0].request.seed is None


# -- the shot editor's invariant ------------------------------------------

def test_shots_always_tile_the_clip_however_they_are_edited(window):
    total = window.storyboard.frames
    for _ in range(4):
        window.shots.add_shot()
        assert sum(s.length for s in window.shots.shots()) == total
    while window.shots.shots():
        window.shots.remove_row(window.shots._rows[0])
        if window.shots.shots():
            assert sum(s.length for s in window.shots.shots()) == total


def test_no_shot_is_ever_shorter_than_the_minimum(window):
    window.duration.set_frames(56)
    window._on_duration_changed(56)
    for _ in range(6):
        window.shots.add_shot()
    assert all(s.length >= MIN_SHOT_FRAMES for s in window.shots.shots())


# -- adding and deleting shots --------------------------------------------

def test_the_first_press_adds_one_shot_not_two(window):
    """One press, one shot. It covers the whole clip, which is what an empty
    list already meant - so the button names the shot rather than cutting it."""
    assert window.shots.shots() == []
    window.shots.add_shot()
    shots = window.shots.shots()
    assert len(shots) == 1
    assert shots[0].length == window.storyboard.frames


def test_later_presses_split_the_highlighted_shot(window):
    window.shots.add_shot()
    total = window.storyboard.frames
    for expected in (2, 3, 4):
        window.shots.add_shot()
        assert len(window.shots.shots()) == expected
        assert sum(s.length for s in window.shots.shots()) == total


def test_deleting_a_shot_gives_its_time_to_the_one_before(window):
    window.shots.add_shot()
    window.shots.add_shot()
    window.shots.add_shot()
    shots = window.shots.shots()
    first, second, third = (s.length for s in shots)

    window.shots.delete_shot(1)

    remaining = window.shots.shots()
    assert len(remaining) == 2
    assert remaining[0].length == first + second
    assert remaining[1].length == third


def test_deleting_the_first_shot_gives_its_time_to_the_next(window):
    window.shots.add_shot()
    window.shots.add_shot()
    first, second = (s.length for s in window.shots.shots())

    window.shots.delete_shot(0)

    remaining = window.shots.shots()
    assert len(remaining) == 1
    assert remaining[0].length == first + second


def test_deleting_never_shortens_the_clip(window):
    total = window.storyboard.frames
    for _ in range(4):
        window.shots.add_shot()
    while window.shots.shots():
        window.shots.delete_shot(0)
        if window.shots.shots():
            assert sum(s.length for s in window.shots.shots()) == total
    assert window.storyboard.frames == total


def test_deleting_the_shot_keeps_the_right_prompts(window):
    window.shots.add_shot()
    window.shots.add_shot()
    window.shots.add_shot()
    for row, text in zip(window.shots._rows, ["one", "two", "three"]):
        row.prompt.setPlainText(text)

    window.shots.delete_shot(1)

    assert [s.prompt for s in window.shots.shots()] == ["one", "three"]


def test_deleting_out_of_range_does_nothing(window):
    window.shots.add_shot()
    window.shots.delete_shot(9)
    window.shots.delete_shot(-1)
    assert len(window.shots.shots()) == 1


def test_the_row_cross_and_delete_shot_are_the_same_operation(window):
    window.shots.add_shot()
    window.shots.add_shot()
    total = window.storyboard.frames
    window.shots.remove_row(window.shots._rows[1])
    assert len(window.shots.shots()) == 1
    assert window.shots.shots()[0].length == total


def test_the_timeline_can_ask_for_a_shot_to_be_removed(window):
    """The right-click menu and the Delete key both come through this signal."""
    window.shots.add_shot()
    window.shots.add_shot()
    window.shots.timeline.remove_requested.emit(0)
    assert len(window.shots.shots()) == 1


def test_the_cross_deletes_the_row_it_is_on_even_when_shots_look_alike(window):
    """Shot is a dataclass, so equal-valued shots are indistinguishable by ==.

    Splitting twice leaves two shots with the same empty prompt and the same
    length. Looking a Shot up with list.index() finds the first of them, so
    pressing the cross on the third row used to delete the second.
    """
    window.shots.add_shot()
    window.shots.add_shot()
    window.shots.add_shot()
    assert [s.length for s in window.shots.shots()] == [62, 31, 31]

    # Mark them after the split, so only position tells them apart.
    window.shots._rows[1].prompt.setPlainText("keep me")

    window.shots.remove_row(window.shots._rows[2])

    remaining = window.shots.shots()
    assert [s.prompt for s in remaining] == ["", "keep me"]
    assert [s.length for s in remaining] == [62, 62]


def test_splitting_a_too_short_shot_falls_back_to_the_longest_one(window):
    """The fallback picks by position too, for the same reason."""
    window.duration.set_frames(56)
    window._on_duration_changed(56)
    window.shots.add_shot()
    for _ in range(5):
        window.shots.add_shot()
    shots = window.shots.shots()
    assert sum(s.length for s in shots) == window.storyboard.frames
    assert all(s.length >= MIN_SHOT_FRAMES for s in shots)


# -- saved settings, through the window ------------------------------------

def test_a_recipe_round_trips_through_the_window(window, tmp_path):
    """Set the window up, save, change everything, load: it comes back."""
    from app.h3 import recipe

    window.prompt.setPlainText("a fox in the snow")
    window.resolution.set_key("768x1344")
    window.duration.set_frames(56)
    window._on_duration_changed(56)
    window.shots.add_shot()
    window.shots.add_shot()
    window.shots._rows[0].prompt.setPlainText("wide")
    window.shots._rows[1].prompt.setPlainText("close")
    window.profile.set_profile("quality")
    window.subjects.slots[1].description.setText("a red fox")
    window._refresh_summary()

    path = tmp_path / "clip.json"
    recipe.save(path, recipe.build(window.storyboard, window.profile.profile(),
                                   seed=777))

    # Move everything somewhere else before loading it back.
    window.prompt.setPlainText("something completely different")
    window.resolution.set_key("1344x768")
    window.duration.set_frames(243)
    window._on_duration_changed(243)
    window.shots.clear()
    window.profile.set_profile("turbo")

    window.apply_recipe(recipe.load(path))

    assert window.prompt.toPlainText() == "a fox in the snow"
    assert window.resolution.key() == "768x1344"
    assert window.storyboard.frames == 56
    assert [s.prompt for s in window.shots.shots()] == ["wide", "close"]
    assert sum(s.length for s in window.shots.shots()) == 56
    assert window.profile.key() == "quality"
    assert window.subjects.slots[1].description.text() == "a red fox"
    assert window.lock_seed.isChecked() and window.seed_spin.value() == 777


def test_loading_puts_the_length_in_before_the_shots(window, tmp_path):
    """Otherwise setting the length rescales the shots just restored."""
    from app.h3 import recipe
    from app.h3.timeline import Shot as S, Storyboard as SB

    board = SB(global_prompt="a fox", shots=[S("a", 20), S("b", 36)], frames=56)
    path = tmp_path / "clip.json"
    recipe.save(path, recipe.build(board, window.profile.profile()))

    window.duration.set_frames(243)          # somewhere else entirely
    window._on_duration_changed(243)
    window.apply_recipe(recipe.load(path))

    assert window.storyboard.frames == 56
    assert [s.length for s in window.shots.shots()] == [20, 36]


def test_loading_reports_a_reference_picture_that_has_moved(window, tmp_path):
    from app.h3 import recipe
    from app.h3.timeline import Storyboard as SB, Subject as Su

    board = SB(global_prompt="a fox", frames=56)
    board.subjects[0] = Su(image=tmp_path / "gone.png", description="a red fox")
    path = tmp_path / "clip.json"
    recipe.save(path, recipe.build(board, window.profile.profile()))

    window.apply_recipe(recipe.load(path))

    assert window.storyboard.subjects[0].image is None
    assert window.subjects.slots[0].description.text() == "a red fox"
    # isVisible() is False for a window that was never shown, so check what
    # the panel was actually told to say.
    assert "no longer" in window.notes.text()


def test_the_request_asks_for_a_last_frame_when_the_node_is_there(window, monkeypatch):
    from app.h3 import graph as h3graph

    monkeypatch.setattr(window.queue, "_start_next", lambda: None)
    monkeypatch.setattr(window.caps, "node_types", {h3graph.LASTFRAME})
    window.create_btn.setEnabled(True)
    window.prompt.setPlainText("a fox")
    window._on_create()
    assert window.queue.items[0].request.can_save_last_frame is True


def test_the_request_does_not_ask_when_the_node_is_absent(window, monkeypatch):
    monkeypatch.setattr(window.queue, "_start_next", lambda: None)
    monkeypatch.setattr(window.caps, "node_types", set())
    window.create_btn.setEnabled(True)
    window.prompt.setPlainText("a fox")
    window._on_create()
    assert window.queue.items[0].request.can_save_last_frame is False


def test_every_length_the_slider_can_make_can_be_loaded_back(qt_app):
    """frames -> slider -> frames has to be exact.

    Deriving the slider position arithmetically overshoots: 124 frames is
    5.17 s, the nearest stop is 5.2 s, and that snaps up to the next grid value
    at 141. Loading a saved length used to make the clip longer than its file.
    """
    from app.h3 import presets
    from app.ui.controls import DurationPicker

    picker = DurationPicker(5.0)
    reachable = set()
    for tenth in range(picker.slider.minimum(), picker.slider.maximum() + 1):
        picker.slider.setValue(tenth)
        reachable.add(picker.frames())

    for frames in sorted(reachable):
        picker.set_frames(frames)
        assert picker.frames() == frames, f"{frames} came back as {picker.frames()}"


def test_a_length_outside_the_range_is_clamped_not_crashed(qt_app):
    from app.ui.controls import DurationPicker
    picker = DurationPicker(5.0)
    picker.set_frames(99999)
    assert picker.frames() == presets_max(picker)
    picker.set_frames(1)
    assert picker.frames() > 0


def presets_max(picker):
    from app.h3 import presets
    return presets.frames_for(picker.slider.maximum() / picker._STEPS)


@pytest.mark.parametrize("frames", [39, 56, 107, 124, 243, 345])
def test_loading_a_length_gives_back_that_length(window, tmp_path, frames):
    from app.h3 import recipe
    from app.h3.timeline import Storyboard as SB

    path = tmp_path / "clip.json"
    recipe.save(path, recipe.build(SB(global_prompt="a fox", frames=frames),
                                   window.profile.profile()))
    window.apply_recipe(recipe.load(path))
    assert window.storyboard.frames == frames
    assert window.duration.frames() == frames


# -- the reference voice ---------------------------------------------------

def test_the_voice_panel_writes_into_the_storyboard(window, tmp_path):
    clip = tmp_path / "v.wav"
    clip.write_bytes(b"pretend audio")
    window.voice.drop.set_path(str(clip))
    window.voice.description.setText("a low calm voice")
    window.voice.subject.setCurrentIndex(2)          # Person 2

    assert window.storyboard.voice.audio == clip
    assert window.storyboard.voice.description == "a low calm voice"
    assert window.storyboard.voice.subject == 2
    assert window.storyboard.uses_references is True


def test_the_voice_fields_are_dead_until_a_clip_is_attached(window):
    assert not window.voice.description.isEnabled()
    assert not window.voice.subject.isEnabled()


def test_binding_a_voice_to_an_empty_person_is_flagged(window, tmp_path):
    """It works, but it points at nothing - worth one line rather than silence."""
    clip = tmp_path / "v.wav"
    clip.write_bytes(b"x")
    window.voice.drop.set_path(str(clip))
    window.voice.subject.setCurrentIndex(1)          # Person 1, which has no picture
    assert "no picture" in window.voice.note.text()


def test_the_request_carries_the_uploaded_voice_field(window, monkeypatch, tmp_path):
    clip = tmp_path / "v.wav"
    clip.write_bytes(b"x")
    monkeypatch.setattr(window.queue, "_start_next", lambda: None)
    window.create_btn.setEnabled(True)
    window.prompt.setPlainText("two people talking")
    window.voice.drop.set_path(str(clip))
    window._on_create()

    request = window.queue.items[0].request
    assert request.storyboard.voice.audio == clip
    assert request.uploaded_voice == ""       # filled in by the worker


def test_a_recipe_restores_the_voice(window, tmp_path):
    from app.h3 import recipe
    from app.h3.timeline import Storyboard as SB, Voice

    clip = tmp_path / "v.wav"
    clip.write_bytes(b"x")
    board = SB(global_prompt="talking", frames=56)
    board.voice = Voice(audio=clip, description="a low calm voice", subject=3)
    path = tmp_path / "clip.json"
    recipe.save(path, recipe.build(board, window.profile.profile()))

    window.apply_recipe(recipe.load(path))

    assert window.storyboard.voice.audio == clip
    assert window.voice.description.text() == "a low calm voice"
    assert window.voice.subject.currentData() == 3


# -- freeing the graphics card --------------------------------------------

def test_no_free_step_is_asked_for_when_the_helper_is_off(window):
    """The default. A machine with no Ollama must pay nothing for this."""
    assert window.cfg.get("llm_enabled") is False
    assert window._free_llm_request() is None


def test_the_free_step_is_asked_for_when_the_helper_is_on(window):
    window.cfg.set("llm_enabled", True)
    window.cfg.set("llm_url", "http://127.0.0.1:11434")
    window.cfg.set("llm_free_wait", 12)
    request = window._free_llm_request()
    assert request is not None
    assert request.url == "http://127.0.0.1:11434"
    assert request.wait == 12


def test_the_free_step_can_be_turned_off_on_its_own(window):
    window.cfg.set("llm_enabled", True)
    window.cfg.set("llm_free_before_render", False)
    assert window._free_llm_request() is None


def test_the_prompt_helper_stays_hidden_without_a_model(window):
    """Enabled but unconfigured must not offer a button that cannot work."""
    window.cfg.set("llm_enabled", True)
    window.cfg.set("llm_model", "")
    assert window._llm_ready() is False


# -- finding the prompt helper on its own ---------------------------------

def test_the_helper_is_looked_for_once_and_only_once(window, monkeypatch):
    """The bug this exists for: a machine with Ollama running and a vision
    model pulled still showed no buttons, because the switch defaults to off
    and nothing ever looked."""
    import app.ui.main_window as mw

    calls = []
    monkeypatch.setattr(mw, "detect_llm",
                        lambda client: calls.append(1) or "qwen2.5vl:7b")

    assert window.cfg.get("llm_checked") is False
    window._detect_llm_once()

    assert window.cfg.get("llm_enabled") is True
    assert window.cfg.get("llm_model") == "qwen2.5vl:7b"
    assert window.cfg.get("llm_checked") is True

    # Second launch: never probes again.
    window._detect_llm_once()
    assert len(calls) == 1


def test_finding_nothing_still_records_that_we_looked(window, monkeypatch):
    """So a machine with no Ollama pays one refused connection, ever."""
    import app.ui.main_window as mw

    calls = []
    monkeypatch.setattr(mw, "detect_llm", lambda client: calls.append(1) or "")

    window._detect_llm_once()
    assert window.cfg.get("llm_checked") is True
    assert window.cfg.get("llm_enabled") is False

    window._detect_llm_once()
    assert len(calls) == 1


def test_a_deliberate_choice_is_never_overridden(window, monkeypatch):
    """Somebody who turned it off must not have it turned back on."""
    import app.ui.main_window as mw

    window.cfg.set("llm_checked", True)
    window.cfg.set("llm_enabled", False)
    monkeypatch.setattr(mw, "detect_llm",
                        lambda client: pytest.fail("it probed again"))

    window._detect_llm_once()
    assert window.cfg.get("llm_enabled") is False


# -- fitting the screen ----------------------------------------------------

def _splitter(window):
    from PySide6.QtWidgets import QSplitter
    found = window.findChild(QSplitter)
    assert found is not None
    return found


def test_the_window_fits_a_laptop_screen(window, qt_app):
    """It used to refuse anything under 1145 x 528, so on a 1366 x 768 laptop
    it opened with its right-hand column past the edge of the screen."""
    window.resize(1280, 620)
    qt_app.processEvents()
    assert (window.width(), window.height()) == (1280, 620)


def test_the_declared_minimum_does_not_clip_the_content(window):
    """A minimum smaller than the content does not make the window smaller,
    it makes it clipped - so the number has to be measured, not chosen."""
    from app.ui.main_window import MIN_HEIGHT, MIN_WIDTH

    needed = window.minimumSizeHint()
    assert MIN_WIDTH >= needed.width(), (
        f"MIN_WIDTH {MIN_WIDTH} is under the {needed.width()} the content needs")
    assert MIN_HEIGHT >= needed.height()


def test_every_column_can_scroll(window, qt_app):
    """Vertical space must never be a hard floor under the whole window."""
    from PySide6.QtWidgets import QScrollArea

    window.resize(1000, 440)
    qt_app.processEvents()
    areas = window.findChildren(QScrollArea)
    scrollable = [a for a in areas if a.verticalScrollBar().maximum() > 0]
    assert scrollable, "nothing scrolls, so the content below the fold is lost"


def test_a_column_can_be_dragged_shut(window, qt_app):
    """The honest way to use a narrow screen: hide a column rather than squeeze
    all three until none of them works."""
    splitter = _splitter(window)
    assert splitter.childrenCollapsible() is True

    splitter.setSizes([0, 600, 400])
    qt_app.processEvents()
    assert splitter.sizes()[0] == 0
    # And it comes back.
    splitter.setSizes([400, 400, 400])
    qt_app.processEvents()
    assert splitter.sizes()[0] > 0


def test_the_create_button_is_reachable_at_the_smallest_size(window, qt_app):
    from app.ui.main_window import MIN_HEIGHT, MIN_WIDTH

    # show() first: a window that was never mapped has no visible region at
    # all, so this would pass or fail for the wrong reason.
    window.show()
    window.resize(MIN_WIDTH, MIN_HEIGHT)
    qt_app.processEvents()
    assert not window.create_btn.visibleRegion().isEmpty()


def test_it_opens_no_larger_than_the_screen_allows(window, qt_app):
    from app.ui.main_window import MIN_HEIGHT, MIN_WIDTH

    room = qt_app.primaryScreen().availableGeometry()
    assert window.width() <= max(MIN_WIDTH, room.width() - 60)
    assert window.height() <= max(MIN_HEIGHT, room.height() - 60)


def test_it_opens_no_larger_than_it_needs(window):
    from app.ui.main_window import MainWindow

    assert window.width() <= MainWindow.WANTED[0]
    assert window.height() <= MainWindow.WANTED[1]
