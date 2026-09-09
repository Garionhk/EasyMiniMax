"""The timeline JSON is the whole contract with the Director node.

Every one of these is a bug the app would otherwise ship silently: a video that
renders the wrong length, a reference picture the model never looks at, or a
shot list that stops lining up with the storyboard markers the model is given.
"""
from __future__ import annotations

import json

import pytest

from app.h3 import presets
from app.h3.timeline import (
    REFERENCE_OFF, REFERENCE_ON, SUBJECT_SLOTS, Shot, Storyboard, Subject, Voice,
    build, describe,
)


# -- the frame grid --------------------------------------------------------

@pytest.mark.parametrize("asked, expected", [
    (120, 124),     # the sample workflow's own value
    (124, 124),     # already on the grid: left alone
    (5, 5),
    (6, 22),
    (96, 107),      # the trained minimum snaps up
    (1, 5),         # below the smallest legal count
])
def test_align_frame_count(asked, expected):
    assert presets.align_frame_count(asked) == expected


def test_every_aligned_count_is_one_h3_accepts():
    for seconds in range(1, 15):
        assert presets.frames_for(seconds) % 17 == 5


def test_length_is_reported_snapped_not_as_asked():
    """Asking for 5 s renders 5.17 s, and the interface has to say so."""
    assert presets.format_length(presets.frames_for(5)) == "5.17 s · 124 frames"


# -- shots tile the clip ---------------------------------------------------

def test_shots_tile_the_window_exactly():
    board = Storyboard(shots=[Shot("a", 36), Shot("b", 48), Shot("c", 36)],
                       frames=120)
    segments = board.segments()
    assert sum(s["length"] for s in segments) == board.frames == 124
    # No gaps and no overlaps: each start is the previous start plus its length.
    running = 0
    for segment in segments:
        assert segment["start"] == running
        running += segment["length"]


def test_no_shots_gives_one_segment_spanning_the_clip():
    board = Storyboard(global_prompt="a fox", frames=124)
    segments = board.segments()
    assert len(segments) == 1
    assert segments[0]["start"] == 0
    assert segments[0]["length"] == 124


def test_shots_rescale_when_the_clip_gets_shorter():
    board = Storyboard(shots=[Shot("a", 200), Shot("b", 200)], frames=56)
    board.normalise()
    assert sum(s.length for s in board.shots) == board.frames
    assert all(s.length >= presets.MIN_SHOT_FRAMES for s in board.shots)


def test_shots_that_cannot_possibly_fit_are_dropped():
    """Five shots do not fit in 39 frames at a 12-frame minimum."""
    board = Storyboard(shots=[Shot("", 20) for _ in range(5)], frames=39)
    board.normalise()
    assert len(board.shots) <= board.frames // presets.MIN_SHOT_FRAMES
    assert sum(s.length for s in board.shots) == board.frames


# -- reference mode picks the checkpoint -----------------------------------

def test_reference_mode_is_off_without_pictures():
    data = json.loads(build(Storyboard(global_prompt="a fox")))
    assert data["reference_mode"] == REFERENCE_OFF


def test_reference_mode_turns_on_only_for_an_uploaded_picture():
    board = Storyboard(global_prompt="a fox")
    board.subjects[1] = Subject(image="fox.png", description="a red fox")
    data = json.loads(build(board, {1: "input/fox.png"}))
    assert data["reference_mode"] == REFERENCE_ON
    assert data["subjects"][1]["images"] == [{"name": "input/fox.png"}]


def test_a_description_without_a_picture_does_not_turn_references_on():
    """Otherwise a stray word loads a 21 GB checkpoint nobody asked for."""
    board = Storyboard()
    board.subjects[0] = Subject(description="a red fox")
    data = json.loads(build(board))
    assert data["reference_mode"] == REFERENCE_OFF


