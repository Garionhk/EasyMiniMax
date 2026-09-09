"""Answers "will this workflow actually run here?" before the user clicks Create.

A workflow that names a custom node the user never installed, or a model file
they never downloaded, fails deep inside ComfyUI and surfaces as a stack trace.
Here the same problem is caught up front and named: *"Needs add-on:
ComfyUI-MiniMaxH3-Director"* - and, for that one, offered as a button.

Copied from EasyAI with the workflow-scanning half removed: this app ships one
graph, so app.h3.preflight asks the questions instead of a generic checker.

Everything is derived from ``GET /object_info``, which lists every node type the
running ComfyUI knows about, and - for loader nodes - the exact list of model
files it can see:

    object_info["UnetLoaderGGUF"]["input"]["required"]["unet_name"][0]
      -> ["Flux 2\\flux-2-klein-9b-Q8_0.gguf", "QWEN\\...", ...]
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.comfy.client import ComfyClient, ComfyError

# class_type prefix/name -> the add-on that provides it. Used to turn
# "UnetLoaderGGUF is unknown" into "install ComfyUI-GGUF".
#: Order matters: the first needle that matches wins, so the Spectrum node -
#: which also begins with "MiniMax" once you squint - is listed before the
#: broad "MiniMaxH3" prefix that catches the Director's own five nodes.
NODE_PACKS: dict[str, str] = {
    "SpectrumApplyMiniMaxH3": "ComfyUI-Spectrum-MiniMax-H3",
    "MiniMaxH3": "ComfyUI-MiniMaxH3-Director",
    "VHS_": "ComfyUI-VideoHelperSuite",
    "KJ": "comfyui-kjnodes",
    "rgthree": "rgthree-comfy",
}

#: Loader inputs whose value is a filename picked from an enum. Checking these
#: catches "you never downloaded that model" before the job is queued.
MODEL_FIELDS = (
    "ckpt_name", "unet_name", "vae_name", "clip_name", "clip_name1", "clip_name2",
    "lora_name", "control_net_name", "model_name", "style_model_name",
    "gguf_name", "upscale_model_name", "text_encoder_name", "audio_encoder_name",
)


@dataclass
class Capabilities:
    """A snapshot of what the connected ComfyUI can do."""
    node_types: set[str] = field(default_factory=set)
    raw: dict = field(default_factory=dict)
    available: bool = False
    error: str = ""

    def has_node(self, class_type: str) -> bool:
        return class_type in self.node_types

    def enum_options(self, class_type: str, field_name: str) -> list[str] | None:
        """The list of values an input accepts, or None if it isn't a choice.

        ComfyUI writes these two ways. The old style puts the list first::

            "ckpt_name": [["a.safetensors", "b.safetensors"], {...}]

        The newer V3 style names the type and carries the list in the options::

            "aspect_ratio": ["COMBO", {"options": ["1:1 (Square)", ...]}]

        Only reading the first shape means V3 inputs look like free text, and
        the missing-model check quietly passes everything.
        """
        node = self.raw.get(class_type, {}).get("input", {})
        spec = node.get("required", {}).get(field_name)
        if spec is None:
            spec = node.get("optional", {}).get(field_name)
        if not isinstance(spec, list) or not spec:
            return None

        if isinstance(spec[0], list):
            return [str(x) for x in spec[0]]
        if len(spec) > 1 and isinstance(spec[1], dict):
            options = spec[1].get("options")
            if isinstance(options, list):
                return [str(x) for x in options]
        return None

    #: Kept as the old name because the pre-flight check reads like a question
    #: about models, not about enums.
    def model_options(self, class_type: str, field_name: str) -> list[str] | None:
        return self.enum_options(class_type, field_name)

    def is_optional_input(self, class_type: str, field_name: str) -> bool:
        """Can this input simply be left out of the graph?

        Auto-growing groups arrive as ``ref_images.ref_image_1`` - one entry per
        attached file, under a single optional group called ``ref_images`` - so
        the part before the dot is what to look up.
        """
        node = self.raw.get(class_type, {}).get("input", {})
        optional = node.get("optional") or {}
        required = node.get("required") or {}

        group = field_name.split(".", 1)[0]
        for name in (field_name, group):
            if name in optional:
                return True
            if name in required:
                return False
        # Unknown input: assume it matters. Dropping something the node needs
        # fails the whole run, while keeping something it doesn't costs nothing.
        return False


def pack_for(class_type: str) -> str:
    """Best guess at which add-on provides a node type."""
    for needle, pack in NODE_PACKS.items():
        if class_type.startswith(needle) or needle in class_type:
            return pack
    return ""


def fetch(client: ComfyClient, refresh: bool = False) -> Capabilities:
    """Read /object_info once and wrap it."""
    try:
        raw = client.object_info(refresh=refresh)
    except ComfyError as e:
        return Capabilities(available=False, error=str(e))
    return Capabilities(node_types=set(raw.keys()), raw=raw, available=True)


class FolderMap:
    """Which models folder each loader input reads from, asked of ComfyUI.

    A loader's option list is exactly one folder's listing, so comparing the
    two identifies the folder with no table to maintain and no chance of the
    unet / diffusion_models confusion that put nine models in folders ComfyUI
    never looks in.

    Both listings are fetched once and reused; /models/{folder} is a directory
    walk on the server and there are twenty-odd folders.
    """

    def __init__(self, client, caps: Capabilities):
        self.client = client
        self.caps = caps
        self._by_folder: dict[str, set[str]] = {}
        self._loaded = False

    def load(self) -> bool:
        if self._loaded:
            return bool(self._by_folder)
        for folder in self.client.model_folders():
            files = self.client.files_in_folder(folder)
            if files:
                self._by_folder[folder] = {_normalise(f) for f in files}
        self._loaded = True
        return bool(self._by_folder)

    def folder_for(self, class_type: str, field_name: str,
                   filename: str = "") -> str | None:
        """The folder a loader input feeds, or None if it cannot be settled."""
        self.load()
        if not self._by_folder:
            return None

        options = self.caps.enum_options(class_type, field_name)
        if options:
            wanted = {_normalise(o) for o in options}
            # An exact match is the loader's own folder. Several folders can
            # hold the same file, so the whole list has to agree, not one name.
            for folder, files in self._by_folder.items():
                if files == wanted:
                    return folder
            best, score = None, 0.0
            for folder, files in self._by_folder.items():
                if not files:
                    continue
                overlap = len(files & wanted) / len(wanted)
                if overlap > score:
                    best, score = folder, overlap
            if score >= 0.9:
                return best

        # No usable option list: fall back to whichever folder holds this file.
        if filename:
            target = _normalise(filename)
            holders = [f for f, files in self._by_folder.items() if target in files]
            if len(holders) == 1:
                return holders[0]
        return None


def _normalise(name: str) -> str:
    """Model paths differ only by slash direction between OSes and workflows."""
    return name.replace("\\", "/").strip().lower()


