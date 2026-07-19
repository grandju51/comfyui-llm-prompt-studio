# -*- coding: utf-8 -*-
"""
LLM Prompt Studio - a ComfyUI node that talks to an OpenAI-compatible local
server (LM Studio or vLLM) to turn your idea into an optimized image/video prompt.

Features
--------
- OpenAI-compatible /chat/completions (works with LM Studio and vLLM).
- Two text boxes: a system prompt ("LLM card") and a chat/user message.
- Target-model dropdown with editable, pre-filled English prompt templates
  (Anima, Illustrious, SDXL, FLUX.2 Klein, FLUX Krea, Ideogram, LTX, Wan...).
- Sampling controls: temperature, top_p, top_k, repeat penalty, max tokens, seed.
- Thinking control (auto / off / on) tuned for Qwen3.x; Gemma-safe.
- Custom "cut tag": everything up to AND including the tag is removed from the
  output (great for stripping a model's </think> reasoning block).
- Optional conversation memory (multi-turn) with a reset toggle.
- Optional image input for vision models (image -> prompt / captioning).
"""

import base64
import io
import json
import traceback
import urllib.error
import urllib.request

from .prompt_templates import TEMPLATE_ORDER, get_template

# Module-level chat history store: { node_unique_id: [ {role, content}, ... ] }
# Only the text turns are kept (images are sent only for the current run).
_HISTORY = {}

THINKING_MODES = ["auto", "off (no thinking)", "on (force thinking)"]

# Image downscaling presets for vision analysis.
# Megapixel entries keep the aspect ratio and target a total pixel count;
# pixel entries cap the LONGEST side. "original" sends the image untouched.
IMAGE_SIZES = [
    "original",
    "2 MP",
    "1.5 MP",
    "1 MP",
    "768 px",
    "512 px",
]
_IMAGE_SIZE_MP = {"2 MP": 2.0, "1.5 MP": 1.5, "1 MP": 1.0}
_IMAGE_SIZE_PX = {"768 px": 768, "512 px": 512}


def _endpoint(base_url: str, path: str) -> str:
    return base_url.strip().rstrip("/") + path


def _http_post_json(url: str, payload: dict, api_key: str, timeout: int):
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key and api_key.strip():
        headers["Authorization"] = "Bearer " + api_key.strip()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# Values that mean "no explicit model -> auto-detect".
_AUTO_MODEL = {
    "", "auto", "(auto)", "(loading...)", "(no models found)",
    "(server unreachable)", "-- click 🔄 refresh models --",
    "-- click 🔄 detect model --",
}
# Substrings that mark a NON-chat model (text encoders, embeddings, etc.).
_NON_CHAT_HINTS = (
    "embed", "embedding", "bge", "gte", "e5-", "rerank", "clip",
    "text-encoder", "text_encoder", "t5", "whisper", "tts", "vae",
)


def _list_models(base_url: str, api_key: str, timeout: int = 10):
    """Return the model ids served at base_url (server order preserved)."""
    req = urllib.request.Request(_endpoint(base_url, "/models"), method="GET")
    if api_key and api_key.strip():
        req.add_header("Authorization", "Bearer " + api_key.strip())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    items = data.get("data", data) if isinstance(data, dict) else data
    out = []
    for it in items or []:
        if isinstance(it, dict):
            mid = it.get("id") or it.get("name")
            if mid:
                out.append(mid)
        elif isinstance(it, str):
            out.append(it)
    return out


def _pick_chat_model(models):
    """Pick the first model that does not look like a text-encoder/embedding."""
    if not models:
        return None
    for m in models:
        low = str(m).lower()
        if not any(h in low for h in _NON_CHAT_HINTS):
            return m
    return models[0]


def _resolve_model(base_url: str, api_key: str, model: str, timeout: int):
    """Use the typed model, or auto-detect the chat model at the address."""
    m = (model or "").strip()
    if m and m.lower() not in _AUTO_MODEL:
        return m
    try:
        picked = _pick_chat_model(_list_models(base_url, api_key, min(timeout, 15)))
        if picked:
            return picked
    except Exception as e:
        print("[coco] auto model detection failed:", e)
    return m if m and m.lower() not in _AUTO_MODEL else "local-model"


