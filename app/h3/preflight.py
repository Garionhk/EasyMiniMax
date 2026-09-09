"""Will this actually run here? Asked before the user presses Create.

Two separate questions, because they have very different answers:

* **Add-ons** - a missing custom node is a small download, and the program
  offers to do it (app.setup.nodes).
* **Models** - a missing MiniMax H3 checkpoint is twenty gigabytes. Offering to
  fetch that behind a progress bar is not a kindness; it is a decision the user
  should make with their own eyes on the size. So this only ever names the file
  and says where it comes from.

Both are read out of ``GET /object_info``, which lists every node type the
running ComfyUI knows and, for loader nodes, the exact filenames it can see.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.comfy.objectinfo import Capabilities
from app.h3.graph import ATTENTION, resolve
from app.i18n import t

#: Where these files come from, for the message when one is missing.
MODEL_SOURCE = "https://huggingface.co/Comfy-Org/MiniMax-H3"

#: Loader inputs that name a file. Everything else in the graph is a number.
MODEL_FIELDS = ("unet_name", "clip_name", "vae_name", "lora_name")

#: What each loader is *for*, in words a person can act on. Keyed by the role
#: name in app.h3.graph.Roles, because "unet_name on node 16" is not a sentence
#: anyone can do anything with.
ROLE_LABELS = {
    "unet_fl2va": "the main video model",
    "unet_ref2va": "the reference-picture model",
    "clip": "the text encoder",
    "vae_video": "the video decoder",
    "vae_audio": "the audio decoder",
    "turbo_lora": "the Turbo accelerator file",
}


@dataclass
class MissingModel:
    role: str
    filename: str
    class_type: str
    field_name: str
    node_id: str
    #: Files this ComfyUI does have in the same folder, best guesses first, so
    #: a user who downloaded a differently-named build can just pick it.
    alternatives: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return t(ROLE_LABELS.get(self.role, self.role))


@dataclass
class Preflight:
    reachable: bool = False
    #: Node types the graph needs that the server does not offer.
    unknown_nodes: list[str] = field(default_factory=list)
    missing_models: list[MissingModel] = field(default_factory=list)
    #: What ModelAttentionBackend actually accepts here, or None when it could
    #: not be read. Passed straight to graph.apply so Turbo can degrade rather
    #: than have the whole prompt rejected.
    attention_options: list[str] | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.reachable and not self.unknown_nodes and not self.missing_models


def _similarity(a: str, b: str) -> float:
    """Crude closeness of two model filenames, for suggesting a stand-in.

    Compares the words in the file's own name - "minimax", "h3", "fl2va",
    "int8" - and ignores the folder, because the same model sits under a
    different folder on every machine.
    """
    def words(name: str) -> set[str]:
        stem = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        for ch in "-_.":
            stem = stem.replace(ch, " ")
        return {w for w in stem.split() if w not in ("safetensors", "gguf", "sft")}

    left, right = words(a), words(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _alternatives(wanted: str, options: list[str]) -> list[str]:
    scored = [(o, _similarity(wanted, o)) for o in options]
    scored = [(o, s) for o, s in scored if s >= 0.3]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [o for o, _ in scored[:8]]


def _normalise(name: str) -> str:
    return name.replace("\\", "/").strip().lower()


def run(graph: dict, caps: Capabilities,
        overrides: dict[str, str] | None = None) -> Preflight:
    """Check one graph against one ComfyUI.

    ``overrides`` maps "class_type/input" to a replacement filename the user
    already picked, so a model that was settled once is not reported missing
    every launch.
    """
    result = Preflight(reachable=bool(caps and caps.available))
    if not result.reachable:
        result.error = caps.error if caps else t("ComfyUI is not answering.")
        return result

    overrides = overrides or {}
    roles = resolve(graph)
    role_of = {node_id: role for role, node_id in roles.__dict__.items() if node_id}

    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        if not caps.has_node(class_type):
            result.unknown_nodes.append(class_type)
            continue

        for field_name, value in (node.get("inputs") or {}).items():
            if field_name not in MODEL_FIELDS or not isinstance(value, str):
                continue
            options = caps.model_options(class_type, field_name)
            if options is None:
                continue
            wanted = overrides.get(f"{class_type}/{field_name}", value)
            if any(_normalise(o) == _normalise(wanted) for o in options):
                continue
            result.missing_models.append(MissingModel(
                role=role_of.get(node_id, class_type),
                filename=wanted,
                class_type=class_type,
                field_name=field_name,
                node_id=node_id,
                alternatives=_alternatives(wanted, options),
            ))

    result.attention_options = caps.enum_options(ATTENTION, "attention")
    return result


def apply_overrides(graph: dict, overrides: dict[str, str]) -> dict:
    """Swap in the model files the user picked instead.

    Written into the copy that gets sent, never back into the workflow file, so
    the shipped workflow stays exactly as it was authored.
    """
    if not overrides:
        return graph
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        for field_name in MODEL_FIELDS:
            key = f"{class_type}/{field_name}"
            if key in overrides and isinstance(
                    (node.get("inputs") or {}).get(field_name), str):
                node["inputs"][field_name] = overrides[key]
    return graph


def summarise(result: Preflight) -> str:
    """One paragraph for the status strip. Empty when everything is fine."""
    if not result.reachable:
        return result.error or t("The AI engine is not running.")
    if result.unknown_nodes:
        names = sorted(set(result.unknown_nodes))
        return t("This ComfyUI does not have: {nodes}", nodes=", ".join(names))
    if result.missing_models:
        first = result.missing_models[0]
        if len(result.missing_models) == 1:
            return t("Missing {label}: {file}",
                     label=first.label, file=first.filename)
        return t("Missing {label} and {n} other model files.",
                 label=first.label, n=len(result.missing_models) - 1)
    return ""
