"""Talks to a running ComfyUI over its HTTP + websocket API.

Ported from PhotoBooth-WEBUI.py:246-396, which had the sequence right:
upload -> POST /prompt -> websocket progress -> /history -> /view. Three things
are fixed here:

1. Binary websocket frames are decoded instead of dropped. They are ComfyUI's
   live preview images, which is exactly what a desktop app wants to show.
2. A rejected /prompt returns structured ``node_errors``; those are turned into
   a sentence a beginner can act on rather than a raw dict.
3. Results are read from images / gifs / audio / video output rows, so the same
   code serves all four modes.

Deliberately Qt-free so it can be exercised from a plain script.
"""
from __future__ import annotations

import json
import mimetypes
import os
import struct
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import requests

# Websocket event ids from ComfyUI's binary protocol.
_EVENT_PREVIEW_IMAGE = 1
_IMAGE_TYPE_JPEG = 1
_IMAGE_TYPE_PNG = 2

# Rows in a history "outputs" entry that can hold a result file.
_OUTPUT_ROWS = ("images", "gifs", "videos", "audio", "video")


class ComfyError(Exception):
    """Any failure talking to ComfyUI."""


class PromptRejected(ComfyError):
    """ComfyUI refused to queue the workflow (validation failed).

    ``missing_nodes`` and ``missing_models`` are pulled out of the node_errors
    payload so the UI can offer a real fix instead of an error dump.
    """

    def __init__(self, message: str, missing_nodes=None, missing_models=None, raw=None):
        super().__init__(message)
        self.missing_nodes = list(missing_nodes or [])
        self.missing_models = list(missing_models or [])
        self.raw = raw


@dataclass
class ResultFile:
    """One output file described by ComfyUI's history."""
    filename: str
    subfolder: str
    type: str          # "output" | "temp" | "input"
    kind: str          # which outputs row it came from: images / gifs / audio ...

    @property
    def suffix(self) -> str:
        return Path(self.filename).suffix.lower().lstrip(".")


def _friendly_prompt_error(payload: dict) -> PromptRejected:
    """Turn ComfyUI's validation response into something a beginner can act on."""
    missing_nodes: list[str] = []
    missing_models: list[str] = []
    lines: list[str] = []

    node_errors = payload.get("node_errors") or {}
    for node_id, info in node_errors.items():
        class_type = info.get("class_type") or "?"
        for err in info.get("errors") or []:
            etype = err.get("type", "")
            message = err.get("message", "")
            details = err.get("details", "")
            if etype == "value_not_in_list":
                # A model / file the workflow names is not installed.
                name = details.split("not in")[0].strip().strip("'\"") or details
                missing_models.append(name)
                lines.append(f"{class_type}: '{name}' is not installed")
            else:
                lines.append(f"{class_type} (node {node_id}): {message} {details}".strip())

    top = payload.get("error") or {}
    if top.get("type") == "invalid_prompt" or "does not exist" in str(top.get("message", "")):
        # Unknown class_type => the custom node pack isn't installed.
        detail = str(top.get("details") or top.get("message") or "")
        missing_nodes.append(detail)
        lines.append(f"Unknown node type: {detail}")
    elif top.get("message"):
        lines.append(str(top["message"]))

    if not lines:
        lines.append(json.dumps(payload)[:400])

    return PromptRejected(
        "ComfyUI would not accept this workflow:\n  - " + "\n  - ".join(lines),
        missing_nodes=missing_nodes,
        missing_models=missing_models,
        raw=payload,
    )


