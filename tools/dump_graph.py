"""Write out the graph the app would send, without sending it.

    python tools/dump_graph.py turbo out.json

The result loads straight into ComfyUI through Workflow -> Open (API), so what
the app actually submits can be inspected and diffed against the workflow it
started from. Useful when a render comes out wrong and the question is whether
the app built the graph badly or the model simply did that.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.h3 import graph as h3graph                     # noqa: E402
from app.h3.presets import profile as get_profile       # noqa: E402
from app.h3.timeline import Shot, Storyboard, Subject, Voice   # noqa: E402

WORKFLOW = Path(__file__).resolve().parent.parent / "workflows" / \
    "minimax_h3_director.api.json"


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "turbo"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("patched.json")

    board = Storyboard(
        global_prompt=("Cinematic desert chase at golden hour, warm sand "
                       "against a deep blue sky.\n"
                       "Audio: a low engine rumble and wind on the microphone."),
        shots=[Shot("wide shot: the rider crests the dune", 40),
               Shot("low tracking shot alongside the bike", 44),
               Shot("close-up on her face as the engine drops away", 40)],
        frames=124, width=768, height=768,
    )
    if "--reference" in sys.argv:
        board.subjects[0] = Subject(image="rider.png",
                                    description="a woman in a leather jacket",
                                    short_name="the rider")
        uploaded = {0: "rider.png"}
    else:
        uploaded = {}

    voice_name = ""
    if "--voice" in sys.argv:
        board.voice = Voice(audio="voice.wav",
                            description="a low, calm woman's voice", subject=1)
        voice_name = "voice.wav"

    with open(WORKFLOW, "r", encoding="utf-8") as f:
        raw = json.load(f)

    patched, report = h3graph.apply(
        raw, board, get_profile(which), uploaded=uploaded,
        uploaded_voice=voice_name, seed=1234,
        attention_options=["pytorch attention", "comfy kitchen attention"])

    with open(out, "w", encoding="utf-8") as f:
        json.dump(patched, f, indent=2, ensure_ascii=False)

    roles = h3graph.resolve(patched)
    director = patched[roles.director]["inputs"]
    timeline = json.loads(director["timeline_data"])

    print(f"wrote {out}")
    print(f"  profile        {report.profile}")
    print(f"  lora strength  {patched[roles.turbo_lora]['inputs']['strength_model']}")
    print(f"  steps          {patched[roles.scheduler]['inputs']['steps']}")
    print(f"  spectrum       {patched[roles.spectrum]['inputs']['enabled']}")
    print(f"  attention      {patched[roles.attention]['inputs']['attention']}")
    print(f"  seed           {report.seed}")
    print(f"  canvas         {director['custom_width']} x {director['custom_height']}")
    print(f"  length         {director['duration_frames']} frames "
          f"({director['duration_seconds']:.2f} s)")
    print(f"  reference mode {timeline['reference_mode']}")
    print(f"  shots          {[s['length'] for s in timeline['segments']]}")
    print(f"  audio track    {director.get('use_custom_audio')}")
    for segment in timeline["audioSegments"]:
        print(f"  voice          {segment['audioFile']} · frames "
              f"{segment['start']}-{segment['start'] + segment['length']} · "
              f"subject {segment['subject'] or 'none'} · {segment['retention']}")
    for note in report.notes:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
