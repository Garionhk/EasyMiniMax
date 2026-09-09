"""The graph patcher, against the workflow the app actually ships.

The point of most of these is that the Turbo switch has to change four things
and nothing else. A patcher that also nudged a sigma shift or a VAE would be
very hard to notice and very hard to debug.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.h3 import graph as G
from app.h3 import preflight
from app.h3.presets import QUALITY, TURBO
from app.h3.timeline import Shot, Storyboard, Subject

WORKFLOW = Path(__file__).resolve().parent.parent / "workflows" / \
    "minimax_h3_director.api.json"


@pytest.fixture
def raw() -> dict:
    with open(WORKFLOW, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def board() -> Storyboard:
    return Storyboard(global_prompt="a fox in the snow",
                      shots=[Shot("wide", 60), Shot("close", 64)], frames=124)


# -- finding the nodes -----------------------------------------------------

def test_every_role_is_found_in_the_bundled_workflow(raw):
    roles = G.resolve(raw)
    unfilled = [name for name, value in roles.__dict__.items() if not value]
    assert not unfilled, f"could not find {unfilled}"


def test_the_two_unets_are_told_apart_by_what_they_feed(raw):
    """Backwards here means loading the reference model for a plain render."""
    roles = G.resolve(raw)
    assert roles.unet_fl2va != roles.unet_ref2va
    assert "fl2va" in raw[roles.unet_fl2va]["inputs"]["unet_name"]
    assert "ref2va" in raw[roles.unet_ref2va]["inputs"]["unet_name"]


def test_the_two_vaes_are_told_apart(raw):
    roles = G.resolve(raw)
    assert "video" in raw[roles.vae_video]["inputs"]["vae_name"]
    assert "audio" in raw[roles.vae_audio]["inputs"]["vae_name"]


def test_the_turbo_lora_is_found_by_name_not_by_being_the_only_one(raw):
    """A second, unrelated LoRA must not have its strength zeroed."""
    roles = G.resolve(raw)
    graph = copy.deepcopy(raw)
    graph["style_lora"] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {"lora_name": "a_style.safetensors", "strength_model": 0.8,
                   "model": [roles.turbo_lora, 0]},
    }
    patched, _ = G.apply(graph, Storyboard(global_prompt="x"), QUALITY)
    assert patched[roles.turbo_lora]["inputs"]["strength_model"] == 0.0
    assert patched["style_lora"]["inputs"]["strength_model"] == 0.8


def test_a_graph_without_the_director_is_refused(raw):
    graph = {k: v for k, v in raw.items()
             if v.get("class_type") != "MiniMaxH3DirectorCS"}
    with pytest.raises(G.GraphError):
        G.resolve(graph)


# -- the Turbo switch ------------------------------------------------------

@pytest.mark.parametrize("profile, lora, steps, spectrum, attention", [
    (TURBO, 1.0, 10, True, "comfy kitchen attention"),
    (QUALITY, 0.0, 20, False, "pytorch attention"),
])
def test_profiles_write_their_four_values(raw, board, profile, lora, steps,
                                          spectrum, attention):
    both = ["pytorch attention", "comfy kitchen attention"]
    patched, _ = G.apply(raw, board, profile, attention_options=both)
    roles = G.resolve(patched)
    assert patched[roles.turbo_lora]["inputs"]["strength_model"] == lora
    assert patched[roles.scheduler]["inputs"]["steps"] == steps
    assert patched[roles.spectrum]["inputs"]["enabled"] is spectrum
    assert patched[roles.attention]["inputs"]["attention"] == attention


def test_the_switch_changes_nothing_else(raw, board):
    """Turbo and Quality must differ in exactly four inputs."""
    both = ["pytorch attention", "comfy kitchen attention"]
    turbo, _ = G.apply(raw, board, TURBO, seed=1, attention_options=both)
    quality, _ = G.apply(raw, copy.deepcopy(board), QUALITY, seed=1,
                         attention_options=both)

    differences = set()
    for node_id in turbo:
        left = turbo[node_id].get("inputs", {})
        right = quality[node_id].get("inputs", {})
        for key in left:
            if left[key] != right.get(key):
                differences.add((turbo[node_id]["class_type"], key))

    assert differences == {
        ("LoraLoaderModelOnly", "strength_model"),
        ("BasicScheduler", "steps"),
        ("SpectrumApplyMiniMaxH3", "enabled"),
        ("ModelAttentionBackend", "attention"),
    }


def test_the_original_graph_is_never_touched(raw, board):
    before = json.dumps(raw, sort_keys=True)
    G.apply(raw, board, QUALITY)
    assert json.dumps(raw, sort_keys=True) == before


# -- the attention fallback ------------------------------------------------

def test_an_unavailable_backend_falls_back_and_says_so(raw, board):
    patched, report = G.apply(raw, board, TURBO,
                              attention_options=["pytorch attention"])
    roles = G.resolve(patched)
    assert patched[roles.attention]["inputs"]["attention"] == "pytorch attention"
    assert report.notes, "a silent downgrade is the failure this guards against"
    assert "comfy kitchen attention" in report.notes[0]


def test_an_unknown_option_list_leaves_the_choice_alone(raw, board):
    """ComfyUI unreachable: guessing is worse than trying what was asked."""
    patched, report = G.apply(raw, board, TURBO, attention_options=None)
    roles = G.resolve(patched)
    assert patched[roles.attention]["inputs"]["attention"] == "comfy kitchen attention"
    assert not report.notes


# -- the rest of the patch -------------------------------------------------

def test_the_window_is_written_in_both_frames_and_seconds(raw, board):
    patched, report = G.apply(raw, board, TURBO)
    director = patched[G.resolve(patched).director]["inputs"]
    assert director["duration_frames"] == 124
    assert director["end_frame"] == 124
    assert director["start_frame"] == 0
    assert director["duration_seconds"] == pytest.approx(124 / 24, abs=0.01)
    assert report.frames == 124


def test_the_canvas_is_written(raw):
    board = Storyboard(global_prompt="x", width=768, height=1344, frames=56)
    patched, _ = G.apply(raw, board, TURBO)
    director = patched[G.resolve(patched).director]["inputs"]
    assert director["custom_width"] == 768
    assert director["custom_height"] == 1344


def test_the_timeline_carries_the_prompt_and_the_shots(raw, board):
    patched, _ = G.apply(raw, board, TURBO)
    director = patched[G.resolve(patched).director]["inputs"]
    data = json.loads(director["timeline_data"])
    assert data["global_prompt"] == "a fox in the snow"
    assert [s["prompt"] for s in data["segments"]] == ["wide", "close"]
    # The dead mirror inputs are kept in step, for anyone who opens the graph.
    assert director["local_prompts"] == "wide | close"


def test_a_fresh_seed_is_used_unless_one_is_given(raw, board):
    first, report_a = G.apply(raw, board, TURBO)
    second, report_b = G.apply(raw, copy.deepcopy(board), TURBO)
    assert report_a.seed != report_b.seed          # 1 in 2^53 says otherwise
    fixed, report_c = G.apply(raw, copy.deepcopy(board), TURBO, seed=99)
    assert report_c.seed == 99
    assert fixed[G.resolve(fixed).noise]["inputs"]["noise_seed"] == 99


def test_a_wired_input_is_never_overwritten(raw, board):
    """Replacing a connection with a literal cuts the graph in half."""
    graph = copy.deepcopy(raw)
    roles = G.resolve(graph)
    graph[roles.scheduler]["inputs"]["steps"] = ["some_node", 0]
    patched, _ = G.apply(graph, board, QUALITY)
    assert patched[roles.scheduler]["inputs"]["steps"] == ["some_node", 0]


# -- what to say before queueing -------------------------------------------

def test_an_empty_prompt_is_refused():
    assert G.problems(Storyboard())


def test_a_shot_prompt_alone_is_enough():
    board = Storyboard(shots=[Shot("a fox runs", 124)], frames=124)
    assert not G.problems(board)


def test_a_picture_with_no_description_is_flagged(tmp_path):
    picture = tmp_path / "fox.png"
    picture.write_bytes(b"not really a png")
    board = Storyboard(global_prompt="a fox")
    board.subjects[0] = Subject(image=picture, description="")
    complaints = G.problems(board)
    assert any("what the picture" in c.lower() or "shows" in c.lower()
               for c in complaints)


def test_a_picture_that_has_been_deleted_is_flagged():
    board = Storyboard(global_prompt="a fox")
    board.subjects[0] = Subject(image=Path("nowhere/gone.png"), description="a fox")
    assert any("no longer" in c for c in G.problems(board))


# -- model overrides -------------------------------------------------------

def test_applying_overrides_does_not_reach_back_into_the_caller(raw):
    """apply_overrides writes into node["inputs"], so the copy has to be deep.

    A shallow copy shares those dicts with the workflow the window holds, and
    one override would then follow every later render for the rest of the
    session - including renders the user never asked to override.
    """
    original = raw["1"]["inputs"]["unet_name"]
    working = copy.deepcopy(raw)
    preflight.apply_overrides(working, {"UNETLoader/unet_name": "swapped.safetensors"})
    assert raw["1"]["inputs"]["unet_name"] == original
    assert working["1"]["inputs"]["unet_name"] == "swapped.safetensors"


def test_the_job_worker_leaves_the_window_s_graph_alone(raw):
    """The same guarantee, through the worker's own patch step."""
    from app.jobs import GenerationRequest, JobWorker

    original = json.dumps(raw, sort_keys=True)
    worker = JobWorker.__new__(JobWorker)          # no Qt thread needed
    worker.graph = raw
    worker.request = GenerationRequest(
        storyboard=Storyboard(global_prompt="a fox"),
        overrides={"UNETLoader/unet_name": "swapped.safetensors"})

    graph = preflight.apply_overrides(
        copy.deepcopy(worker.graph), worker.request.overrides)
    G.apply(graph, worker.request.storyboard, TURBO)

    assert json.dumps(raw, sort_keys=True) == original


