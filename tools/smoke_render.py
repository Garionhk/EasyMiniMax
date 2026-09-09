"""Run one real render against the live ComfyUI, without the interface.

    python tools/smoke_render.py turbo

Does exactly what JobWorker does - upload, patch, queue, follow, save - but with
prints instead of Qt signals, so the pipeline can be proven end to end from a
terminal and in CI-ish conditions. If this works and the window does not, the
problem is in the window.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.comfy import objectinfo                        # noqa: E402
from app.comfy.client import ComfyClient                # noqa: E402
from app.config import Config                           # noqa: E402
from app.h3 import graph as h3graph                     # noqa: E402
from app.h3 import preflight                            # noqa: E402
from app.h3 import recipe                               # noqa: E402
from app.h3.presets import profile as get_profile       # noqa: E402
from app.h3.timeline import Shot, Storyboard            # noqa: E402
from app.jobs import OUTPUT_ROWS, _is_video             # noqa: E402


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "turbo"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0

    cfg = Config()
    client = ComfyClient(cfg.server)
    if not client.is_alive():
        print("ComfyUI is not answering at", cfg.server)
        return 1

    caps = objectinfo.fetch(client, refresh=True)
    with open(cfg.workflow_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    check = preflight.run(raw, caps)
    if not check.ok:
        print("preflight:", preflight.summarise(check))
        return 1
    print("preflight  : ok |", check.attention_options)

    from app.h3.presets import frames_for
    frames = frames_for(seconds)
    board = Storyboard(
        global_prompt=("A red fox trotting through fresh snow at dawn, soft "
                       "low sunlight, shallow depth of field.\n"
                       "Audio: crunching snow underfoot and a light wind."),
        shots=[Shot("wide shot: the fox crosses the frame left to right",
                    frames // 2),
               Shot("close-up: the fox stops and looks toward camera",
                    frames - frames // 2)],
        frames=frames, width=640, height=640,     # 1:1 fast, from the preset table
    )

    profile = get_profile(which)
    can_save_last = caps.has_node(h3graph.LASTFRAME)
    print("last frame :", "available" if can_save_last else "node missing")
    graph, report = h3graph.apply(raw, board, profile, seed=1234,
                                  attention_options=check.attention_options,
                                  save_last_frame=can_save_last)
    print(f"profile    : {profile.key} | steps={profile.steps} "
          f"lora={profile.lora_strength} spectrum={profile.spectrum}")
    print(f"length     : {report.frames} frames @ {board.width}x{board.height}")
    for note in report.notes:
        print("note       :", note)

    started = time.time()
    prompt_id, client_id = client.queue(graph)
    print("queued     :", prompt_id)

    last = [-1]

    def on_progress(pct: int, message: str) -> None:
        if pct != last[0]:
            last[0] = pct
            print(f"  {pct:3d}%  {message}", flush=True)

    finished = client.listen(client_id, prompt_id, on_progress=on_progress,
                             on_preview=lambda _b: None, timeout=5400)

    results = client.wait_for_results(prompt_id, want=OUTPUT_ROWS,
                                      finished=finished, timeout=5400)
    if not results:
        print("no output produced")
        return 1

    out_dir = cfg.output_dir()
    video, last_frame = None, None
    for item in results:
        stem = (f"smoke_{profile.key}" if _is_video(item.filename)
                else f"smoke_{profile.key}_lastframe")
        path = client.save_result(item, out_dir, stem=stem)
        size = Path(path).stat().st_size
        print(f"saved      : {path}  ({size/1e6:.1f} MB)")
        if _is_video(item.filename):
            video = path
        else:
            last_frame = path

    elapsed = time.time() - started
    if video:
        written = recipe.save(recipe.path_for(video), recipe.build(
            board, profile, seed=report.seed, frames=report.frames,
            elapsed=elapsed, notes=report.notes, video=Path(video).name,
            last_frame=Path(last_frame).name if last_frame else ""))
        print(f"settings   : {written}")

    print(f"elapsed    : {elapsed:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