def test_a_picture_with_no_uploaded_name_does_not_turn_references_on():
    """The upload failed, so there is nothing on the server to point at."""
    board = Storyboard()
    board.subjects[0] = Subject(image="fox.png", description="a red fox")
    data = json.loads(build(board, uploaded={}))
    assert data["reference_mode"] == REFERENCE_OFF


# -- the subject slots -----------------------------------------------------

def test_all_three_slots_are_always_present_and_in_order():
    """The planner walks them by index, so a missing slot shifts the rest."""
    board = Storyboard()
    board.subjects[2] = Subject(image="c.png", description="the car")
    data = json.loads(build(board, {2: "input/c.png"}))
    assert len(data["subjects"]) == SUBJECT_SLOTS == 3
    assert data["subjects"][0]["images"] == []
    assert data["subjects"][1]["images"] == []
    assert data["subjects"][2]["description"] == "the car"


def test_an_invented_kind_or_retention_is_clamped():
    """The Director clamps these itself; sending them is still a bug."""
    board = Storyboard()
    board.subjects[0] = Subject(image="x.png", kind="spaceship",
                                retention="totally_kept")
    entry = json.loads(build(board, {0: "x.png"}))["subjects"][0]
    assert entry["kind"] == "person"
    assert entry["retention"] == "fully_preserved"


# -- the shape the node expects --------------------------------------------

#: Every key minimax_plan.py reads out of timeline_data. If the Director grows
#: a new one this test is where it should be noticed.
READ_BY_THE_PLANNER = (
    "reference_mode", "segments", "subjects", "global_prompt", "prompt_format",
    "retakeMode", "retake_global_prompt", "overall_soundscape",
    "non_diegetic_music", "prompt_override", "prompt_override_on",
    "task_type_override", "summary", "motionSegments", "audioSegments",
)


def test_every_key_the_planner_reads_is_present():
    data = json.loads(build(Storyboard(global_prompt="a fox")))
    missing = [key for key in READ_BY_THE_PLANNER if key not in data]
    assert not missing, f"timeline_data is missing {missing}"


def test_global_prompt_travels_in_the_json_not_as_an_input():
    """The node's global_prompt input is force_input, so this is the only way."""
    data = json.loads(build(Storyboard(global_prompt="  a fox in the snow  ")))
    assert data["global_prompt"] == "a fox in the snow"


def test_the_prompt_is_never_overridden():
    data = json.loads(build(Storyboard(global_prompt="a fox")))
    assert data["prompt_override_on"] is False
    assert data["prompt_format"] == "minimax"


def test_output_is_a_single_json_string():
    """The node stores this as one escaped string on one input."""
    out = build(Storyboard(global_prompt="a fox"))
    assert isinstance(out, str)
    assert json.loads(out)["global_prompt"] == "a fox"


# -- the mirrored inputs ---------------------------------------------------

def test_mirrored_inputs_match_the_segments():
    board = Storyboard(shots=[Shot("one", 62), Shot("two", 62)], frames=124)
    assert board.local_prompts() == "one | two"
    assert board.segment_lengths() == "62,62"


def test_describe_reports_the_snapped_length():
    board = Storyboard(global_prompt="a fox", frames=120)
    board.subjects[0] = Subject(image="x.png")
    text = describe(board)
    assert "124 frames" in text
    assert "1 reference picture" in text


# -- the resolution list ---------------------------------------------------

def test_every_resolution_is_one_h3_can_render():
    """A 768 short edge snapped to 32, straight out of the Director's table."""
    for r in presets.RESOLUTIONS:
        assert r.width % 32 == 0 and r.height % 32 == 0, r.label
        assert min(r.width, r.height) in (480, 576, 608, 640, 672, 736, 768,
                                          960, 992, 1088), r.label


def test_the_resolution_keys_are_unique():
    keys = [r.key for r in presets.RESOLUTIONS]
    assert len(keys) == len(set(keys))


def test_the_label_and_the_pixels_agree():
    """The label carries its own size, so a typo in the table is a lie."""
    for r in presets.RESOLUTIONS:
        assert f"{r.width}×{r.height}" in r.label.replace(" ", ""), r.label