# -- the last frame --------------------------------------------------------

def test_the_last_frame_saver_is_spliced_in_front_of_create_video(raw, board):
    patched, report = G.apply(raw, board, TURBO, save_last_frame=True)
    roles = G.resolve(raw)

    node = patched[G.LASTFRAME_ID]
    assert node["class_type"] == G.LASTFRAME
    assert node["inputs"]["save"] is True
    # It reads the picture decode, and CreateVideo now reads it.
    assert node["inputs"]["images"] == [roles.decode_video, 0]
    assert patched[roles.create_video]["inputs"]["images"] == [G.LASTFRAME_ID, 0]
    assert "last_frame" in report.applied


def test_the_saver_taps_the_video_decode_not_the_audio_one(raw):
    roles = G.resolve(raw)
    assert raw[roles.decode_video]["class_type"] == "VAEDecode"
    assert "video" in raw[roles.vae_video]["inputs"]["vae_name"]


def test_without_the_add_on_node_the_graph_is_left_alone(raw, board):
    """A graph naming a node ComfyUI does not know is rejected whole, so a
    missing saver has to cost the last frame and not the render."""
    patched, _ = G.apply(raw, board, TURBO, save_last_frame=False)
    roles = G.resolve(raw)
    assert G.LASTFRAME_ID not in patched
    assert patched[roles.create_video]["inputs"]["images"] == [roles.decode_video, 0]