def _ui_result(prompt_text: str, raw_text: str):
    """Return both the on-node text preview and the two STRING outputs."""
    return {"ui": {"text": [prompt_text]}, "result": (prompt_text, raw_text)}


def _target_size(width: int, height: int, size_mode: str):
    """Return the (w, h) the image should be resized to, or None to keep it.

    Megapixel modes scale the image so w*h matches the target while keeping the
    aspect ratio; pixel modes cap the longest side. Images already smaller than
    the target are never upscaled.
    """
    if size_mode in _IMAGE_SIZE_MP:
        target_px = _IMAGE_SIZE_MP[size_mode] * 1_000_000.0
        current_px = float(width * height)
        if current_px <= target_px:
            return None
        scale = (target_px / current_px) ** 0.5
    elif size_mode in _IMAGE_SIZE_PX:
        longest = max(width, height)
        target = _IMAGE_SIZE_PX[size_mode]
        if longest <= target:
            return None
        scale = target / float(longest)
    else:  # "original" or anything unknown
        return None
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def _image_to_data_url(image_tensor, size_mode: str = "original") -> str:
    """Convert a ComfyUI IMAGE tensor (B,H,W,C float 0..1) to a PNG data URL.

    ``size_mode`` optionally downscales the image before encoding, which cuts
    the request size and the number of vision tokens the model has to chew on.
    """
    import numpy as np
    from PIL import Image

    # take the first image of the batch
    img = image_tensor[0].detach().cpu().numpy() if hasattr(image_tensor[0], "detach") \
        else np.asarray(image_tensor[0])
    arr = (np.clip(img, 0.0, 1.0) * 255.0).round().astype("uint8")
    pil = Image.fromarray(arr)

    new_size = _target_size(pil.width, pil.height, size_mode)
    if new_size is not None:
        print("[LLMPromptStudio] image %dx%d -> %dx%d (%s)"
              % (pil.width, pil.height, new_size[0], new_size[1], size_mode))
        pil = pil.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return "data:image/png;base64," + b64


def _strip_before_tags(text: str, tags_csv: str) -> str:
    """Remove everything up to AND including the LATEST-occurring of the tags.

    Multiple tags may be given comma-separated; the comma is only a separator
    (it never counts as part of a tag). Whichever tag ends furthest into the
    text defines the cut point, so any reasoning block is removed regardless of
    which closing tag the model used.
    """
    tags = [t.strip() for t in (tags_csv or "").split(",") if t.strip()]
    if not tags:
        return text
    cut = -1
    for tag in tags:
        idx = text.rfind(tag)
        if idx != -1:
            cut = max(cut, idx + len(tag))
    if cut == -1:
        return text
    return text[cut:].lstrip("\n ").lstrip()


