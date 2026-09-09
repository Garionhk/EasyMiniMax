"""Talking to a local Ollama, and getting it back out of the graphics card.

Two jobs that look unrelated and are not:

* **Describing things.** A vision model can look at a reference picture and write
  the sentence the user left blank, or turn "a fox in the snow" into a
  description H3 can actually work with.
* **Getting out of the way.** That same model is then sitting in VRAM, and H3
  needs twenty gigabytes of it. On a single card those two facts are the same
  problem, so the code that loads the model also owns putting it away.

Shaped like app/comfy/client.py on purpose: synchronous, Qt-free, built on
``requests``, and raising one exception type already phrased for a person. The
Qt layer wraps it in a thread (app/llm/worker.py).

The request shapes are copied from the Director add-on's own implementation
(``minimax_media.py``), which is the reference for what this server wants:
raw base64 in an ``images`` array on ``/api/generate``, ``stream: false``,
``think: false`` so a thinking model does not narrate, and ``num_predict`` as
the only thing that reliably stops a small model rambling.
"""
from __future__ import annotations

import base64
import io
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import requests

DEFAULT_URL = "http://127.0.0.1:11434"

#: Only a suggestion. Any vision model Ollama can serve will do.
SUGGESTED_MODEL = "qwen2.5vl:7b"

#: What the picture is scaled to before being sent. The Director's node uses the
#: same default: big enough to read a face, small enough not to spend a minute
#: on the upload.
MAX_IMAGE_PX = 768

#: Short, because every one of these runs while somebody is waiting and looking
#: at the window - and because a refused connection is not always instant. On
#: the Windows box this was written on, a plain socket connect to a closed
#: loopback port takes two full seconds to come back refused, so without a tight
#: timeout every probe on a machine with no Ollama would stall the window for
#: exactly as long as one with a slow model. The timeout does cap it: 0.6 s in,
#: 0.6 s out.
PROBE_TIMEOUT = 0.6

#: How long an "is it there?" answer is trusted before asking again. The probe
#: is cheap but not free, and it gets called from several places in one pass -
#: rebuilding the window, refreshing a button, starting a render.
ALIVE_CACHE_SECONDS = 20.0


class LlmError(Exception):
    """Something the user can act on, already phrased as a sentence."""


class Cancelled(Exception):
    """The user stopped a download. Not a failure."""


@dataclass(frozen=True)
class LoadedModel:
    name: str
    #: Bytes of VRAM, as Ollama reports them. 0 when it does not say.
    vram: int = 0

    @property
    def gigabytes(self) -> float:
        return round(self.vram / 1e9, 1)


