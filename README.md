# EasyMiniMax

A plain front end for the **MiniMax H3 Director** workflow in ComfyUI, built on
the same foundations as EasyAI.

MiniMax H3 makes short video **with its own sound**, and it takes direction in
shots rather than in one long paragraph. This program puts that in front of you
as three columns — describe it, cut it into shots, press Create — and hides the
parts that are not decisions: which of two 21 GB checkpoints to load, what a
17k+5 frame grid is, and which four numbers have to move together to make it
fast.

```
┌─ your video ──────────┬─ shots ────────┬─ result ─────┐
│ what happens          │ the strip      │ live preview │
│ reference pictures    │ a box per shot │ what you made│
│ a reference voice     │                │              │
│ ⚡ Turbo / ✨ Quality   │                │              │
│ shape · length · seed │                │              │
│ [ Create ]            │                │              │
└───────────────────────┴────────────────┴──────────────┘
```

## Installing it

Run **EasyMiniMax Setup.exe**, point it at your EasyAI folder, and press
Install. It needs no Python, no administrator, and no `git`.

It puts both programs together and keeps their files apart:

```
<your EasyAI folder>  EasyAI.exe                     already yours
  settings.json                  EasyAI's - read once, never written
  EasyMiniMax.exe                ← installed
  EasyMiniMax Setup.exe          ← installed, so you can run it again later
  EasyMiniMax\                   ← everything of ours
    settings.json
    workflows    output```

The subfolder is not tidiness. Both programs work out where their settings live
from the folder their exe is in, so sharing one folder would mean sharing one
settings.json — they would overwrite each other's window position and carry each
other's settings around for ever.

The installer fetches, skipping anything already present:

| | |
|---|---|
| the two ComfyUI add-ons | a few hundred KB, downloaded as zips — no `git` needed |
| Ollama | ~1.6 GB, the official installer run quietly; no administrator |
| `qwen2.5vl:7b` | ~6 GB, the vision model behind the ✨ buttons |

The last two are tick-boxes, on by default. Untick them to save 7.6 GB — the
video side works perfectly without them, and the ✨ buttons simply stay hidden.

**Where ComfyUI is** comes from EasyAI's settings.json, read once so you are not
asked a question you have already answered. After that our own settings win: point
EasyMiniMax at a different ComfyUI and it stays there, whatever EasyAI later says.
EasyAI's file is never written to.

### Running it from source

Double-click **EasyMiniMax.bat** (or **EasyMiniMaxSetup.bat**). The first run
installs the Python libraries it needs. Requirements: Python 3.10+ and a working
ComfyUI (0.32 or newer).

### Building it

```bash
"Build EXE.bat"
```

Builds `dist\EasyMiniMax.exe` and then `dist\EasyMiniMax Setup.exe`, in that
order — the installer carries the program inside itself, so the program has to
exist first. The spec refuses to build if it does not.

## Turbo and Quality

One switch, four values, because they only make sense together:

| | Turbo | Quality |
|---|---|---|
| Turbo LoRA strength | 1.0 | 0 |
| Sampling steps | 10 | 20 |
| Spectrum accelerator | on | off |
| Attention | comfy kitchen | pytorch |

Turbo is roughly twice as fast and is the right place to stay while you are
trying ideas out. Quality follows your description a little more closely.

The switch is not a black box — **What this changes** under it lists all four
values, so a step count you read about somewhere is findable rather than hidden.

`comfy kitchen attention` only exists when the comfy_kitchen int8 module is
installed, and it is known to fail on some int8 checkpoints. The program checks
the running ComfyUI first and quietly falls back to `pytorch attention` **with a
note in the results panel** rather than letting the render fail — Turbo is then
a little slower than it could be, and the video is unaffected.

## Shots

The Director compiles your shots into a storyboard prompt with explicit time
markers, which is the model's own native mechanism for timed control — H3's text
encoder was trained on exactly that shape. A five-second clip cut into three
shots follows direction noticeably better than the same clip described in one
paragraph.

