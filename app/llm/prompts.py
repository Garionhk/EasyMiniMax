"""What to ask the vision model, and how.

These follow the rules the Director add-on's own Enhance node uses
(``minimax_enhance.py``: SYSTEM_GLOBAL, and ``minimax_media.analyze_prompt``),
because those are tuned for what MiniMax H3's text encoder was trained on. They
are written out here rather than imported, since this program talks to ComfyUI
over HTTP and never has that package on its import path.

The rules that matter, and why:

* **Prose, never a list.** H3 was trained on flowing description. A bulleted
  answer is worse than a shorter sentence.
* **Only what is visible or audible.** No intent, no symbolism, no backstory -
  the model cannot render "she feels uneasy", and words spent on it are words
  not spent on the coat she is wearing.
* **Say the sound out loud.** H3 generates its own audio, and a description with
  no `Audio:` line gets whatever the model guesses.
* **No labels in the answer.** The words below name what the prose has to cover.
  A model that writes "Lighting:" into its answer has misunderstood, and that
  text ends up in the prompt.
"""
from __future__ import annotations

import re

#: What to look at, per subject kind. A slot is not always a character - the
#: Director's <Subject N> covers scenes, props, styles and poses too - and
#: asking for hair and clothing when the picture is a coffee shop produces
#: exactly the answer you would expect. Copied from minimax_media._ANALYZE_SUBJECT.
SUBJECT_LOOK_FOR = {
    "person": ("person", "hair colour and style, face, build, and clothing type and colour"),
    "animal": ("animal", "species, markings, coat, and build"),
    "object": ("object", "shape, material, colour, and any markings"),
    "environment": ("place", "architecture, furnishing, materials, and lighting"),
    "clothing": ("garment", "cut, colour, material, and fastenings"),
    "prop": ("prop", "shape, material, colour, and signs of wear"),
    "interface": ("interface", "layout, typography, iconography, and colour"),
    "effect": ("visual effect", "shape, colour, motion, and how it interacts with the scene"),
    "style": ("visual style", "palette, contrast, grain, and grade"),
    "action": ("action", "the movement, its timing, and the body mechanics"),
    "expression": ("facial expression", "the eyes, mouth, and what it conveys"),
    "pose": ("pose", "posture, limb placement, and weight distribution"),
}

DESCRIBE_SYSTEM = (
    "You describe reference images for a video model. You answer with one "
    "sentence of plain English and nothing else: no preamble, no labels, no "
    "quotation marks, no commentary about the image being an image. Describe "
    "only what is visible. Never guess at mood, intent or backstory."
)


#: Openers that describe the picture rather than its contents. A pattern rather
#: than a list, because the variants multiply faster than anyone can enumerate
#: them - "the image shows", "this reference image depicts", "in the provided
#: photo," were all produced by one model in three consecutive calls.
_IMAGE_OPENER = re.compile(
    r"""^(?:in\s+)?
         (?:this|the)\s+
         (?:reference\s+|provided\s+|attached\s+|given\s+)?
         (?:image|picture|photo|photograph)\s*
         (?:,\s*|(?:shows|depicts|features|portrays|displays|contains|
                    is\s+of|appears\s+to\s+show)\s+)""",
    re.IGNORECASE | re.VERBOSE)

#: The other shape: "This is a picture of a fox", "This appears to be a fox".
_IS_A_PICTURE = re.compile(
    r"""^this\s+(?:is|appears\s+to\s+be)\s+
         # The article belongs to the picture-phrase, not to what follows: it is
         # only eaten as part of "a picture of". Pulling it out on its own turned
         # "This appears to be a red fox" into "Red fox".
         (?:(?:an?\s+)?(?:reference\s+|close-up\s+)?
            (?:image|picture|photo|photograph)\s+of\s+)?""",
    re.IGNORECASE | re.VERBOSE)