def normalise_url(url: str) -> str:
    """'127.0.0.1:11434' -> 'http://127.0.0.1:11434', and no trailing slash."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return DEFAULT_URL
    if "://" not in url:
        url = "http://" + url
    return url


class Ollama:
    """One Ollama server."""

    def __init__(self, url: str = DEFAULT_URL, timeout: int = 120):
        self.url = normalise_url(url)
        self.timeout = timeout
        self.session = requests.Session()
        self._alive: tuple[float, bool] | None = None

    # -- asking it things --------------------------------------------------
    def is_alive(self, timeout: float = PROBE_TIMEOUT, fresh: bool = False) -> bool:
        """Is there a server there at all?

        Deliberately cheap and deliberately quiet. This decides whether the whole
        feature appears, so on the very common machine with no Ollama it has to
        cost almost nothing and say nothing at all.

        The answer is remembered briefly - see ALIVE_CACHE_SECONDS. Pass
        ``fresh`` when the user has just changed the address or pressed Test,
        where a stale "no" would be infuriating.
        """
        if not fresh and self._alive is not None:
            when, answer = self._alive
            if time.monotonic() - when < ALIVE_CACHE_SECONDS:
                return answer
        try:
            r = self.session.get(f"{self.url}/api/tags", timeout=timeout)
            answer = r.status_code == 200
        except requests.RequestException:
            answer = False
        self._alive = (time.monotonic(), answer)
        return answer

    def models(self) -> list[str]:
        """Every model this server has, for the chooser.

        Offered as a list rather than a text box because a mistyped model name
        fails at generate time with a message about a manifest, which is not
        something anyone should have to decode.
        """
        try:
            r = self.session.get(f"{self.url}/api/tags", timeout=PROBE_TIMEOUT * 2)
            r.raise_for_status()
            entries = (r.json() or {}).get("models") or []
        except (requests.RequestException, ValueError) as e:
            raise LlmError(f"Could not ask {self.url} what models it has: {e}") from e
        return sorted(str(m.get("name", "")) for m in entries if m.get("name"))

    def loaded(self, timeout: float = PROBE_TIMEOUT * 2) -> list[LoadedModel]:
        """What is in the graphics card right now.

        This is the only honest answer to "has it let go yet?" - asking a model
        to unload returns immediately whether or not anything happened.
        """
        try:
            r = self.session.get(f"{self.url}/api/ps", timeout=timeout)
            r.raise_for_status()
            entries = (r.json() or {}).get("models") or []
        except (requests.RequestException, ValueError):
            # Unreachable or unreadable. "Nothing loaded" is the safe answer:
            # it lets a render carry on rather than blocking on a server that
            # is not going to answer.
            return []
        return [LoadedModel(name=str(m.get("name") or m.get("model") or ""),
                            vram=int(m.get("size_vram") or 0))
                for m in entries]

    # -- using it ----------------------------------------------------------
    def generate(self, model: str, prompt: str, *, system: str = "",
                 images: list[str] | None = None, max_words: int = 0,
                 timeout: int | None = None) -> str:
        """One round-trip. Returns the answer, with any thinking preamble gone."""
        if not model:
            raise LlmError("No model chosen. Pick one in Settings.")

        payload: dict = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            # Drop the model the moment it has answered. The whole point is to
            # not be holding VRAM when H3 starts; free_vram below is the
            # belt-and-braces for when this is not honoured.
            "keep_alive": 0,
            # Honoured by thinking-capable models, harmless on the rest.
            "think": False,
        }
        if system:
            payload["system"] = system
        if images:
            payload["images"] = images
        if max_words:
            # Roughly 1.4 tokens a word, plus a little slack. Asking for a word
            # count in the prompt itself does not work on small models.
            payload["options"] = {"num_predict": int(max_words * 2)}

        try:
            r = self.session.post(f"{self.url}/api/generate", json=payload,
                                  timeout=timeout or self.timeout)
        except requests.Timeout as e:
            raise LlmError(
                f"{model} took longer than {timeout or self.timeout} seconds to "
                f"answer. A smaller model, or a longer wait in Settings, would "
                f"help.") from e
        except requests.RequestException as e:
            raise LlmError(f"Could not reach {self.url}: {e}") from e

        if r.status_code == 404:
            raise LlmError(
                f"{self.url} has no model called “{model}”.\n\n"
                f"Pull it first:  ollama pull {model}")
        if r.status_code != 200:
            raise LlmError(f"{self.url} answered {r.status_code}: "
                           f"{r.text.strip()[:200]}")

        try:
            body = r.json()
        except ValueError as e:
            raise LlmError(f"{self.url} sent something that was not JSON.") from e

        text = (body.get("response") or "").strip()
        if not text:
            # A thinking model that spent its whole budget thinking.
            text = (body.get("thinking") or "").strip()
        if not text:
            raise LlmError(f"{model} answered with nothing at all. It may not be "
                           f"a vision model, or the picture may be too large.")
        return text

    # -- putting it away ---------------------------------------------------
    def unload(self, model: str) -> bool:
        """Ask for one model to be dropped. Never raises."""
        if not model:
            return False
        try:
            self.session.post(f"{self.url}/api/generate",
                              json={"model": model, "keep_alive": 0}, timeout=10)
            return True
        except requests.RequestException:
            return False


#: Name fragments that mean "this model can look at a picture". Not exhaustive
#: and cannot be - it is a guess used only to pre-select something sensible, and
#: the user can pick any model in Settings.
VISION_HINTS = ("vl", "vision", "llava", "moondream", "gemma3", "minicpm-v")


def best_vision_model(names: list[str]) -> str:
    """The most likely vision model in a list, or "" if none looks like one.

    A text-only model produces confident nonsense when handed a picture rather
    than an error, so guessing wrong is worse than not guessing - hence the
    empty string rather than "just use the first one".
    """
    for name in names:
        if any(hint in name.lower() for hint in VISION_HINTS):
            return name
    return ""


def detect(client: "Ollama") -> str:
    """Is there a usable prompt helper here? Returns the model, or "".

    Run once, the first time the program starts, so somebody who already has
    Ollama and a vision model gets the feature without going to look for it -
    and somebody who does not pays one probe, once, and never again.
    """
    if not client.is_alive(fresh=True):
        return ""
    try:
        return best_vision_model(client.models())
    except LlmError:
        return ""


def encode_image(path: Path | str, max_px: int = MAX_IMAGE_PX) -> str:
    """A picture as the base64 JPEG Ollama wants in its ``images`` array.

    Scaled down first. A 24-megapixel photo tells the model nothing a 768 px one
    does not, and sending it whole turns a two-second answer into a minute of
    upload and preprocessing.
    """
    try:
        from PIL import Image
    except ImportError as e:              # pragma: no cover - Pillow is a dep
        raise LlmError("Pillow is not installed, so pictures cannot be sent.") from e

    path = Path(path)
    if not path.is_file():
        raise LlmError(f"That picture is no longer there:\n{path}")

    try:
        with Image.open(path) as picture:
            picture = picture.convert("RGB")
            picture.thumbnail((max_px, max_px), Image.LANCZOS)
            buffer = io.BytesIO()
            picture.save(buffer, format="JPEG", quality=85)
    except OSError as e:
        raise LlmError(f"{path.name} could not be read as a picture: {e}") from e

    return base64.b64encode(buffer.getvalue()).decode("ascii")


def free_vram(client: Ollama, wait: int = 30, on_status=None,
              poll: float = 0.5) -> tuple[bool, str]:
    """Ask Ollama to let go of the graphics card, then wait until it has.

    ``keep_alive: 0`` on the generate call is a request, not a guarantee. A call
    that errored before the option was honoured, or a server with its own TTL,
    leaves the model resident - so it is asked again explicitly and then
    *watched*. H3 needs around twenty gigabytes, and starting to allocate while
    a vision model still holds memory is the difference between a render and a
    crash.

    Everything loaded is unloaded, not only the model this program used: another
    program's model sitting in the card is exactly the same problem.

    Returns (freed, note). Never raises, and never blocks for longer than
    ``wait`` - a render must not be refused because memory could not be freed,
    only warned about.
    """
    if not client.is_alive():
        # No server, nothing held, nothing to say. This is the common case and
        # it has to be free.
        return True, ""

    resident = client.loaded()
    if not resident:
        return True, ""

    names = ", ".join(m.name for m in resident if m.name)
    held = sum(m.vram for m in resident)
    if on_status:
        on_status(f"Freeing graphics memory ({names})…")

    for model in resident:
        client.unload(model.name)

    deadline = time.monotonic() + max(0, wait)
    while time.monotonic() < deadline:
        if not client.loaded():
            return True, ""
        time.sleep(poll)

    # Out of time. Say so and let the render go ahead: it may still work, and
    # refusing to start would be worse than a slow start.
    still = client.loaded()
    if not still:
        return True, ""
    return False, (
        f"The language model ({', '.join(m.name for m in still if m.name)}) was "
        f"still holding about {held / 1e9:.0f} GB of graphics memory after "
        f"{wait} seconds. The video may run slowly or run out of memory. "
        f"Closing Ollama frees it immediately.")


# -- installing and fetching models ---------------------------------------

#: Where the official installer puts things. It needs no administrator and so
#: installs per-user, which is why this is not in Program Files.
_OLLAMA_LOCATIONS = (
    Path.home() / "AppData/Local/Programs/Ollama/ollama.exe",
    Path("C:/Program Files/Ollama/ollama.exe"),
)


def find_ollama_exe() -> Path | None:
    """The ollama command, wherever it ended up.

    PATH first, because that is what a normal install leaves. The fixed
    locations cover the gap right after a silent install, when PATH has been
    written but this process inherited the old one.
    """
    found = shutil.which("ollama")
    if found:
        return Path(found)
    for candidate in _OLLAMA_LOCATIONS:
        if candidate.is_file():
            return candidate
    return None


@dataclass
class PullProgress:
    """One line of Ollama's pull stream."""
    status: str = ""
    completed: int = 0
    total: int = 0

    @property
    def fraction(self) -> float:
        return self.completed / self.total if self.total else 0.0

    @property
    def percent(self) -> int:
        return int(self.fraction * 100)


