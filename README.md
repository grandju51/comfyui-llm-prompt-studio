# LLM Prompt Studio for ComfyUI

A ComfyUI custom node that connects to a **local OpenAI-compatible LLM server**
(**LM Studio** or **vLLM**) and turns your idea into an **optimized prompt for a
specific image / video generator**.

It ships with editable, ready-to-use prompt "cards" for:
**Anima base v1**, **Illustrious**, **SDXL**, **FLUX.2 Klein (9B)**,
**Krea 2 (Krea AI)**, **Ideogram**, **LTX-2 / LTX 2.3**, **Wan 2.2**,
**MiniMax H3 / Hailuo 3** (two cards: *normal* and *ref*), plus a
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
  vLLM; ignored by backends that don't know it). `0.0` disables it. A common
  setup is `min_p = 0.05-0.1` with `top_p = 1.0` so min-p does the filtering.
- **top_k / repeat_penalty** are sent as top-level fields. Both
  `repetition_penalty` (vLLM) and `repeat_penalty` (LM Studio/llama.cpp) are
  included so each backend uses the one it understands.
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
  | **ref** | the full-reference format: `subject_definitions` → `summary` (prefixed with its bracketed task types) → `retention_analysis` (`fully_preserved` / `attribute_transfer` / `fully_copy`…) → `detailed_description` (350–500 words, style stated **before** `[Shot 1]`) → `overall_soundscape` → `non_diegetic_music` | building from reference assets: reuse a character, a costume, a style, a source video or its audio |

  Both cards are told that connected images arrive as `<Picture 1>`,
  `<Picture 2>`… in socket order and must be cited by those exact labels — which
  is why the node takes 8 of them. The **ref** card also uses the guide's other
  labels: `<Subject N>` for reusable visible content, `<Video N>`, `<Audio N>`;
  an image that only defines a character or a style gets no `<Picture N>` line of
  its own, it is cited inside the `<Subject N>` that uses it.

  H3 accepts 7000 characters, so a full shot list with its sound design fits in
  one request.

  > The instruction line needs a duration. Both cards default to **10.00 s**;
  > ask for another length in `user_prompt` ("8 seconds…") or pin it once in
  > `global_directives`.

> **Krea 2** here is Krea AI's own foundation image model (not the BFL "FLUX
> Krea" collaboration). **Klein 9b** is read as FLUX.2 [klein] 9B. If you meant
> different checkpoints, just edit the preset text — the node logic is
> model-agnostic.
