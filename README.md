# LLM Prompt Studio for ComfyUI

A ComfyUI custom node that connects to a **local OpenAI-compatible LLM server**
(**LM Studio** or **vLLM**) and turns your idea into an **optimized prompt for a
specific image / video generator**.

It ships with editable, ready-to-use prompt "cards" for:
**Anima base v1**, **Illustrious**, **SDXL**, **FLUX.2 Klein (9B)**,
**Krea 2 (Krea AI)**, **Ideogram**, **LTX-2 / LTX 2.3**, **Wan 2.2**,
**MiniMax H3 / Hailuo 3** (three cards: *normal*, *ref* and *edit*), plus a
generic preset.

---

## Features

- **OpenAI mode**: works with LM Studio (`/v1`) and vLLM (`--api ... /v1`).
- **No model picking**: leave the `model` field **empty** and the node
  automatically uses the chat model loaded at your address (text-encoder /
  embedding models are skipped). The optional 🔄 button just shows you which
  one was detected; type a name only to force a specific model.
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
  `repeat_penalty`, `seed` (with `control_after_generate`).
- **Output tokens**: `max_tokens` caps the length of the generated answer.
- **Thinking control** (`auto` / `off` / `on`): `off` states "no thinking" in
  every dialect at once — `/no_think` + `enable_thinking=false` (**Qwen3.x**,
  GLM), `thinking=false` (**DeepSeek V3.1+** on vLLM/SGLang), and the
  **non-thinking model alias** for DeepSeek. Gemma has no thinking mode → use
  `auto`/`off`. See [Real "no think" (DeepSeek & co)](#real-no-think-deepseek--co).
- **`no_think_model`** (optional): the model alias called **instead** when
  thinking is `off`. Empty = auto.
- **Custom cut tag(s)** (`strip_before_tag`): everything **up to and including**
  the tag is removed from the output. Default `</think>,</mm:think>` strips a
  model's reasoning block (the second one is MiniMax M3's tag). You can list
  **several tags separated by commas**
  (e.g. `</think>,</thinking>,</reasoning>`) — the comma is only a separator,
  and whichever tag ends furthest into the text is used as the cut point. This
  is the robust safety net for hiding thinking.
- **Conversation memory**: `keep_history` for multi-turn chat,
  `max_history_turns` to set how many past turns are remembered (context depth),
  and `reset_history` to clear it.
- **Up to 8 image inputs**: `image`, `image_2` … `image_8`. Each connected
  socket is announced to the LLM as **`<Picture 1>`, `<Picture 2>`…** following
  the socket order, and the label is sent **just before its own image** so the
  model binds the two. Empty sockets are skipped, so `image` + `image_3` still
  gives you `<Picture 1>` and `<Picture 2>` — never a gap. `<Picture N>` is
  MiniMax H3's own reference label, so the H3 cards can cite an exact image
  instead of "the second one"; any vision model reads it just as well.
  These are link sockets, not widgets, so they can't shift a saved workflow.
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

Outputs:
- `prompt` – the cleaned text (after the cut tag, **no thinking**). It is a
  `STRING`, so you can wire it into a CLIP Text Encode, an API node, **or any
  Text / Show-Text / Text-Preview node**.
- `raw_response` – the full untouched answer (for debugging the thinking).
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
4. Leave `model` **empty** (auto) — or click **🔄 Detect model** to see which
   chat model was found.

### vLLM
```bash
vllm serve Qwen/Qwen3-8B --port 8000          # add --api-key YOURKEY if you want auth
```
1. `base_url` = `http://localhost:8000/v1`.
2. `api_key` = your `--api-key` (or leave default if none).
3. Leave `model` empty (auto-detected) or **🔄 Detect model**.

### Then
- Choose your **target_model** (e.g. *Illustrious*). The matching English prompt
  card loads into `system_prompt` – edit it however you like.
- Type your idea in **user_prompt**.
- Connect **`prompt`** to your text encoder / image node and queue.

---

## Notes & tips

- **Thinking / Qwen3.x**: with `thinking = off`, the node appends `/no_think`
  and asks vLLM to disable the reasoning template. Even if a model still emits a
  `<think>…</think>` block, the `strip_before_tag` cleanup removes it from
  `prompt`. For models that use other reasoning tags, list them all
  comma-separated, e.g. `</think>,</thinking>,</reasoning>`.
- **min_p** is sent as a top-level field (supported by LM Studio/llama.cpp and
  vLLM). `0.0` disables it. A common setup is `min_p = 0.05-0.1` with
  `top_p = 1.0` so min-p does the filtering.
- **top_k / repeat_penalty** are sent as top-level fields. Both
  `repetition_penalty` (vLLM) and `repeat_penalty` (LM Studio/llama.cpp) are
  included so each backend uses the one it understands.
- **Extension fields are only sent when they do something.** `top_k`, `min_p`
  and the two penalty spellings are *not* part of the OpenAI schema, and a
  backend that validates its request body answers **400** on the first one it
  doesn't know — generating nothing at all, which looks from ComfyUI like the
  request was simply cancelled. So a neutral value (`top_k = 0`, `min_p = 0`,
  `repeat_penalty = 1.0`) is left out of the request entirely, and if a 400/422
  still comes back the node **retries once without every extension** —
  including the thinking switches — and prints exactly what it dropped. You get
  an answer instead of a dead end; the cut tag still strips any reasoning block
  the retry let through.
- **Multi-turn**: turn on `keep_history`. Each queued run appends a turn, and
  `max_history_turns` controls how many past turns are remembered (context
  depth). Flip `reset_history` on (and queue once) to wipe the memory.
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
| Prompt trigger | ` /no_think` appended to your idea | Qwen3.x chat template |

The alias swap is **automatic and safe**: a DeepSeek model is switched to
`deepseek-chat` **only if the server actually serves that name** (checked via
`/models`). A local GGUF loaded as `deepseek-v3.2-exp` keeps its name and gets
the flags only — no renaming, no 404. What happened is printed in the console:

```
[LLMPromptStudio] no think: deepseek-reasoner -> deepseek-chat
```

The ` /no_think` trigger is **not** appended for DeepSeek — it is a Qwen chat
template rule, and DeepSeek would simply read it as part of your idea.

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
- FLUX.2 [klein] (9B) — Black Forest Labs FLUX.2 prompting guide (natural
  language, 40–120 words, no weight syntax).
- Krea 2 (Krea AI) — Krea's own foundation model: aesthetic-first, art-directed
  look, handles photo + non-photo styles, quoted short text (fal / Krea docs).
- Ideogram — official docs (plain prose, quoted text for rendering).
- LTX-2 / LTX 2.3 — Lightricks LTX prompting guide (4–8 sentences,
  subject→action→camera→lighting, motion verbs).
- Wan 2.2 — Wan prompting guides (subject→motion→camera→scene, front-loaded).
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
