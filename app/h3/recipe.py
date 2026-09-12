"""The settings behind one video, saved beside it and loadable again.

Every render writes a ``.json`` next to its ``.mp4`` holding everything that
produced it. Two reasons, and the second is the one that matters:

* You can see what you did. Six months later "which of these was Quality?" has
  an answer that is not guesswork.
* You can get back to it. Load the file and every control returns to where it
  was, so a video you liked is a starting point rather than a dead end.

The format is deliberately plain and readable - names a person would use, not
node ids - because a file nobody can read by eye is a file nobody trusts. It is
not a ComfyUI workflow and does not try to be; ``tools/dump_graph.py`` is there
for that.

Loading is forgiving on purpose. A file from an older version, or one somebody
hand-edited, restores whatever it can and says what it could not, rather than
refusing the lot over one unexpected key.
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from app import __version__
from app.h3.presets import PROFILES, Profile, resolution
from app.h3.presets import profile as get_profile
from app.h3.timeline import (
    RETENTION_AUDIO, RETENTION_AUDIO_DEFAULT, Shot, Storyboard, Subject, Voice,
)

#: Bumped when the shape changes in a way a reader has to know about. Anything
#: with a higher number than this is loaded on a best-effort basis and said so.
FORMAT_VERSION = 1

SUFFIX = ".json"


@dataclass
class Loaded:
    """What came back out of a file, and what could not."""
    storyboard: Storyboard = field(default_factory=Storyboard)
    profile: Profile | None = None
    seed: int | None = None
    resolution_key: str = ""
    #: Sentences for the user: a reference picture that has moved, a setting
    #: from a newer version, a file that was only half a recipe.
    notes: list[str] = field(default_factory=list)


def build(storyboard: Storyboard, profile: Profile, *, seed: int | None = None,
          frames: int | None = None, elapsed: float = 0.0,
          notes: list[str] | None = None,
          video: str = "", last_frame: str = "") -> dict:
    """Everything worth recording about one render."""
    return {
        "app": "EasyMiniDirector",
        "app_version": __version__,
        "format": FORMAT_VERSION,
        "made": _dt.datetime.now().isoformat(timespec="seconds"),
        "model": "MiniMax H3",

        "video": video,
        "last_frame": last_frame,

        "prompt": storyboard.global_prompt,
        "shots": [{"prompt": s.prompt, "frames": s.length}
                  for s in storyboard.shots],
        "subjects": [
            {
                "image": str(s.image) if s.image else "",
                "description": s.description,
                "short_name": s.short_name,
                "kind": s.kind,
                "retention": s.retention,
            }
            for s in storyboard.subjects
        ],

        "voice": {
            "audio": str(storyboard.voice.audio) if storyboard.voice.audio else "",
            "description": storyboard.voice.description,
            # 1-based subject slot, or null for "nobody in particular".
            "subject": storyboard.voice.subject,
            "retention": storyboard.voice.retention,
        },

        "speed": {
            # The profile's name and its four values. The values are written
            # out rather than left implied by the name, so a file still says
            # what it did if the presets are ever retuned.
            "profile": profile.key,
            "turbo_lora_strength": profile.lora_strength,
            "steps": profile.steps,
            "spectrum": profile.spectrum,
            "attention": profile.attention,
        },

        "size": {
            "width": storyboard.width,
            "height": storyboard.height,
            "preset": f"{storyboard.width}x{storyboard.height}",
        },
        "length": {
            "frames": int(frames or storyboard.frames),
            "seconds": round(int(frames or storyboard.frames) / 24.0, 2),
            "fps": 24,
        },
        "seed": seed,
        "references": storyboard.uses_references,
        "elapsed_seconds": round(elapsed, 1) if elapsed else None,
        "notes": list(notes or []),
    }


def save(path: Path | str, data: dict) -> Path | None:
    """Write the recipe beside its video. Never fails a finished render."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return path
    except OSError as e:
        # The video is already on disk. Losing its notes is a shame; losing the
        # render because the notes would not write is not acceptable.
        print(f"[recipe] could not write {path}: {e}")
        return None