Drag the dividers on the strip to change how long each shot lasts. Frames move
between neighbouring shots, so the total never changes. Leave the list empty for
a single shot across the whole clip.

## Reference pictures

Attach a picture to keep a person, animal or outfit consistent through the clip.
Say what it shows and what to call it, so a shot prompt can say "the rider leans
into the turn" and have that land on the right face.

This switches the render onto a second, larger checkpoint (ref2va rather than
fl2va), so the first render after adding or removing a picture takes noticeably
longer. The panel says so rather than letting it look like a fault.

## Shapes and sizes

The size list is the Director's own preset table, copied pixel for pixel from
its editor (`js/minimax_director.js`), in three bands:

- **Native — 768 short edge** (13 shapes, 21:9 through 9:21). The default is
  16:9 at 1344 × 768.
- **Fast — 480 short edge** (13 shapes). Quicker and lighter on memory, at a
  visible cost in detail.
- **Past native** — 1920 × 1088 only. Bigger than the model was trained for:
  slow, heavy, and often worse rather than better.

The two bands that cost something say so under the list. The editor's *Custom*
entry is left out — it means `0 × 0`, "take the size from the first timeline
image", and this program always has a size to give.

## Lengths

H3 only accepts frame counts on a 17k+5 grid, so asking for 5 seconds renders
124 frames — 5.17 seconds. The snapped figure is what the duration control shows,
because a program that says 5 and produces 5.17 looks broken, where one that says
5.17 up front just looks precise.

The model was trained for 4 to 15 seconds. Shorter still renders, and the control
says so rather than leaving stiff-looking movement to be blamed on the prompt.

## What each render leaves behind

Three files per render, sharing one name:

```
2026-08-24_231532_turbo.mp4        the video, with its sound
2026-08-24_231532_turbo_lastframe.png   the final frame
2026-08-24_231532_turbo.json       every setting that made it
```

The **last frame** is what you feed back in to carry a scene on into the next
clip, so it is saved every time rather than only when you think to ask. It comes
from the add-on's own `MiniMax H3 Save Last Frame` node, spliced in ahead of the
video muxer — it passes the whole batch through untouched, so the video is made
from exactly the same frames whether the saver is there or not.

The **settings file** is plain, readable JSON — names a person would use, not
node ids — holding the prompt, the shots, the reference pictures, the speed
setting and all four of its values, the size, the length and the seed.
**File → Load settings from a video…** (`Ctrl+O`) puts every control back where
it was, so a video you liked becomes a starting point rather than a dead end.

It is not a ComfyUI workflow and does not try to be; `tools/dump_graph.py` is
there for that. Loading is forgiving: a file from an older version, or one
hand-edited, restores what it can and tells you what it could not — a reference
picture that has moved, a size this version no longer offers.

## Reference voice

H3 generates its own sound, so this is not a soundtrack to lay over the video —
it is a reference for how the speaking should sound. Attach a clip of someone
talking and the model follows its voice and timbre. You can tie it to one of the
reference pictures, and the compiled prompt then says *"this voice belongs to
that face"* rather than leaving the model to guess.

Like a reference picture, a voice clip switches the render onto the ref2va
checkpoint — the planner only reads the audio track in reference mode, so a
voice attached without it would be uploaded and then silently ignored. The panel
says so, and warns if you bind the voice to a person who has no picture.

## Prompt helper (optional)