class ComfyClient:
    """A thin, synchronous client for one ComfyUI server."""

    def __init__(self, server: str = "127.0.0.1:8188", http_timeout: int = 60):
        self.server = server.strip().rstrip("/")
        self.http_timeout = http_timeout
        self.session = requests.Session()
        self._object_info: dict | None = None

    # -- addresses ---------------------------------------------------------
    @property
    def base_url(self) -> str:
        return f"http://{self.server}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.server}/ws"

    # -- liveness ----------------------------------------------------------
    def is_alive(self, timeout: float = 2.0) -> bool:
        """Cheap probe used by the launcher and the status bar."""
        try:
            r = self.session.get(f"{self.base_url}/system_stats", timeout=timeout)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def system_stats(self) -> dict:
        r = self.session.get(f"{self.base_url}/system_stats", timeout=self.http_timeout)
        r.raise_for_status()
        return r.json()

    # -- capability probe --------------------------------------------------
    def object_info(self, refresh: bool = False) -> dict:
        """The full node catalogue. Large, so it is cached per client."""
        if self._object_info is None or refresh:
            try:
                r = self.session.get(f"{self.base_url}/object_info", timeout=120)
                r.raise_for_status()
                self._object_info = r.json()
            except requests.RequestException as e:
                raise ComfyError(f"Could not read the node list from ComfyUI: {e}") from e
        return self._object_info

    def model_folders(self) -> list[str]:
        """The folder names ComfyUI reads models from.

        These are folder_paths' own keys - "diffusion_models", "loras",
        "text_encoders" and so on - which is the only authoritative answer to
        "where does this file go?". The input field name cannot tell you:
        UNETLoader reads diffusion_models and UnetLoaderGGUF reads unet, and
        both call the field unet_name.
        """
        try:
            r = self.session.get(f"{self.base_url}/models", timeout=30)
            r.raise_for_status()
            found = r.json()
            return [str(x) for x in found] if isinstance(found, list) else []
        except (requests.RequestException, ValueError):
            return []

    def files_in_folder(self, folder: str) -> list[str]:
        """The filenames ComfyUI can currently see in one models folder."""
        try:
            r = self.session.get(f"{self.base_url}/models/{folder}", timeout=60)
            if r.status_code == 404:
                return []
            r.raise_for_status()
            found = r.json()
            return [str(x) for x in found] if isinstance(found, list) else []
        except (requests.RequestException, ValueError):
            return []

    def clear_cache(self) -> None:
        self._object_info = None

    # -- uploads -----------------------------------------------------------
    def upload_file(self, path: str | Path, subfolder: str = "") -> str:
        """Copy a local file into ComfyUI's input folder; return its reference name.

        Uploading rather than passing a local path is what lets ComfyUI run on a
        different machine. ComfyUI's /upload/image endpoint is what its own
        frontend uses for video and audio too, so one method covers every loader.
        """
        path = Path(path)
        if not path.is_file():
            raise ComfyError(f"File not found: {path}")

        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = {"overwrite": "true"}
        if subfolder:
            data["subfolder"] = subfolder
        try:
            with open(path, "rb") as f:
                r = self.session.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (path.name, f, mime)},
                    data=data,
                    timeout=max(self.http_timeout, 120),
                )
            r.raise_for_status()
        except requests.RequestException as e:
            raise ComfyError(f"Could not upload {path.name} to ComfyUI: {e}") from e

        info = r.json()
        name, sub = info.get("name", path.name), info.get("subfolder", "")
        return f"{sub}/{name}" if sub else name

    # -- queueing ----------------------------------------------------------
    def queue(self, graph: dict, client_id: str | None = None) -> tuple[str, str]:
        """Submit a workflow. Returns (prompt_id, client_id)."""
        client_id = client_id or str(uuid.uuid4())
        try:
            r = self.session.post(
                f"{self.base_url}/prompt",
                json={"prompt": graph, "client_id": client_id},
                timeout=self.http_timeout,
            )
        except requests.RequestException as e:
            raise ComfyError(f"Could not reach ComfyUI to start the job: {e}") from e

        try:
            payload = r.json()
        except ValueError:
            raise ComfyError(f"ComfyUI returned an unreadable response ({r.status_code}).")

        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise _friendly_prompt_error(payload)
        return prompt_id, client_id

    def interrupt(self) -> None:
        """Ask ComfyUI to abandon the running job."""
        try:
            self.session.post(f"{self.base_url}/interrupt", timeout=10)
        except requests.RequestException:
            pass

    # -- progress ----------------------------------------------------------
    def listen(
        self,
        client_id: str,
        prompt_id: str,
        on_progress: Callable[[int, str], None] | None = None,
        on_preview: Callable[[bytes], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        timeout: int = 1800,
    ) -> bool:
        """Follow one prompt on the websocket. True if it finished cleanly.

        Callers must tolerate this returning False or raising - the history poll
        is the source of truth, and this is only here for a responsive UI.
        """
        import websocket  # websocket-client, optional at runtime

        ws = websocket.WebSocket()
        ws.connect(f"{self.ws_url}?clientId={client_id}", timeout=10)
        ws.settimeout(5)
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                if should_stop and should_stop():
                    return False
                try:
                    msg = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue  # no traffic for 5s; loop so should_stop stays responsive

                if isinstance(msg, (bytes, bytearray)):
                    if on_preview:
                        payload = self._decode_preview(bytes(msg))
                        if payload:
                            on_preview(payload)
                    continue

                try:
                    data = json.loads(msg)
                except ValueError:
                    continue
                mtype, d = data.get("type"), data.get("data", {}) or {}

                if mtype == "progress":
                    mx = d.get("max") or 1
                    pct = int(d.get("value", 0) * 100 / mx)
                    if on_progress:
                        on_progress(pct, f"Generating… {pct}%")
                elif mtype == "execution_error" and d.get("prompt_id") == prompt_id:
                    raise ComfyError(
                        f"{d.get('node_type', 'A node')} failed: "
                        f"{d.get('exception_message', 'unknown error')}"
                    )
                elif mtype == "execution_interrupted" and d.get("prompt_id") == prompt_id:
                    return False
                elif mtype == "executing" and d.get("node") is None:
                    # node == None marks the end of this prompt.
                    if d.get("prompt_id") in (prompt_id, None):
                        if on_progress:
                            on_progress(100, "Saving…")
                        return True
            return False
        finally:
            try:
                ws.close()
            except Exception:
                pass

    @staticmethod
    def _decode_preview(msg: bytes) -> bytes | None:
        """Strip ComfyUI's 8-byte binary header off a preview frame.

        Layout: uint32 event id, uint32 image format, then the raw JPEG/PNG.
        """
        if len(msg) < 9:
            return None
        event, image_type = struct.unpack(">II", msg[:8])
        if event != _EVENT_PREVIEW_IMAGE or image_type not in (_IMAGE_TYPE_JPEG, _IMAGE_TYPE_PNG):
            return None
        return msg[8:]

    # -- results -----------------------------------------------------------
    def history(self, prompt_id: str) -> dict:
        try:
            r = self.session.get(f"{self.base_url}/history/{prompt_id}", timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise ComfyError(f"Could not read the job result: {e}") from e

    def collect_results(self, prompt_id: str, want: Iterable[str] | None = None) -> list[ResultFile]:
        """Pull every output file out of a finished prompt's history entry.

        ``want`` restricts which outputs rows are considered (see modes.py);
        anything not listed is still returned if nothing else matched, so an
        unexpected node type never produces a silent empty result.
        """
        entry = self.history(prompt_id).get(prompt_id)
        if not entry:
            return []

        rows = tuple(want) if want else _OUTPUT_ROWS
        preferred: list[ResultFile] = []
        fallback: list[ResultFile] = []

        for node_output in (entry.get("outputs") or {}).values():
            for row_name, items in node_output.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict) or "filename" not in item:
                        continue
                    rf = ResultFile(
                        filename=item["filename"],
                        subfolder=item.get("subfolder", ""),
                        type=item.get("type", "output"),
                        kind=row_name,
                    )
                    # ComfyUI writes intermediate frames as type "temp"; those are
                    # previews, not deliverables.
                    if rf.type == "temp":
                        continue
                    (preferred if row_name in rows else fallback).append(rf)

        return preferred or fallback

    def collect_text(self, prompt_id: str) -> list[str]:
        """Pull text results out of a finished prompt.

        Preview nodes report their value in the history as
        ``{"<node id>": {"text": ["..."]}}`` - plain strings, not files - so a
        text-producing workflow has nothing for collect_results to find.
        """
        entry = self.history(prompt_id).get(prompt_id)
        if not entry:
            return []

        found: list[str] = []
        for node_output in (entry.get("outputs") or {}).values():
            for row_name, items in node_output.items():
                if row_name not in ("text", "string") or not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, str) and item.strip():
                        found.append(item)
        return found

    def wait_for_text(
        self,
        prompt_id: str,
        finished: bool = False,
        timeout: int = 600,
        should_stop: Callable[[], bool] | None = None,
    ) -> list[str]:
        for _ in range(max(1, 20 if finished else timeout)):
            if should_stop and should_stop():
                return []
            found = self.collect_text(prompt_id)
            if found:
                return found
            time.sleep(1)
        return []

    def wait_for_results(
        self,
        prompt_id: str,
        want: Iterable[str] | None = None,
        finished: bool = False,
        timeout: int = 1800,
        should_stop: Callable[[], bool] | None = None,
    ) -> list[ResultFile]:
        """Poll /history until the outputs appear.

        When the websocket already reported completion we only need a few polls
        to read the filenames back; otherwise poll the full timeout.
        """
        attempts = 20 if finished else timeout
        for _ in range(max(1, attempts)):
            if should_stop and should_stop():
                return []
            results = self.collect_results(prompt_id, want)
            if results:
                return results
            time.sleep(1)
        return []

    def download(self, result: ResultFile) -> bytes:
        params = {
            "filename": result.filename,
            "subfolder": result.subfolder,
            "type": result.type,
        }
        try:
            r = self.session.get(f"{self.base_url}/view", params=params,
                                 timeout=max(self.http_timeout, 300))
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            raise ComfyError(f"Could not download {result.filename}: {e}") from e

    def save_result(self, result: ResultFile, dest_dir: str | Path,
                    stem: str | None = None) -> Path:
        """Download one result into dest_dir, never overwriting an existing file."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(result.filename).suffix or ".bin"
        name = (stem or Path(result.filename).stem) + suffix

        target = dest_dir / name
        counter = 1
        while target.exists():
            target = dest_dir / f"{Path(name).stem}_{counter}{suffix}"
            counter += 1

        data = self.download(result)
        tmp = target.with_suffix(target.suffix + ".part")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
        return target
