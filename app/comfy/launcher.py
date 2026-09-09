"""Start ComfyUI if it isn't already running, and wait until it answers.

This is the Python version of start-comfyui-and-booth.bat: probe /system_stats,
launch the portable .bat only if nothing responds, then poll until ready. The
point is that a beginner never sees a console window or has to start two things
in the right order.

Rules that matter:
* Never start a second copy. A live server always wins over our config.
* Never kill a server we didn't start - it may be someone's main session.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.comfy.client import ComfyClient

# Windows: don't flash a console window for the launcher process.
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _kill_on_close_job():
    """A Windows job object that kills its members when EasyAI goes away.

    stop() handles an orderly exit, but not EasyAI being killed from Task
    Manager or falling over. Without this, ComfyUI would be left running with
    the graphics card still fully committed - and a beginner has no idea there
    is a second, invisible program to go and end.

    Putting the engine in a job whose last handle belongs to EasyAI makes the
    operating system do the cleanup, however EasyAI dies. Returns None on any
    failure, in which case stop() remains the only route and nothing is worse
    than it was.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION, of which we need one flag.
        class _IoCounters(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                        ("WriteOperationCount", ctypes.c_ulonglong),
                        ("OtherOperationCount", ctypes.c_ulonglong),
                        ("ReadTransferCount", ctypes.c_ulonglong),
                        ("WriteTransferCount", ctypes.c_ulonglong),
                        ("OtherTransferCount", ctypes.c_ulonglong)]

        class _BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BasicLimits),
                        ("IoInfo", _IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
                job, 9,  # JobObjectExtendedLimitInformation
                ctypes.byref(limits), ctypes.sizeof(limits)):
            kernel32.CloseHandle(job)
            return None
        return job
    except (OSError, AttributeError, ValueError):
        return None


@dataclass
class LaunchResult:
    ok: bool
    message: str
    started_by_us: bool = False


