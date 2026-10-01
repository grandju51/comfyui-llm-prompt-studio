# LLM Prompt Studio for ComfyUI

A ComfyUI custom node that connects to a **local OpenAI-compatible LLM server**
(**LM Studio** or **vLLM**) and turns your idea into an **optimized prompt for a
specific image / video generator**.

It ships with editable, ready-to-use prompt "cards" for:
**Anima base v1**, **Illustrious**, **SDXL**,
**Qwen-Image 2.1** (two cards: *text to image* and *edit / i2i*),
**FLUX.2 Klein (9B)**,
**Krea 2 (Krea AI)**, **Ideogram**, **LTX-2 / LTX 2.3**, **Wan 2.2**,
**MiniMax H3 / Hailuo 3** (three cards: *normal*, *ref* and *edit*),
**ACE-Step 1.5 XL** and **YuE2** (a *style* card and a *lyrics* card each, to
wire into the music model's two separate inputs), plus a generic preset.

---

## Features

- **OpenAI mode**: works with LM Studio (`/v1`) and vLLM (`--api ... /v1`).
- **No model picking**: leave `model` on `(auto)` and the node automatically
  uses the chat model loaded at your address (text-encoder / embedding models
  are skipped). Nothing to type.
- **Model dropdown**: the `model` field itself is a list, chat models on top.
  It holds **every model on the server, loaded or not** — LM Studio's own API is
  asked for the whole catalogue, so you can switch between the models you have
  without going to load them by hand first; one that is not in memory is loaded
  on the first run that needs it. `(auto) use the loaded model` is the default
  and the one to leave alone; `✏️ type a name…` opens a box, filled with the
  current name, for anything the list does not have — a remote model, a proxy
  alias, a name that does not exist yet. The list refreshes when you change
  `base_url` or `api_key`, and **🔄 Refresh the model list** re-reads it.
- **Text boxes**:
  - `global_directives` – your own global rules, applied on top of the system
    prompt for **every** target model (e.g. "always add cinematic lighting").
    Stays put when you switch target models.
  - `system_prompt` – the LLM "card" (how to write the prompt; preset per model).
  - `user_prompt` – your chat message / idea.
- **Target model dropdown** – pick the generator; its English preset auto-loads
  into `system_prompt` and stays **fully editable**. (Leave `system_prompt`
  empty to always use the current preset.)
- **Sampling controls**: `temperature`, `top_p`, `top_k`, `min_p`,
  `repeat_penalty`, `seed` (with `control_after_generate`). `min_p` and the
  repetition penalty each have their own on/off switch.
  The defaults — `temperature 1.0`, `top_p 0.95`, `top_k 20`, `min_p 0`,
  `repeat_penalty 1.0` (neutral, so nothing is sent) — are the production
  settings Qwen publishes for its own prompt-rewriting checkpoints
  ([`prompt_rewrite/README.md`](https://github.com/QwenLM/Qwen-Image-2.1/tree/main/prompt_rewrite)),
  which are also Qwen3's published values for writing. The repetition penalty is
  neutral on purpose: a prompt card asks for its own vocabulary over and over
  (positional phrases, a fixed closing sentence), and penalising that walks the
  model off the format halfway through. Qwen controls repetition with
  `presence_penalty` instead — 1.5 for text-to-image, 0 for editing — which is
  different math and not what `repeat_penalty` sends.
- **Output tokens**: `max_tokens` caps the length of the generated answer,
  **thinking included**. It defaults to 4096: a long card (Qwen-Image 2.1 asks
  for 400–500 words, MiniMax H3 for a whole structured block) plus a reasoning
  block does not fit in 1024, and the prompt comes back cut off mid-sentence.
- **Always English**: every card writes the prompt in English whatever language
  you type in — except the two *lyrics* cards, which write the words to be
  sung and so follow the language you ask for — including the Qwen-Image 2.1 edit card, where Qwen's own rule
  would answer a Chinese instruction in Chinese. Text *rendered inside the
  image* is the one exception: it follows the words or the language you asked
  for, or the language the input image already uses, and falls back to English
  when you named neither.
- **Thinking control** (`auto` / `off` / `on` / `on - low…xhigh effort`): `off`
  states "no thinking" in every dialect at once — `/no_think` +
  `enable_thinking=false` (**Qwen3.x**, GLM), `thinking=false` (**DeepSeek
  V3.1+** on vLLM/SGLang), and the **non-thinking model alias** for DeepSeek.
  The ` /no_think` trigger goes only to the templates that read it — Gemma,
  Mistral, Llama, gpt-oss, MiniMax & co get the switches alone.
  The `on - … effort` entries are thinking ON **plus a level** — the dial
  **Qwen3.8** added on top of the on/off switch — *translated to what each
  family reads*: `reasoning_effort` capped at `xhigh` for Qwen3.8, at `high` for
  DeepSeek and gpt-oss, a `thinking_budget` token cap for Qwen3.x before 3.8,
  and nothing at all for the families without a dial. Gemma has no thinking mode
  → use `auto`/`off`. See
  [Thinking levels (Qwen3.8 and co)](#thinking-levels-qwen38-and-co) and
  [Real "no think" (DeepSeek & co)](#real-no-think-deepseek--co).
- **`no_think_model`** (optional): the model alias called **instead** when
  thinking is `off`. Empty = auto.
- **Custom cut tag(s)** (`strip_before_tag`): everything **up to and including**
  the tag is removed from the output. Default `</think>,</mm:think>` strips a
  model's reasoning block (the second one is MiniMax M3's tag). You can list
  **several tags separated by commas**
  (e.g. `</think>,</thinking>,</reasoning>`) — the comma is only a separator,
  and whichever tag ends furthest into the text is used as the cut point. This
  is the robust safety net for hiding thinking.
- **Story mode** (`story_mode` + `story_sheet`): for a series of images that
  tells one story. The node keeps a **sheet** — each character's looks, outfit
  and accessories, the setting, the scene, the style — and `user_prompt` becomes
  a **change request** against it:

  1. `a red-haired woman in a blue coat on a rainy street` → builds the sheet.
  2. `give her a pink hat instead` → only the hat line changes.
  3. `put her in a forest, one hand in her pocket, facing a tree with a mouth
     and eyes` → the setting and the pose change; the coat and the pink hat stay.

  Looks, clothes and accessories never change unless you name them; the setting
  stays until you give another; the pose is rewritten from your request, and
  kept when you only ask for something else. The prompt is rewritten **in full**
  from the sheet on every run, so nothing is ever forgotten, however long the
  story gets. The sheet sits in the `story_sheet` box: it is saved with the
  workflow, you can **edit it by hand**, **🆕 New story** empties it and
  **↩ Undo** takes back the last change. It also comes out of the third output.
  Made for text-to-image cards (pair it with a LoRA for the face). While it is
  on, `keep_history` is not used. The sheet and the prompt are both written out
  of `max_tokens`: raise it if the node reports a sheet with no prompt.
- **Conversation memory**: `keep_history` for multi-turn chat,
  `max_history_turns` to set how many past turns are remembered (context depth),
  and `reset_history` to clear it.
- **As many image inputs as you connect**: the node shows `image` and grows a
  fresh empty socket (`image_2`, `image_3`, …) the moment the last one is
  filled, so you are never capped — unwiring the bottom ones takes the spares
  away again. Each connected socket is announced to the LLM as **`<Picture 1>`,
  `<Picture 2>`…** following the socket order, and the label is sent **just
  before its own image** so the model binds the two. Empty sockets are skipped,
  so `image` + `image_3` still gives you `<Picture 1>` and `<Picture 2>` —
  never a gap. `<Picture N>` is MiniMax H3's own reference label, so the H3
  cards can cite an exact image instead of "the second one"; the Qwen-Image 2.1
  edit card rewrites it into Qwen's own `<image1>` syntax, and any vision model
  reads it just as well. These are link sockets, not widgets, so they can't
  shift a saved workflow.
  <br>Eight sockets are declared server-side so the node still works with the
  front-end extension disabled; past those, the sockets the UI adds are
  resolved by name (`_PictureSlots` in `nodes.py`), which is what keeps an
  image wired into `image_23` from being silently dropped.
  <br>Note that **Qwen-Image 2.1 itself reads up to 10 reference images** — the
  node will happily send more, but the image model is the limit that counts.
- **No invented pictures**: the node counts the connected sockets and states it
  in the system prompt at request time — *"exactly 2 pictures are connected,
  labelled `<Picture 1>`, `<Picture 2>` … never cite `<Picture 3>`"*, or *"no
  image is connected, never mention one"* when the sockets are empty. Without
  it, a model reads a `<Picture 3>` sitting in a card's formatting example and
  cites an image it was never shown. The line is computed per run, so it always
  matches what is actually wired — you never have to edit the card for it.
- **Audio reference** (`audio` socket + `audio_role` dropdown): declares to the
  LLM that an audio signal exists and is labelled **`<Audio 1>`**, and what it
  is *for*, in MiniMax H3's own vocabulary. The node does **not** listen to the
  track — it carries the relationship the prompt has to state:

  | `audio_role` | Retention marker | Task type added to `summary` |
  |---|---|---|
  | reuse in full | `fully_copy` | `audio reuse` |
  | reuse in part | `partially_copy` | `audio reuse` |
  | reference the style, timbre or rhythm | `reference` | `audio reference` |
  | loose atmosphere only | `weak_reference` | `audio reference` |
  | voice timbre reference for a speaker | `reference` | `audio reference` |

  Picking anything but `none` declares `<Audio 1>` **even with nothing wired**,
  which is what you want when the track reaches the video model further down the
  graph. Leave the role on `none` but connect the socket and the LLM is told to
  infer the role from your request. With neither, not a word about audio is
  added — the nine non-video cards stay clean.
- **Video input** (`video` socket + the `video_*` widgets): connect the `IMAGE`
  batch a video loader outputs and the node **samples frames out of it** and
  shows them to the vision model, labelled `<Video 1> frame 3 of 8`. That is the
  cure for the model inventing a clip it never saw.

  | Widget | What it does |
  |---|---|
  | `video_stride` | keep 1 frame out of N (`4` by default) |
  | `video_max_frames` | hard cap, spread over the **whole** clip — `8` by default, `0` = send no frame at all |
  | `video_frame_size` | longest side per frame: `512`, `384`, `256`, `128`, `768`, `1024 px`, `original` |
  | `video_fps` | the clip's frame rate; set it and each frame gets its real `MM:SS.mmm` timestamp, so H3's `At 00:02.400` lands on a real time. `0` = unknown |
  | `video_role` | what the video is *for* (see below) |

  The cap thins the strided selection **evenly across the clip** instead of
  taking the first N — sampling only the opening seconds is the surest way to
  make the model guess the rest. Above ~16 frames the request gets heavy
  (≈700 vision tokens per frame at 512 px); the node prints a warning and obeys,
  the ceiling is yours. Set `video_max_frames` to `0` when the writer is a
  **text-only LLM**: the video is still declared as `<Video 1>`, but the prompt
  is told plainly that it was *not* seen and must reuse your own words about it
  rather than describe it.

  | `video_role` | Declared as | Task type added to `summary` |
  |---|---|---|
  | edit the source video | `<Video 1> is the source video for the target video edit.` | `video editing` |
  | continue from the source video | `…the source video the target video continues from.` | `video continuation` |
  | keep its motion, cuts and rhythm | `…the reference for camera movement, cuts and temporal structure.` | none — retention line only |
  | loose atmosphere or style only | `…a loose atmosphere reference.` | none — retention line only |

  Like `audio_role`, picking anything but `none` declares `<Video 1>` even with
  nothing wired — for when the clip goes straight to the video model further
  down the graph. Frames are sent **after** the pictures and are announced as
  one timeline, never as extra `<Picture N>`.
- **Image analysis size** (`image_analysis_size`): downscale the images before
  they are sent — `original`, `2 MP`, `1.5 MP`, `1 MP`, `768 px`, `512 px`.
  The `MP` presets keep the aspect ratio and target a total pixel count; the
  `px` presets cap the longest side. Images already smaller than the target are
  never upscaled. It applies to every connected socket, which matters once you
  send 5–8 of them.
- **`unload_after`** (LM Studio only, off by default): turns the run into a full
  **load → prompt → unload** cycle. The model is put in memory before the
  request (only if it isn't there already — asking twice would build a *second*
  instance and cost the VRAM twice) and dropped from it as soon as the answer is
  in, so the Flux/SDXL/Wan model further down the graph gets the VRAM back.
  It uses LM Studio's own API, which lives next to the `base_url` you typed
  (`http://localhost:1234/v1` → `POST http://localhost:1234/api/v1/models/unload`
  with `{"instance_id": …}`), so an **LM Studio 0.3.30+** is required — an older
  build or a vLLM server answers `404` and the node just prints a line, the
  prompt is never affected. Every instance of that model is unloaded, and the
  unload also happens when the request **fails**: a model loaded by a run that
  then died is exactly the one still sitting on the memory you need. Leave it
  **off** while you iterate on a prompt — reloading costs several seconds (or
  minutes, on a big model) on every run.
- **`context_length` + `context_slots`** (LM Studio only, `0` = leave its own
  settings alone): how much memory the model books when the node loads it. This
  is the VRAM knob, and it is **not** the prompt size — LM Studio reserves
  `context_length` tokens **per slot**, so what a model really books is
  `context_length × context_slots`, sitting **next to** the weights:

  ```
  srv load_model: initializing, n_slots = 4, n_ctx_slot = 60416
  → 241 664 tokens reserved, + 17 GB of weights, on a 24 GB card
  ```

  That card then runs out **in the middle of a prompt**, and the engine does not
  fail gracefully — it aborts. You get `Error: Channel Error` in the LM Studio
  log and `{"error":"terminated"}` (then `Engine protocol predict request
  failed`) in the node. Setting `context_length` to what a prompt actually needs
  (a picture + a few turns of history fits in ~8k) and `context_slots` to `1`
  (ComfyUI sends one request at a time; the usual default of 4 quadruples the
  reservation for nothing) is what stops it. The size is **fixed when the model
  loads**, so a copy already in memory at another size is unloaded and loaded
  again — the node says so in the console.
- **Server status dot + `on_llm_offline`**: a dot at the right of the node's
  title is **green** when the LLM answers at `base_url`, **red** when it does
  not (checked every 10 s, grey until the first check). When a run finds the
  server off, the workflow no longer gets an error text as its prompt:
  - `reuse last prompt` (default): sends the last prompt this node wrote for
    this target model. It is saved in `last_prompts.json`, so it survives a
    ComfyUI restart. On a node that has never produced one, the run stops.
  - `pass user_prompt through`: sends your `user_prompt` as typed, for when it
    is already a prompt that the LLM only improves.
  - `stop with an error`: the run stops.

  Either way a warning toast pops up, the on-node preview starts with
  `⚠ LLM OFFLINE`, and `raw_response` says why. A fixed seed does not keep the
  stand-in cached: the node runs again on the next queue. A server that is up
  but too slow (`timeout`) is still an error, not an offline server.

Outputs:
- `prompt` – the cleaned text (after the cut tag, **no thinking**). It is a
  `STRING`, so you can wire it into a CLIP Text Encode, an API node, **or any
  Text / Show-Text / Text-Preview node**.
- `raw_response` – the full untouched answer (for debugging the thinking).
- `story_sheet` – the story sheet as it stands after the run (empty when
  `story_mode` is off).
- **On-node preview**: after each run the node also shows the generated prompt
  (cleaned, no thinking) right on itself, so you can read it without wiring a
  preview node.

---

## Second node: `Load Image + Prompt (Civitai/A1111/ComfyUI)`

Loads an image **and recovers the prompt it was generated with** from the
metadata embedded in the file. Nothing is encrypted — it is plain text stored
in fields image viewers simply don't display.

Outputs: `image`, `mask`, `positive`, `negative`, `raw_metadata`.

> **Why a loader and not an `IMAGE` input?** A ComfyUI `IMAGE` is a decoded
> pixel tensor — all metadata is stripped the moment the file is loaded. The
> prompt can only be read from the **file**, so this node opens it itself. It
> is a drop-in replacement for `LoadImage` (same dropdown + upload button).

Supported sources, tried most-explicit first:

| # | Source | Where it lives |
|---|--------|----------------|
| 1 | **Civitai `extraMetadata`** | JSON blob with explicit `prompt` / `negativePrompt`, at the workflow root **or** under `extra` |
| 2 | **A1111 / Civitai block** | PNG `parameters` chunk, or EXIF `UserComment` (0x9286, `UNICODE\0` + UTF-16) |
| 3 | **ComfyUI API graph** | PNG `prompt` chunk, or JSON in EXIF |
| 4 | **ComfyUI UI graph** | PNG `workflow` chunk (widget values, best-effort) |

For ComfyUI graphs the positive prompt is found by **following the sampler's
`positive` link** and walking through passthrough nodes (ControlNet,
ConditioningCombine, primitives…). Grabbing "the first `CLIPTextEncode`" would
frequently return the *negative* prompt instead.

`image_path` (optional) overrides the dropdown to read any file on disk.

**`strip_lora_tags`** (on by default) removes network tags from both outputs —
`<lora:Train_Entrance_SDXL-000027:1>`, `<lyco:…>`, `<hypernet:…>`, any weight
including negative ones. The separators the removal orphans are cleaned up
(`1girl, <lora:x:1>, solo` → `1girl, solo`), and a tag alone on its line takes
the line with it instead of leaving a hole. Trigger words next to a tag are
kept — only the tag itself goes. Turn it off to get the prompt verbatim.

Tested on 4654 local images: **4635 prompts recovered (99.6%)**. The remainder
genuinely contain none — screenshots, and `LoadImage → RemBg → SaveImage` style
workflows with no sampler.

---

## Third node: `Text Preview + Token Count`

Shows a text on the node — like any preview node — and tells you **how many
tokens it is**. Outputs: `text` (passthrough), `tokens` (INT), `report`
(STRING).

Four ways to count, picked with the `tokenizer` dropdown:

| Mode | Needs | Good for |
|------|-------|----------|
| **estimate** | nothing | a number right now, within ~15% |
| **CLIP (connected)** | a `CLIP` link | the *exact* count your image model sees |
| **tiktoken** | `pip install tiktoken` | OpenAI-style budgeting (o200k / cl100k) |
| **LLM server /tokenize** | a vLLM server | the exact count for the model that will read the prompt |

`auto` takes the connected CLIP if there is one, else tiktoken if it is
installed, else the estimate. Anything that fails (no tiktoken, server down)
falls back to the estimate and says so in the report instead of erroring out.

**`token_limit`** turns the count into a budget: the report says how many
tokens are left, or how far over you are — in red. `0` disables it. Use `75`
for a CLIP chunk. When counting through CLIP, going past 75 also prints how
many chunks the encoder will split the prompt into, which is the thing that
quietly weakens a long prompt.

The CLIP count works out of the box on CLIP-L, CLIP-G, T5 and anything added
later: the padding and the start/end markers are measured from the encoder
itself (by tokenizing an empty string) rather than hard-coded per flavour.

---

## Fourth node: `Token Count (simple)`

The same estimate, stripped of everything else: a text in, `~123 tokens` on
the node. No dropdown, no CLIP to connect, nothing to install. Outputs `text`
(passthrough) and `tokens` (INT).

Take this one unless you specifically need an exact count.

---

## Install

1. Copy the **`comfyui-llm-prompt-studio`** folder into
   `ComfyUI/custom_nodes/`.
2. Restart ComfyUI and refresh the browser.
3. No `pip install` needed (the node only uses the standard library; PIL/torch,
   used for the optional image input, already come with ComfyUI).

The node appears under **Add Node → LLM Prompt Studio**, named
**“LLM Prompt Studio (LM Studio / vLLM)”**.

---

## Quick start

### LM Studio
1. Load a model and start the local server (Developer tab → *Start Server*).
2. `base_url` = `http://localhost:1234/v1` (default).
3. `api_key` can be anything (e.g. `lm-studio`).
4. Leave `model` on **`(auto) use the loaded model`**, or pick one in the list
   (press **🔄 Refresh the model list** if it looks stale).

### vLLM
```bash
vllm serve Qwen/Qwen3-8B --port 8000          # add --api-key YOURKEY if you want auth
```
1. `base_url` = `http://localhost:8000/v1`.
2. `api_key` = your `--api-key` (or leave default if none).
3. Leave `model` on `(auto)`, or pick one in the list.

### Then
- Choose your **target_model** (e.g. *Illustrious*). The matching English prompt
  card loads into `system_prompt` – edit it however you like.
- Type your idea in **user_prompt**.
- Connect **`prompt`** to your text encoder / image node and queue.

---

## Notes & tips

- **Keeping the same prompt (2-pass / hires workflows)**: set `seed` to
  **fixed** and the node is cached — the next queue reuses the prompt it already
  wrote instead of calling the LLM again. `control_after_generate` on
  *randomize* is what makes it write a new one every run. Inside a single
  workflow you never need this: wire the one `prompt` output to both passes'
  text encoders and the LLM is called once.
- **Thinking / Qwen3.x**: with `thinking = off`, the node appends `/no_think`
  and asks vLLM to disable the reasoning template. Even if a model still emits a
  `<think>…</think>` block, the `strip_before_tag` cleanup removes it from
  `prompt`. For models that use other reasoning tags, list them all
  comma-separated, e.g. `</think>,</thinking>,</reasoning>`.
- **An empty prompt is reported, never returned.** A thinking model that runs
  out of budget answers with a reasoning block and nothing else — the cut tag
  then leaves an empty string, and an empty prompt silently poisons the image
  node below. The node says so instead, naming `finish_reason = length` when
  that is what happened, and puts the reasoning it did get on `raw_response`.
  Raise `max_tokens`, drop to a lower effort level, or turn thinking off.
- **min_p** is sent as a top-level field (supported by LM Studio/llama.cpp and
  vLLM). `0.0` disables it. A common setup is `min_p = 0.05-0.1` with
  `top_p = 1.0` so min-p does the filtering.
  **It does not work on a vLLM server running speculative decoding** (a draft
  model or n-gram proposer): vLLM itself refuses it with
  `The min_p and logit_bias sampling parameters are not yet supported with
  speculative decoding` and generates nothing. That is a property of how the
  server was launched, not of the model — either restart vLLM without the
  speculative config, or turn `enable_min_p` off and filter with `top_p` /
  `top_k`.
- **top_k / repeat_penalty** are sent as top-level fields. Both
  `repetition_penalty` (vLLM) and `repeat_penalty` (LM Studio/llama.cpp) are
  included so each backend uses the one it understands.
- **Extension fields are only sent when they do something.** `top_k`, `min_p`
  and the two penalty spellings are *not* part of the OpenAI schema, and a
  backend that validates its request body answers **400** on the first one it
  doesn't know — generating nothing at all, which looks from ComfyUI like the
  request was simply cancelled. So a neutral value (`top_k = 0`, `min_p = 0`,
  `repeat_penalty = 1.0`) is left out of the request entirely. The two switches
  at the bottom of the node, **`enable_min_p`** and **`enable_repeat_penalty`**,
  are the manual override: off means the field is never sent whatever the
  slider says, so a tuned value can stay parked there while you talk to a
  backend that refuses it. If a 400/422 — or a **500**, which is how a chat
  template refusing one of these values surfaces — still comes back, the node
  **retries once without every extension** —
  including the thinking switches — and prints exactly what it dropped. You get
  an answer instead of a dead end; the cut tag still strips any reasoning block
  the retry let through.
- **Multi-turn**: turn on `keep_history`. Each queued run appends a turn, and
  `max_history_turns` controls how many past turns are remembered (context
  depth). Flip `reset_history` on (and queue once) to wipe the memory. What is
  stored is the **clean** turn: your idea without the `/no_think` trigger, and
  the answer after the cut tag — a reasoning block belongs to the turn that
  produced it, and Qwen's own guidance is to keep it out of the history.
- **Context window vs. output**: `max_tokens` sets the **output** length. The
  model's raw **context window** (how much it can read in) is fixed when you
  load the model in LM Studio / vLLM, not per request — set it there.
- **Vision**: connect one or more images and use a multimodal model (e.g. a
  Qwen-VL or Gemma vision model) to caption/describe them into a prompt. Refer
  to them in `user_prompt` by the labels they arrive with: *"`<Picture 1>` is
  the first frame, `<Picture 2>` the last one"*.
- **Model presets are editable defaults** — tweak them in the box, or edit the
  source defaults in `prompt_templates.py`.
- **Resizable text boxes** — drag the bottom-right corner of `global_directives`,
  `system_prompt`, `user_prompt` or the result preview to give each one the
  height it deserves; the node grows to match and the heights are saved with the
  workflow. (They live in the node's `properties`, never in `widgets_values`,
  which is positional.) ComfyUI ships those textareas with `resize: none` and
  recomputes their height on every redraw, so the extension both restores the
  handle and feeds the dragged height back into the widget layout.

---

## Thinking levels (Qwen3.8 and co)

Qwen3.8-Flash-Next thinks **by default** and exposes a *reasoning effort* dial
instead of a plain on/off: `low`, `medium`, `xhigh` (its own default). The
`thinking` widget carries it — `on - low effort`, `on - medium effort`,
`on - high effort`, `on - xhigh effort` — next to `enable_thinking: true`, so
one entry states the mode and the depth at once. `on (force thinking)` sends no
level and leaves the model on its own default.

### The level is translated, never forwarded blindly

The rungs are **not the same from one family to the next**, and a family does
not politely ignore a rung it does not know: a Qwen3.8 asked for
`reasoning_effort: "high"` answers **HTTP 500** — its ladder stops at `xhigh`.
So the node reads the model name and sends what that family actually
understands:

| Family (by model name) | What goes out | Rungs |
|---|---|---|
| **Qwen3.8** — `qwen3.8*`, `*flash-next*` | `reasoning_effort`, top level **and** in `chat_template_kwargs` | `low` · `medium` · `xhigh` — **`high` → `xhigh`** |
| **DeepSeek** — `*deepseek*` | `reasoning_effort` (+ its `thinking: {"type": "enabled"}` block) | `low` · `medium` · `high` (its default) — **`xhigh` → `high`** |
| **gpt-oss, Phi, o-series** | `reasoning_effort` | `low` · `medium` · `high` — **`xhigh` → `high`** |
| **Qwen3.x before 3.8** — `qwen3*`, `qwen-3*` | `chat_template_kwargs: {thinking_budget: N}` — a token **cap** on the thinking block, its only dial | 1024 · 4096 · 16384 · 32768 tokens |
| **No dial** — other Qwen (2.5, VL), QwQ, GLM, MiniMax | *nothing* — the level only means "thinking ON" | — |
| **Unknown name** — `local-model`, a renamed GGUF | `reasoning_effort` (the widest bet) | `low` · `medium` · `high` |

Two dials are never sent together: Qwen3.8-max answers with an error when
`reasoning_effort` and `thinking_budget` both arrive.

Everything is printed, so the translation is never a mystery:

```
[LLMPromptStudio] Qwen3.8-27B (qwen3.8) has no 'high' rung: reasoning_effort = xhigh
[LLMPromptStudio] qwen3-8b: Qwen3.x has no effort dial, sending thinking_budget = 4096 tokens (…)
[LLMPromptStudio] glm-4.6 has no reasoning-depth dial; the level only says thinking ON
```

And if a template refuses a value anyway, the [retry](#notes--tips) catches it:
a chat template raising inside the server is reported as **500**, not 400, so
500 is retried too — once, without the extension fields, and the console names
what it dropped.

**A level costs output tokens.** Thinking is spent from the same `max_tokens`
budget as the prompt, so `xhigh` with `max_tokens = 256` returns a reasoning
block and no prompt. The node names that failure instead of handing an empty
string down the graph.

Flash-Next also dropped the ` /think` and ` /no_think` soft switches, so the
node keeps the trigger out of its requests and lets `enable_thinking` +
`reasoning_effort` carry the mode — same treatment as every other family that
does not implement it, see
[Who gets the ` /no_think` trigger](#who-gets-the-no_think-trigger):

```
[LLMPromptStudio] Qwen3.8-Flash-Next has no /think trigger (Flash-Next); the thinking switches carry the mode alone
```

Its third switch, `preserve_thinking`, decides whether thinking blocks from
**past** turns stay in the conversation. The node needs no widget for it: the
history it keeps stores the **cleaned** answer, so there is never a reasoning
block in it to preserve.

---

## Real "no think" (DeepSeek & co)

A request that says nothing about reasoning is **not** neutral. DeepSeek-style
servers (the official API, `ds4`, and most local re-implementations) read the
**absence** of the thinking field as *thinking ON* — so "just don't ask for it"
silently gives you a reasoning model. Worse: in thinking mode these backends
**ignore your sampling settings**, which is exactly what you don't want for
prompt writing.

`thinking = off` therefore states it in every dialect at once:

| Lever | Sent as | Understood by |
|-------|---------|---------------|
| Non-thinking **model alias** | `model: "deepseek-chat"` | DeepSeek API / ds4 — **the one that always wins** |
| Chat-template switch | `chat_template_kwargs: {enable_thinking: false, thinking: false}` | Qwen3.x, GLM (`enable_thinking`) · DeepSeek V3.1+ on vLLM/SGLang (`thinking`) |
| Reasoning block | `thinking: {"type": "disabled"}` | DeepSeek-compatible proxies |
| Prompt trigger | ` /no_think` appended to your idea | Qwen3.x and GLM chat templates — **skipped** for every family that does not implement it |

The alias swap is **automatic and safe**: a DeepSeek model is switched to
`deepseek-chat` **only if the server actually serves that name** (checked via
`/models`). A local GGUF loaded as `deepseek-v3.2-exp` keeps its name and gets
the flags only — no renaming, no 404. What happened is printed in the console:

```
[LLMPromptStudio] no think: deepseek-reasoner -> deepseek-chat
```

### Who gets the ` /no_think` trigger

It is a **chat-template rule, not a protocol**: a template that does not
implement it leaves the word inside your idea, where the model takes it for one
more thing to write about. The model name is the only clue available, so the
node sorts it in that order:

1. **Flash-Next** (`qwen3.8-flash-next`, quants included) → no trigger. It
   dropped the soft switches; `enable_thinking` + `reasoning_effort` carry the
   mode.
2. **`qwen` or `glm` in the name** → trigger. Checked *before* the list below,
   so a Qwen3 quant whose file name happens to carry "llama" is still a Qwen3.
3. **A family known not to read it** → no trigger: `gemma`, `mistral`
   (+ `ministral`, `magistral`, `devstral`, `codestral`, `pixtral`), `llama`,
   `nemotron`, `phi`, `gpt-` (gpt-oss included), `granite`, `command-`,
   `gemini`, `claude`, `minimax`. Either they have no thinking mode at all, or
   their reasoning is driven by something else (`reasoning_effort` for gpt-oss
   and Phi, a system-prompt line for Nemotron), or it is simply on by default
   with nothing to flip (MiniMax M2/M3 and its interleaved thinking).
4. **DeepSeek** → no trigger, the alias and `thinking: {"type": "disabled"}` do
   the job.
5. **Anything else**, including an opaque or renamed GGUF (`local-model`,
   `my-fine-tune-v2`) → **trigger**. That default is deliberate: an unnamed
   local model is more often a Qwen3 than not, and the trigger is the only lever
   left on a server that hands `chat_template_kwargs` to nobody.

Whatever the outcome, the chat-template switches are sent to **everyone** — the
trigger is only the second belt. What was decided is printed:

```
[LLMPromptStudio] gemma-3-27b-it has no /think trigger ('gemma' family); the thinking switches carry the mode alone
```

**`no_think_model`** (optional, last widget) overrides all of it: type the exact
alias your server exposes (e.g. `deepseek-chat`, `my-chat-alias`) and it is
called whenever thinking is `off`. It also forces the DeepSeek behaviour for
servers whose model names don't contain "deepseek".

> For **code** work outside ComfyUI, keeping thinking on is usually better — the
> reason to kill it is sampling control, which only matters for writing. Two
> profiles, two configs.

---

## How prompts were tuned (sources)

- Anima base v1 — CircleStone Labs / Comfy Org docs & Civitai (hybrid tags +
  natural language, `@artist`, `score_X`).
- Illustrious-XL — Danbooru-tag conventions, `masterpiece, best quality`,
  **no** Pony `score_9` tags.
- SDXL — natural language + light tags, SDXL-safe weights, `BREAK`.
- Qwen-Image 2.1 (both cards) — Qwen's **own prompt-rewriting checkpoints**
  (`Qwen-Image-2.1-PE-T2I` and `-PE-I2I`), whose system prompts ship in
  [QwenLM/Qwen-Image-2.1 → `prompt_rewrite/prompts/`](https://github.com/QwenLM/Qwen-Image-2.1/tree/main/prompt_rewrite/prompts).
  The model was trained on what those rewriters emit, so their house style *is*
  the prompt format: for text-to-image, one ~20-sentence English paragraph
  describing the finished frame as an observer, with positional phrases and
  every readable string quoted literally; for editing, one continuous directive
  built on attribute disentanglement (edit exactly what was named, at full
  strength, everything else held at input fidelity) that cites reference
  pictures as **`<image1>`, `<image2>`…** — mandatory from two images on, and
  forbidden with a single one. The rewriters' JSON envelope
  (`wh_ratio` / `ratio_follow`) is dropped on purpose: in ComfyUI the aspect
  ratio is the latent's job, not prompt text.
  Two rules go **beyond** Qwen's own text: the prompt is always written in
  English (Qwen answers a Chinese instruction in Chinese), and the edit card
  spells out that an edit to what *covers* a subject never changes the subject
  underneath — a new outfit keeps the body's proportions exactly as they are,
  no part excepted, along with the pose and framing. Qwen states the principle
  (attribute disentanglement) but leaves the body implicit, and an image model
  asked for clothes will slim a waist on the way out.
- FLUX.2 [klein] (9B) — Black Forest Labs FLUX.2 prompting guide (natural
  language, 40–120 words, no weight syntax).
- Krea 2 (Krea AI) — Krea's own foundation model: aesthetic-first, art-directed
  look, handles photo + non-photo styles, quoted short text (fal / Krea docs).
- Ideogram — official docs (plain prose, quoted text for rendering).
- LTX-2 / LTX 2.3 — Lightricks LTX prompting guide (4–8 sentences,
  subject→action→camera→lighting, motion verbs).
- Wan 2.2 — Wan prompting guides (subject→motion→camera→scene, front-loaded).
- ACE-Step 1.5 XL (style + lyrics) — the official
  [`docs/en/Tutorial.md`](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/Tutorial.md):
  tags *or* natural language both work, "specific beats vague", and BPM, key,
  time signature and duration stay **out** of the caption because the model has
  dedicated inputs for them. The lyrics card follows the guide's structure tags
  with a single descriptor after the hyphen (`[Chorus - anthemic]` — stacking
  them confuses the model), 6–10 syllables a line within ±1–2 of its
  neighbours, uppercase for intensity, (parentheses) for backing vocals, and
  resolves clashing genres by repetition or by turning the clash into an
  evolution over time.
- YuE2 (style + lyrics) — the [ComfyUI YuE2 guide](https://docs.comfy.org/tutorials/audio/yue2/yue2)
  and the model's own examples
  ([`m-a-p/YuE2-3B`](https://huggingface.co/m-a-p/YuE2-3B)). The style card
  defaults to a tag line but allows prose, and keeps the language tag and the
  BPM optional — two of the three official example styles carry neither. The
  lyrics card matches `examples/tonight-awake.json` exactly: plain labels with
  no numbers or descriptors, `[Intro]` and `[Interlude]` allowed to stand
  empty, the chorus written twice under a single `[Chorus]`, and Chinese lines
  as two short phrases separated by a space with no punctuation.
  The one rule the split design puts at risk is ACE-Step's caption/lyrics
  consistency (*"if descriptions in Caption and Lyrics contradict, the model
  gets confused"*): the lyrics cards only name an instrument the brief itself
  named, since the two runs cannot see each other.
- MiniMax H3 / Hailuo 3 — the **two official prompt-writing guides** shipped
  with the model
  ([MiniMaxAI/MiniMax-H3 → docs](https://huggingface.co/MiniMaxAI/MiniMax-H3/tree/main/docs)):
  `VIDEO_PROMPT_WRITING_GUIDE_base_en` and `..._ref_en`. They replace the
  community write-ups, which had the timing syntax wrong: H3 is **not** driven
  by `[0s-2s]` spans but by **named fields plus numbered shots**. Both cards
  encode the rules the two guides share — `[Shot 1]` carries no timestamp, later
  shots open on a strictly increasing `At MM:SS.mmm`, a cut has to bring new
  information (otherwise you move the camera instead), the camera is written as
  natural English combining **motion type + amplitude + speed**, speakers get
  stable `(S1)`/`(S2)` IDs with only the language tag and the verbatim words
  inside `<d>…</d>` (`<scenetrans>` across a cut, `<cutoff>` at the end),
  and on-screen text is quoted verbatim. Everything is English except dialogue
  and text visible in the scene. What differs is the shape:

  | Card | What it writes | Use it for |
  |------|----------------|------------|
  | **normal** | the base format: an optional task instruction line (**T2VA** none · **I2VA** first frame · **FL2VA** first+last · **L2VA** last frame, each verbatim from the guide), a blank line, then `integrated_multimodal_description:` → `overall_soundscape:` → `non_diegetic_music:` | the everyday case — text-to-video, and image-to-video where the pictures are frames |
  | **ref** | the full-reference format: `subject_definitions` → `summary` (prefixed with its bracketed task types) → `retention_analysis` (`fully_preserved` / `attribute_transfer` / `fully_copy`…) → `detailed_description` (350–500 words, style stated **before** `[Shot 1]`) → `overall_soundscape` → `non_diegetic_music` | building from **image** reference assets: reuse a character, a costume, a style, or an audio track |
  | **edit** | the same six sections, but centred on `retention_analysis` and deliberately shorter | anything that **starts from an existing video**: editing it, continuing it, swapping its character or its setting, following its camera work |

  The **edit** card exists because of one failure mode: asked about a source
  video, an LLM writes a beautiful description of footage it never saw, and H3
  then generates that invention instead of editing your clip. So the card's
  first rule outranks all the others — *what you write about the source comes
  from the sampled frames or from the user's own words, and from nothing else*.
  When it has not seen the video it **designates** it (`the subject of
  <Video 1>`, `the original camera movement`) instead of describing it, states
  no duration, shot count or timestamp the user did not give, and keeps
  `detailed_description` short: padding it to the 350–500 words of a generation
  task is precisely how invented content gets in. What it *does* author is the
  **change** — what is replaced, what is preserved — which is what
  `retention_analysis` is for. The `normal` and `ref` cards were emptied of all
  source-video material and now point here instead.

  Both cards are told that connected images arrive as `<Picture 1>`,
  `<Picture 2>`… in socket order and must be cited by those exact labels — which
  is why the node takes 8 of them. The **ref** card also uses the guide's other
  labels: `<Subject N>` for reusable visible content, `<Video N>`, `<Audio N>`;
  an image that only defines a character or a style gets no `<Picture N>` line of
  its own, it is cited inside the `<Subject N>` that uses it.

  H3 accepts 7000 characters, so a full shot list with its sound design fits in
  one request.

  > **State the graphic style, always.** Left unsaid, H3 picks a look of its own
  > and re-styles your reference image. Both cards now treat it as mandatory and
  > read it off the connected pictures — medium (photo, anime, 3D render,
  > illustration, painting), line and shading treatment, palette, grain,
  > lighting. Placement differs: **inside `[Shot 1]`** on the *normal* card,
  > **in one or two sentences before `[Shot 1]`** on the *ref* card.

  > The instruction line needs a duration. Both cards default to **10.00 s**;
  > ask for another length in `user_prompt` ("8 seconds…") or pin it once in
  > `global_directives`.

> **Krea 2** here is Krea AI's own foundation image model (not the BFL "FLUX
> Krea" collaboration). **Klein 9b** is read as FLUX.2 [klein] 9B. If you meant
> different checkpoints, just edit the preset text — the node logic is
> model-agnostic.
