# -*- coding: utf-8 -*-
"""
LLM Prompt Studio - a ComfyUI node that talks to an OpenAI-compatible local
server (LM Studio or vLLM) to turn your idea into an optimized image/video prompt.

Features
--------
- OpenAI-compatible /chat/completions (works with LM Studio and vLLM).
- Two text boxes: a system prompt ("LLM card") and a chat/user message.
- Target-model dropdown with editable, pre-filled English prompt templates
  (Anima, Illustrious, SDXL, FLUX.2 Klein, FLUX Krea, Ideogram, LTX, Wan,
  MiniMax H3...).
- Sampling controls: temperature, top_p, top_k, repeat penalty, max tokens, seed.
- Thinking control (auto / off / on) tuned for Qwen3.x; Gemma-safe, with a real
  "no think" for DeepSeek-style servers (non-thinking alias + explicit flags).
- Custom "cut tag": everything up to AND including the tag is removed from the
  output (great for stripping a model's </think> reasoning block).
- Optional conversation memory (multi-turn) with a reset toggle.
- Up to 8 image inputs for vision models, announced to the LLM as <Picture 1>,
  <Picture 2>... in input order - the reference labels MiniMax H3 itself uses.
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

# --- "no think" -------------------------------------------------------------
# A request that says nothing about reasoning is NOT neutral: DeepSeek-style
# servers read the absence of the field as "thinking ON". Turning it off has to
# be stated, and each backend listens to a different lever, so "off" sends them
# all at once:
#   - the non-thinking model alias (deepseek-chat is the twin of the reasoner);
#   - chat_template_kwargs, whose spelling differs per model family;
#   - the Anthropic-style thinking block, for DeepSeek-compatible proxies.
_DEEPSEEK_NO_THINK_ALIAS = "deepseek-chat"

# chat template switches, sent together: a template only reads the name it knows
# and ignores the other, so both families are covered by one request.
_TEMPLATE_THINK_KWARGS = {
    False: {"enable_thinking": False, "thinking": False},
    True: {"enable_thinking": True, "thinking": True},
}


def _looks_deepseek(model: str) -> bool:
    return "deepseek" in (model or "").lower()


# How many images the node can take. Each connected socket becomes one picture,
# announced to the LLM as <Picture N> following the socket order - the reference
# label MiniMax H3 uses in its own prompt format.
MAX_PICTURES = 8

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


def _no_think_model(base_url: str, api_key: str, model: str, override: str,
                    timeout: int) -> str:
    """Return the model to call when thinking is off.

    ``override`` (the no_think_model widget) always wins. Otherwise a DeepSeek
    model is switched to its non-thinking twin - but ONLY if the server really
    serves that alias: a local GGUF loaded as "deepseek-v3.2" has no such name
    and renaming it blindly would turn every run into a 404.
    """
    override = (override or "").strip()
    if override:
        return override
    if not _looks_deepseek(model) or model.strip().lower() == _DEEPSEEK_NO_THINK_ALIAS:
        return model
    try:
        served = {str(m).strip().lower()
                  for m in _list_models(base_url, api_key, min(timeout, 15))}
    except Exception as e:
        print("[LLMPromptStudio] no-think alias lookup failed:", e)
        return model
    if _DEEPSEEK_NO_THINK_ALIAS in served:
        print("[LLMPromptStudio] no think: %s -> %s" % (model, _DEEPSEEK_NO_THINK_ALIAS))
        return _DEEPSEEK_NO_THINK_ALIAS
    print("[LLMPromptStudio] no think: %s has no '%s' alias, using flags only"
          % (base_url, _DEEPSEEK_NO_THINK_ALIAS))
    return model


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


def _collect_pictures(images, size_mode: str):
    """Data-URL every connected image, following the socket order.

    The position in the returned list is what the LLM is told the picture is:
    entry 0 is announced as <Picture 1>. Empty sockets are skipped rather than
    counted, so leaving a hole in the middle (image + image_3) still produces
    consecutive labels instead of a gap in the numbering.
    """
    urls = []
    for slot, tensor in enumerate(images, 1):
        if tensor is None:
            continue
        try:
            if len(tensor) > 1:
                print("[LLMPromptStudio] image slot %d carries a batch of %d, "
                      "sending its first image" % (slot, len(tensor)))
            urls.append(_image_to_data_url(tensor, size_mode))
        except Exception as e:
            print("[LLMPromptStudio] image slot %d could not be encoded: %s" % (slot, e))
    return urls


def _picture_manifest(count: int) -> str:
    """State how many pictures exist, so the model stops inventing them.

    The cards are full of <Picture N> examples and the model happily copies a
    number it was never given - citing <Picture 3> with a single image attached.
    Counting the sockets is something only the node can do, so it says it out
    loud in the system prompt rather than hoping the model infers it.
    """
    if count <= 0:
        return ("[IMAGE INPUTS] None. No image is connected to this node, so you "
                "have not been shown any picture. Never mention, cite or invent "
                "one: no <Picture N> label may appear in your output, and any "
                "example above that uses such a label is a formatting sample, not "
                "a picture you were given.")
    labels = ", ".join("<Picture %d>" % i for i in range(1, count + 1))
    if count == 1:
        head = "Exactly 1 picture is connected, labelled <Picture 1>."
        rule = "It is the only picture that exists"
    else:
        head = ("Exactly %d pictures are connected, labelled %s, in that order."
                % (count, labels))
        rule = "They are the only pictures that exist"
    return ("[IMAGE INPUTS] %s %s: never cite <Picture %d> or any higher number, "
            "and never invent a picture you were not shown. Labels appearing in "
            "the examples above are formatting samples, not extra pictures."
            % (head, rule, count + 1))


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
        spec = {
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
                    "tooltip": "Reasoning control. 'off' sends every dialect at "
                               "once: /no_think (Qwen3.x), enable_thinking/thinking "
                               "= false, and the non-thinking model alias for "
                               "DeepSeek. Gemma has no thinking mode; use auto/off.",
                }),
                # --- output cleanup ---
                "strip_before_tag": ("STRING", {
                    "default": "</think>,</mm:think>",
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
                "image": ("IMAGE", {"tooltip": "Optional image for vision models. The "
                                               "LLM is told this one is <Picture 1>."}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

        # Extra picture sockets, added after the literal so the block above keeps
        # its shape. They are inputs, not widgets, so they never appear in
        # widgets_values and cannot shift a saved workflow.
        optional = spec["optional"]
        for i in range(2, MAX_PICTURES + 1):
            optional["image_%d" % i] = ("IMAGE", {
                "tooltip": "Extra reference image. Connected images are numbered in "
                           "socket order, so this one reaches the LLM as <Picture %d> "
                           "when every socket above it is used too." % i})
        # Kept LAST: widget values are serialized positionally, so a new widget
        # anywhere else would shift saved workflows by one slot.
        optional["no_think_model"] = ("STRING", {"default": "",
            "tooltip": "No-think override: the model alias called INSTEAD when "
                       "thinking = off. DeepSeek-style servers read a request with "
                       "no thinking field as thinking ON, so naming the non-thinking "
                       "alias is what really turns reasoning off. Empty = auto (a "
                       "DeepSeek model becomes 'deepseek-chat' if the server serves "
                       "it)."})
        return spec

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
                 api_key="", image=None, no_think_model="", unique_id=None,
                 **pictures):

        # 1) encode the connected images first: how many there are is a fact the
        #    system prompt has to state, otherwise the model reads <Picture 3> in
        #    a card's example and cites a picture that was never sent.
        slots = [image] + [pictures.get("image_%d" % i) for i in range(2, MAX_PICTURES + 1)]
        picture_urls = _collect_pictures(slots, image_analysis_size)

        # 2) resolve the system prompt (fallback to preset if empty), then add
        #    the picture manifest and the user's global directives so they apply
        #    to every target model.
        sys_prompt = system_prompt.strip() or get_template(target_model)
        sys_prompt += "\n\n" + _picture_manifest(len(picture_urls))
        gd = (global_directives or "").strip()
        if gd:
            sys_prompt = (
                sys_prompt
                + "\n\n[GLOBAL DIRECTIVES - always apply these on top of everything "
                + "above]\n" + gd
            )

        # 3) thinking control. The model is resolved FIRST because "off" may have
        #    to call a different one: on DeepSeek-style servers the reasoning mode
        #    is chosen by the alias, and a request that stays silent means ON.
        resolved_model = _resolve_model(base_url, api_key, model, timeout)
        user_text = user_prompt
        extra_template_kwargs = None
        extra_payload = {}
        if thinking.startswith("off"):
            resolved_model = _no_think_model(base_url, api_key, resolved_model,
                                             no_think_model, timeout)
            extra_template_kwargs = dict(_TEMPLATE_THINK_KWARGS[False])
            if _looks_deepseek(resolved_model) or (no_think_model or "").strip():
                # /no_think is a Qwen3 chat-template trigger; DeepSeek has no such
                # rule and would read it as part of the idea. Its proxies take the
                # Anthropic-style block instead.
                extra_payload["thinking"] = {"type": "disabled"}
            else:
                user_text = user_text.rstrip() + " /no_think"
        elif thinking.startswith("on"):
            extra_template_kwargs = dict(_TEMPLATE_THINK_KWARGS[True])
            if _looks_deepseek(resolved_model):
                extra_payload["thinking"] = {"type": "enabled"}
            else:
                user_text = user_text.rstrip() + " /think"

        # 4) build the user message content (text, + pictures for vision models).
        #    Each label is sent right BEFORE its own image so the model binds the
        #    two: <Picture N> is the reference label H3 uses in its prompt format,
        #    which lets the LLM cite an exact image instead of "the second one".
        if picture_urls:
            user_content = [{"type": "text", "text": user_text}]
            for n, url in enumerate(picture_urls, 1):
                user_content.append({"type": "text", "text": "<Picture %d>:" % n})
                user_content.append({"type": "image_url", "image_url": {"url": url}})
            print("[LLMPromptStudio] sending %d picture(s) as <Picture 1>..<Picture %d>"
                  % (len(picture_urls), len(picture_urls)))
        else:
            user_content = user_text

        # 5) assemble messages (with optional history)
        hist_key = str(unique_id) if unique_id is not None else "_default"
        if reset_history:
            _HISTORY.pop(hist_key, None)

        keep_msgs = max(0, int(max_history_turns)) * 2
        messages = [{"role": "system", "content": sys_prompt}]
        if keep_history and keep_msgs:
            hist = _HISTORY.get(hist_key, [])
            messages.extend(hist[-keep_msgs:])
        messages.append({"role": "user", "content": user_content})

        # 6) build the request payload (top-level extras work for LM Studio + vLLM;
        #    both penalty spellings are sent so each backend picks the one it knows)
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
            # vLLM/SGLang pass this through to the chat template: enable_thinking
            # for Qwen3.x and GLM, thinking for DeepSeek V3.1+. A template simply
            # ignores the name it does not use.
            payload["chat_template_kwargs"] = extra_template_kwargs
        # top-level reasoning switch for DeepSeek-compatible servers and proxies
        payload.update(extra_payload)

        url = _endpoint(base_url, "/chat/completions")

        # 7) call the server
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

        # 8) extract the text
        try:
            choice = result["choices"][0]["message"]
            raw = choice.get("content") or ""
            # some servers expose reasoning separately; ignore it for the clean output
        except Exception:
            msg = "[LLM ERROR] Unexpected response shape:\n" + json.dumps(result)[:2000]
            print(msg)
            return _ui_result(msg, msg)

        cleaned = _strip_before_tags(raw, strip_before_tag)

        # 9) update history (store text turns only)
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