def path_for(video: Path | str) -> Path:
    """The recipe that belongs beside a given video."""
    return Path(video).with_suffix(SUFFIX)


def load(path: Path | str) -> Loaded:
    """Read a recipe back into something the window can apply."""
    path = Path(path)
    out = Loaded()

    try:
        # utf-8-sig, so a settings file opened and re-saved in Notepad
        # still loads. See app/config.py for the same reason.
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        out.notes.append(f"{path.name} could not be read: {e}")
        return out

    if not isinstance(data, dict):
        out.notes.append(f"{path.name} is not a settings file.")
        return out
    if data.get("app") and data.get("app") != "EasyMiniDirector":
        out.notes.append(
            f"{path.name} was written by {data.get('app')}, not this program. "
            "Whatever could be read has been loaded.")
    if int(data.get("format") or 0) > FORMAT_VERSION:
        out.notes.append(
            "This file was written by a newer version. Anything it holds that "
            "this version does not know about has been left out.")

    board = Storyboard()
    board.global_prompt = str(data.get("prompt") or "")

    for entry in data.get("shots") or []:
        if not isinstance(entry, dict):
            continue
        board.shots.append(Shot(prompt=str(entry.get("prompt") or ""),
                                length=int(entry.get("frames") or 12)))

    for index, entry in enumerate(data.get("subjects") or []):
        if index >= len(board.subjects) or not isinstance(entry, dict):
            continue
        picture = str(entry.get("image") or "")
        if picture and not Path(picture).is_file():
            # Restore everything except the picture, and say so. Silently
            # dropping it would leave a description with nothing to describe.
            out.notes.append(
                f"The picture for person {index + 1} is no longer at:\n{picture}")
            picture = ""
        board.subjects[index] = Subject(
            image=Path(picture) if picture else None,
            description=str(entry.get("description") or ""),
            short_name=str(entry.get("short_name") or ""),
            kind=str(entry.get("kind") or "person"),
            retention=str(entry.get("retention") or "fully_preserved"),
        )

    voice = data.get("voice") or {}
    if isinstance(voice, dict):
        clip = str(voice.get("audio") or "")
        if clip and not Path(clip).is_file():
            # Same treatment as a missing picture: keep everything that was
            # said about it and name what has gone, rather than silently
            # loading a voice reference with no voice in it.
            out.notes.append(f"The voice clip is no longer at:\n{clip}")
            clip = ""
        slot = voice.get("subject")
        board.voice = Voice(
            audio=Path(clip) if clip else None,
            description=str(voice.get("description") or ""),
            subject=int(slot) if isinstance(slot, int) and slot > 0 else None,
            retention=(str(voice.get("retention") or "")
                       if str(voice.get("retention") or "") in RETENTION_AUDIO
                       else RETENTION_AUDIO_DEFAULT),
        )

    size = data.get("size") or {}
    width, height = int(size.get("width") or 0), int(size.get("height") or 0)
    if width and height:
        board.width, board.height = width, height
        # Matched against the preset table, so a size this version no longer
        # offers falls back to the default rather than to an empty menu.
        chosen = resolution(size.get("preset") or f"{width}x{height}")
        out.resolution_key = chosen.key
        if (chosen.width, chosen.height) != (width, height):
            out.notes.append(
                f"{width} × {height} is not one of the sizes this version "
                f"offers, so {chosen.pixels} was used instead.")
            board.width, board.height = chosen.width, chosen.height

    length = data.get("length") or {}
    if length.get("frames"):
        board.frames = int(length["frames"])

    board.normalise()
    out.storyboard = board

    speed = data.get("speed") or {}
    key = str(speed.get("profile") or "")
    if key in PROFILES:
        out.profile = get_profile(key)
    elif key:
        out.notes.append(f"“{key}” is not a speed this version has, so Turbo "
                         "was used.")

    seed = data.get("seed")
    if isinstance(seed, int):
        out.seed = seed

    return out
