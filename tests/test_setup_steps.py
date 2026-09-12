"""The installer's steps.

Nothing here touches the network, runs an installer, or writes outside tmp_path.
What is worth guarding is the behaviour that only shows up on somebody else's
machine: installing into the wrong folder, doing 7.6 GB of work that was already
done, and one failure taking the rest of the install down with it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from setup import steps


@pytest.fixture
def easyai(tmp_path):
    """A stand-in EasyAI installation."""
    folder = tmp_path / "EasyAI"
    folder.mkdir()
    (folder / "EasyAI.exe").write_bytes(b"MZ")
    (folder / "settings.json").write_text(json.dumps({
        "comfyui_dir": str(tmp_path / "ComfyUI_portable"),
        "comfyui_launcher": "run_nvidia_gpu.bat",
        "comfyui_server": "127.0.0.1:8188",
        "default_ratio": "2:3",
    }), encoding="utf-8")
    (tmp_path / "ComfyUI_portable" / "ComfyUI" / "custom_nodes").mkdir(parents=True)
    return folder


@pytest.fixture
def offline(monkeypatch):
    """Nothing reaches the network, whatever the machine actually has.

    The add-on fetch is replaced by one that writes a plausible folder rather
    than one that fails the test: these tests want the whole run to complete, so
    that what lands on disk afterwards can be checked. `fetched` records what
    was asked for, for the tests that care.
    """
    fetched: list[str] = []

    def fake_fetch(self, pack, destination):
        fetched.append(pack.name)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "__init__.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(steps.node_setup.Installer, "_fetch", fake_fetch)
    monkeypatch.setattr(steps.ollama_install, "already_here", lambda *a, **k: True)
    monkeypatch.setattr(steps.ollama_install, "install",
                        lambda **k: pytest.fail("it tried to install Ollama"))
    monkeypatch.setattr(steps.Installer, "install_model",
                        lambda self: self.report.skipped.append(steps.MODEL))
    return fetched


@pytest.fixture
def bundle(monkeypatch, tmp_path):
    """A stand-in for the installer's own bundle.

    Needed by any test that fakes sys.frozen: resource_dir() then follows the
    exe rather than the project, and the workflow would not be found.
    """
    folder = tmp_path / "bundle"
    (folder / "workflows").mkdir(parents=True)
    (folder / "workflows" / steps.WORKFLOW).write_text("{}", encoding="utf-8")
    (folder / steps.APP_EXE).write_bytes(b"MZ app")
    monkeypatch.setattr(steps, "resource_dir", lambda: folder)
    return folder


def _choices(easyai, **kwargs):
    return steps.Choices(easyai_dir=easyai, **kwargs)


# -- finding EasyAI --------------------------------------------------------

def test_a_real_easyai_folder_is_recognised(easyai):
    assert steps.looks_like_easyai(easyai) is True


def test_our_own_folder_is_not_mistaken_for_easyais(tmp_path):
    """settings.json was tried as a marker first and was worse than useless:
    this program has one too, so the guess proposed our own folder."""
    ours = tmp_path / "EasyMiniDirector"
    (ours / "EasyMiniDirector").mkdir(parents=True)
    (ours / "EasyMiniDirector" / "settings.json").write_text("{}", encoding="utf-8")
    (ours / "EasyMiniDirector.exe").write_bytes(b"MZ")
    assert steps.looks_like_easyai(ours) is False


@pytest.mark.parametrize("what", ["an empty folder", "one with other files"])
def test_an_unrelated_folder_is_refused(tmp_path, what):
    folder = tmp_path / "somewhere"
    folder.mkdir()
    if "other" in what:
        (folder / "holiday.jpg").write_bytes(b"")
    assert steps.looks_like_easyai(folder) is False


def test_a_folder_that_is_not_there_is_refused(tmp_path):
    assert steps.looks_like_easyai(tmp_path / "nope") is False


def test_installing_into_the_wrong_folder_stops_before_writing(tmp_path):
    """The one mistake here that leaves a mess someone has to clean up."""
    wrong = tmp_path / "Documents"
    wrong.mkdir()
    report = steps.Installer(_choices(wrong)).run()

    assert not report.ok
    assert "does not look like an EasyAI installation" in report.failed[0]
    assert list(wrong.iterdir()) == [], "it wrote into the wrong folder anyway"


# -- reading EasyAI's settings --------------------------------------------

def test_only_the_engine_keys_are_read(easyai):
    settings = steps.read_easyai_settings(easyai)
    assert set(settings) == {"comfyui_dir", "comfyui_launcher", "comfyui_server"}


def test_a_missing_or_broken_easyai_settings_file_is_survived(tmp_path, easyai):
    (easyai / "settings.json").write_text("{ not json", encoding="utf-8")
    assert steps.read_easyai_settings(easyai) == {}
    (easyai / "settings.json").unlink()
    assert steps.read_easyai_settings(easyai) == {}


def test_easyais_settings_are_never_written_to(easyai, offline):
    before = (easyai / "settings.json").read_bytes()
    steps.Installer(_choices(easyai, install_ollama=False,
                             install_model=False)).run()
    assert (easyai / "settings.json").read_bytes() == before


# -- what gets written where ----------------------------------------------

def test_our_files_go_in_a_subfolder(easyai, offline):
    steps.Installer(_choices(easyai, install_ollama=False,
                             install_model=False)).run()

    ours = easyai / "EasyMiniDirector"
    assert (ours / "settings.json").is_file()
    assert (ours / "workflows" / steps.WORKFLOW).is_file()
    assert (ours / "output").is_dir()


def test_our_settings_take_easyais_comfyui_location(easyai, offline, tmp_path):
    steps.Installer(_choices(easyai, install_ollama=False,
                             install_model=False)).run()

    written = json.loads((easyai / "EasyMiniDirector" / "settings.json")
                         .read_text(encoding="utf-8"))
    assert written["comfyui_dir"] == str(tmp_path / "ComfyUI_portable")
    assert written["comfyui_server"] == "127.0.0.1:8188"
    # EasyAI's own business is not copied across.
    assert "default_ratio" not in written


def test_running_it_twice_keeps_what_the_user_changed(easyai, offline):
    """A second run must not throw away someone's choices."""
    steps.Installer(_choices(easyai, install_ollama=False,
                             install_model=False)).run()

    ours = easyai / "EasyMiniDirector" / "settings.json"
    settings = json.loads(ours.read_text(encoding="utf-8"))
    settings["comfyui_dir"] = r"D:\My Own ComfyUI"
    settings["duration_seconds"] = 9.0
    ours.write_text(json.dumps(settings), encoding="utf-8")

    steps.Installer(_choices(easyai, install_ollama=False,
                             install_model=False)).run()

    again = json.loads(ours.read_text(encoding="utf-8"))
    assert again["comfyui_dir"] == r"D:\My Own ComfyUI"
    assert again["duration_seconds"] == 9.0


