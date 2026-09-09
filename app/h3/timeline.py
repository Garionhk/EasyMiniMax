"""Builds the ``timeline_data`` JSON the MiniMax H3 Director actually reads.

This is the whole app in one file, and it is worth saying why.

The Director node looks like it has ordinary inputs - ``local_prompts``,
``segment_lengths``, ``global_prompt`` - and it does not. ``execute()`` accepts
those three and never reads them; they exist only so the node's JavaScript
editor has somewhere to mirror its state. Everything that decides what gets
rendered is parsed out of a single escaped-JSON string on the ``timeline_data``
input (``minimax_plan.parse_timeline`` / ``plan_timeline``). ``global_prompt``
is declared ``force_input=True``, so it cannot even be set as a literal - the
node falls back to ``timeline_data["global_prompt"]`` instead.

So a front end for this workflow is not a patcher that writes values into
inputs. It is a program that writes that editor's save file.

Two consequences worth keeping in mind while reading:

* **reference_mode chooses the checkpoint.** ``plan.ref_mode_from`` treats any
  value other than "OFF" as on, and the Director's ``check_lazy_status`` uses
  that to decide which of two 21 GB UNETs to read off disk. Getting it wrong is
  not a subtle quality difference; it loads the wrong model, or loads both and
  pages a 32 GB machine to death.
* **Frame counts snap.** Segments are laid out in timeline frames, and the total
  has to be a value H3 accepts (see ``presets.align_frame_count``), or the shot
  boundaries stop lining up with the storyboard markers the model is given.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.h3.presets import MIN_SHOT_FRAMES, MODEL_FPS, align_frame_count
from app.i18n import plural, t

#: The Director clamps anything it does not recognise back to a default rather
#: than letting an invented word reach the prompt, so these lists are copied
#: from minimax_plan.py to keep the interface honest about what it is offering.
SUBJECT_KINDS = (
    "person", "animal", "object", "environment", "clothing", "prop",
    "interface", "effect", "style", "action", "expression", "pose",
)
SUBJECT_KIND_DEFAULT = "person"

RETENTION_VISIBLE = (
    "fully_preserved", "partially_preserved", "attribute_transfer", "weak_reference",
)
RETENTION_DEFAULT = "fully_preserved"

#: Audio has its own vocabulary and its own default. A voice clip guides timbre
#: rather than being copied wholesale, so "reference" is right where a picture
#: wants "fully_preserved" - and sending a picture's word here gets it clamped
#: back by sanitize_retention(..., audio=True), silently changing what was asked
#: for.
RETENTION_AUDIO = (
    "fully_copy", "partially_copy", "reference", "weak_reference",
)
RETENTION_AUDIO_DEFAULT = "reference"

#: How many subject slots the editor shows. Three is what the JS editor ships
#: with, and the compiled prompt numbers them Subject 1 to Subject 3.
SUBJECT_SLOTS = 3

REFERENCE_ON = "REF2VA"
REFERENCE_OFF = "OFF"


@dataclass
class Shot:
    """One cut. Becomes a time-marked block in the storyboard prompt."""
    prompt: str = ""
    length: int = MIN_SHOT_FRAMES     # timeline frames

    def seconds(self) -> float:
        return round(self.length / MODEL_FPS, 2)


@dataclass
class Subject:
    """A person or thing to keep consistent, from a reference picture."""
    image: Path | None = None
    #: What it is: "a woman in a white bikini top".
    description: str = ""
    #: What the shot prompts call it: "the rider".
    short_name: str = ""
    kind: str = SUBJECT_KIND_DEFAULT
    retention: str = RETENTION_DEFAULT
    note: str = ""

    @property
    def active(self) -> bool:
        """Only a slot with a picture counts - a description on its own has
        nothing for the reference model to look at."""
        return self.image is not None


@dataclass
class Voice:
    """A clip whose voice and timbre the generated speech should follow.

    H3 makes its own sound, so this is not a soundtrack to lay over the video -
    it is a reference for how the speaking should sound. Bound to a subject, the
    compiled prompt says "<Audio 1> is the voice-timbre reference for
    <Subject 1>"; unbound it is a general voice reference for the whole clip.
    """
    audio: Path | None = None
    #: What it sounds like: "a low, calm woman's voice".
    description: str = ""
    #: Which subject slot this voice belongs to, 1-based, or None for nobody in
    #: particular. The editor stores it as the string a <select> hands over.
    subject: int | None = None
    retention: str = RETENTION_AUDIO_DEFAULT
    note: str = ""

    @property
    def active(self) -> bool:
        return self.audio is not None


@dataclass
class Storyboard:
    """Everything the user set up for one render."""
    global_prompt: str = ""
    shots: list[Shot] = field(default_factory=list)
    subjects: list[Subject] = field(default_factory=lambda:
                                    [Subject() for _ in range(SUBJECT_SLOTS)])
    voice: Voice = field(default_factory=Voice)
    frames: int = 124
    width: int = 1344
    height: int = 768

    # -- derived -----------------------------------------------------------
    @property
    def uses_references(self) -> bool:
        """Does this render need the reference checkpoint?

        A voice counts. The planner only reads audioSegments inside its
        `if ref_mode_on:` branch, so a voice attached in plain text-to-video
        mode is silently thrown away - the clip would be uploaded, named in the
        timeline, and never reach the model.
        """
        return any(s.active for s in self.subjects) or self.voice.active

    @property
    def seconds(self) -> float:
        return round(self.frames / MODEL_FPS, 2)

    def active_subjects(self) -> list[tuple[int, Subject]]:
        """(slot index, subject) for every slot with a picture in it."""
        return [(i, s) for i, s in enumerate(self.subjects) if s.active]

    # -- shot bookkeeping --------------------------------------------------
    def normalise(self) -> None:
        """Make the shots tile the window exactly, in place.

        The editor lets shots be dragged around, and the render length can be
        changed underneath them. Rather than forbidding either, the lengths are
        rescaled to whatever the window is now and the remainder handed to the
        last shot - so the sum is exact by construction and no arithmetic
        anywhere else has to worry about a one-frame gap.
        """
        self.frames = align_frame_count(self.frames)
        if not self.shots:
            return

        # Drop shots that cannot fit at all before rescaling, or a window that
        # shrank a long way would leave every shot below the minimum.
        room = max(1, self.frames // MIN_SHOT_FRAMES)
        if len(self.shots) > room:
            del self.shots[room:]

        total = sum(max(1, s.length) for s in self.shots)
        scale = self.frames / total
        running = 0
        for shot in self.shots[:-1]:
            shot.length = max(MIN_SHOT_FRAMES, int(round(max(1, shot.length) * scale)))
            running += shot.length
        # The last shot absorbs the rounding, so sum(lengths) == frames always.
        self.shots[-1].length = self.frames - running

        # Rescaling can still push the last shot under the minimum; take the
        # difference back off its neighbours, longest first.
        while self.shots[-1].length < MIN_SHOT_FRAMES and len(self.shots) > 1:
            donor = max(self.shots[:-1], key=lambda s: s.length)
            if donor.length <= MIN_SHOT_FRAMES:
                del self.shots[-2]
                self.shots[-1].length = self.frames - sum(
                    s.length for s in self.shots[:-1])
                continue
            take = min(donor.length - MIN_SHOT_FRAMES,
                       MIN_SHOT_FRAMES - self.shots[-1].length)
            donor.length -= take
            self.shots[-1].length += take

    def segments(self) -> list[dict]:
        """The shots as the editor stores them: cumulative starts, no gaps."""
        self.normalise()
        shots = self.shots or [Shot(prompt="", length=self.frames)]
        out, start = [], 0
        for i, shot in enumerate(shots):
            out.append({
                "id": "seg%d" % i,
                "start": start,
                "length": int(shot.length),
                "prompt": shot.prompt.strip(),
                "type": "text",
                "isEndFrame": False,
            })
            start += int(shot.length)
        return out

    # -- mirrored into the graph for anyone who opens it in ComfyUI --------
    def local_prompts(self) -> str:
        return " | ".join(s["prompt"] for s in self.segments())

    def segment_lengths(self) -> str:
        return ",".join(str(s["length"]) for s in self.segments())


def _subject_entry(subject: Subject, uploaded_name: str) -> dict:
    """One subject slot, in the shape the editor writes and the planner reads.

    Empty slots keep the full shape rather than being left out: the planner
    walks all three by index (minimax_plan.py, around line 1122), so a missing
    slot would shift the second subject onto the third slot's picture.
    """
    images = [{"name": uploaded_name}] if uploaded_name else []
    kind = subject.kind if subject.kind in SUBJECT_KINDS else SUBJECT_KIND_DEFAULT
    retention = (subject.retention if subject.retention in RETENTION_VISIBLE
                 else RETENTION_DEFAULT)
    return {
        "images": images,
        "description": subject.description.strip(),
        "shortName": subject.short_name.strip(),
        "kind": kind,
        "retention": retention,
        "retentionNote": subject.note.strip(),
    }


def _voice_segment(voice: Voice, uploaded_name: str, frames: int) -> list[dict]:
    """The voice clip as one audio segment, or nothing.

    It spans the whole render window on purpose. The planner keeps only clips
    that `overlaps()` the window, and a reference clip is not a sound placed at
    a moment - it describes how the speaking should sound throughout - so a
    short segment at the start would simply be a way to have it dropped.
    """
    if not uploaded_name:
        # Nothing on the server to point at. Emitting a segment naming a file
        # ComfyUI cannot open fails the render, and emitting one with an empty
        # name is filtered out by the planner anyway.
        return []

    retention = (voice.retention if voice.retention in RETENTION_AUDIO
                 else RETENTION_AUDIO_DEFAULT)
    return [{
        "id": "aud0",
        "start": 0,
        "length": int(frames),
        "audioFile": uploaded_name,
        "refDesc": voice.description.strip(),
        "refNote": voice.note.strip(),
        "retention": retention,
        # 1-based, as the string the editor's <select> hands over. Empty means
        # "nobody in particular", which the planner reads as an unbound clip.
        "subject": str(voice.subject) if voice.subject else "",
    }]


def build(storyboard: Storyboard, uploaded: dict[int, str] | None = None,
          uploaded_voice: str = "") -> str:
    """Return the ``timeline_data`` string for one render.

    ``uploaded`` maps a subject's slot index to the name ComfyUI gave the file
    after ``/upload/image`` - "subfolder/name.jpg". The picture has to be
    referred to by that server-side name, not by the path on this machine,
    because the node opens it from ComfyUI's own input folder.

    ``uploaded_voice`` is the same thing for the voice clip, and is deliberately
    its own argument rather than another key in ``uploaded``: that dict is keyed
    by subject slot and every entry of it is read as a picture, so a sound file
    smuggled in there would be handed to a slot as an image.
    """
    uploaded = uploaded or {}
    segments = storyboard.segments()          # also normalises frames + lengths
    subjects = [_subject_entry(s, uploaded.get(i, ""))
                for i, s in enumerate(storyboard.subjects)]

    audio_segments = _voice_segment(storyboard.voice, uploaded_voice,
                                    storyboard.frames)

    # Pictures *or* a voice. Both live behind the same switch: the planner reads
    # audioSegments only when reference mode is on, so a voice with this left
    # OFF is uploaded, written into the timeline, and then ignored.
    references_on = any(entry["images"] for entry in subjects) or bool(audio_segments)

    data = {
        # -- editor chrome. Not read by the planner, but the node's JS reloads
        # -- this same blob, so leaving it out makes the graph look broken if
        # -- the user ever opens it in ComfyUI to see what the app sent.
        "mainTrackEnabled": True,
        "audioTrackEnabled": True,
        "motionTrackEnabled": True,
        "propHeight": 90,
        "globalPropHeight": 126,
        "showFilenames": True,
        "showPromptZones": True,

        # -- what actually conditions the render --------------------------
        "global_prompt": storyboard.global_prompt.strip(),
        "segments": segments,
        "subjects": subjects,
        "subjectSlotCount": SUBJECT_SLOTS,
        "reference_mode": REFERENCE_ON if references_on else REFERENCE_OFF,
        "prompt_format": "minimax",

        # -- the reference tracks -----------------------------------------
        "audioSegments": audio_segments,
        #: The reference-video track. Not offered by this program.
        "motionSegments": [],
        "overrideAudio": False,
        "inpaint_audio": True,
        "overall_soundscape": "",
        "non_diegetic_music": "",

        # -- the prompt is never overridden: the storyboard the planner
        # -- compiles out of the segments above is the whole point.
        "prompt_override": "",
        "prompt_override_on": False,
        "task_type_override": "",
        "summary": "",

        # -- retake is a second-pass feature this app does not expose ------
        "retakeMode": False,
        "retake_global_prompt": "",
        "retakeStart": 0,
        "retakeLength": 0,
        "retakePrompt": "",
        "retakeStrength": 1,
        "retakeVideo": None,

        "normalStartFrame": 0,
        "normalDurationFrames": int(storyboard.frames),

        # -- the Analyze button's vision endpoint. Unused here.
        "analyzeProvider": "ollama",
        "analyzeBaseUrl": "",
        "analyzeModel": "",
    }
    return json.dumps(data, ensure_ascii=False)


def describe(storyboard: Storyboard) -> str:
    """One line under the Create button saying what is about to be made."""
    from app.h3.presets import format_length

    # Aligned here rather than trusting the caller: this line is the one place
    # the user is told how long their video will be, and reporting the number
    # they asked for while rendering a different one is exactly the confusion
    # the snapped label exists to prevent.
    bits = [format_length(align_frame_count(storyboard.frames)),
            "%d × %d" % (storyboard.width, storyboard.height)]
    # plural(), not an f-string: a language that counts differently needs the
    # whole sentence, not a word with an "s" glued on. This line was built by
    # hand and so stayed English in every language until somebody looked at a
    # screenshot of it.
    shots = len(storyboard.shots) or 1
    bits.append(plural(shots, "1 shot", "{n} shots"))
    n = len(storyboard.active_subjects())
    if n:
        bits.append(plural(n, "1 reference picture", "{n} reference pictures"))
    if storyboard.voice.active:
        bits.append(t("a reference voice"))
    return " · ".join(bits)
