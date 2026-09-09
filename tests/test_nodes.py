"""The add-on preflight and installer.

Two things worth guarding. The program must never offer to install something
already installed, and must never overwrite a folder it did not create - a
custom-node folder can hold the user's own edits, and a "helpful" reinstall that
throws them away is not recoverable.

Nothing here touches the network. The download path is exercised against a
local server serving a real zip, which is what makes it worth having: the
wrapping-folder strip and the path-traversal guard are both properties of a real
archive, not of a mock.
"""
from __future__ import annotations

import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import requests

from app.setup import nodes

#: The real one, captured before any test can replace it. `nodes.requests` is
#: the global requests module, so patching its `get` patches it everywhere -
#: including for the fixture that wants to hand it back.
_REAL_GET = requests.get


class FakeCaps:
    """Stands in for app.comfy.objectinfo.Capabilities."""

    def __init__(self, known=(), available=True):
        self.node_types = set(known)
        self.available = available
        self.error = "" if available else "not answering"

    def has_node(self, class_type: str) -> bool:
        return class_type in self.node_types


EVERYTHING = ("MiniMaxH3DirectorCS", "MiniMaxH3PreviewOverrideCS",
              "SpectrumApplyMiniMaxH3")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing in this file may reach GitHub.

    Without this the installer tests quietly downloaded the real add-ons: they
    passed, slowly, and would fail on a machine with no connection.
    """
    def refuse(*_a, **_k):
        raise AssertionError("a test tried to use the network")

    monkeypatch.setattr(nodes.requests, "get", refuse)
    monkeypatch.setattr(nodes, "git_available", lambda: False)


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture
def zip_server():
    """Serves a GitHub-shaped source archive on a free port."""
    state = {"branches": {"main"}, "body": _zip_bytes({
        # GitHub wraps everything in one <repo>-<branch> folder.
        "ComfyUI-MiniMaxH3-Director-main/__init__.py": b"NODE_CLASS_MAPPINGS = {}",
        "ComfyUI-MiniMaxH3-Director-main/minimax_director.py": b"# nodes",
        "ComfyUI-MiniMaxH3-Director-main/js/editor.js": b"// ui",
    }), "asked": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            state["asked"].append(self.path)
            branch = self.path.rsplit("/", 1)[-1].removesuffix(".zip")
            if branch not in state["branches"]:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            self.wfile.write(state["body"])

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/owner/repo"
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def local_installer(monkeypatch, zip_server):
    """Let the installer talk to the local server, and nowhere else."""
    monkeypatch.setattr(nodes.requests, "get", _REAL_GET)
    return zip_server


# -- folder naming ---------------------------------------------------------

@pytest.mark.parametrize("folder, pack", [
    ("ComfyUI-MiniMaxH3-Director", "ComfyUI-MiniMaxH3-Director"),   # cloned
    ("minimaxh3-director", "ComfyUI-MiniMaxH3-Director"),           # Manager
    ("MiniMaxH3_Director", "ComfyUI-MiniMaxH3-Director"),           # renamed
    ("comfyui-spectrum-minimax-h3", "ComfyUI-Spectrum-MiniMax-H3"),
])
def test_an_installed_add_on_is_recognised_whatever_the_folder_is_called(
        tmp_path, folder, pack):
    """This is not hypothetical - ComfyUI Manager uses the registry id."""
    (tmp_path / folder).mkdir()
    wanted = next(p for p in nodes.REQUIRED if p.name == pack)
    assert nodes.folder_for(wanted, tmp_path) == tmp_path / folder


def test_a_matching_git_remote_wins_over_a_similar_name(tmp_path):
    pack = nodes.REQUIRED[0]
    decoy = tmp_path / "minimaxh3-director"
    decoy.mkdir()
    real = tmp_path / "something-else-entirely"
    (real / ".git").mkdir(parents=True)
    (real / ".git" / "config").write_text(
        f'[remote "origin"]\n\turl = {pack.url}.git\n', encoding="utf-8")
    assert nodes.folder_for(pack, tmp_path) == real


def test_an_unrelated_folder_is_not_mistaken_for_an_add_on(tmp_path):
    (tmp_path / "comfyui-videohelpersuite").mkdir()
    assert nodes.folder_for(nodes.REQUIRED[0], tmp_path) is None


# -- the survey ------------------------------------------------------------

def test_nothing_is_offered_when_the_server_has_everything(tmp_path):
    statuses = nodes.survey(FakeCaps(EVERYTHING), tmp_path)
    assert not nodes.missing(statuses)
    assert not nodes.blocking(statuses)


def test_a_missing_director_blocks_and_is_offered(tmp_path):
    statuses = nodes.survey(FakeCaps(("SpectrumApplyMiniMaxH3",)), tmp_path)
    assert [s.pack.name for s in nodes.missing(statuses)] == \
        ["ComfyUI-MiniMaxH3-Director"]
    assert [s.pack.name for s in nodes.blocking(statuses)] == \
        ["ComfyUI-MiniMaxH3-Director"]


def test_a_missing_spectrum_is_offered_but_does_not_block(tmp_path):
    """Turbo without Spectrum is slower, not broken. Refusing would be wrong."""
    statuses = nodes.survey(
        FakeCaps(("MiniMaxH3DirectorCS", "MiniMaxH3PreviewOverrideCS")), tmp_path)
    assert [s.pack.name for s in nodes.missing(statuses)] == \
        ["ComfyUI-Spectrum-MiniMax-H3"]
    assert not nodes.blocking(statuses)


def test_a_folder_present_but_not_loaded_means_restart_not_install(tmp_path):
    (tmp_path / "ComfyUI-MiniMaxH3-Director").mkdir()
    (tmp_path / "ComfyUI-Spectrum-MiniMax-H3").mkdir()
    statuses = nodes.survey(FakeCaps(()), tmp_path)
    assert not nodes.missing(statuses)
    assert all(s.needs_restart for s in statuses)


def test_an_unreachable_server_does_not_offer_to_reinstall(tmp_path):
    """Everything reads as absent, so only the folder check saves us."""
    (tmp_path / "minimaxh3-director").mkdir()
    (tmp_path / "comfyui-spectrum-minimax-h3").mkdir()
    assert not nodes.missing(nodes.survey(FakeCaps((), available=False), tmp_path))


# -- installing from a zip -------------------------------------------------

def test_an_add_on_installs_from_a_zip_with_no_git(tmp_path, local_installer):
    """The whole point of the change: requiring a developer tool to install an
    add-on made the installer useless to the people it is for."""
    pack = nodes.Pack(name="ComfyUI-MiniMaxH3-Director",
                      url=local_installer["url"],
                      provides=("MiniMaxH3DirectorCS",), why="", required=True)
    installer = nodes.Installer(tmp_path)
    installer._fetch(pack, tmp_path / pack.name)

    folder = tmp_path / pack.name
    assert (folder / "__init__.py").read_bytes() == b"NODE_CLASS_MAPPINGS = {}"
    assert (folder / "js" / "editor.js").is_file()


def test_the_archives_wrapping_folder_is_stripped(tmp_path, local_installer):
    """ComfyUI looks for __init__.py directly inside the custom_nodes entry,
    not one level down inside "<repo>-main"."""
    pack = nodes.Pack(name="Director", url=local_installer["url"],
                      provides=(), why="")
    nodes.Installer(tmp_path)._fetch(pack, tmp_path / pack.name)

    folder = tmp_path / "Director"
    assert not (folder / "ComfyUI-MiniMaxH3-Director-main").exists()
    assert (folder / "__init__.py").is_file()


def test_main_is_tried_before_master(tmp_path, local_installer):
    pack = nodes.Pack(name="Director", url=local_installer["url"],
                      provides=(), why="")
    nodes.Installer(tmp_path)._fetch(pack, tmp_path / pack.name)
    assert local_installer["asked"][0].endswith("/archive/refs/heads/main.zip")


def test_master_is_tried_when_main_is_missing(tmp_path, local_installer):
    """Plenty of repositories never followed GitHub's rename."""
    local_installer["branches"] = {"master"}

    pack = nodes.Pack(name="Director", url=local_installer["url"],
                      provides=(), why="")
    nodes.Installer(tmp_path)._fetch(pack, tmp_path / pack.name)

    assert any("master.zip" in path for path in local_installer["asked"])
    assert (tmp_path / "Director" / "__init__.py").is_file()