def test_the_default_is_native_sixteen_by_nine():
    default = presets.resolution(presets.DEFAULT_RESOLUTION)
    assert (default.width, default.height) == (1344, 768)
    assert default.group == presets.NATIVE


def test_an_unknown_size_falls_back_to_the_default():
    assert presets.resolution("4096x4096").key == presets.DEFAULT_RESOLUTION


@pytest.mark.parametrize("key, warns", [
    ("1344x768", False),        # native
    ("864x480", True),          # fast
    ("1920x1088", True),        # past native
])
def test_the_bands_that_cost_something_say_so(key, warns):
    assert bool(presets.resolution_warning(presets.resolution(key))) is warns


# -- the reference voice ---------------------------------------------------

def _with_voice(**kwargs):
    board = Storyboard(global_prompt="two people talking", frames=124)
    board.voice = Voice(**kwargs)
    return board


def test_a_voice_becomes_one_segment_spanning_the_whole_clip():
    """The planner keeps only clips that overlap the render window, and a
    reference voice describes the whole clip rather than a moment in it."""
    board = _with_voice(audio="v.wav", description="a low calm voice")
    data = json.loads(build(board, uploaded_voice="input/v.wav"))
    assert len(data["audioSegments"]) == 1
    segment = data["audioSegments"][0]
    assert segment["start"] == 0
    assert segment["length"] == board.frames == 124
    assert segment["audioFile"] == "input/v.wav"
    assert segment["refDesc"] == "a low calm voice"


def test_a_voice_alone_switches_to_the_reference_model():
    """The planner reads audioSegments only when reference mode is on, so a
    voice with this left OFF is uploaded and then silently ignored."""
    board = _with_voice(audio="v.wav")
    data = json.loads(build(board, uploaded_voice="input/v.wav"))
    assert data["reference_mode"] == REFERENCE_ON
    assert board.uses_references is True


def test_a_voice_that_did_not_upload_emits_nothing():
    """No file on the server to point at, so no segment and no model switch."""
    board = _with_voice(audio="v.wav")
    data = json.loads(build(board, uploaded_voice=""))
    assert data["audioSegments"] == []
    assert data["reference_mode"] == REFERENCE_OFF


def test_the_voice_names_its_subject_the_way_the_editor_does():
    board = _with_voice(audio="v.wav", subject=2)
    segment = json.loads(build(board, uploaded_voice="v.wav"))["audioSegments"][0]
    assert segment["subject"] == "2", "1-based, and a string, as the editor stores it"


def test_an_unbound_voice_leaves_the_subject_empty():
    board = _with_voice(audio="v.wav", subject=None)
    segment = json.loads(build(board, uploaded_voice="v.wav"))["audioSegments"][0]
    assert segment["subject"] == ""


def test_audio_retention_uses_the_audio_vocabulary():
    """A picture's word here is clamped by the node, silently changing what was
    asked for - so the wrong one must never be sent."""
    board = _with_voice(audio="v.wav", retention="fully_preserved")
    segment = json.loads(build(board, uploaded_voice="v.wav"))["audioSegments"][0]
    assert segment["retention"] == "reference"


@pytest.mark.parametrize("word", ["fully_copy", "partially_copy",
                                  "reference", "weak_reference"])
def test_every_audio_retention_word_survives(word):
    board = _with_voice(audio="v.wav", retention=word)
    segment = json.loads(build(board, uploaded_voice="v.wav"))["audioSegments"][0]
    assert segment["retention"] == word


def test_no_voice_means_an_empty_audio_track():
    data = json.loads(build(Storyboard(global_prompt="a fox")))
    assert data["audioSegments"] == []
    assert data["reference_mode"] == REFERENCE_OFF


def test_describe_mentions_a_voice():
    board = _with_voice(audio="v.wav")
    assert "reference voice" in describe(board)