# -- skipping work already done -------------------------------------------

def test_an_installed_ollama_is_not_installed_again(easyai, offline):
    """`offline` fails the test if install() is called at all."""
    report = steps.Installer(_choices(easyai, install_model=False)).run()
    assert "Ollama" in report.skipped


def test_unticking_ollama_skips_it(easyai, monkeypatch):
    monkeypatch.setattr(steps.ollama_install, "already_here",
                        lambda *a, **k: pytest.fail("it probed anyway"))
    monkeypatch.setattr(steps.node_setup.Installer, "_fetch",
                        lambda self, p, d: None)
    report = steps.Installer(_choices(easyai, install_ollama=False,
                                      install_model=False)).run()
    assert "Ollama" in report.skipped


def test_an_add_on_already_present_is_left_alone(easyai, offline, tmp_path):
    folder = tmp_path / "ComfyUI_portable" / "ComfyUI" / "custom_nodes"
    (folder / "minimaxh3-director").mkdir()
    (folder / "minimaxh3-director" / "mine.py").write_text("# keep", encoding="utf-8")
    (folder / "comfyui-spectrum-minimax-h3").mkdir()

    report = steps.Installer(_choices(easyai, install_ollama=False,
                                      install_model=False)).run()

    assert "ComfyUI-MiniMaxH3-Director" in report.skipped
    assert (folder / "minimaxh3-director" / "mine.py").read_text(
        encoding="utf-8") == "# keep"