def test_a_repo_on_neither_branch_falls_through_to_git(tmp_path, local_installer,
                                                       monkeypatch):
    """And with no git either, it fails with something the user can act on."""
    local_installer["branches"] = set()
    pack = nodes.Pack(name="Director", url=local_installer["url"],
                      provides=(), why="")

    with pytest.raises(requests.RequestException):
        nodes.Installer(tmp_path)._fetch(pack, tmp_path / pack.name)
    assert not (tmp_path / "Director").exists()


def test_an_archive_that_writes_outside_its_folder_is_refused(tmp_path):
    """This one comes from GitHub, but an unpacker that trusts an archive is
    one that will eventually be pointed somewhere else."""
    evil = _zip_bytes({"repo-main/../../escaped.txt": b"gotcha"})
    with zipfile.ZipFile(io.BytesIO(evil)) as archive:
        with pytest.raises(OSError, match="outside the folder"):
            nodes.Installer._extract(archive, tmp_path / "dest")
    assert not (tmp_path.parent / "escaped.txt").exists()


# -- installing, the whole run --------------------------------------------

def test_an_existing_folder_is_skipped_never_overwritten(tmp_path):
    folder = tmp_path / "minimaxh3-director"
    folder.mkdir()
    (folder / "my_edit.py").write_text("# do not lose me", encoding="utf-8")
    (tmp_path / "comfyui-spectrum-minimax-h3").mkdir()

    report = nodes.Installer(tmp_path).run(nodes.survey(FakeCaps(()), tmp_path))

    assert "ComfyUI-MiniMaxH3-Director" in report.skipped
    assert (folder / "my_edit.py").read_text(encoding="utf-8") == "# do not lose me"