class ComfyLauncher:
    """Owns the lifecycle of the local ComfyUI process."""

    #: One job for the whole application, not one per launcher: Settings
    #: builds a replacement launcher, and the engine must stay in the same job
    #: across that. Held open for as long as EasyAI runs, which is the point.
    _job = None

    def __init__(self, comfyui_dir: str | Path, launcher_name: str,
                 client: ComfyClient, timeout: int = 300):
        self.comfyui_dir = Path(comfyui_dir)
        self.launcher_name = launcher_name
        self.client = client
        self.timeout = timeout
        self.process: subprocess.Popen | None = None
        self.started_by_us = False

    # -- discovery ---------------------------------------------------------
    @property
    def launcher_path(self) -> Path:
        return self.comfyui_dir / self.launcher_name

    def find_launchers(self) -> list[str]:
        """Every .bat in the ComfyUI folder, best guess first.

        Used by the settings dialog so the user picks from a list instead of
        typing a filename.
        """
        if not self.comfyui_dir.is_dir():
            return []
        bats = sorted(p.name for p in self.comfyui_dir.glob("*.bat"))

        def rank(name: str) -> tuple[int, str]:
            low = name.lower()
            if "cpu" in low:
                return (3, low)          # last resort
            if "update" in low or "install" in low:
                return (4, low)          # not a run script at all
            if "nvidia" in low or "start comfyui" in low:
                return (0, low)
            return (1, low)

        return sorted(bats, key=rank)

    def validate(self) -> str | None:
        """Return a human-readable problem with the configured paths, or None."""
        if not self.comfyui_dir.is_dir():
            return f"The ComfyUI folder does not exist:\n{self.comfyui_dir}"
        if not self.launcher_path.is_file():
            available = self.find_launchers()
            hint = ("\n\nStart files found here:\n  " + "\n  ".join(available[:6])) if available else ""
            return f"Start file not found:\n{self.launcher_path}{hint}"
        return None

    # -- lifecycle ---------------------------------------------------------
    def ensure_running(
        self,
        auto_launch: bool = True,
        on_status: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> LaunchResult:
        """Make sure a ComfyUI server is answering, launching one if allowed."""
        def say(msg: str) -> None:
            if on_status:
                on_status(msg)

        say("Looking for the AI engine…")
        if self.client.is_alive():
            return LaunchResult(True, "AI engine is already running.", started_by_us=False)

        if not auto_launch:
            return LaunchResult(
                False,
                "The AI engine is not running.\n\n"
                f"Start ComfyUI yourself, or turn on 'Start ComfyUI automatically' "
                f"in Settings.\n\nLooking for it at: {self.client.server}",
            )

        problem = self.validate()
        if problem:
            return LaunchResult(False, problem)

        say("Starting the AI engine… (this can take a minute on first run)")
        try:
            self._spawn()
        except OSError as e:
            return LaunchResult(False, f"Could not start ComfyUI:\n{e}")

        return self._wait_until_ready(say, should_stop)

    def _spawn(self) -> None:
        flags = 0
        if sys.platform == "win32":
            flags = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        self.process = subprocess.Popen(
            [str(self.launcher_path)],
            cwd=str(self.comfyui_dir),
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
        self.started_by_us = True
        self._put_in_job(self.process)

    def _put_in_job(self, process) -> bool:
        """Tie the engine's lifetime to EasyAI's, so a crash cannot orphan it.

        Returns whether it worked, because a silent failure here is invisible
        until the day someone finds ComfyUI still holding their graphics card.
        """
        if sys.platform != "win32" or process is None:
            return False
        if ComfyLauncher._job is None:
            ComfyLauncher._job = _kill_on_close_job()
        if not ComfyLauncher._job:
            return False
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            # AssignProcessToJobObject needs PROCESS_TERMINATE as well as
            # PROCESS_SET_QUOTA. Ask for only the latter and the call fails
            # with access denied - and nothing appears to be wrong at all.
            rights = 0x0001 | 0x0100          # TERMINATE | SET_QUOTA
            handle = kernel32.OpenProcess(rights, False, process.pid)
            if not handle:
                return False
            ok = bool(kernel32.AssignProcessToJobObject(ComfyLauncher._job, handle))
            kernel32.CloseHandle(handle)
            return ok
        except (OSError, AttributeError):
            return False

    def _wait_until_ready(
        self,
        say: Callable[[str], None],
        should_stop: Callable[[], bool] | None,
    ) -> LaunchResult:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if should_stop and should_stop():
                return LaunchResult(False, "Cancelled.", started_by_us=self.started_by_us)

            if self.client.is_alive():
                return LaunchResult(True, "AI engine is ready.", started_by_us=True)

            # If the batch file exited immediately something is wrong with the
            # install; say so rather than burning the full timeout.
            if self.process and self.process.poll() is not None:
                return LaunchResult(
                    False,
                    "ComfyUI closed straight away.\n\n"
                    f"Try running this by hand to see the error:\n{self.launcher_path}",
                    started_by_us=True,
                )

            remaining = int(deadline - time.time())
            say(f"Starting the AI engine… ({remaining}s left)")
            time.sleep(2)

        return LaunchResult(
            False,
            f"The AI engine did not start within {self.timeout} seconds.\n\n"
            f"Try running this by hand:\n{self.launcher_path}",
            started_by_us=self.started_by_us,
        )

    def adopt(self, other: "ComfyLauncher") -> None:
        """Take over responsibility for a ComfyUI another launcher started.

        Changing anything in Settings builds a fresh launcher for the new
        folder or address. Without this, that new object has no handle on the
        running ComfyUI, so it quietly stops being able to close it - and the
        engine outlives EasyAI, holding the graphics memory, for no reason the
        user could ever guess at.
        """
        if other is self or not other.started_by_us:
            return
        if other.process and other.process.poll() is None:
            self.process = other.process
            self.started_by_us = True

    def stop(self) -> None:
        """Shut down ComfyUI, but only if we were the ones who started it.

        Never a server we merely connected to: that may be someone's main
        session with work in it.
        """
        if not (self.started_by_us and self.process and self.process.poll() is None):
            return
        try:
            if sys.platform == "win32":
                # The .bat spawns python as a child, so kill the whole tree.
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                    capture_output=True, timeout=15,
                    creationflags=_CREATE_NO_WINDOW,
                )
            else:
                self.process.terminate()
                self.process.wait(timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass


def default_comfyui_dir() -> str:
    """Best guess at a ComfyUI install, checked in order of likelihood."""
    candidates = [
        r"C:\AI ComfyUI - New Version\ComfyUI_windows_portable",
        r"C:\AI ComfyUI Sage\ComfyUI-Easy-Install",
        r"C:\ComfyUI_windows_portable",
        os.path.expanduser(r"~\ComfyUI_windows_portable"),
    ]
    for c in candidates:
        if Path(c).is_dir():
            return c
    return candidates[0]