class LLMPromptStudio:
    """Generate image/video prompts with a local OpenAI-compatible LLM."""

    CATEGORY = "LLM Prompt Studio"
    FUNCTION = "generate"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompt", "raw_response")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # --- connection ---
                "base_url": ("STRING", {
                    "default": "http://localhost:1234/v1",
                    "tooltip": "OpenAI-compatible base URL. LM Studio: "
                               "http://localhost:1234/v1 | vLLM: http://localhost:8000/v1",
                }),
                # Leave empty = automatically use the model loaded at base_url
                # (text-encoder / embedding models are skipped).
                "model": ("STRING", {
                    "default": "",
                    "tooltip": "Leave EMPTY to auto-use the chat model loaded at the "
                               "address. Only type a name to force a specific model.",
                }),
                # --- target model preset ---
                "target_model": (TEMPLATE_ORDER, {
                    "default": TEMPLATE_ORDER[0],
                    "tooltip": "Which generator the prompt is FOR. Loads its preset "
                               "into the system prompt box.",
                }),
                # --- global directives (apply to EVERY target model) ---
                "global_directives": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "Your own global rules added on top of the system "
                               "prompt for EVERY target model (e.g. 'always add "
                               "cinematic lighting', 'avoid text in the image'). "
                               "Stays put when you switch target models.",
                }),
                # --- the LLM system prompt ('card') ---
                "system_prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "The LLM 'card'. Leave empty to use the preset of the "
                               "selected target model.",
                }),
                "user_prompt": ("STRING", {
                    "multiline": True,
                    "default": "A lone astronaut discovering a glowing forest on an "
                               "alien planet",
                    "tooltip": "Your message / idea (the chat box).",
                }),
                # --- sampling ---
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.01}),
                "top_p": ("FLOAT", {"default": 0.9, "min": 0.0, "max": 1.0, "step": 0.01}),
                "top_k": ("INT", {"default": 40, "min": 0, "max": 1000}),
                "min_p": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "Min-p sampling: drops tokens below this fraction of the "
                               "top token's probability. 0 = disabled. Try 0.05-0.1 and "
                               "raise top_p to 1.0 to use min_p alone."}),
                "repeat_penalty": ("FLOAT", {"default": 1.1, "min": 0.0, "max": 2.0, "step": 0.01}),
                "max_tokens": ("INT", {"default": 1024, "min": 16, "max": 32768}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                                 "control_after_generate": True}),
                # --- thinking / reasoning ---
                "thinking": (THINKING_MODES, {
                    "default": "off (no thinking)",
                    "tooltip": "Reasoning control. 'off' best for Qwen3.x (sends "
                               "/no_think + enable_thinking=false). Gemma has no "
                               "thinking mode; use auto/off.",
                }),
                # --- output cleanup ---
                "strip_before_tag": ("STRING", {
                    "default": "</think>",
                    "tooltip": "Everything up to AND including the tag is removed from "
                               "the output. Put SEVERAL tags separated by commas "
                               "(e.g. </think>,</thinking>,</reasoning>); the comma is "
                               "only a separator. Empty = keep everything.",
                }),
                # --- conversation memory ---
                "keep_history": ("BOOLEAN", {"default": False,
                    "tooltip": "Multi-turn chat: remember previous turns of this node."}),
                "max_history_turns": ("INT", {"default": 6, "min": 0, "max": 100,
                    "tooltip": "Context memory depth: how many past user+assistant "
                               "turns to keep when keep_history is on (0 = none)."}),
                "reset_history": ("BOOLEAN", {"default": False,
                    "tooltip": "Clear this node's memory before generating."}),
                "timeout": ("INT", {"default": 120, "min": 5, "max": 1800,
                    "tooltip": "Request timeout in seconds."}),
                # --- vision input ---
                "image_analysis_size": (IMAGE_SIZES, {
                    "default": "1 MP",
                    "tooltip": "Downscale the connected image before sending it to the "
                               "vision model. Smaller = faster and fewer vision tokens. "
                               "MP presets keep the aspect ratio; px presets cap the "
                               "longest side. Images already smaller are left as-is.",
                }),
            },
            "optional": {
                "api_key": ("STRING", {"default": "lm-studio",
                    "tooltip": "API key if required (LM Studio: any value; vLLM: your "
                               "--api-key). Leave default if none."}),
                "image": ("IMAGE", {"tooltip": "Optional image for vision models "
                                               "(image -> prompt)."}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    # Always re-run when the seed changes (control_after_generate); fixed seed = cached.
    @classmethod
    def IS_CHANGED(cls, seed=0, **kwargs):
        return seed

    # ------------------------------------------------------------------ main
    def generate(self, base_url, model, target_model, global_directives,
                 system_prompt, user_prompt, temperature, top_p, top_k, min_p,
                 repeat_penalty, max_tokens, seed, thinking, strip_before_tag,
                 keep_history, max_history_turns, reset_history, timeout,
                 image_analysis_size="original",
                 api_key="", image=None, unique_id=None):

        # 1) resolve the system prompt (fallback to preset if empty), then add
        #    the user's global directives so they apply to every target model.
        sys_prompt = system_prompt.strip() or get_template(target_model)
        gd = (global_directives or "").strip()
        if gd:
            sys_prompt = (
                sys_prompt
                + "\n\n[GLOBAL DIRECTIVES - always apply these on top of everything "
                + "above]\n" + gd
            )

        # 2) thinking control (Qwen3.x convention; harmless/ignored elsewhere)
        user_text = user_prompt
        extra_template_kwargs = None
        if thinking.startswith("off"):
            user_text = user_text.rstrip() + " /no_think"
            extra_template_kwargs = {"enable_thinking": False}
        elif thinking.startswith("on"):
            user_text = user_text.rstrip() + " /think"
            extra_template_kwargs = {"enable_thinking": True}

        # 3) build the user message content (text, + image for vision models)
        if image is not None:
            try:
                data_url = _image_to_data_url(image, image_analysis_size)
                user_content = [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
            except Exception as e:
                print("[LLMPromptStudio] image encode failed:", e)
                user_content = user_text
        else:
            user_content = user_text

        # 4) assemble messages (with optional history)
        hist_key = str(unique_id) if unique_id is not None else "_default"
        if reset_history:
            _HISTORY.pop(hist_key, None)

        keep_msgs = max(0, int(max_history_turns)) * 2
        messages = [{"role": "system", "content": sys_prompt}]
        if keep_history and keep_msgs:
            hist = _HISTORY.get(hist_key, [])
            messages.extend(hist[-keep_msgs:])
        messages.append({"role": "user", "content": user_content})

        # 5) build the request payload (top-level extras work for LM Studio + vLLM;
        #    both penalty spellings are sent so each backend picks the one it knows)
        resolved_model = _resolve_model(base_url, api_key, model, timeout)
        payload = {
            "model": resolved_model,
            "messages": messages,
            "temperature": float(temperature),
            "top_p": float(top_p),
            "top_k": int(top_k),
            "min_p": float(min_p),
            "repetition_penalty": float(repeat_penalty),  # vLLM / many backends
            "repeat_penalty": float(repeat_penalty),       # LM Studio (llama.cpp)
            "max_tokens": int(max_tokens),
            "seed": int(seed),
            "stream": False,
        }
        if extra_template_kwargs is not None:
            # vLLM passes this through to the chat template (Qwen3 enable_thinking)
            payload["chat_template_kwargs"] = extra_template_kwargs

        url = _endpoint(base_url, "/chat/completions")

        # 6) call the server
        try:
            result = _http_post_json(url, payload, api_key, timeout)
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
            except Exception:
                body = ""
            msg = "[LLM HTTP ERROR %s] %s\n%s" % (e.code, url, body)
            print(msg)
            return _ui_result(msg, msg)
        except Exception as e:
            msg = "[LLM ERROR] %s\nURL: %s\n%s" % (e, url, traceback.format_exc())
            print(msg)
            return _ui_result(msg, msg)

        # 7) extract the text
        try:
            choice = result["choices"][0]["message"]
            raw = choice.get("content") or ""
            # some servers expose reasoning separately; ignore it for the clean output
        except Exception:
            msg = "[LLM ERROR] Unexpected response shape:\n" + json.dumps(result)[:2000]
            print(msg)
            return _ui_result(msg, msg)

        cleaned = _strip_before_tags(raw, strip_before_tag)

        # 8) update history (store text turns only)
        if keep_history:
            turn = _HISTORY.setdefault(hist_key, [])
            turn.append({"role": "user", "content": user_text})
            turn.append({"role": "assistant", "content": raw})
            # keep stored memory bounded to the requested number of turns
            if keep_msgs == 0:
                turn.clear()
            elif len(turn) > keep_msgs:
                del turn[: len(turn) - keep_msgs]

        # ui preview shows the CLEANED prompt (no thinking); raw stays on output 2
        return _ui_result(cleaned, raw)


NODE_CLASS_MAPPINGS = {"LLMPromptStudio": LLMPromptStudio}
NODE_DISPLAY_NAME_MAPPINGS = {"LLMPromptStudio": "coco"}
