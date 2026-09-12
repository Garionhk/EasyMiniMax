"""Where the program's files go, in a built copy and from source.

This is the one piece of the installer that cannot be checked by running the
program here, because a source checkout never takes the frozen branch. It is
also the piece that would do real damage if it were wrong: a built copy writes
its settings.json into whatever data_dir() returns, and EasyAI's settings.json
is one level up with exactly the same name.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import paths


@pytest.fixture
def built(monkeypatch, tmp_path):
    """Pretend to be a one-file build living inside an EasyAI installation."""
    install = tmp_path / "AI" / "EasyAI"
    install.mkdir(parents=True)
    (install / "EasyAI.exe").write_bytes(b"")
    (install / "settings.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "EasyMiniDirector.exe"))
    # A one-file build unpacks its read-only contents somewhere else entirely.
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "unpacked"), raising=False)
    return install


# -- a built copy ----------------------------------------------------------

def test_our_files_go_one_level_below_the_exe(built):
    assert paths.data_dir() == built / "EasyMiniDirector"


def test_our_files_never_go_in_easyais_folder(built):
    """The bug this whole layout exists to prevent.

    Both programs compute settings.json from data_dir(). If ours returned the
    exe's own folder they would share one file: each would overwrite the
    other's window position and carry the other's settings around for ever.
    """
    assert paths.data_dir() != built
    assert paths.data_dir() != paths.install_dir()


def test_our_settings_file_is_not_easyais(built):
    from app.config import DEFAULTS  # noqa: F401  (import cost only)

    ours = paths.data_dir() / "settings.json"
    theirs = paths.install_dir() / "settings.json"
    assert ours != theirs
    assert theirs.is_file(), "the fixture's EasyAI settings should be untouched"


def test_the_install_folder_is_the_exes_own(built):
    assert paths.install_dir() == built


def test_read_only_files_come_from_the_unpacked_bundle(built, tmp_path):
    """Not from the install folder - a one-file build unpacks elsewhere, and
    that folder is deleted on exit."""
    assert paths.resource_dir() == tmp_path / "unpacked"
    assert paths.resource_dir() != paths.data_dir()


# -- a source checkout -----------------------------------------------------

def test_from_source_everything_is_the_project_folder():
    """No installation to be a guest in, so no subfolder either."""
    assert paths.frozen() is False
    assert paths.data_dir() == paths.PROJECT_ROOT
    assert paths.install_dir() == paths.PROJECT_ROOT
    assert paths.resource_dir() == paths.PROJECT_ROOT


def test_the_workflow_ships_where_the_program_looks_for_it():
    """Guards the spec file's datas= against the loader's expectations."""
    assert (paths.resource_dir() / "workflows" /
            "minimax_h3_director.api.json").is_file()