def test_the_space_check_asks_for_less_when_nothing_big_is_wanted(easyai,
                                                                  monkeypatch):
    asked = []
    monkeypatch.setattr(steps, "free_space",
                        lambda p: asked.append(p) or 500 * 1024 ** 3)
    monkeypatch.setattr(steps.node_setup.Installer, "_fetch", lambda s, p, d: None)
    monkeypatch.setattr(steps.ollama_install, "already_here", lambda *a, **k: True)

    everything = steps.Installer(_choices(easyai))
    everything.check_space()
    lean = steps.Installer(_choices(easyai, install_ollama=False,
                                    install_model=False))
    lean.check_space()
    assert asked, "the space check never looked at the drive"


def test_not_enough_room_stops_before_anything_is_written(easyai, monkeypatch):
    monkeypatch.setattr(steps, "free_space", lambda p: 1024 ** 3)   # 1 GB
    report = steps.Installer(_choices(easyai)).run()

    assert not report.ok
    assert "not enough room" in report.failed[0]
    assert not (easyai / "EasyMiniDirector").exists()


# -- carrying on after a failure ------------------------------------------

def test_a_failed_add_on_does_not_stop_the_settings_being_written(easyai,
                                                                  monkeypatch):
    """A machine that cannot reach GitHub should still end up with a working
    program pointed at ComfyUI, and be told what is missing."""
    monkeypatch.setattr(steps.ollama_install, "already_here", lambda *a, **k: True)
    monkeypatch.setattr(steps.node_setup.Installer, "_fetch",
                        lambda self, p, d: (_ for _ in ()).throw(
                            OSError("no route to host")))

    report = steps.Installer(_choices(easyai, install_model=False)).run()

    assert report.failed, "the add-on failure was not reported"
    assert (easyai / "EasyMiniDirector" / "settings.json").is_file()


def test_an_easyai_that_was_never_set_up_still_installs(tmp_path, offline):
    """No settings.json to borrow from: the program asks on first run."""
    folder = tmp_path / "EasyAI"
    folder.mkdir()
    (folder / "EasyAI.exe").write_bytes(b"MZ")

    report = steps.Installer(_choices(folder, install_ollama=False,
                                      install_model=False)).run()

    assert (folder / "EasyMiniDirector" / "settings.json").is_file()
    assert "comfyui_dir" in report.skipped


def test_the_summary_names_what_failed(easyai):
    report = steps.Report(done=["Ollama"], skipped=["add-ons"],
                          failed=["the model: no route to host"])
    text = steps.summarise(report, _choices(easyai))
    assert "Ollama" in text
    assert "no route to host" in text
    assert "will still start" in text


def test_the_installer_leaves_a_copy_of_itself(easyai, offline, bundle,
                                               monkeypatch, tmp_path):
    """So both programs sit together and setup can be run again later."""
    import sys

    fake_setup = tmp_path / "EasyMiniDirector Setup.exe"
    fake_setup.write_bytes(b"MZ installer")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_setup))

    steps.Installer(_choices(easyai, install_ollama=False,
                             install_model=False)).run()

    assert (easyai / steps.SETUP_EXE).read_bytes() == b"MZ installer"


def test_running_the_installer_from_where_it_already_lives_is_fine(easyai, offline,
                                                                   bundle,
                                                                   monkeypatch):
    """Copying a file onto itself must not fail the install."""
    import sys

    inside = easyai / steps.SETUP_EXE
    inside.write_bytes(b"MZ installer")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(inside))

    report = steps.Installer(_choices(easyai, install_ollama=False,
                                      install_model=False)).run()
    assert report.ok
    assert inside.read_bytes() == b"MZ installer"


def test_the_app_exe_is_copied_out_of_the_bundle(easyai, offline, bundle):
    """The one-file delivery: the program travels inside the installer."""
    steps.Installer(_choices(easyai, install_ollama=False,
                             install_model=False)).run()
    assert (easyai / steps.APP_EXE).read_bytes() == b"MZ app"


def test_easyais_settings_are_read_even_with_a_byte_order_mark(easyai):
    """Notepad adds one, and reading it as plain utf-8 would silently lose the
    ComfyUI location the installer came for."""
    (easyai / "settings.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"comfyui_dir": r"E:\Somewhere"}).encode())
    assert steps.read_easyai_settings(easyai) == {"comfyui_dir": r"E:\Somewhere"}
