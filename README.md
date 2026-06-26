# LLM Prompt Studio for ComfyUI

A ComfyUI custom node that connects to a **local OpenAI-compatible LLM server**
(**LM Studio** or **vLLM**) and turns your idea into an **optimized prompt for a
specific image / video generator**.

It ships with editable, ready-to-use prompt "cards" for:
**Anima base v1**, **Illustrious**, **SDXL**, **FLUX.2 Klein (9B)**,
**Krea 2 (Krea AI)**, **Ideogram**, **LTX-2 / LTX 2.3**, **Wan 2.2**, plus a
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
- **Sampling controls**: `temperature`, `top_p`, `top_k`, `repeat_penalty`,
  `seed` (with `control_after_generate`).
- **Output tokens**: `max_tokens` caps the length of the generated answer.
- **Thinking control** (`auto` / `off` / `on`): tuned for **Qwen3.x** (sends
  `/no_think` + `enable_thinking=false`). Gemma has no thinking mode → use
  `auto`/`off`.
- **Custom cut tag(s)** (`strip_before_tag`): everything **up to and including**
  the tag is removed from the output. Default `</think>` strips a model's
  reasoning block. You can list **several tags separated by commas**
  (e.g. `</think>,</thinking>,</reasoning>`) — the comma is only a separator,
  and whichever tag ends furthest into the text is used as the cut point. This
  is the robust safety net for hiding thinking.
- **Conversation memory**: `keep_history` for multi-turn chat,
  `max_history_turns` to set how many past turns are remembered (context depth),
  and `reset_history` to clear it.
- **Optional image input**: connect an `IMAGE` to use a vision model
  (image → prompt / captioning).

Outputs:
- `prompt` – the cleaned text (after the cut tag, **no thinking**). It is a
  `STRING`, so you can wire it into a CLIP Text Encode, an API node, **or any
  Text / Show-Text / Text-Preview node**.
- `raw_response` – the full untouched answer (for debugging the thinking).
- **On-node preview**: after each run the node also shows the generated prompt
  (cleaned, no thinking) right on itself, so you can read it without wiring a
  preview node.

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
  `<think>…</think>` block, the `strip_before_tag = </think>` cleanup removes it
  from `prompt`. For models that use other reasoning tags, list them all
  comma-separated, e.g. `</think>,</thinking>,</reasoning>`.
- **top_k / repeat_penalty** are sent as top-level fields. Both
  `repetition_penalty` (vLLM) and `repeat_penalty` (LM Studio/llama.cpp) are
  included so each backend uses the one it understands.
- **Multi-turn**: turn on `keep_history`. Each queued run appends a turn, and
  `max_history_turns` controls how many past turns are remembered (context
  depth). Flip `reset_history` on (and queue once) to wipe the memory.
- **Context window vs. output**: `max_tokens` sets the **output** length. The
  model's raw **context window** (how much it can read in) is fixed when you
  load the model in LM Studio / vLLM, not per request — set it there.
- **Vision**: connect an image and use a multimodal model (e.g. a Qwen-VL or
  Gemma vision model) to caption/describe it into a prompt.
- **Model presets are editable defaults** — tweak them in the box, or edit the
  source defaults in `prompt_templates.py`.

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

> **Krea 2** here is Krea AI's own foundation image model (not the BFL "FLUX
> Krea" collaboration). **Klein 9b** is read as FLUX.2 [klein] 9B. If you meant
> different checkpoints, just edit the preset text — the node logic is
> model-agnostic.