def test_the_saver_does_not_change_what_the_video_is_made_from(raw, board):
    """It passes the batch through whole - a tap, not a filter."""
    with_saver, _ = G.apply(raw, board, TURBO, seed=1, save_last_frame=True)
    without, _ = G.apply(raw, copy.deepcopy(board), TURBO, seed=1,
                         save_last_frame=False)
    # Everything except the splice itself is identical.
    del with_saver[G.LASTFRAME_ID]
    roles = G.resolve(raw)
    with_saver[roles.create_video]["inputs"]["images"] = \
        without[roles.create_video]["inputs"]["images"]
    assert json.dumps(with_saver, sort_keys=True) == json.dumps(without, sort_keys=True)


# -- the reference voice ---------------------------------------------------

def test_a_voice_reaches_the_timeline_and_turns_the_audio_track_on(raw, board):
    from app.h3.timeline import Voice

    board.voice = Voice(audio="v.wav", description="a low calm voice", subject=1)
    patched, report = G.apply(raw, board, TURBO, uploaded_voice="input/v.wav")
    director = patched[G.resolve(patched).director]["inputs"]

    assert director["use_custom_audio"] is True
    data = json.loads(director["timeline_data"])
    assert data["reference_mode"] == "REF2VA"
    assert data["audioSegments"][0]["audioFile"] == "input/v.wav"
    assert "audio_track" in report.applied


def test_no_voice_leaves_the_audio_track_alone(raw, board):
    """Nothing to send, so nothing is claimed to have been applied."""
    patched, report = G.apply(raw, board, TURBO)
    data = json.loads(patched[G.resolve(patched).director]["inputs"]["timeline_data"])
    assert data["audioSegments"] == []
    assert "audio_track" not in report.applied


def test_a_voice_reports_as_using_references(raw, board):
    from app.h3.timeline import Voice
    board.voice = Voice(audio="v.wav")
    _, report = G.apply(raw, board, TURBO, uploaded_voice="v.wav")
    assert report.references is True
