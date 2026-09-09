"""Background workers, so the window never freezes.

Two QThreads, both of which only ever talk to the interface through signals:

* :class:`EngineWorker` - probes for ComfyUI and starts it if needed, so the
  splash screen can report progress instead of the app hanging on launch.
* :class:`JobWorker` - runs one render: upload the reference pictures, patch the
  graph, queue it, follow the websocket, save what comes back.

Adapted from EasyAI's app/jobs.py. The shape is the same; what changed is the
middle step. EasyAI patches an arbitrary graph through a manifest of bindings,
where this builds one known graph from a storyboard - see app/h3/graph.py.

Nothing here touches a widget directly.
"""
from __future__ import annotations

import copy
import datetime as _dt
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.comfy.client import ComfyClient, ComfyError, PromptRejected
from app.comfy.launcher import ComfyLauncher
from app.h3 import graph as h3graph
from app.h3 import preflight, recipe
from app.h3.presets import Profile, TURBO
from app.h3.timeline import Storyboard
from app.i18n import N, t
from app.llm.ollama import Ollama, free_vram

#: Rows in ComfyUI's history "outputs" that can hold a finished video. SaveVideo
#: has moved between these across versions, and collect_results falls back to
#: anything unlisted, so a new row name cannot make a finished render vanish.
OUTPUT_ROWS = ("videos", "gifs", "images")


@dataclass(frozen=True)
class FreeLlm:
    """Where to go and how long to wait to get the graphics card back."""
    url: str
    wait: int = 30


@dataclass
class GenerationRequest:
    """Everything the user chose for one render."""
    storyboard: Storyboard
    profile: Profile = TURBO
    #: None means "pick a fresh one".
    seed: int | None = None
    #: Replacement model filenames, keyed "class_type/input".
    overrides: dict[str, str] = field(default_factory=dict)
    #: What ModelAttentionBackend accepts on this server, so Turbo can degrade
    #: instead of having the whole prompt rejected.
    attention_options: list[str] | None = None
    #: Filled in by the worker after uploading, keyed by subject slot.
    uploaded: dict[int, str] = field(default_factory=dict)
    #: The voice clip's ComfyUI-side name, filled in by the worker.
    uploaded_voice: str = ""
    #: Whether this ComfyUI has the add-on's last-frame saver.
    can_save_last_frame: bool = False
    #: Where the language model lives, when it should be evicted before this
    #: render. None means "there is nothing to free" - no Ollama configured, or
    #: the user turned it off - and the whole step is then skipped.
    free_llm: FreeLlm | None = None

    @property
    def prompt(self) -> str:
        """What the queue shows for this item."""
        text = self.storyboard.global_prompt.strip()
        if text:
            return text
        for shot in self.storyboard.shots:
            if shot.prompt.strip():
                return shot.prompt.strip()
        return ""


@dataclass
class JobResult:
    """What one finished render produced."""
    files: list[Path] = field(default_factory=list)
    seed: int | None = None
    width: int = 0
    height: int = 0
    frames: int = 0
    profile: str = ""
    elapsed: float = 0.0
    #: The still pulled off the end of the clip, when there is one.
    last_frame: Path | None = None
    #: The settings file written beside the video.
    recipe: Path | None = None
    #: Things the run could not honour - an attention backend this ComfyUI does
    #: not have, for instance. Shown to the user, because silently producing
    #: something other than what was asked for is the worst way to fail.
    notes: list[str] = field(default_factory=list)


class EngineWorker(QThread):
    """Gets ComfyUI running without blocking the interface."""

    status = Signal(str)
    done = Signal(bool, str)      # ok, message

    def __init__(self, launcher: ComfyLauncher, auto_launch: bool, parent=None):
        super().__init__(parent)
        self.launcher = launcher
        self.auto_launch = auto_launch
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            result = self.launcher.ensure_running(
                auto_launch=self.auto_launch,
                on_status=self.status.emit,
                should_stop=lambda: self._cancelled,
            )
            self.done.emit(result.ok, result.message)
        except Exception as e:                  # never let a thread die silently
            traceback.print_exc()
            self.done.emit(False, f"Could not start the AI engine:\n{e}")


