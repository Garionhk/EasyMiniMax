"""What the installer actually does, in order.

Follows EasyAI's setup/steps.py: a list of steps, each of which checks whether
its work is already done before doing any of it. That matters more here than it
looks, because the two expensive steps - Ollama at 1.6 GB and the vision model at
6 GB - are exactly the ones somebody is most likely to already have.

Nothing here touches Qt. The interface drives it through callbacks, so the same
sequence can be run from a terminal or a test.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.comfy import objectinfo
from app.comfy.client import ComfyClient
from app.i18n import t
from app.llm.ollama import DEFAULT_URL, Ollama, best_vision_model
from app.llm.ollama import pull as pull_model
from app.paths import APP_FOLDER, resource_dir
from app.setup import nodes as node_setup
from setup import ollama_install
from setup.download import Cancelled, DownloadError, free_space

#: The vision model the prompt helper is set up with. Any vision model works;
#: this one is the suggestion, and it is what the program pre-selects.
MODEL = "qwen2.5vl:7b"

#: Rough sizes, for the space check. Ollama 1.6 GB, the model 6 GB, the program
#: itself under 100 MB, and room to unpack.
NEEDED_BYTES = 9 * 1024 ** 3

#: What tells us a folder really is an EasyAI installation.
#:
#: EasyAI's own program file, and nothing else. settings.json was tried first
#: and is worse than useless: *this* program has one of those too, so the guess
#: happily proposed EasyMiniMax's own folder as the place to install into.
#: A fresh unzip always has the exe; a source checkout has EasyAI.py.
EASYAI_MARKERS = ("EasyAI.exe", "EasyAI.py")

#: The files that make up the program, taken out of the installer's own bundle.
APP_EXE = "EasyMiniMax.exe"
SETUP_EXE = "EasyMiniMax Setup.exe"
WORKFLOW = "minimax_h3_director.api.json"


class SetupError(Exception):
    """Something that stops the install, phrased for a person."""


@dataclass
class Report:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


@dataclass
class Choices:
    """What the user picked on the first screen."""
    easyai_dir: Path
    install_ollama: bool = True
    install_model: bool = True


def looks_like_easyai(folder: Path | str) -> bool:
    """Is this an EasyAI installation, rather than some other folder?

    Checked because the alternative is scattering our files into whatever
    folder happened to be typed, and because a wrong answer here is the one
    mistake in this installer that leaves a mess somebody has to clean up.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return False
    return any((folder / marker).exists() for marker in EASYAI_MARKERS)


def guess_easyai_dir() -> Path | None:
    """Where EasyAI probably is.

    The installer's own folder first: the normal way to run this is to put it
    beside EasyAI.exe and double-click it, and then the answer is simply "here".
    """
    import sys

    candidates = [Path(sys.executable).parent, Path.cwd()]
    for base in (Path.home() / "Desktop", Path.home(), Path("C:/"), Path("D:/")):
        candidates += [base / "EasyAI", base / "AI" / "EasyAI"]

    for candidate in candidates:
        try:
            if looks_like_easyai(candidate):
                return candidate
        except OSError:
            continue
    return None


def read_easyai_settings(easyai_dir: Path) -> dict:
    """EasyAI's ComfyUI details, or an empty dict.

    Read-only. EasyAI's settings file is never written to - the two programs
    share a ComfyUI, not a configuration.
    """
    try:
        # utf-8-sig: EasyAI's settings file may carry a byte-order mark if
        # anyone has edited it in Notepad, and reading it as plain utf-8 would
        # quietly lose the ComfyUI location we came here for.
        with open(Path(easyai_dir) / "settings.json", "r",
                  encoding="utf-8-sig") as f:
            stored = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {key: stored[key] for key in
            ("comfyui_dir", "comfyui_launcher", "comfyui_server")
            if stored.get(key)}


def custom_nodes_dir(comfyui_dir: Path | str) -> Path:
    """Where add-ons live inside a portable ComfyUI.

    The portable layout nests a second ComfyUI folder inside the one the
    launcher sits in. A source checkout does not, so both are accepted. Same
    rule as app/config.py, deliberately duplicated rather than imported: this
    runs before there is a Config to ask.
    """
    root = Path(comfyui_dir)
    nested = root / "ComfyUI" / "custom_nodes"
    if nested.parent.is_dir():
        return nested
    return root / "custom_nodes"


