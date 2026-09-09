"""Getting Ollama onto the machine.

The official installer, run silently. It is 1.6 GB, which is a lot to download
on somebody's behalf, so the first thing this does is check whether it is needed
at all - and on a machine that already has Ollama, it is not.

Why the official installer rather than the portable zip: it needs no
administrator, installs into the user's own folder, starts with Windows, is
shared with anything else that wants Ollama, and leaves a real entry in
Add/Remove Programs. A portable copy would be ours alone and would have to be
started and stopped by us, which is a worse deal for the user in exchange for
saving them nothing.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from app.i18n import t
from app.llm.ollama import DEFAULT_URL, Ollama, find_ollama_exe
from setup.download import Cancelled, DownloadError, Progress, download

#: The current build, always. Ollama's release page keeps this URL pointing at
#: whatever is newest, so this does not need updating every few weeks.
INSTALLER_URL = ("https://github.com/ollama/ollama/releases/latest/download/"
                 "OllamaSetup.exe")

#: Checked against what actually arrives, purely so a truncated download or an
#: error page saved as a .exe is caught before it is executed. Approximate on
#: purpose - the real guard is that the file is many hundreds of megabytes.
MIN_INSTALLER_BYTES = 500 * 1024 * 1024

#: Roughly what to expect, for the disk-space check and the progress bar.
APPROX_INSTALLER_BYTES = 1_570_000_000

#: Windows: no console window from the silent installer.
_NO_WINDOW = 0x08000000

#: How long to wait for the service to answer once the installer has finished.
SERVE_TIMEOUT = 90


def already_here(url: str = DEFAULT_URL) -> bool:
    """Is Ollama installed, by either sign?

    A running server is the strongest answer. The exe on disk covers the case
    where it is installed but not currently running, which is common right
    after a reboot on a machine where the user disabled the startup entry.
    """
    if Ollama(url).is_alive(fresh=True):
        return True
    return find_ollama_exe() is not None


def install(work_dir: Path, on_say=None, on_progress=None,
            should_stop=None) -> None:
    """Download the official installer and run it without asking anything.

    Raises DownloadError with something a person can act on. Never leaves a
    partial .exe where a later run might execute it.
    """
    say = on_say or (lambda _m: None)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    installer = work_dir / "OllamaSetup.exe"

    say(t("Downloading Ollama (about 1.6 GB)…"))
    download(INSTALLER_URL, installer, on_progress=on_progress,
             should_stop=should_stop)

    size = installer.stat().st_size
    if size < MIN_INSTALLER_BYTES:
        # An error page, a captive portal's login screen, or a truncated file.
        # Running it would at best do nothing and at worst run something else.
        installer.unlink(missing_ok=True)
        raise DownloadError(t(
            "What arrived was only {size} MB, which is not the Ollama "
            "installer. Check the connection and try again.",
            size=f"{size / 1e6:.0f}"))

    say(t("Installing Ollama…"))
    try:
        # /VERYSILENT: no window at all. /NORESTART: never reboot someone's
        # machine on their behalf. /SP-: skip the "this will install..." prompt
        # that would otherwise appear even in silent mode.
        result = subprocess.run(
            [str(installer), "/VERYSILENT", "/NORESTART", "/SP-"],
            capture_output=True, timeout=1800, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        raise DownloadError(t(
            "The Ollama installer did not finish within half an hour. It may "
            "be waiting for an answer behind another window.")) from e
    except OSError as e:
        raise DownloadError(t("Ollama could not be installed: {problem}",
                              problem=e)) from e
    finally:
        # 1.6 GB in the temp folder is not worth keeping, and Ollama's own
        # updater will fetch its own copy in future.
        installer.unlink(missing_ok=True)

    if result.returncode != 0:
        raise DownloadError(t(
            "The Ollama installer stopped with code {code}. Installing it by "
            "hand from https://ollama.com/download will give a clearer "
            "message.", code=result.returncode))

    say(t("Waiting for Ollama to start…"))
    if not wait_until_answering(should_stop=should_stop):
        # Installed but not yet serving. Not a failure worth stopping for: the
        # program probes again every time it starts.
        say(t("Ollama is installed but has not started answering yet. It "
              "usually starts with Windows; a restart will sort it out."))


def wait_until_answering(url: str = DEFAULT_URL, timeout: int = SERVE_TIMEOUT,
                         should_stop=None) -> bool:
    """Poll until the server answers, or give up.

    A fresh install starts the service itself, but not instantly, and the model
    pull that follows would fail against a server that is not up yet.
    """
    should_stop = should_stop or (lambda: False)
    client = Ollama(url)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if should_stop():
            raise Cancelled("ollama")
        if client.is_alive(fresh=True):
            return True
        time.sleep(1.0)
    return False


def start_server(should_stop=None) -> bool:
    """Start `ollama serve` ourselves, for when the service is not running.

    Detached, because this is the user's Ollama and not ours - it must outlive
    the installer rather than dying with it.
    """
    exe = find_ollama_exe()
    if exe is None:
        return False
    try:
        subprocess.Popen([str(exe), "serve"], creationflags=_NO_WINDOW,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return wait_until_answering(should_stop=should_stop)
