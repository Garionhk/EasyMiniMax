"""Persistent user settings.

The EasyAI pattern, unchanged: a defaults dict merged with whatever is on disk,
so adding a setting in code never breaks an existing settings.json, and writes
go through a temp file plus os.replace so a crash mid-save cannot leave a
truncated config behind.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.h3.presets import DEFAULT_RESOLUTION
from app.paths import data_dir, frozen, install_dir

# The user's own things: the project folder from source, the folder holding the
# .exe in a built copy. Never the unpacked bundle - that is temporary, and
# settings written there would vanish on exit.
ROOT = data_dir()

def _sibling_settings() -> Path:
    """EasyAI's own settings.json.

    A built copy sits inside EasyAI's folder, so EasyAI's settings file is one
    level above our data folder - right beside our exe. From a source checkout
    there is no installation to be inside, so the developer's own copy is used;
    a missing file simply means the defaults are kept.

    Resolved at run time rather than written down, because a path baked in on
    the machine that built the exe is a path that is wrong on every other one.
    """
    if frozen():
        return install_dir() / "settings.json"
    return Path(r"D:\Programming\EasyAI\settings.json")


#: EasyAI, if it is installed, already knows where ComfyUI lives. Asking the
#: same question twice is the sort of small rudeness that makes a program feel
#: like work, so first run borrows the answer rather than starting from a guess.
#:
#: A module-level constant, not a call, so a test can replace it.
SIBLING_SETTINGS = _sibling_settings()

DEFAULTS = {
    # --- ComfyUI backend ---
    "comfyui_dir": r"C:\AI ComfyUI - New Version\ComfyUI_windows_portable",
    "comfyui_launcher": "run_nvidia_gpu.bat",
    "comfyui_server": "127.0.0.1:8188",
    "auto_launch": True,
    "launch_timeout": 300,      # seconds to wait for the engine to come up
    #: Video is slow. A 15-second clip on the Quality path can take most of an
    #: hour on a 16 GB card, so the ceiling is well above EasyAI's.
    "job_timeout": 5400,
    "stop_engine_on_exit": True,

    # --- Folders ---
    "workflow_dir": str(ROOT / "workflows"),
    "output_dir": str(ROOT / "output"),

    # --- Generation defaults ---
    #: "turbo" or "quality" - see app.h3.presets.
    "profile": "turbo",
    "resolution": DEFAULT_RESOLUTION,
    "duration_seconds": 5.0,
    "lock_seed": False,
    "locked_seed": 0,
    #: A different file of the same kind, when the workflow names one this
    #: ComfyUI does not have. Keyed by "class_type/input", value is the
    #: replacement filename. The workflow file itself is never rewritten, so it
    #: can still be shared or re-added unchanged.
    "model_overrides": {},

    # --- the prompt helper (a local Ollama) ---
    #: Off until asked for. A machine with no Ollama is the normal case, and
    #: probing for one costs a real fraction of a second on a refused
    #: connection - so nothing here touches the network until this is on.
    "llm_enabled": False,
    #: Set once the program has looked for an Ollama, so the probe happens
    #: exactly once on a machine that has none rather than at every launch.
    "llm_checked": False,
    "llm_url": "http://127.0.0.1:11434",
    "llm_model": "",
    "llm_timeout": 120,
    #: Roughly how long the expanded description should be.
    "llm_max_words": 160,

    # --- graphics memory ---
    #: Ask the language model to leave the graphics card before queueing a
    #: render, and wait until it actually has. A vision model and a 21 GB H3
    #: checkpoint do not fit on one card together.
    "llm_free_before_render": True,
    #: Seconds to wait for it to go. After this the render starts anyway, with
    #: a note - refusing to render because memory could not be freed would be
    #: worse than a slow render.
    "llm_free_wait": 30,

    # --- add-ons ---
    #: Set once the required custom nodes have been seen on the server, so the
    #: install offer is not repeated on every launch.
    "nodes_ready": False,

    # --- UI ---
    "language": "en",
    "theme": "dark",
    "window_geometry": "",
    "first_run_done": False,
}

SETTINGS_PATH = ROOT / "settings.json"


class Config:
    """Dict-backed settings with defaults, disk persistence and plain access."""

    def __init__(self, path: Path | str = SETTINGS_PATH):
        self.path = Path(path)
        self.data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            # utf-8-sig, not utf-8: Notepad and PowerShell both write a byte-order mark when saving as UTF-8, and json.load then fails on the very first character. That failure is silent here - it falls back to the defaults - so a file somebody edited by hand would lose every setting in it. utf-8-sig reads both.
            with open(self.path, "r", encoding="utf-8-sig") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                # Merge rather than replace: unknown keys are kept, missing
                # keys fall back to the shipped default.
                self.data.update(stored)
        except FileNotFoundError:
            self._borrow_engine_settings()
        except (json.JSONDecodeError, OSError) as e:
            print(f"[config] could not read {self.path}: {e} - using defaults")

    def _borrow_engine_settings(self) -> None:
        """First run with EasyAI already set up: reuse its ComfyUI details.

        Read-only, and only the three keys that describe the engine. EasyAI's
        own settings file is never written to - the two programs share a
        ComfyUI, not a configuration.

        Called from the FileNotFoundError branch of load() and nowhere else,
        which is the whole of the rule: EasyAI's answer is a *starting point*,
        used once. From the moment this program has a settings.json of its own,
        that file is the only one consulted - so a ComfyUI chosen here stays
        chosen even when EasyAI is later pointed somewhere else.
        """
        try:
            with open(SIBLING_SETTINGS, "r", encoding="utf-8-sig") as f:
                other = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(other, dict):
            return
        for key in ("comfyui_dir", "comfyui_launcher", "comfyui_server"):
            if other.get(key):
                self.data[key] = other[key]
        print(f"[config] took the ComfyUI location from {SIBLING_SETTINGS}")

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"[config] could not write {self.path}: {e}")

    # -- convenience -------------------------------------------------------
    def get(self, key: str, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def __getitem__(self, key: str):
        return self.get(key)

    def __setitem__(self, key: str, value) -> None:
        self.set(key, value)

    # -- derived paths -----------------------------------------------------
    @property
    def server(self) -> str:
        return str(self.get("comfyui_server", "127.0.0.1:8188")).strip().rstrip("/")

    @property
    def base_url(self) -> str:
        return f"http://{self.server}"

    @property
    def workflow_path(self) -> Path:
        return Path(self.get("workflow_dir")) / "minimax_h3_director.api.json"

    def output_dir(self) -> Path:
        return Path(self.get("output_dir"))

    def comfyui_launcher_path(self) -> Path:
        return Path(self.get("comfyui_dir")) / self.get("comfyui_launcher")

    def custom_nodes_dir(self) -> Path:
        """Where add-ons live inside a portable ComfyUI.

        The portable layout nests a second ComfyUI folder inside the one the
        launcher sits in. A source checkout does not, so both are accepted.
        """
        root = Path(self.get("comfyui_dir"))
        nested = root / "ComfyUI" / "custom_nodes"
        if nested.parent.is_dir():
            return nested
        return root / "custom_nodes"

    def embedded_python(self) -> Path | None:
        """The portable build's own Python, for installing an add-on's deps."""
        candidate = Path(self.get("comfyui_dir")) / "python_embeded" / "python.exe"
        return candidate if candidate.is_file() else None


def ensure_folders(cfg: Config) -> None:
    Path(cfg.get("workflow_dir")).mkdir(parents=True, exist_ok=True)
    cfg.output_dir().mkdir(parents=True, exist_ok=True)