Two ✨ buttons, backed by a local [Ollama](https://ollama.com):

- **On each reference picture** — look at it and write the "what it shows"
  description. That field is the one beginners leave blank, and leaving it blank
  is what makes a reference picture do nothing.
- **Under the main prompt** — turn a rough line like *"a fox in the snow"* into a
  full description, including the `Audio:` line. It keeps what you wrote for one
  **Undo**.

The first time the program starts it looks once for a running Ollama with a
vision model, and switches the helper on if it finds one — so if you already
have it, the buttons are simply there. It records that it looked, so a machine
with no Ollama pays one refused connection, once, and never again.

Otherwise: **Settings → Prompt helper** — tick the box, press **Find models**,
pick a vision model. Every button stays hidden while nothing answers, because a
greyed button with no explanation is worse than no button.

```bash
ollama pull qwen2.5vl:7b
```

The prompts follow the same rules the Director's own Enhance node uses: prose
rather than lists, only what is visibly or audibly present, and never intent or
backstory — a video model cannot render "she feels uneasy".

## Graphics memory

A vision model and a 21 GB H3 checkpoint do not fit on one card together. So
before every render the program asks Ollama to let go — and then **waits until
`/api/ps` says it actually has**, because asking returns immediately whether or
not anything happened.

Everything resident is unloaded, not only what this program loaded: another
program's model in the card is the same problem. If the memory has not cleared
within the timeout the render still goes ahead, with a note — refusing to render
because memory could not be freed would be worse than a slow render.

There is an **AI engine → Free the graphics memory now** menu item for the same
thing on demand, and the status bar shows the card filling and emptying.

When the prompt helper is off — the default — none of this runs and costs
nothing.

## What it needs in ComfyUI

Two add-ons. The installer fetches both; the running program also offers to, if
one goes missing later:

| Add-on | Provides | Needed? |
|---|---|---|
| [ComfyUI-MiniMaxH3-Director](https://github.com/seesee75-commits/ComfyUI-MiniMaxH3-Director) | the timeline, the live preview, the last-frame saver | yes |
| [ComfyUI-Spectrum-MiniMax-H3](https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3) | the accelerator behind Turbo | optional |

`ModelAttentionBackend` is part of ComfyUI itself from 0.32.

Five model files, which are **not** downloaded automatically — they come to about
42 GB, and that is a decision to make with the size in front of you. If one is
missing the program names it and points at
[Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3).

| File | Folder |
|---|---|
| `minimax_h3_fl2va_*` | `models/diffusion_models/` |
| `minimax_h3_ref2va_*` | `models/diffusion_models/` |
| `qwen3vl_32b_minimax_h3_*` | `models/text_encoders/` (or `models/clip/`) |
| `minimax_h3_video_vae_*` | `models/vae/` |
| `minimax_h3_audio_vae_*` | `models/vae/` |
| `minimax_h3_turbo_*` | `models/loras/` |

## How it is put together

```
app/
  comfy/          copied from EasyAI: the ComfyUI client, launcher, /object_info
  h3/
    timeline.py   builds the timeline_data JSON  ← the heart of it
    graph.py      finds the nodes by role and writes one render's choices in
    presets.py    Turbo/Quality, the size table, the frame grid
    preflight.py  will this actually run here?
    recipe.py     the settings file saved beside each video
  llm/            the optional Ollama: describing, expanding, and getting
                  back out of the graphics card before ComfyUI needs it
  setup/nodes.py  fetching an add-on, from a zip or with git
setup/           the installer: finding EasyAI, Ollama, the model
  ui/             the window, the shot editor, the subject panel
  jobs.py         one render, on a thread
  queue.py        one at a time, because there is one graphics card
workflows/        the bundled API graph
```

The one thing worth knowing before reading the code: the Director node's
`local_prompts` and `segment_lengths` inputs are **dead**. `execute()` accepts
them and never reads them — they exist so its JavaScript editor has somewhere to
mirror its state. Every prompt, shot boundary and reference picture is parsed
out of the `timeline_data` string. So this app is not a patcher that writes
values into inputs; it is a program that writes that editor's save file. That is
what `app/h3/timeline.py` does, and why it has the longest comment in the
project.

## Checking it works

```bash
python -m pytest -q
```

To see the graph the app would send, without sending it:

```bash
python tools/dump_graph.py turbo patched.json
```

Add `--voice` or `--reference` to see what a voice clip or a reference picture
does to the graph.

That file loads straight into ComfyUI through **Workflow → Open (API)**, so when
a render comes out wrong you can tell whether the app built the graph badly or
the model simply did that.

To prove the whole pipeline against a live ComfyUI from a terminal:

```bash
python tools/smoke_render.py turbo 2
```