class JobWorker(QThread):
    """Runs a single render end to end."""

    #: percent (0-100), message
    progress = Signal(int, str)
    #: raw JPEG/PNG bytes of ComfyUI's live preview
    preview = Signal(bytes)
    #: one finished file, emitted as soon as it lands so the gallery fills in
    file_ready = Signal(str)
    finished_ok = Signal(object)      # JobResult
    failed = Signal(str)

    def __init__(self, client: ComfyClient, graph: dict,
                 request: GenerationRequest, output_dir: Path,
                 timeout: int = 5400, parent=None):
        super().__init__(parent)
        self.client = client
        self.graph = graph
        self.request = request
        self.output_dir = Path(output_dir)
        self.timeout = timeout
        self._cancelled = False
        self._prompt_id: str | None = None
        self._notes: list[str] = []
        self._started = _dt.datetime.now()

    # -- control -----------------------------------------------------------
    def cancel(self) -> None:
        """Ask ComfyUI to stop, then unwind."""
        self._cancelled = True
        if self._prompt_id:
            self.client.interrupt()

    #: The one failure message that is not an error. Both ends translate this
    #: same constant rather than comparing two separately written sentences.
    CANCELLED = N("Cancelled.")

    def _stopped(self) -> bool:
        return self._cancelled

    # -- the run -----------------------------------------------------------
    def run(self) -> None:
        started = self._started = _dt.datetime.now()
        try:
            result = self._generate()
            if self._cancelled:
                self.failed.emit(t(self.CANCELLED))
                return
            result.elapsed = (_dt.datetime.now() - started).total_seconds()
            self.finished_ok.emit(result)
        except PromptRejected as e:
            # Already phrased for a human by the client.
            self.failed.emit(str(e))
        except (ComfyError, h3graph.GraphError) as e:
            self.failed.emit(str(e))
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(t("Something went wrong:\n{problem}", problem=e))

    def _generate(self) -> JobResult:
        storyboard = self.request.storyboard

        # 1. Upload the reference pictures first, so the timeline can name them
        #    the way ComfyUI will see them rather than by a path on this machine.
        self.progress.emit(0, t("Getting things ready…"))
        slots = storyboard.active_subjects()
        for index, (slot, subject) in enumerate(slots, start=1):
            if self._stopped():
                return JobResult()
            if len(slots) > 1:
                self.progress.emit(2, t("Sending picture {n} of {total}…",
                                        n=index, total=len(slots)))
            else:
                self.progress.emit(2, t("Sending your picture…"))
            self.request.uploaded[slot] = self.client.upload_file(subject.image)

        if storyboard.voice.active and not self._stopped():
            self.progress.emit(2, t("Sending the voice clip…"))
            self.request.uploaded_voice = self.client.upload_file(
                storyboard.voice.audio)

        # 2. Patch a copy of the graph. The model swaps go on first, so the
        #    turbo LoRA's own filename can be one of them.
        #    A deep copy, not a shallow one: apply_overrides writes into each
        #    node's "inputs" dict, and a shallow copy shares those with the
        #    workflow the window holds - so one override would follow every
        #    later render for the rest of the session.
        graph = preflight.apply_overrides(
            copy.deepcopy(self.graph), self.request.overrides)
        graph, report = h3graph.apply(
            graph, storyboard, self.request.profile,
            uploaded=self.request.uploaded,
            uploaded_voice=self.request.uploaded_voice,
            seed=self.request.seed,
            attention_options=self.request.attention_options,
            save_last_frame=self.request.can_save_last_frame,
        )
        self._notes = list(report.notes)
        for note in report.notes:
            print(f"[job] note: {note}")

        # 3. Get the graphics card back before asking for twenty gigabytes of
        #    it. The language model is asked to leave and then *watched* until
        #    it has - see app/llm/ollama.free_vram. This never fails a render:
        #    if the memory does not clear, the run goes ahead with a note.
        if self.request.free_llm is not None and not self._stopped():
            self.progress.emit(3, t("Freeing graphics memory…"))
            freed, note = free_vram(
                Ollama(self.request.free_llm.url),
                wait=self.request.free_llm.wait,
                on_status=lambda message: self.progress.emit(3, message))
            if not freed and note:
                print(f"[job] {note}")
                self._notes.append(note)

        # 4. Queue it.
        if self._stopped():
            return JobResult()
        self.progress.emit(4, t("Sending to the AI engine…"))
        prompt_id, client_id = self.client.queue(graph)
        self._prompt_id = prompt_id

        # 5. Follow along. The websocket is for responsiveness only - the
        #    history poll below decides whether we actually got anything.
        self.progress.emit(5, t("Starting…"))
        finished = False
        try:
            finished = self.client.listen(
                client_id, prompt_id,
                on_progress=lambda pct, msg: self.progress.emit(max(5, pct), msg),
                on_preview=self.preview.emit,
                should_stop=self._stopped,
                timeout=self.timeout,
            )
        except ImportError:
            print("[job] websocket-client not installed; falling back to polling")
        except ComfyError:
            raise
        except Exception as e:
            print(f"[job] websocket unavailable, polling instead: {e}")

        if self._stopped():
            return JobResult()

        # 6. Collect.
        self.progress.emit(96, t("Collecting the video…"))
        results = self.client.wait_for_results(
            prompt_id, want=OUTPUT_ROWS, finished=finished,
            timeout=self.timeout, should_stop=self._stopped)
        if self._stopped():
            return JobResult()
        if not results:
            raise ComfyError(t(
                "The AI engine finished but produced no video.\n\n"
                "It may have run out of graphics memory. Try a shorter clip or "
                "a smaller size."))

        # The video and the last frame both come back in the same list, and in
        # no guaranteed order, so they are named by what they are rather than
        # by the position ComfyUI happened to list them in.
        stem = _timestamp_stem(self.request.profile.key)
        videos = [r for r in results if _is_video(r.filename)]
        stills = [r for r in results if not _is_video(r.filename)]

        saved: list[Path] = []
        last_frame: Path | None = None
        ordered = videos + stills
        for i, item in enumerate(ordered):
            if self._stopped():
                break
            self.progress.emit(96 + int(3 * (i + 1) / len(ordered)),
                               t("Saving {n} of {total}…", n=i + 1,
                                 total=len(ordered)))
            if _is_video(item.filename):
                name = stem if len(videos) == 1 else f"{stem}_{i + 1:02d}"
            else:
                name = f"{stem}_lastframe"
            path = self.client.save_result(item, self.output_dir, stem=name)
            if _is_video(item.filename):
                saved.append(path)
            else:
                last_frame = path
            self.file_ready.emit(str(path))

        if not last_frame and self.request.can_save_last_frame:
            self._notes.append(t(
                "The last frame did not come back from this render."))

        # -- the settings, beside the video ------------------------------
        written = None
        if saved:
            self.progress.emit(99, t("Saving the settings…"))
            written = recipe.save(
                recipe.path_for(saved[0]),
                recipe.build(storyboard, self.request.profile,
                             seed=report.seed, frames=report.frames,
                             elapsed=(_dt.datetime.now() - self._started
                                      ).total_seconds(),
                             notes=list(self._notes),
                             video=saved[0].name,
                             last_frame=last_frame.name if last_frame else ""))

        self.progress.emit(100, t("Done"))
        return JobResult(files=saved, seed=report.seed,
                         width=report.width, height=report.height,
                         frames=report.frames, profile=report.profile,
                         last_frame=last_frame, recipe=written,
                         notes=list(self._notes))


#: Suffixes CreateVideo / SaveVideo can produce. Anything else in the results
#: is a still, which for this graph means the last frame.
VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv", ".mov", ".gif", ".avi")


def _is_video(filename: str) -> bool:
    return Path(filename).suffix.lower() in VIDEO_SUFFIXES


def _timestamp_stem(profile_key: str) -> str:
    """2026-08-24_153012_turbo - sorts by time, says which path made it."""
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in profile_key)
    return f"{stamp}_{safe}".strip("_")
