"""Getting the add-ons this workflow needs into the user's ComfyUI.

The MiniMax H3 Director is not part of ComfyUI. Without it the workflow is
rejected the moment it is queued, with a message about an unknown node type that
means nothing to someone who just wanted to make a video. So the first thing
this program does is ask the running ComfyUI what it knows about, and offer to
fetch whatever is missing.

Follows EasyAI's installer (setup/steps.py) in the parts that matter: a shallow
clone rather than a full one, an add-on's own requirements.txt installed with
ComfyUI's embedded Python rather than the system one, an existing folder left
alone rather than overwritten, and one pack failing recorded without sinking the
rest.

What it deliberately does not do is act on its own. This writes into the user's
ComfyUI installation, so it is always offered and never automatic.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import requests

from app.i18n import N, t

#: Windows: keep console windows from flashing up during git and pip.
_NO_WINDOW = 0x08000000

#: Branches to try when downloading a zip, in order. GitHub renamed the default
#: years ago but plenty of repositories never followed.
_BRANCHES = ("main", "master")

#: Long enough for a slow connection, short enough that a hung server does not
#: look like a hung installer. These archives are a few hundred kilobytes.
_ZIP_TIMEOUT = 180


@dataclass(frozen=True)
class Pack:
    name: str
    url: str
    #: The node types this pack provides. Presence is decided by asking the
    #: server for these, not by looking for a folder - a folder can exist while
    #: the pack fails to import, and then the nodes are still missing.
    provides: tuple[str, ...]
    why: str
    #: False for add-ons the workflow can run without. Spectrum only speeds the
    #: Turbo path up; refusing to start without it would be wrong.
    required: bool = True


REQUIRED: tuple[Pack, ...] = (
    Pack(
        name="ComfyUI-MiniMaxH3-Director",
        url="https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director",
        provides=("MiniMaxH3DirectorCS", "MiniMaxH3PreviewOverrideCS"),
        why=N("The timeline and the live preview. Nothing works without it."),
        required=True,
    ),
    Pack(
        name="ComfyUI-Spectrum-MiniMax-H3",
        url="https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3",
        provides=("SpectrumApplyMiniMaxH3",),
        why=N("The accelerator behind Turbo. Without it Turbo still works, "
              "just more slowly."),
        required=False,
    ),
)


@dataclass
class PackStatus:
    pack: Pack
    #: The server offers this pack's nodes: genuinely installed and loaded.
    present: bool = False
    #: The folder is there but the nodes are not - almost always "installed,
    #: needs a restart", which is a different sentence to "not installed".
    on_disk: bool = False

    @property
    def needs_install(self) -> bool:
        return not self.present and not self.on_disk

    @property
    def needs_restart(self) -> bool:
        return not self.present and self.on_disk


@dataclass
class InstallReport:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    #: pack name -> the commit it landed on, so an update can say what changed.
    commits: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failed


def _normalise(name: str) -> str:
    """A folder name reduced to what identifies the add-on.

    Folder names are not stable. Cloning by hand gives the repo name
    ("ComfyUI-MiniMaxH3-Director"); installing through ComfyUI Manager gives the
    registry id ("minimaxh3-director"); people rename them. Dropping the
    "ComfyUI" prefix and every separator makes all three land on the same
    string, so an add-on that is already installed is never offered again under
    a name the user does not recognise.
    """
    text = "".join(ch for ch in name.lower() if ch.isalnum())
    for prefix in ("comfyui", "comfy"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def _remote_of(folder: Path) -> str:
    """The repo a folder was cloned from, read straight out of .git/config."""
    config = folder / ".git" / "config"
    try:
        text = config.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("url = "):
            return line[6:].strip().rstrip("/").removesuffix(".git").lower()
    return ""


def folder_for(pack: Pack, nodes_dir: Path) -> Path | None:
    """Where this add-on already lives, whatever it happens to be called."""
    nodes_dir = Path(nodes_dir)
    exact = nodes_dir / pack.name
    if exact.is_dir():
        return exact
    if not nodes_dir.is_dir():
        return None

    wanted_name = _normalise(pack.name)
    wanted_url = pack.url.rstrip("/").removesuffix(".git").lower()
    fallback = None
    for child in nodes_dir.iterdir():
        if not child.is_dir():
            continue
        # A matching git remote is proof; a matching name is a good guess.
        if _remote_of(child) == wanted_url:
            return child
        if fallback is None and _normalise(child.name) == wanted_name:
            fallback = child
    return fallback


def survey(caps, nodes_dir: Path) -> list[PackStatus]:
    """Which add-ons are already here, according to the running ComfyUI.

    ``caps`` is an app.comfy.objectinfo.Capabilities. When ComfyUI is not
    answering, everything reads as missing on the server, so the folder check is
    what stops the program offering to install something that is already there.
    """
    out = []
    for pack in REQUIRED:
        present = bool(caps and caps.available
                       and all(caps.has_node(n) for n in pack.provides))
        out.append(PackStatus(
            pack=pack,
            present=present,
            on_disk=folder_for(pack, nodes_dir) is not None,
        ))
    return out


def missing(statuses: list[PackStatus]) -> list[PackStatus]:
    return [s for s in statuses if s.needs_install]


def blocking(statuses: list[PackStatus]) -> list[PackStatus]:
    """The ones whose absence stops a render outright."""
    return [s for s in statuses if not s.present and s.pack.required]


def git_available() -> bool:
    return shutil.which("git") is not None


def manual_instructions(statuses: list[PackStatus], nodes_dir: Path) -> str:
    """What to do by hand, for when neither the download nor git worked."""
    lines = [t("With git installed, open a command prompt and run:"), "",
             f'cd "{nodes_dir}"']
    for status in statuses:
        lines.append(f"git clone {status.pack.url}")

    lines += ["", t("Or download each one as a zip and unpack it into that "
                    "folder, renaming the unpacked folder to the add-on's "
                    "name:"), ""]
    for status in statuses:
        lines.append(f"{status.pack.url}/archive/refs/heads/main.zip")
        lines.append(f"    -> {nodes_dir / status.pack.name}")

    lines += ["", t("Then restart ComfyUI.")]
    return "\n".join(lines)


class Installer:
    """Clones the missing packs. One object per attempt."""

    def __init__(self, nodes_dir: Path, python: Path | None = None,
                 on_say=None, should_stop=None):
        self.nodes_dir = Path(nodes_dir)
        self.python = Path(python) if python else None
        self._say_cb = on_say
        self._should_stop = should_stop or (lambda: False)
        self.report = InstallReport()

    def _say(self, message: str) -> None:
        print(f"[nodes] {message}")
        if self._say_cb:
            self._say_cb(message)

    def run(self, statuses: list[PackStatus]) -> InstallReport:
        # Note there is no "is git installed?" gate here any more. The zip
        # download below needs nothing but a network connection, and requiring
        # a developer tool to install an add-on made the installer useless to
        # exactly the people it is for.
        self.nodes_dir.mkdir(parents=True, exist_ok=True)
        for status in statuses:
            if self._should_stop():
                break
            pack = status.pack
            existing = folder_for(pack, self.nodes_dir)
            destination = existing or (self.nodes_dir / pack.name)

            if existing is not None:
                # Never overwrite. The user may have their own changes in
                # there, and a half-deleted add-on is worse than a missing one.
                self._say(t("{name} is already here, as {folder}",
                            name=pack.name, folder=existing.name))
                self.report.skipped.append(pack.name)
                continue

            self._say(t("Fetching {name}…", name=pack.name))
            try:
                self._fetch(pack, destination)
                self._requirements(destination)
                self.report.done.append(pack.name)
                self.report.commits[pack.name] = self._head(destination)
                self._say(t("{name} installed", name=pack.name))
            except (subprocess.SubprocessError, OSError,
                    requests.RequestException, zipfile.BadZipFile) as e:
                # One awkward add-on must not sink the rest, and a half-cloned
                # folder would look installed forever after.
                shutil.rmtree(destination, ignore_errors=True)
                self._say(t("{name} could not be installed: {problem}",
                            name=pack.name, problem=e))
                self.report.failed.append(f"{pack.name}: {e}")
        return self.report

    # -- getting the files -------------------------------------------------
    def _fetch(self, pack: Pack, destination: Path) -> None:
        """Download the add-on, by whichever route works.

        The zip first, because it needs nothing installed. Requiring git to
        install a ComfyUI add-on is a fine thing to ask of a developer and a
        dead end for everybody else - which is most of the people this
        installer exists for.

        git stays as the fallback: it copes with a repository whose default
        branch is neither main nor master, and with a network that blocks
        codeload but not the git protocol.
        """
        try:
            self._download_zip(pack, destination)
            return
        except (requests.RequestException, zipfile.BadZipFile, OSError) as e:
            shutil.rmtree(destination, ignore_errors=True)
            if not git_available():
                raise
            self._say(t("  the download did not work ({problem}); trying git",
                        problem=e))

        self._clone(pack, destination)

    def _download_zip(self, pack: Pack, destination: Path) -> None:
        """GitHub's source archive for a branch, unpacked into place.

        The archive holds one top-level folder named "<repo>-<branch>", so
        everything is moved out of it - ComfyUI looks for the node's __init__.py
        directly inside the custom_nodes entry, not one level down.
        """
        last: Exception | None = None
        for branch in _BRANCHES:
            url = f"{pack.url.rstrip('/')}/archive/refs/heads/{branch}.zip"
            try:
                response = requests.get(url, timeout=_ZIP_TIMEOUT)
                response.raise_for_status()
            except requests.RequestException as e:
                last = e
                continue

            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                self._extract(archive, destination)
            return

        raise last or requests.RequestException("no branch could be downloaded")

    @staticmethod
    def _extract(archive: zipfile.ZipFile, destination: Path) -> None:
        """Unpack, stripping the archive's single wrapping folder.

        Paths are checked before anything is written. A zip can name
        "../../somewhere" and unpacking it blindly writes outside the folder -
        this one comes from GitHub, but a downloader that trusts an archive is
        a downloader that will one day be pointed somewhere else.
        """
        destination.mkdir(parents=True, exist_ok=True)
        root = destination.resolve()

        for member in archive.infolist():
            parts = Path(member.filename).parts
            if len(parts) < 2:
                continue                      # the wrapping folder itself
            target = (destination / Path(*parts[1:])).resolve()
            if not str(target).startswith(str(root)):
                raise OSError(f"the archive tried to write outside the folder: "
                              f"{member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)

    # -- git ---------------------------------------------------------------
    def _clone(self, pack: Pack, destination: Path) -> None:
        """A shallow clone of the default branch.

        Depth 1 because these repos carry years of history for files nobody
        here reads, and the Director pack alone is a few hundred kilobytes of
        Python behind a much larger log.
        """
        self._git(self.nodes_dir, "clone", "--depth", "1", pack.url, pack.name)

    def _head(self, folder: Path) -> str:
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=str(folder),
                capture_output=True, text=True, timeout=30,
                creationflags=_NO_WINDOW)
            return out.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return ""

    def _git(self, cwd: Path, *args: str) -> None:
        result = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            timeout=900, creationflags=_NO_WINDOW)
        if result.returncode != 0:
            raise subprocess.SubprocessError(
                (result.stderr or result.stdout or "git failed").strip()
                .splitlines()[-1])

    def _requirements(self, folder: Path) -> None:
        """Install an add-on's own Python packages, into ComfyUI's Python.

        Into the *embedded* interpreter, not the one running this program: the
        add-on is imported by ComfyUI, so that is the environment that has to
        have them. The Director declares no third-party dependencies, so this is
        usually a no-op - but Spectrum may not stay that way.
        """
        req = folder / "requirements.txt"
        if not req.is_file():
            return
        if self.python is None:
            self.report.failed.append(t(
                "{name} needs extra Python packages, but ComfyUI's own Python "
                "could not be found. Install them by hand with:\n"
                "  pip install -r \"{path}\"", name=folder.name, path=req))
            return
        self._say(t("Installing its Python packages…"))
        subprocess.run([str(self.python), "-m", "pip", "install", "-r", str(req)],
                       capture_output=True, timeout=1800,
                       creationflags=_NO_WINDOW)


def update(nodes_dir: Path, pack_name: str) -> str:
    """git pull one add-on. Returns a short message for the user."""
    pack = next((p for p in REQUIRED if p.name == pack_name), None)
    folder = folder_for(pack, nodes_dir) if pack else Path(nodes_dir) / pack_name
    if folder is None or not (folder / ".git").is_dir():
        return t("{name} was not installed by this program, so it is left "
                 "alone.", name=pack_name)
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"], cwd=str(folder), capture_output=True,
            text=True, timeout=900, creationflags=_NO_WINDOW)
    except (subprocess.SubprocessError, OSError) as e:
        return t("{name} could not be updated: {problem}",
                 name=pack_name, problem=e)
    if result.returncode != 0:
        return t("{name} could not be updated: {problem}", name=pack_name,
                 problem=(result.stderr or "").strip().splitlines()[-1:] or "")
    if "Already up to date" in result.stdout:
        return t("{name} is already the latest version.", name=pack_name)
    return t("{name} was updated. Restart the AI engine to use it.",
             name=pack_name)