class Installer:
    """One installation attempt."""

    def __init__(self, choices: Choices, on_say=None, on_progress=None,
                 should_stop=None):
        self.choices = choices
        self.easyai_dir = Path(choices.easyai_dir)
        self.data_dir = self.easyai_dir / APP_FOLDER
        self._say_cb = on_say
        self._progress_cb = on_progress
        self._stop = should_stop or (lambda: False)
        self.report = Report()
        self.settings: dict = {}
        self.model_ready = False

    # -- talking to the interface -----------------------------------------
    def _say(self, message: str) -> None:
        print(f"[setup] {message}")
        if self._say_cb:
            self._say_cb(message)

    def _progress(self, progress) -> None:
        if self._progress_cb:
            self._progress_cb(progress)

    # -- the sequence ------------------------------------------------------
    def run(self) -> Report:
        steps = (
            (t("Checking the folder"), self.check_folder),
            (t("Checking disk space"), self.check_space),
            (t("Installing the program"), self.install_program),
            (t("Reading EasyAI's settings"), self.read_settings),
            (t("Installing the ComfyUI add-ons"), self.install_nodes),
            (t("Installing Ollama"), self.install_ollama),
            (t("Fetching the vision model"), self.install_model),
            (t("Writing the settings"), self.write_settings),
        )

        for index, (title, step) in enumerate(steps, start=1):
            if self._stop():
                self.report.failed.append(t("Stopped."))
                break
            self._say("")
            self._say(f"[{index}/{len(steps)}] {title}")
            try:
                step()
            except Cancelled:
                self.report.failed.append(t("Stopped."))
                break
            except SetupError as e:
                # The folder and space checks are the only fatal ones: nothing
                # after them can work if they fail.
                self._say(f"  !! {e}")
                self.report.failed.append(str(e))
                break
            except (DownloadError, OSError) as e:
                # Everything else carries on. A machine that cannot reach
                # GitHub should still end up with a working program pointed at
                # ComfyUI, and be told exactly what is missing.
                self._say(f"  !! {e}")
                self.report.failed.append(f"{title}: {e}")

        return self.report

    # -- 1. the folder -----------------------------------------------------
    def check_folder(self) -> None:
        if not looks_like_easyai(self.easyai_dir):
            raise SetupError(t(
                "{folder} does not look like an EasyAI installation - there is "
                "no EasyAI.exe or settings.json in it.\n\nChoose the folder "
                "EasyAI.exe is in.", folder=self.easyai_dir))
        self._say(t("  found EasyAI in {folder}", folder=self.easyai_dir))

    # -- 2. space ----------------------------------------------------------
    def check_space(self) -> None:
        wanted = NEEDED_BYTES
        if not self.choices.install_ollama:
            wanted -= 2 * 1024 ** 3
        if not self.choices.install_model:
            wanted -= 6 * 1024 ** 3

        available = free_space(self.easyai_dir)
        self._say(t("  {free} GB free, about {need} GB needed",
                    free=f"{available / 1024 ** 3:.0f}",
                    need=f"{wanted / 1024 ** 3:.0f}"))
        if available < wanted:
            raise SetupError(t(
                "There is not enough room on that drive: {free} GB free, and "
                "about {need} GB is needed.",
                free=f"{available / 1024 ** 3:.0f}",
                need=f"{wanted / 1024 ** 3:.0f}"))

    # -- 3. the program ----------------------------------------------------
    def install_program(self) -> None:
        """Put the exe beside EasyAI's, and our own files one level down."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "output").mkdir(exist_ok=True)
        workflows = self.data_dir / "workflows"
        workflows.mkdir(exist_ok=True)

        source = resource_dir() / APP_EXE
        target = self.easyai_dir / APP_EXE
        if source.is_file():
            shutil.copy2(source, target)
            self._say(t("  {name} installed", name=APP_EXE))
            self.report.done.append(APP_EXE)
        elif target.is_file():
            self._say(t("  {name} is already here", name=APP_EXE))
            self.report.skipped.append(APP_EXE)
        else:
            # Running the installer from source, where there is no built exe to
            # copy. Everything else still applies, so this is worth saying
            # rather than failing over.
            self._say(t("  no built {name} to install (running from source)",
                        name=APP_EXE))
            self.report.skipped.append(APP_EXE)

        self._install_self()

        graph = resource_dir() / "workflows" / WORKFLOW
        if graph.is_file():
            shutil.copy2(graph, workflows / WORKFLOW)
            self._say(t("  the workflow is in place"))
        else:
            raise SetupError(t("The workflow file is missing from this "
                               "installer, so it cannot be installed."))

    def _install_self(self) -> None:
        """Leave a copy of the installer in the folder too.

        Both programs then sit together, and the setup can be run again later -
        to add the prompt helper that was unticked the first time, or to put the
        add-ons back after a ComfyUI reinstall. Copying the running exe is safe
        on Windows: a running image can be read, just not written to.
        """
        import sys

        if not getattr(sys, "frozen", False):
            return
        source = Path(sys.executable)
        target = self.easyai_dir / SETUP_EXE
        try:
            if target.resolve() == source.resolve():
                return                    # already running from there
            shutil.copy2(source, target)
            self._say(t("  {name} installed", name=SETUP_EXE))
        except (OSError, shutil.SameFileError):
            # Not worth failing an otherwise complete install over.
            self._say(t("  (could not leave a copy of the installer here)"))

    # -- 4. EasyAI's settings ---------------------------------------------
    def read_settings(self) -> None:
        self.settings = read_easyai_settings(self.easyai_dir)
        if self.settings.get("comfyui_dir"):
            self._say(t("  ComfyUI is at {path}",
                        path=self.settings["comfyui_dir"]))
        else:
            # Not fatal. The program asks for it on first run, and its Settings
            # window tests the answer as it is typed.
            self._say(t("  EasyAI has not been set up yet, so ComfyUI's "
                        "location will be asked for on first run."))
            self.report.skipped.append("comfyui_dir")

    # -- 5. the add-ons ----------------------------------------------------
    def install_nodes(self) -> None:
        comfyui = self.settings.get("comfyui_dir")
        if not comfyui:
            self._say(t("  skipped: ComfyUI's location is not known yet"))
            self.report.skipped.append("add-ons")
            return

        folder = custom_nodes_dir(comfyui)
        if not folder.parent.is_dir():
            raise SetupError(t(
                "ComfyUI does not seem to be at {path}. The add-ons can be "
                "installed later from inside the program.", path=comfyui))

        # Ask the server what it already has, when it is running. When it is
        # not, folder_for() still recognises an add-on under any of its names.
        caps = objectinfo.Capabilities()
        server = self.settings.get("comfyui_server", "127.0.0.1:8188")
        client = ComfyClient(server)
        if client.is_alive(timeout=2.0):
            caps = objectinfo.fetch(client)

        statuses = node_setup.survey(caps, folder)
        report = node_setup.Installer(
            folder, python=self._embedded_python(comfyui),
            on_say=lambda m: self._say(f"  {m}"),
            should_stop=self._stop).run(statuses)

        self.report.done += report.done
        self.report.skipped += report.skipped
        self.report.failed += report.failed
        if report.done:
            self._say(t("  ComfyUI must be restarted before it sees them."))

    @staticmethod
    def _embedded_python(comfyui_dir: str) -> Path | None:
        candidate = Path(comfyui_dir) / "python_embeded" / "python.exe"
        return candidate if candidate.is_file() else None

    # -- 6. Ollama ---------------------------------------------------------
    def install_ollama(self) -> None:
        if not self.choices.install_ollama:
            self._say(t("  skipped, as asked"))
            self.report.skipped.append("Ollama")
            return

        if ollama_install.already_here():
            self._say(t("  Ollama is already installed"))
            self.report.skipped.append("Ollama")
            return

        ollama_install.install(
            work_dir=self.data_dir / "downloads",
            on_say=lambda m: self._say(f"  {m}"),
            on_progress=self._progress, should_stop=self._stop)
        self.report.done.append("Ollama")

    # -- 7. the model ------------------------------------------------------
    def install_model(self) -> None:
        if not self.choices.install_model:
            self._say(t("  skipped, as asked"))
            self.report.skipped.append(MODEL)
            return

        client = Ollama(DEFAULT_URL)
        if not client.is_alive(fresh=True):
            # Installed a moment ago but not answering, or never installed.
            if not ollama_install.start_server(should_stop=self._stop):
                self._say(t("  skipped: Ollama is not running"))
                self.report.skipped.append(MODEL)
                return

        try:
            existing = client.models()
        except Exception:
            existing = []
        if MODEL in existing:
            self._say(t("  {model} is already here", model=MODEL))
            self.report.skipped.append(MODEL)
            self.model_ready = True
            return

        self._say(t("  fetching {model} (about 6 GB)…", model=MODEL))
        pull_model(client, MODEL, on_progress=self._on_pull,
                   should_stop=self._stop)
        self.model_ready = True
        self.report.done.append(MODEL)

    def _on_pull(self, progress) -> None:
        """Ollama's pull stream, reshaped into the downloader's Progress."""
        from setup.download import Progress

        self._progress(Progress(done=progress.completed, total=progress.total,
                                name=progress.status or MODEL))

    # -- 8. our settings ---------------------------------------------------
    def write_settings(self) -> None:
        """Seed EasyMiniMax's own settings.json.

        Merged into whatever is already there rather than replacing it: running
        the installer a second time must not throw away someone's choices.
        """
        path = self.data_dir / "settings.json"
        current: dict = {}
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                current = loaded
        except (OSError, json.JSONDecodeError):
            pass

        for key, value in self.settings.items():
            current.setdefault(key, value)

        current.setdefault("workflow_dir", str(self.data_dir / "workflows"))
        current.setdefault("output_dir", str(self.data_dir / "output"))

        if self.model_ready:
            # Switch the prompt helper on, since we just made it work. The
            # program would find it by itself on first run, but saying so here
            # means the buttons are there the very first time the window opens.
            current.setdefault("llm_enabled", True)
            current.setdefault("llm_model", MODEL)
            current.setdefault("llm_checked", True)

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
        os.replace(temporary, path)
        self._say(t("  settings written to {path}", path=path))


def summarise(report: Report, choices: Choices) -> str:
    """What to show when it is over."""
    lines = []
    if report.done:
        lines.append(t("Installed: {things}", things=", ".join(report.done)))
    if report.skipped:
        lines.append(t("Already there: {things}",
                       things=", ".join(report.skipped)))
    if report.failed:
        lines.append("")
        lines.append(t("These did not work:"))
        lines += [f"  • {problem}" for problem in report.failed]
        lines.append("")
        lines.append(t("The program will still start, and will offer to finish "
                       "the missing pieces itself."))
    else:
        lines.append("")
        lines.append(t("Done. EasyMiniMax.exe is in {folder}.",
                       folder=choices.easyai_dir))
    return "\n".join(lines)