def test_one_pack_failing_does_not_sink_the_other(tmp_path, monkeypatch):
    """A machine that cannot reach GitHub for one add-on should still get the
    other, and be told exactly which one is missing."""
    calls = []

    def fetch(self, pack, destination):
        calls.append(pack.name)
        if "Spectrum" in pack.name:
            raise OSError("no route to host")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "__init__.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(nodes.Installer, "_fetch", fetch)
    report = nodes.Installer(tmp_path).run(nodes.survey(FakeCaps(()), tmp_path))

    assert report.done == ["ComfyUI-MiniMaxH3-Director"]
    assert len(report.failed) == 1
    assert "Spectrum" in report.failed[0]
    assert len(calls) == 2


def test_a_failed_install_leaves_no_half_written_folder(tmp_path, monkeypatch):
    """A half-unpacked folder would look installed for ever after."""
    def fetch(self, pack, destination):
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "half.py").write_text("", encoding="utf-8")
        raise OSError("connection reset")

    monkeypatch.setattr(nodes.Installer, "_fetch", fetch)
    nodes.Installer(tmp_path).run(nodes.survey(FakeCaps(()), tmp_path))

    assert not (tmp_path / "ComfyUI-MiniMaxH3-Director").exists()


def test_the_manual_instructions_offer_both_routes(tmp_path):
    statuses = nodes.survey(FakeCaps(()), tmp_path)
    text = nodes.manual_instructions(statuses, tmp_path)
    assert str(tmp_path) in text
    for pack in nodes.REQUIRED:
        assert pack.url in text                       # git clone
        assert f"{pack.url}/archive/" in text         # and the zip
    assert "restart ComfyUI" in text


def test_update_leaves_an_add_on_it_did_not_clone_alone(tmp_path):
    (tmp_path / "minimaxh3-director").mkdir()      # no .git inside
    assert "left alone" in nodes.update(tmp_path, "ComfyUI-MiniMaxH3-Director")
