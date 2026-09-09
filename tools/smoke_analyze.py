"""Exercise the prompt helper against a real Ollama, from a terminal.

    python tools/smoke_analyze.py [picture.png]

Does what the two buttons in the window do - describe a picture, expand an idea -
and then proves the part that matters on a single graphics card: that the model
is out of VRAM before ComfyUI would be asked for anything.

Prints the card's occupancy on either side of the handover, because "it says it
unloaded" and "it unloaded" are different claims.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config                                   # noqa: E402
from app.llm import prompts                                     # noqa: E402
from app.llm.ollama import (                                    # noqa: E402
    LlmError, Ollama, encode_image, free_vram,
)


def _resident(client: Ollama) -> str:
    models = client.loaded()
    if not models:
        return "nothing loaded"
    return ", ".join(f"{m.name} ({m.gigabytes} GB)" for m in models)


def main() -> int:
    cfg = Config()
    url = str(cfg.get("llm_url"))
    client = Ollama(url, timeout=int(cfg.get("llm_timeout") or 120))

    if not client.is_alive(fresh=True):
        print(f"Nothing is answering at {url}. Is Ollama running?")
        return 1

    available = client.models()
    if not available:
        print(f"{url} has no models. Try:  ollama pull qwen2.5vl:7b")
        return 1

    model = str(cfg.get("llm_model") or "")
    if model not in available:
        vision = [n for n in available
                  if any(h in n.lower() for h in ("vl", "vision", "llava", "moondream"))]
        model = vision[0] if vision else available[0]

    print(f"server     : {url}")
    print(f"model      : {model}")
    print(f"before     : {_resident(client)}")
    print()

    picture = Path(sys.argv[1]) if len(sys.argv) > 1 else None

    # 1. Describe a picture, the way the ✨ on a subject slot does.
    if picture and picture.is_file():
        system, prompt = prompts.describe_picture("animal")
        started = time.time()
        try:
            answer = client.generate(model, prompt, system=system,
                                     images=[encode_image(picture)], max_words=60)
        except LlmError as e:
            print(f"describe   : FAILED - {e}")
            return 1
        print(f"describe   : ({time.time() - started:.0f}s) {prompts.tidy(answer)}")
        print(f"  resident : {_resident(client)}")
        print()
    else:
        print("describe   : skipped (no picture given)")
        print()

    # 2. Expand a rough idea, the way the ✨ under the prompt box does.
    system, prompt = prompts.expand_idea("a fox in the snow", 5.17, 160)
    started = time.time()
    try:
        answer = client.generate(model, prompt, system=system, max_words=160)
    except LlmError as e:
        print(f"expand     : FAILED - {e}")
        return 1
    print(f"expand     : ({time.time() - started:.0f}s)")
    for line in prompts.tidy(answer).splitlines():
        print(f"  {line}")
    print()
    print(f"resident   : {_resident(client)}")

    # 3. The handover. This is the whole point on a single card.
    print()
    started = time.time()
    freed, note = free_vram(client, wait=int(cfg.get("llm_free_wait") or 30),
                            on_status=lambda m: print(f"  {m}"))
    print(f"free_vram  : freed={freed} in {time.time() - started:.1f}s")
    if note:
        print(f"  note     : {note}")
    print(f"after      : {_resident(client)}")
    return 0 if freed else 1


if __name__ == "__main__":
    raise SystemExit(main())
