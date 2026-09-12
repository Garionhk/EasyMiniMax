"""The settings file saved beside each video.

The one that matters is the round trip: what comes back has to be what went in,
or "load these settings" quietly makes a different video from the one whose
settings you loaded.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.h3 import recipe
from app.h3.presets import QUALITY, TURBO, resolution
from app.h3.timeline import Shot, Storyboard, Subject


@pytest.fixture
def board() -> Storyboard:
    b = Storyboard(
        global_prompt="a fox in the snow\nAudio: crunching snow",
        shots=[Shot("wide shot", 60), Shot("close-up", 64)],
        frames=124, width=1024, height=768)
    b.subjects[0] = Subject(description="a red fox", short_name="the fox",
                            kind="animal")
    return b


# -- the round trip --------------------------------------------------------

def test_a_saved_recipe_loads_back_the_same(tmp_path, board):
    path = tmp_path / "clip.json"
    recipe.save(path, recipe.build(board, QUALITY, seed=4242))

    out = recipe.load(path)
    assert out.storyboard.global_prompt == board.global_prompt
    assert [(s.prompt, s.length) for s in out.storyboard.shots] == \
        [("wide shot", 60), ("close-up", 64)]
    assert out.storyboard.frames == 124
    assert (out.storyboard.width, out.storyboard.height) == (1024, 768)
    assert out.resolution_key == "1024x768"
    assert out.profile is QUALITY
    assert out.seed == 4242
    assert not out.notes


def test_the_subjects_survive_the_round_trip(tmp_path, board):
    picture = tmp_path / "fox.png"
    picture.write_bytes(b"pretend png")
    board.subjects[0].image = picture

    path = tmp_path / "clip.json"
    recipe.save(path, recipe.build(board, TURBO))
    out = recipe.load(path)

    first = out.storyboard.subjects[0]
    assert first.image == picture
    assert first.description == "a red fox"
    assert first.short_name == "the fox"
    assert first.kind == "animal"
    # The empty slots come back empty, not missing.
    assert len(out.storyboard.subjects) == 3
    assert out.storyboard.subjects[1].image is None


def test_a_shotless_video_round_trips_as_shotless(tmp_path):
    board = Storyboard(global_prompt="a fox", frames=56)
    path = tmp_path / "clip.json"
    recipe.save(path, recipe.build(board, TURBO))
    assert recipe.load(path).storyboard.shots == []


# -- what a person reading the file sees -----------------------------------

def test_the_file_records_the_four_speed_values_not_just_the_name(tmp_path, board):
    """So a file still says what it did if the presets are ever retuned."""
    data = recipe.build(board, TURBO, seed=1)
    assert data["speed"] == {
        "profile": "turbo",
        "turbo_lora_strength": 1.0,
        "steps": 10,
        "spectrum": True,
        "attention": "comfy kitchen attention",
    }


def test_the_file_names_its_video_and_last_frame(board):
    data = recipe.build(board, TURBO, video="clip.mp4",
                        last_frame="clip_lastframe.png")
    assert data["video"] == "clip.mp4"
    assert data["last_frame"] == "clip_lastframe.png"


def test_the_recipe_sits_beside_its_video():
    assert recipe.path_for(Path("out/2026_turbo.mp4")) == Path("out/2026_turbo.json")


# -- being forgiving about odd files ---------------------------------------

def test_a_missing_reference_picture_is_reported_not_swallowed(tmp_path, board):
    board.subjects[0].image = tmp_path / "gone.png"        # never created
    path = tmp_path / "clip.json"
    recipe.save(path, recipe.build(board, TURBO))

    out = recipe.load(path)
    assert out.storyboard.subjects[0].image is None
    # The description survives, so it is obvious what is missing.
    assert out.storyboard.subjects[0].description == "a red fox"
    assert any("no longer" in n for n in out.notes)


def test_a_size_this_version_no_longer_offers_falls_back_and_says_so(tmp_path):
    path = tmp_path / "clip.json"
    path.write_text(json.dumps({
        "app": "EasyMiniDirector", "format": 1, "prompt": "a fox",
        "size": {"width": 768, "height": 768, "preset": "768x768"},
        "length": {"frames": 56},
    }), encoding="utf-8")

    out = recipe.load(path)
    assert out.resolution_key == resolution("").key      # the default
    assert any("768" in n for n in out.notes)


def test_a_newer_file_loads_what_it_can(tmp_path):
    path = tmp_path / "clip.json"
    path.write_text(json.dumps({
        "app": "EasyMiniDirector", "format": 99, "prompt": "a fox",
        "something_from_the_future": {"nonsense": True},
    }), encoding="utf-8")

    out = recipe.load(path)
    assert out.storyboard.global_prompt == "a fox"
    assert any("newer version" in n for n in out.notes)


def test_an_unreadable_file_is_reported_rather_than_raising(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json at all", encoding="utf-8")
    out = recipe.load(path)
    assert out.notes and "could not be read" in out.notes[0]
    assert out.storyboard.global_prompt == ""


def test_a_file_from_another_program_says_so(tmp_path):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"app": "SomethingElse", "prompt": "a fox"}),
                    encoding="utf-8")
    out = recipe.load(path)
    assert any("not this program" in n for n in out.notes)


def test_an_unknown_speed_falls_back_to_turbo(tmp_path):
    path = tmp_path / "clip.json"
    path.write_text(json.dumps({
        "app": "EasyMiniDirector", "format": 1, "prompt": "a fox",
        "speed": {"profile": "ludicrous"},
    }), encoding="utf-8")
    out = recipe.load(path)
    assert out.profile is None                    # left for the window's default
    assert any("ludicrous" in n for n in out.notes)


def test_loaded_shots_still_tile_the_clip(tmp_path):
    """A hand-edited file must not produce shots that do not add up."""
    path = tmp_path / "clip.json"
    path.write_text(json.dumps({
        "app": "EasyMiniDirector", "format": 1, "prompt": "a fox",
        "length": {"frames": 124},
        "shots": [{"prompt": "a", "frames": 5}, {"prompt": "b", "frames": 5}],
    }), encoding="utf-8")

    out = recipe.load(path)
    assert sum(s.length for s in out.storyboard.shots) == out.storyboard.frames


# -- the reference voice ---------------------------------------------------

def test_the_voice_round_trips(tmp_path, board):
    from app.h3.timeline import Voice

    clip = tmp_path / "v.wav"
    clip.write_bytes(b"pretend audio")
    board.voice = Voice(audio=clip, description="a low calm voice", subject=2)

    path = tmp_path / "clip.json"
    recipe.save(path, recipe.build(board, TURBO))
    voice = recipe.load(path).storyboard.voice

    assert voice.audio == clip
    assert voice.description == "a low calm voice"
    assert voice.subject == 2
    assert voice.retention == "reference"


def test_a_moved_voice_clip_is_reported_and_its_words_kept(tmp_path, board):
    from app.h3.timeline import Voice

    board.voice = Voice(audio=tmp_path / "gone.wav", description="a low voice",
                        subject=1)
    path = tmp_path / "clip.json"
    recipe.save(path, recipe.build(board, TURBO))

    out = recipe.load(path)
    assert out.storyboard.voice.audio is None
    assert out.storyboard.voice.description == "a low voice"
    assert out.storyboard.voice.subject == 1
    assert any("voice clip is no longer" in n for n in out.notes)


def test_a_file_with_no_voice_block_loads_fine(tmp_path):
    """Every recipe written before voices existed."""
    path = tmp_path / "old.json"
    path.write_text(json.dumps({
        "app": "EasyMiniDirector", "format": 1, "prompt": "a fox",
        "length": {"frames": 56},
    }), encoding="utf-8")
    out = recipe.load(path)
    assert out.storyboard.voice.active is False
    assert not out.notes
