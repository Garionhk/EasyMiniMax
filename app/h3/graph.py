"""Writes one render's choices into a copy of the bundled workflow.

The app ships exactly one graph, so this could have been a handful of writes to
hard-coded node ids - the Director is "5", the turbo LoRA is "380:388". It is
not, for two reasons. Subgraph ids like "380:388" are assigned by ComfyUI when
the workflow is exported and change if the user ever re-exports it; and a role
resolved by class_type fails loudly with a sentence naming what is missing,
where a hard-coded id fails silently by writing into whatever now holds that id.

Two of the roles are genuinely ambiguous by class_type alone - there are two
UNETLoaders and two VAELoaders - so those are settled by *which Director input
they feed*, the same trick EasyAI's manifest autodetection uses. Getting the two
UNETs the wrong way round would load the reference checkpoint for a
text-to-video render, which produces a plausible-looking but quietly wrong
result: the worst kind.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field

from app.h3 import timeline
from app.h3.presets import SAFE_ATTENTION, Profile
from app.i18n import N, t

#: ComfyUI seeds are unsigned 64-bit, but most nodes validate against this.
MAX_SEED = 2 ** 53 - 1

DIRECTOR = "MiniMaxH3DirectorCS"
PREVIEW = "MiniMaxH3PreviewOverrideCS"
SPECTRUM = "SpectrumApplyMiniMaxH3"
ATTENTION = "ModelAttentionBackend"
LASTFRAME = "MiniMaxH3SaveLastFrameCS"

#: The id given to the last-frame saver when it is spliced in. Not a number, so
#: it can never collide with an id ComfyUI assigned on export.
LASTFRAME_ID = "easyminimax_lastframe"

#: Every class_type the bundled graph needs, mapped to whether the run can go
#: ahead without it. The Director and the samplers are the workflow; Spectrum
#: and the attention backend only matter on the Turbo path, and the app can
#: degrade rather than refuse.
ESSENTIAL = (DIRECTOR, PREVIEW, "UNETLoader", "CLIPLoader", "VAELoader",
             "BasicScheduler", "SamplerCustomAdvanced", "CreateVideo", "SaveVideo")


class GraphError(Exception):
    """The bundled workflow is not the shape this app knows how to drive."""


@dataclass
class Report:
    """What actually changed, and anything the run could not honour."""
    seed: int | None = None
    frames: int = 0
    width: int = 0
    height: int = 0
    profile: str = ""
    references: bool = False
    applied: list[str] = field(default_factory=list)
    #: Shown to the user after the render. Silently producing something other
    #: than what was asked for is the worst way to fail, so anything that got
    #: downgraded says so here.
    notes: list[str] = field(default_factory=list)


# -- finding the nodes -----------------------------------------------------

def _by_class(graph: dict, class_type: str) -> list[str]:
    return [nid for nid, node in graph.items()
            if isinstance(node, dict) and node.get("class_type") == class_type]


def _one(graph: dict, class_type: str) -> str | None:
    found = _by_class(graph, class_type)
    return found[0] if len(found) == 1 else None


def _source_of(graph: dict, node_id: str, input_name: str) -> str | None:
    """Which node feeds this input, or None if it holds a plain value.

    A wired input is stored as ["380:388", 0] - the node id and its output
    slot. That is also why nothing here ever overwrites a list: doing so would
    replace a connection with a literal and break the graph.
    """
    value = (graph.get(node_id) or {}).get("inputs", {}).get(input_name)
    if isinstance(value, list) and value:
        return str(value[0])
    return None


@dataclass
class Roles:
    """Every node this app writes to, found by what it is rather than its id."""
    director: str
    preview: str = ""
    scheduler: str = ""
    turbo_lora: str = ""
    spectrum: str = ""
    attention: str = ""
    noise: str = ""
    save: str = ""
    decode_video: str = ""
    create_video: str = ""
    unet_fl2va: str = ""
    unet_ref2va: str = ""
    vae_video: str = ""
    vae_audio: str = ""
    clip: str = ""


def resolve(graph: dict) -> Roles:
    """Work out which node plays which part in the bundled graph."""
    missing = [c for c in ESSENTIAL if not _by_class(graph, c)]
    if missing:
        raise GraphError(t(
            "This workflow file is missing {nodes}. Replace it with the copy "
            "that came with the program.", nodes=", ".join(missing)))

    director = _by_class(graph, DIRECTOR)[0]
    roles = Roles(director=director)
    roles.preview = _one(graph, PREVIEW) or ""
    roles.scheduler = _one(graph, "BasicScheduler") or ""
    roles.spectrum = _one(graph, SPECTRUM) or ""
    roles.attention = _one(graph, ATTENTION) or ""
    roles.noise = _one(graph, "RandomNoise") or ""
    roles.save = _one(graph, "SaveVideo") or ""
    roles.clip = _one(graph, "CLIPLoader") or ""
    roles.create_video = _one(graph, "CreateVideo") or ""
    # The picture decode, told from the sound one by what CreateVideo reads.
    roles.decode_video = _source_of(graph, roles.create_video, "images") or ""

    # The two model loaders are told apart by which Director input they feed.
    roles.unet_fl2va = _source_of(graph, director, "model") or ""
    roles.unet_ref2va = _source_of(graph, director, "model_ref2va") or ""
    # Same for the two VAEs: one decodes pictures, one decodes sound.
    roles.vae_video = _source_of(graph, director, "vae") or ""
    roles.vae_audio = _source_of(graph, director, "audio_vae") or ""

    # The turbo LoRA is the one whose filename says so. A graph with a second
    # LoRA - a style one, say - must not have its strength zeroed by the
    # Quality switch, which is why this is a name check and not "the only
    # LoraLoaderModelOnly".
    for nid in _by_class(graph, "LoraLoaderModelOnly"):
        name = str((graph[nid].get("inputs") or {}).get("lora_name", "")).lower()
        if "turbo" in name:
            roles.turbo_lora = nid
            break

    return roles


# -- writing ---------------------------------------------------------------

def _set(graph: dict, node_id: str, input_name: str, value,
         report: Report, label: str) -> bool:
    """Write one input, refusing to overwrite a connection."""
    if not node_id:
        return False
    node = graph.get(node_id)
    if not isinstance(node, dict):
        return False
    inputs = node.setdefault("inputs", {})
    if isinstance(inputs.get(input_name), list):
        # Wired from another node. Replacing it with a number would silently
        # cut the graph in half.
        return False
    inputs[input_name] = value
    report.applied.append(label)
    return True


def choose_attention(wanted: str, options: list[str] | None,
                     report: Report) -> str:
    """The attention backend to actually ask for.

    "comfy kitchen attention" only appears in ModelAttentionBackend's list when
    the comfy_kitchen int8 module is present, and it is known to throw an
    alignment error on int8 checkpoints - which is exactly what this workflow
    loads. Asking for a backend this ComfyUI does not offer gets the whole
    prompt rejected, so it is checked first and the run continues on the
    backend that is always there.
    """
    if not wanted or options is None or wanted in options:
        return wanted
    report.notes.append(t(
        "This ComfyUI does not offer “{wanted}”, so the standard "
        "“{fallback}” was used instead. The video is fine — Turbo is just a "
        "little slower than it could be.",
        wanted=wanted, fallback=SAFE_ATTENTION))
    return SAFE_ATTENTION


def apply(graph: dict, storyboard: timeline.Storyboard, profile: Profile, *,
          uploaded: dict[int, str] | None = None,
          uploaded_voice: str = "",
          seed: int | None = None,
          attention_options: list[str] | None = None,
          filename_prefix: str = "video/EasyMiniMax",
          save_last_frame: bool = False,
          ) -> tuple[dict, Report]:
    """Return a patched deep copy of the graph plus a report of what changed.

    The original is never touched, so a run that falls over part-way leaves
    nothing behind to confuse the next one.
    """
    patched = copy.deepcopy(graph)
    roles = resolve(patched)
    report = Report(profile=profile.key)

    storyboard.normalise()
    report.frames = storyboard.frames
    report.width, report.height = storyboard.width, storyboard.height
    report.references = storyboard.uses_references

    # -- the storyboard ---------------------------------------------------
    _set(patched, roles.director, "timeline_data",
         timeline.build(storyboard, uploaded, uploaded_voice), report, "timeline")

    # The audio track has to be switched on for the clip to be read at all. It
    # is already on in the bundled workflow, but a re-exported one may not be,
    # and a voice that is uploaded, named in the timeline and then ignored is
    # exactly the kind of silent nothing this program exists to avoid.
    if uploaded_voice:
        _set(patched, roles.director, "use_custom_audio", True, report, "audio_track")
    # Mirrored for anyone who opens the sent graph in ComfyUI. The node ignores
    # both of these - they exist so its editor has somewhere to show its state -
    # but leaving them stale would make the graph read as if it did something
    # other than what it does.
    _set(patched, roles.director, "local_prompts",
         storyboard.local_prompts(), report, "local_prompts")
    _set(patched, roles.director, "segment_lengths",
         storyboard.segment_lengths(), report, "segment_lengths")

    # -- the render window ------------------------------------------------
    # All six are written together. The node resolves the window from whichever
    # pair is set, so a seconds value left over from a previous length would
    # quietly win over the frame count.
    seconds = storyboard.seconds
    _set(patched, roles.director, "start_second", 0.0, report, "start_second")
    _set(patched, roles.director, "end_second", seconds, report, "end_second")
    _set(patched, roles.director, "duration_seconds", seconds, report, "duration_seconds")
    _set(patched, roles.director, "start_frame", 0, report, "start_frame")
    _set(patched, roles.director, "end_frame", storyboard.frames, report, "end_frame")
    _set(patched, roles.director, "duration_frames", storyboard.frames,
         report, "duration_frames")

    # -- the canvas -------------------------------------------------------
    _set(patched, roles.director, "custom_width", int(storyboard.width), report, "width")
    _set(patched, roles.director, "custom_height", int(storyboard.height), report, "height")

    # -- Turbo / Quality: four values that only make sense together -------
    _set(patched, roles.turbo_lora, "strength_model",
         float(profile.lora_strength), report, "turbo_lora")
    _set(patched, roles.scheduler, "steps", int(profile.steps), report, "steps")
    _set(patched, roles.spectrum, "enabled", bool(profile.spectrum), report, "spectrum")
    _set(patched, roles.attention, "attention",
         choose_attention(profile.attention, attention_options, report),
         report, "attention")

    # -- the seed ---------------------------------------------------------
    # The workflow ships with a fixed one, so without this every user of every
    # copy gets the same video.
    value = seed if seed is not None else random.randint(0, MAX_SEED)
    if _set(patched, roles.noise, "noise_seed", int(value), report, "seed"):
        report.seed = int(value)

    if filename_prefix:
        _set(patched, roles.save, "filename_prefix", filename_prefix, report, "output")

    # -- the last frame ---------------------------------------------------
    if save_last_frame:
        _add_last_frame_saver(patched, roles, report)

    return patched, report


def _add_last_frame_saver(graph: dict, roles: Roles, report: Report) -> None:
    """Splice the pack's last-frame saver in front of CreateVideo.

    The last frame is what you feed back in to carry a scene on into the next
    clip, so it is worth having every time rather than only when someone thinks
    to ask. The node passes the whole batch through unchanged, so the video is
    made from exactly the same frames whether this is here or not - it is a tap,
    not a filter.

    Not part of the bundled workflow on purpose: an older copy of the add-on may
    not have this node, and a graph naming a node ComfyUI does not know is
    rejected whole. Adding it here, only when the server says it exists, means a
    missing saver costs the last frame rather than the render.
    """
    if not roles.create_video or not roles.decode_video:
        report.notes.append(t(
            "The last frame could not be saved: this workflow does not have "
            "the usual video decode."))
        return

    graph[LASTFRAME_ID] = {
        "inputs": {
            "images": [roles.decode_video, 0],
            "save": True,
            "filename_prefix": "EasyMiniMax/lastframe",
        },
        "class_type": LASTFRAME,
        "_meta": {"title": "Save Last Frame"},
    }
    graph[roles.create_video]["inputs"]["images"] = [LASTFRAME_ID, 0]
    report.applied.append("last_frame")


def problems(storyboard: timeline.Storyboard) -> list[str]:
    """Things to say before queueing anything, phrased for a person."""
    out: list[str] = []
    has_global = bool(storyboard.global_prompt.strip())
    has_shot = any(s.prompt.strip() for s in storyboard.shots)
    if not has_global and not has_shot:
        out.append(t("Please describe your video first."))

    for index, subject in enumerate(storyboard.subjects, start=1):
        if not subject.active:
            continue
        from pathlib import Path
        if not Path(subject.image).is_file():
            out.append(t("The picture for person {n} is no longer there:\n{path}",
                         n=index, path=subject.image))
        elif not subject.description.strip():
            # Without a description the compiled prompt can number the picture
            # but has nothing to say about it, so the model mostly ignores it.
            out.append(t("Say what the picture for person {n} shows, or the "
                         "model has no idea what to keep.", n=index))
    return out


#: Marked for translation here so the interface and the report agree on wording.
LABELS = {
    "turbo_lora": N("Turbo LoRA strength"),
    "steps": N("Sampling steps"),
    "spectrum": N("Spectrum accelerator"),
    "attention": N("Attention"),
}
