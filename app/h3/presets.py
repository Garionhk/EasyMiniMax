"""The fixed choices this app offers, and what each one means in the graph.

Everything here is a table rather than a computation, because MiniMax H3 is not
a model you can hand arbitrary numbers to. The frame count sits on a 17k+5 grid,
the canvas wants a 768 px short edge, and the turbo path is four values that
only make sense together. Writing them down once means the interface and the
patcher cannot drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.i18n import N, t

#: H3's output rate is fixed. The timeline can be edited at another rate, but
#: what comes out is always 24 fps.
MODEL_FPS = 24.0

#: The attention backend that is always present. Everything else is optional
#: and has to be checked against the running ComfyUI before it is used.
SAFE_ATTENTION = "pytorch attention"


@dataclass(frozen=True)
class Profile:
    """One end of the Turbo / Quality switch.

    These four values move together. Turning the LoRA on without dropping the
    step count produces a slow, over-cooked render; dropping the steps without
    the LoRA produces mush. Presenting them as one choice is the whole point of
    the switch.
    """
    key: str
    label: str
    blurb: str
    lora_strength: float
    steps: int
    spectrum: bool
    attention: str

    def describe(self) -> list[tuple[str, str]]:
        """The advanced disclosure: exactly what this switch changes."""
        return [
            (N("Turbo LoRA strength"), f"{self.lora_strength:g}"),
            (N("Sampling steps"), str(self.steps)),
            (N("Spectrum accelerator"), N("on") if self.spectrum else N("off")),
            (N("Attention"), self.attention),
        ]


TURBO = Profile(
    key="turbo",
    label=N("⚡  Turbo"),
    blurb=N("Roughly twice as fast. Ideal while you are trying ideas out."),
    lora_strength=1.0,
    steps=10,
    spectrum=True,
    attention="comfy kitchen attention",
)

QUALITY = Profile(
    key="quality",
    label=N("✨  Quality"),
    blurb=N("Slower, and follows your description a little more closely."),
    lora_strength=0.0,
    steps=20,
    spectrum=False,
    attention=SAFE_ATTENTION,
)

PROFILES = {p.key: p for p in (TURBO, QUALITY)}


def profile(key: str) -> Profile:
    return PROFILES.get(key, TURBO)


@dataclass(frozen=True)
class Resolution:
    label: str
    width: int
    height: int
    #: Which of the three bands below it belongs to.
    group: str = ""

    @property
    def key(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def pixels(self) -> str:
        return f"{self.width} × {self.height}"


#: The three bands the Director's own preset menu is divided into, with what
#: each one costs, because "fast" and "past native" are not free choices.
NATIVE = N("Native — 768 short edge")
FAST = N("Fast — 480 short edge")
PAST_NATIVE = N("Past native — outside the trained canvas")

GROUP_NOTES = {
    NATIVE: "",
    FAST: N("Quicker and lighter on memory, at a visible cost in detail."),
    PAST_NATIVE: N("Bigger than this model was trained for. Slow, heavy on "
                   "memory, and often worse rather than better."),
}

#: Copied from the preset table in the Director's own editor
#: (js/minimax_director.js, `const RES`), pixel for pixel. Those numbers are a
#: 768 px short edge with the long edge capped at 1344, snapped to a multiple
#: of 32 - not something to re-derive here, because the editor's table is what
#: users of this model see everywhere else and a list that disagreed with it by
#: 32 pixels would be its own bug report.
#:
#: The editor's "Custom" entry is left out: it is `0 × 0`, which tells the node
#: to take its size from the first timeline image, and this program always has
#: a size to give it.
RESOLUTIONS = (
    Resolution(N("21:9  —  1344×576"), 1344, 576, NATIVE),
    Resolution(N("2:1  —  1344×672"), 1344, 672, NATIVE),
    Resolution(N("16:9  —  1344×768"), 1344, 768, NATIVE),
    Resolution(N("3:2  —  1152×768"), 1152, 768, NATIVE),
    Resolution(N("4:3  —  1024×768"), 1024, 768, NATIVE),
    Resolution(N("5:4  —  960×768"), 960, 768, NATIVE),
    Resolution(N("1:1  —  992×992"), 992, 992, NATIVE),
    Resolution(N("4:5  —  768×960"), 768, 960, NATIVE),
    Resolution(N("3:4  —  768×1024"), 768, 1024, NATIVE),
    Resolution(N("2:3  —  768×1152"), 768, 1152, NATIVE),
    Resolution(N("9:16  —  768×1344"), 768, 1344, NATIVE),
    Resolution(N("1:2  —  672×1344"), 672, 1344, NATIVE),
    Resolution(N("9:21  —  576×1344"), 576, 1344, NATIVE),

    Resolution(N("21:9 fast  —  1120×480"), 1120, 480, FAST),
    Resolution(N("2:1 fast  —  960×480"), 960, 480, FAST),
    Resolution(N("16:9 fast  —  864×480"), 864, 480, FAST),
    Resolution(N("3:2 fast  —  736×480"), 736, 480, FAST),
    Resolution(N("4:3 fast  —  640×480"), 640, 480, FAST),
    Resolution(N("5:4 fast  —  608×480"), 608, 480, FAST),
    Resolution(N("1:1 fast  —  640×640"), 640, 640, FAST),
    Resolution(N("4:5 fast  —  480×608"), 480, 608, FAST),
    Resolution(N("3:4 fast  —  480×640"), 480, 640, FAST),
    Resolution(N("2:3 fast  —  480×736"), 480, 736, FAST),
    Resolution(N("9:16 fast  —  480×864"), 480, 864, FAST),
    Resolution(N("1:2 fast  —  480×960"), 480, 960, FAST),
    Resolution(N("9:21 fast  —  480×1120"), 480, 1120, FAST),

    Resolution(N("16:9 past native  —  1920×1088"), 1920, 1088, PAST_NATIVE),
)

#: 16:9 at the native short edge - the shape most people mean by "a video".
DEFAULT_RESOLUTION = "1344x768"

#: The order the bands appear in the menu.
GROUP_ORDER = (NATIVE, FAST, PAST_NATIVE)


def resolution(key: str) -> Resolution:
    for r in RESOLUTIONS:
        if r.key == key:
            return r
    for r in RESOLUTIONS:
        if r.key == DEFAULT_RESOLUTION:
            return r
    return RESOLUTIONS[0]


def resolution_warning(res: Resolution) -> str:
    """A plain sentence when a choice costs something worth knowing about."""
    return GROUP_NOTES.get(res.group, "")



# -- duration --------------------------------------------------------------
#: The shortest a shot may be, in timeline frames. Half a second: below this
#: the storyboard marker rounds to a zero-length range and the shot vanishes
#: from the compiled prompt.
MIN_SHOT_FRAMES = 12

MIN_SECONDS = 1.0
MAX_SECONDS = 14.0

#: What MiniMax H3 was actually trained to produce: 4 to 15 seconds at 24 fps
#: (minimax_plan.TRAINED_MIN_FRAMES / TRAINED_MAX_FRAMES). Shorter still renders,
#: but the model is off the end of its own envelope and the result usually shows
#: it - so the interface says so rather than letting it look like a bad prompt.
TRAINED_MIN_FRAMES = 96
TRAINED_MAX_FRAMES = 360


def align_frame_count(n: int) -> int:
    """Snap up to H3's 17k+5 frame grid.

    A mirror of ``minimax_plan.align_frame_count`` in the Director pack. It has
    to be a mirror rather than an import, because this app talks to ComfyUI over
    HTTP and never has the pack on its own import path - but if the two ever
    disagreed, the length shown in the interface would not be the length that
    came out, which is the sort of thing a beginner reads as the program lying.
    """
    n = max(5, int(n))
    while n % 17 != 5:
        n += 1
    return n


def frames_for(seconds: float) -> int:
    """Seconds the user asked for -> frames H3 will actually render."""
    return align_frame_count(round(float(seconds) * MODEL_FPS))


def seconds_for(frames: int) -> float:
    return round(int(frames) / MODEL_FPS, 2)


def format_length(frames: int) -> str:
    """'5.2 s · 124 frames' - the snap is shown, never hidden.

    Ask for 5 seconds and H3 renders 124 frames, which is 5.17 seconds. Showing
    the rounded request and quietly producing something else is how a
    perfectly correct render ends up reported as a bug.
    """
    return t("{seconds} s · {frames} frames",
             seconds=f"{seconds_for(frames):g}", frames=frames)


def length_warning(frames: int) -> str:
    """A plain sentence when the length is outside what H3 was trained on."""
    if frames < TRAINED_MIN_FRAMES:
        return N("Under 4 seconds is shorter than this model was trained for. "
                 "It will still render, but movement often comes out stiff.")
    if frames > TRAINED_MAX_FRAMES:
        return N("Over 15 seconds is longer than this model was trained for, "
                 "and needs a lot of graphics memory.")
    return ""