def _article(word: str) -> str:
    """"a" or "an". Half these nouns start with a vowel - animal, object,
    interface, expression - and "a animal" in a prompt reads as carelessness
    to a reader and is noise to a model."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def describe_picture(kind: str) -> tuple[str, str]:
    """(system, prompt) for filling in a subject slot's description.

    One sentence, because that is what the field holds and what the compiled
    prompt inserts. The Director's own node asks for two - a description and a
    separate retention line - but this program does not expose retention, so
    asking for it would produce text with nowhere to go.
    """
    noun, look_for = SUBJECT_LOOK_FOR.get(kind or "", SUBJECT_LOOK_FOR["person"])
    prompt = (
        f"Look at this reference image of {_article(noun)} {noun}. In one "
        f"concise sentence, name the {noun} and its {look_for}. "
        f"Write only that sentence."
    )
    return DESCRIBE_SYSTEM, prompt


IDEA_SYSTEM = """You write the opening description of a MiniMax H3 video prompt.

You are given a short, informal idea. You expand it into the description a video
model can work from. Your answer is inserted into a larger prompt that another
tool assembles, so it adds every heading, shot marker and timestamp. You write
only the description.

FORM
One paragraph of flowing English prose, then a single line beginning "Audio:".
Never a list. Never a heading. The words below name what the prose has to cover;
they are not labels to print. Writing "Lighting:" into your answer is wrong -
write the sentence instead.

WHAT TO COVER, in this order, as one paragraph
The visual style and format (cinematic live action, 2D animation, 3D CG,
claymation, watercolour, vintage film). The opening framing and composition.
Every person, animal or object that matters, by visible identity: age, build,
hair, face, clothing with colours, distinctive props. The place: location, key
props, time of day, weather. The lighting: direction, hardness, colour
temperature, any visible practical sources. What happens and what changes. How
the camera behaves, written as a natural action inside the sentence.

THE AUDIO LINE
This model generates its own sound, so the last line always begins "Audio:" and
names what is heard: the diegetic sounds of the scene, any speech and its tone,
and any score. Without it the model invents a soundtrack of its own.

ONLY WHAT IS THERE
Every detail must correspond to something visible or audible. No intent, no
feelings, no symbolism, no backstory, no plot summary. Any sign or label
actually visible goes in double quotation marks, verbatim.

LENGTH
About {words} words for the paragraph, then the audio line. Stop there."""


def expand_idea(idea: str, seconds: float, words: int = 160) -> tuple[str, str]:
    """(system, prompt) for turning a rough line into a full description."""
    system = IDEA_SYSTEM.format(words=int(words))
    prompt = (
        f"The idea: {idea.strip()}\n\n"
        f"The finished video is about {seconds:g} seconds long, so describe "
        f"only what can happen in that time - one situation, not a sequence of "
        f"scenes.\n\n"
        f"Write the description now."
    )
    return system, prompt


def tidy(text: str) -> str:
    """Strip the wrappers small models add however firmly they are told not to.

    Fenced blocks, a leading "Sure, here is...", surrounding quotes, and the
    labels the system prompt just forbade. Cheaper to remove three of these than
    to fight the model over them, and every one of them would otherwise be typed
    verbatim into the prompt that reaches H3.
    """
    text = (text or "").strip()

    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # "The image shows a red fox..." - forbidden by the system prompt and
    # produced anyway, by every model tried. It matters more than it looks:
    # the description is inserted into the compiled prompt as what <Subject 1>
    # *is*, so this leaves H3 being told the subject is a picture of a fox
    # rather than a fox.
    for pattern in (_IMAGE_OPENER, _IS_A_PICTURE):
        trimmed = pattern.sub("", text, count=1)
        if trimmed != text:
            # "shows a red fox" -> "A red fox": it is a sentence now, so it
            # starts like one.
            text = trimmed.lstrip()
            text = text[:1].upper() + text[1:]
            break

    lowered = text.lower()
    for opener in ("sure,", "certainly,", "here is", "here's", "of course,"):
        if lowered.startswith(opener):
            # Cut to the end of that first sentence, not the first newline: the
            # preamble and the answer are often one paragraph.
            head, _, tail = text.partition(". ")
            if tail:
                text = tail.strip()
            break

    for label in ("DESCRIPTION:", "Description:", "ANSWER:", "Answer:"):
        if text.startswith(label):
            text = text[len(label):].strip()

    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()

    return text
