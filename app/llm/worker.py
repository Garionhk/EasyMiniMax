"""Threads, so a slow model never freezes the window.

Same shape as app/jobs.py: a QThread that only ever talks to the interface
through signals, and never lets an exception die silently inside itself.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.llm import prompts
from app.llm.ollama import LlmError, Ollama, encode_image, free_vram


class AnalyzeWorker(QThread):
    """One question to the vision model."""

    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, client: Ollama, model: str, system: str, prompt: str,
                 image: Path | str | None = None, max_words: int = 0,
                 parent=None):
        super().__init__(parent)
        self.client = client
        self.model = model
        self.system = system
        self.prompt = prompt
        self.image = image
        self.max_words = max_words

    def run(self) -> None:
        try:
            images = [encode_image(self.image)] if self.image else None
            answer = self.client.generate(
                self.model, self.prompt, system=self.system,
                images=images, max_words=self.max_words)
            self.finished_ok.emit(prompts.tidy(answer))
        except LlmError as e:
            self.failed.emit(str(e))
        except Exception as e:                  # never die silently
            import traceback
            traceback.print_exc()
            self.failed.emit(f"The prompt helper went wrong:\n{e}")


class FreeVramWorker(QThread):
    """Empties the graphics card, off the interface thread.

    Its own thread because it *waits* - up to the configured timeout - for the
    model to actually go. Doing that on the interface thread would freeze the
    window at exactly the moment the user is watching for something to happen.
    """

    status = Signal(str)
    done = Signal(bool, str)      # freed, note

    def __init__(self, client: Ollama, wait: int = 30, parent=None):
        super().__init__(parent)
        self.client = client
        self.wait = wait

    def run(self) -> None:
        try:
            freed, note = free_vram(self.client, wait=self.wait,
                                    on_status=self.status.emit)
            self.done.emit(freed, note)
        except Exception as e:                  # freeing must never be fatal
            import traceback
            traceback.print_exc()
            self.done.emit(False, f"Could not free the graphics memory:\n{e}")
