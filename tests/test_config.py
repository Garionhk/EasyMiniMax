"""Settings, and the one rule about EasyAI's copy of them.

EasyAI already knows where ComfyUI is, so first run borrows the answer rather
than making the user find it again. The rule that matters is what happens
afterwards: **our settings win**. Once this program has a settings.json of its
own, EasyAI's is never read again, so a ComfyUI chosen here stays chosen even
when EasyAI is later pointed somewhere else.
"""
from __future__ import annotations

import json

import pytest

import app.config
from app.config import Config


@pytest.fixture
def easyai(tmp_path):
    """A stand-in for EasyAI's settings.json, one level up from ours."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "comfyui_dir": r"C:\EasyAI-ComfyUI",
        "comfyui_launcher": "run_nvidia_gpu.bat",
        "comfyui_server": "127.0.0.1:8188",
        # Things that are EasyAI's business and not ours.
        "default_ratio": "2:3",
        "window_geometry": "deadbeef",
    }), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _point_at_the_fixture(monkeypatch, easyai):
    # A module constant, so it has to be patched there.
    monkeypatch.setattr(app.config, "SIBLING_SETTINGS", easyai)


# -- first run -------------------------------------------------------------

def test_first_run_borrows_the_comfyui_location(tmp_path):
    cfg = Config(tmp_path / "EasyMiniMax" / "settings.json")
    assert cfg.get("comfyui_dir") == r"C:\EasyAI-ComfyUI"
    assert cfg.get("comfyui_launcher") == "run_nvidia_gpu.bat"
    assert cfg.get("comfyui_server") == "127.0.0.1:8188"


def test_only_the_engine_keys_are_borrowed(tmp_path):
    """EasyAI's window position and aspect ratio are none of our business."""
    cfg = Config(tmp_path / "EasyMiniMax" / "settings.json")
    assert cfg.get("window_geometry") == ""
    assert "default_ratio" not in cfg.data


def test_easyais_file_is_never_written_to(tmp_path, easyai):
    before = easyai.read_bytes()
    cfg = Config(tmp_path / "EasyMiniMax" / "settings.json")
    cfg.set("comfyui_dir", r"D:\Somewhere\Else")
    cfg.save()
    assert easyai.read_bytes() == before


# -- afterwards: ours wins -------------------------------------------------

def test_our_own_settings_beat_easyais(tmp_path):
    """The requirement.

    Point this program at a different ComfyUI, and it keeps that choice - even
    though EasyAI's file still says something else.
    """
    ours = tmp_path / "EasyMiniMax" / "settings.json"

    first = Config(ours)
    assert first.get("comfyui_dir") == r"C:\EasyAI-ComfyUI"     # borrowed
    first.set("comfyui_dir", r"D:\My Own ComfyUI")
    first.save()

    again = Config(ours)
    assert again.get("comfyui_dir") == r"D:\My Own ComfyUI"


def test_easyai_moving_afterwards_does_not_drag_us_along(tmp_path, easyai):
    ours = tmp_path / "EasyMiniMax" / "settings.json"
    Config(ours).save()                       # we now have a file of our own

    easyai.write_text(json.dumps({"comfyui_dir": r"E:\EasyAI Moved"}),
                      encoding="utf-8")

    assert Config(ours).get("comfyui_dir") == r"C:\EasyAI-ComfyUI"


def test_the_borrow_happens_once_not_on_every_load(tmp_path, easyai):
    ours = tmp_path / "EasyMiniMax" / "settings.json"
    Config(ours).save()
    easyai.unlink()                           # EasyAI uninstalled underneath us
    assert Config(ours).get("comfyui_dir") == r"C:\EasyAI-ComfyUI"


# -- when EasyAI is not there ---------------------------------------------

def test_no_easyai_settings_leaves_the_defaults(tmp_path, easyai):
    easyai.unlink()
    cfg = Config(tmp_path / "EasyMiniMax" / "settings.json")
    assert cfg.get("comfyui_dir") == app.config.DEFAULTS["comfyui_dir"]


def test_a_corrupt_easyai_file_is_survived(tmp_path, easyai):
    easyai.write_text("{ not json", encoding="utf-8")
    cfg = Config(tmp_path / "EasyMiniMax" / "settings.json")
    assert cfg.get("comfyui_dir") == app.config.DEFAULTS["comfyui_dir"]


def test_an_easyai_file_that_is_not_a_dict_is_survived(tmp_path, easyai):
    easyai.write_text("[1, 2, 3]", encoding="utf-8")
    assert Config(tmp_path / "x" / "settings.json").get("comfyui_dir") == \
        app.config.DEFAULTS["comfyui_dir"]


def test_blank_values_in_easyais_file_are_ignored(tmp_path, easyai):
    easyai.write_text(json.dumps({"comfyui_dir": "", "comfyui_server": ""}),
                      encoding="utf-8")
    cfg = Config(tmp_path / "x" / "settings.json")
    assert cfg.get("comfyui_dir") == app.config.DEFAULTS["comfyui_dir"]


# -- saving ----------------------------------------------------------------

def test_saving_creates_our_folder(tmp_path):
    ours = tmp_path / "EasyMiniMax" / "settings.json"
    assert not ours.parent.exists()
    Config(ours).save()
    assert ours.is_file()


def test_an_unknown_key_on_disk_is_kept(tmp_path):
    """A file written by a newer version must not lose its settings here."""
    ours = tmp_path / "EasyMiniMax" / "settings.json"
    ours.parent.mkdir(parents=True)
    ours.write_text(json.dumps({"something_new": 42}), encoding="utf-8")

    cfg = Config(ours)
    cfg.save()
    assert json.loads(ours.read_text(encoding="utf-8"))["something_new"] == 42


# -- files people have edited by hand --------------------------------------

def _with_bom(path, data):
    """Exactly what Notepad and PowerShell write when saving as UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(data).encode("utf-8"))


def test_a_settings_file_with_a_byte_order_mark_still_loads(tmp_path):
    """Found by a live test, and it loses everything when it goes wrong.

    Notepad and PowerShell both add a byte-order mark when saving as UTF-8.
    Read as plain utf-8, json.load fails on the first character - and because
    that failure falls back to the defaults, every setting in the file
    disappears without a word.
    """
    ours = tmp_path / "EasyMiniMax" / "settings.json"
    _with_bom(ours, {"comfyui_dir": r"D:\Hand Edited",
                     "duration_seconds": 9.0})

    cfg = Config(ours)
    assert cfg.get("comfyui_dir") == r"D:\Hand Edited"
    assert cfg.get("duration_seconds") == 9.0


def test_easyais_settings_with_a_byte_order_mark_still_lend_us_comfyui(tmp_path,
                                                                       easyai):
    _with_bom(easyai, {"comfyui_dir": r"E:\EasyAI ComfyUI"})
    cfg = Config(tmp_path / "EasyMiniMax" / "settings.json")
    assert cfg.get("comfyui_dir") == r"E:\EasyAI ComfyUI"


def test_a_hand_edited_file_survives_a_save(tmp_path):
    """Loading, then saving, must not quietly drop what was in it."""
    ours = tmp_path / "EasyMiniMax" / "settings.json"
    _with_bom(ours, {"comfyui_dir": r"D:\Hand Edited", "notes_to_self": "keep"})

    cfg = Config(ours)
    cfg.save()

    again = json.loads(ours.read_text(encoding="utf-8"))
    assert again["comfyui_dir"] == r"D:\Hand Edited"
    assert again["notes_to_self"] == "keep"