def pull(client: "Ollama", model: str, on_progress=None,
         should_stop=None, timeout: int = 7200) -> None:
    """Fetch a model, reporting real byte counts as it goes.

    Streamed NDJSON rather than the `ollama pull` command, for two reasons: the
    numbers are real, so a six-gigabyte download gets an honest progress bar
    instead of a spinner; and there is no console window to hide.

    Ollama resumes a partial pull by itself, so a cancelled download is not
    wasted - which is the only reason it is acceptable to let someone stop one.

    Raises LlmError with something a person can act on.
    """
    should_stop = should_stop or (lambda: False)
    try:
        response = client.session.post(
            f"{client.url}/api/pull",
            json={"model": model, "stream": True},
            stream=True, timeout=(10, timeout))
    except requests.RequestException as e:
        raise LlmError(f"Could not reach {client.url} to fetch {model}: {e}") from e

    if response.status_code != 200:
        raise LlmError(f"{client.url} refused to fetch {model}: "
                       f"{response.status_code} {response.text.strip()[:200]}")

    saw_success = False
    try:
        for line in response.iter_lines(decode_unicode=True):
            if should_stop():
                raise Cancelled(model)
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue

            # An error arrives as a field on a 200 response, not as a status
            # code - so a pull that fails looks exactly like one that worked
            # unless this is checked.
            if message.get("error"):
                raise LlmError(f"{model} could not be fetched: {message['error']}")

            status = str(message.get("status") or "")
            if status == "success":
                saw_success = True
            if on_progress:
                on_progress(PullProgress(
                    status=status,
                    completed=int(message.get("completed") or 0),
                    total=int(message.get("total") or 0)))
    except requests.RequestException as e:
        raise LlmError(f"The download of {model} was interrupted: {e}") from e
    finally:
        response.close()

    if not saw_success:
        # The stream ended without Ollama saying it finished. Treating that as
        # done would leave a half-pulled model looking installed.
        raise LlmError(f"The download of {model} ended early. Ollama resumes "
                       f"where it stopped, so running this again will carry on.")
