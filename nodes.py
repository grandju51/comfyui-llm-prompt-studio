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
- A video input sampled into frames (1 out of N, capped, spread over the whole
  clip) so the model describes a video it has actually seen, plus audio/video
  role widgets declaring what each asset is FOR when it cannot be shown.
- Optional load -> prompt -> unload cycle (unload_after): LM Studio is told to
  drop the model from memory once the answer is in, so the image/video model
  further down the graph gets the VRAM back.
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

# What a connected audio is FOR. The names are MiniMax H3's own relationship
# markers: the node cannot listen to the track, but it can state the role, which
# is the part the prompt has to carry. Definitions come from the reference guide.
# No "/" in a value: ComfyUI reads it as a path separator and splits the entry
# into nested submenus, the same trap TEMPLATE_ORDER carries a warning about.
AUDIO_ROLES = [
    "none - no audio",
    "reuse in full (fully_copy)",
    "reuse in part (partially_copy)",
    "reference the style, timbre or rhythm (reference)",
    "loose atmosphere only (weak_reference)",
    "voice timbre reference for a speaker (reference)",
]
# role -> (retention marker, summary task type, what the marker means)
_AUDIO_ROLE_RULES = {
    AUDIO_ROLES[1]: (
        "fully_copy", "audio reuse",
        "the complete source audio is the target video's complete final audio track"),
    AUDIO_ROLES[2]: (
        "partially_copy", "audio reuse",
        "only part of the timeline or selected audio layers are copied, or other "
        "sounds are added, removed or replaced"),
    AUDIO_ROLES[3]: (
        "reference", "audio reference",
        "the signal is not copied: only timbre, rhythm, music style, dialogue "
        "content or sound texture is referenced"),
    AUDIO_ROLES[4]: (
        "weak_reference", "audio reference",
        "only a broad similarity in category or atmosphere is retained"),
    AUDIO_ROLES[5]: (
        "reference", "audio reference",
        "it is the voice-timbre reference for one speaker: define it as "
        "\"<Audio 1> is the voice-timbre reference for <Subject N> (Sx)\" and reuse "
        "that speaker's existing (Sx) instead of assigning a new one"),
}

# What a connected video is FOR, in H3's own vocabulary. Same trap as above: no
# "/" in a value. A video reaches ComfyUI as a batch of frames, so the node CAN
# show some of them to a vision model - but it still cannot see the frames it did
# not sample, which is why the role is declared separately from the frames.
VIDEO_ROLES = [
    "none - no video",
    "edit the source video (video editing)",
    "continue from the source video (video continuation)",
    "keep its motion, cuts and rhythm (reference)",
    "loose atmosphere or style only (weak_reference)",
]
# role -> (bracketed task type or None, the sentence that defines <Video 1>,
#          how retention_analysis should treat it)
_VIDEO_ROLE_RULES = {
    VIDEO_ROLES[1]: (
        "video editing",
        "<Video 1> is the source video for the target video edit. The target video "
        "is an edited version of <Video 1>.",
        "name what changes and what is kept: partially_preserved when you alter "
        "something in it, and an explicit fully_preserved line for the aspects you "
        "keep untouched, e.g. \"<Video 1> (camera movement, background and pacing): "
        "fully_preserved\""),
    VIDEO_ROLES[2]: (
        "video continuation",
        "<Video 1> is the source video the target video continues from.",
        "state which aspects carry over: subject, setting, camera behaviour and "
        "pacing are usually fully_preserved across the cut"),
    VIDEO_ROLES[3]: (
        None,
        "<Video 1> is the reference for the target video's camera movement, cuts "
        "and temporal structure.",
        "give it one line \"<Video 1> (camera movement, cuts and pacing): "
        "fully_preserved - ...\" and do NOT add a bracketed task type for it"),
    VIDEO_ROLES[4]: (
        None,
        "<Video 1> is a loose atmosphere reference for the target video.",
        "give it one line \"<Video 1> (style and atmosphere): weak_reference - ...\" "
        "and do NOT add a bracketed task type for it"),
}

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

# Frames are sent by the dozen, so they get their own (smaller) size ladder.
# Every entry caps the LONGEST side; _target_size reads them from _IMAGE_SIZE_PX.
VIDEO_FRAME_SIZES = [
    "512 px", "384 px", "256 px", "128 px", "768 px", "1024 px", "original",
]
_IMAGE_SIZE_PX = {"1024 px": 1024, "768 px": 768, "512 px": 512,
                  "384 px": 384, "256 px": 256, "128 px": 128}

# Past this many frames a single request gets heavy (roughly 700 vision tokens
# per frame at 512 px). The node warns and obeys: the ceiling is the user's call.
VIDEO_FRAMES_ADVISED = 16


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


# LM Studio serves two APIs side by side: /v1 is the OpenAI-compatible surface
# this node talks to, /api/v1 its own one - and only the native one can free the
# memory. Both hang off the same root, so the unload address is the base_url
# with its API suffix cut off. Longest suffix first: "/openai/v1" also ends
# with "/v1" and cutting the short one would leave a dead ".../openai" root.
_API_SUFFIXES = ("/openai/v1", "/api/v1", "/api/v0", "/v1")


def _server_root(base_url: str) -> str:
    root = (base_url or "").strip().rstrip("/")
    low = root.lower()
    for suffix in _API_SUFFIXES:
        if low.endswith(suffix):
            return root[: -len(suffix)]
    return root


def _loaded_instances(root: str, api_key: str, model: str, timeout: int):
    """(instance id, config) pairs LM Studio currently holds for ``model``.

    An instance id is usually the model key itself, but the same model loaded
    twice gets one entry per copy - and unloading takes ids, not names, so the
    list is what has to be walked to really empty the VRAM. The config comes
    along because a copy already in memory was loaded with a context size that
    may not be the one asked for, and only a reload can change it.
    """
    req = urllib.request.Request(root + "/api/v1/models", method="GET")
    if api_key and api_key.strip():
        req.add_header("Authorization", "Bearer " + api_key.strip())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    wanted = (model or "").strip().lower()
    found = []
    for m in (data.get("models") if isinstance(data, dict) else None) or []:
        key = str(m.get("key") or "").strip().lower()
        for inst in m.get("loaded_instances") or []:
            if isinstance(inst, dict):
                iid, cfg = inst.get("id"), inst.get("config") or {}
            else:
                iid, cfg = inst, {}
            if not iid:
                continue
            if key == wanted or str(iid).strip().lower() == wanted:
                found.append((iid, cfg))
    return found


def _wrong_size(cfg: dict, context_length: int, parallel: int) -> str:
    """Why a copy already in memory does not match the asked-for size, if so.

    LM Studio reserves ``context_length`` tokens PER slot, so what a model
    really books is context_length x parallel - 60416 x 4 = 241664 tokens next
    to a 17 GB model is what makes a 24 GB card run out mid-prompt. Neither can
    be changed on a live instance, so a mismatch has to be reported here for
    the caller to reload.
    """
    if context_length > 0 and int(cfg.get("context_length") or 0) != context_length:
        return "context_length %s -> %d" % (cfg.get("context_length"), context_length)
    if parallel > 0 and int(cfg.get("parallel") or 0) != parallel:
        return "parallel %s -> %d" % (cfg.get("parallel"), parallel)
    return ""


def _load_model(base_url: str, api_key: str, model: str, timeout: int = 300,
                context_length: int = 0, parallel: int = 0):
    """Put ``model`` in memory before the request, at the asked-for size.

    LM Studio loads a model on its own when a request names one (JIT), so this
    is only the explicit half of the load -> prompt -> unload cycle: it makes
    the run work with JIT turned off, and it gives the loading time its own line
    in the log instead of hiding it inside the chat timeout. Asking twice is NOT
    free - a second load builds a SECOND instance ('model:2') and costs the VRAM
    twice - hence the lookup first. A copy already there but loaded at another
    size is dropped and loaded again, because that size is fixed at load time
    and is the whole point of asking. Failure is never fatal: the chat request
    that follows falls back to whatever the server does by itself.
    """
    root = _server_root(base_url)
    try:
        live = _loaded_instances(root, api_key, model, min(timeout, 15))
    except Exception as e:
        print("[LLMPromptStudio] load: could not list the loaded models (%s)" % e)
        return
    if live:
        mismatch = _wrong_size(live[0][1], context_length, parallel)
        if not mismatch:
            return
        print("[LLMPromptStudio] '%s' is loaded with %s; reloading it"
              % (live[0][0], mismatch))
        _unload_model(base_url, api_key, model, min(timeout, 60))
    body = {"model": model}
    if context_length > 0:
        body["context_length"] = context_length
    if parallel > 0:
        body["parallel"] = parallel
    try:
        res = _http_post_json(root + "/api/v1/models/load", body, api_key, timeout)
        print("[LLMPromptStudio] loaded '%s' in %ss%s"
              % (res.get("instance_id", model), res.get("load_time_seconds", "?"),
                 (" (context %d x %d slot(s))" % (context_length, parallel or 1))
                 if context_length > 0 else ""))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        print("[LLMPromptStudio] load failed (HTTP %s); leaving it to the server\n%s"
              % (e.code, body))
    except Exception as e:
        print("[LLMPromptStudio] load failed (%s); leaving it to the server" % e)


def _unload_model(base_url: str, api_key: str, model: str, timeout: int = 60):
    """Ask LM Studio to drop ``model`` from memory once the answer is in.

    This is a side effect, never a result: whatever happens here the prompt is
    already written, so a server that cannot unload only prints a line. Older
    LM Studio builds have no /api/v1 at all and answer 404 on the address - the
    message says so instead of pretending the memory was freed.
    """
    root = _server_root(base_url)
    url = root + "/api/v1/models/unload"
    try:
        targets = _loaded_instances(root, api_key, model, min(timeout, 15))
    except Exception as e:
        # No listing (old build, another backend): name the model directly and
        # let the server be the judge of whether it is loaded.
        print("[LLMPromptStudio] unload: could not list the loaded models (%s)" % e)
        targets = []
    targets = [iid for iid, _cfg in targets] or [model]
    for iid in targets:
        try:
            _http_post_json(url, {"instance_id": iid}, api_key, timeout)
            print("[LLMPromptStudio] unloaded '%s' from %s" % (iid, root))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
            except Exception:
                body = ""
            if "model_not_found" in body:
                print("[LLMPromptStudio] unload: '%s' was not loaded any more" % iid)
            elif e.code == 404:
                print("[LLMPromptStudio] unload: %s has no %s endpoint (LM Studio "
                      "0.3.30+ only); model left in memory" % (root, "/api/v1/models/unload"))
            else:
                print("[LLMPromptStudio] unload failed (HTTP %s) on %s\n%s"
                      % (e.code, url, body))
        except Exception as e:
            print("[LLMPromptStudio] unload failed on %s: %s" % (url, e))


# Everything here is an extension to the OpenAI chat schema. Backends that
# validate their request body reject the first one they do not know.
_EXTENSION_FIELDS = ("top_k", "min_p", "repetition_penalty", "repeat_penalty",
                     "chat_template_kwargs", "thinking")


def _carried_error(result) -> str:
    """The error text a 200 response carries instead of a completion, if any."""
    if not isinstance(result, dict) or "choices" in result:
        return ""
    err = result.get("error")
    if not err:
        return ""
    if isinstance(err, dict):
        err = err.get("message") or json.dumps(err)
    return str(err)


def _post_chat(url: str, payload: dict, api_key: str, timeout: int):
    """POST the request, retrying once without the extension fields.

    A strict backend answers 400/422 on an unknown field and generates nothing
    at all, which reads from ComfyUI as the request being simply cancelled. The
    retry turns that dead end into an answer and names what it had to drop -
    including the thinking switches, whose reasoning block the cut tag still
    removes from the output. Returns (result, error_text); one of the two is
    always None.
    """
    def _body(e):
        try:
            return e.read().decode("utf-8")
        except Exception:
            return ""

    try:
        res = _http_post_json(url, payload, api_key, timeout)
        carried = _carried_error(res)
        if carried:
            return None, ("[LLM ERROR] %s answered 200 with an error instead of a "
                          "completion:\n%s\n\nA server that does not serve that "
                          "address answers exactly this - check base_url: LM "
                          "Studio's OpenAI API is http://localhost:1234/v1, and "
                          "/api/v1 is its own API, which has no /chat/completions."
                          % (url, carried))
        return res, None
    except urllib.error.HTTPError as e:
        first = "[LLM HTTP ERROR %s] %s\n%s" % (e.code, url, _body(e))
        stripped = {k: v for k, v in payload.items() if k not in _EXTENSION_FIELDS}
        if e.code not in (400, 422) or len(stripped) == len(payload):
            return None, first
        dropped = ", ".join(k for k in payload if k not in stripped)
        print("[LLMPromptStudio] %s refused the request (HTTP %s); retrying "
              "without %s" % (url, e.code, dropped))
        try:
            return _http_post_json(url, stripped, api_key, timeout), None
        except urllib.error.HTTPError as e2:
            return None, ("%s\n[retry without %s] HTTP %s\n%s"
                          % (first, dropped, e2.code, _body(e2)))


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


def _pick_frame_indices(total: int, stride: int, max_frames: int):
    """Which frames of a video batch to send.

    Two knobs, applied in that order: keep one frame out of ``stride``, then thin
    the result down to ``max_frames`` spread over the WHOLE clip - taking the
    first N instead would show the opening seconds and nothing else, which is the
    surest way to make the model invent the rest. 0 sends no frame at all, the
    mode to pick when the writer is a text-only LLM.
    """
    total = int(total)
    max_frames = int(max_frames)
    if total <= 0 or max_frames == 0:
        return []
    idx = list(range(0, total, max(1, int(stride))))
    if max_frames > 0 and len(idx) > max_frames:
        if max_frames == 1:
            return [idx[0]]
        step = (len(idx) - 1) / float(max_frames - 1)
        idx = sorted({idx[int(round(i * step))] for i in range(max_frames)})
    return idx


def _timestamp(frame_index: int, fps: float) -> str:
    """MM:SS.mmm for a frame number, the timestamp shape H3 writes."""
    t = frame_index / float(fps)
    return "%02d:%06.3f" % (int(t // 60), t % 60)


def _collect_video_frames(video, stride: int, max_frames: int, size_mode: str,
                          fps: float):
    """Sample the connected video into (label, data URL) pairs.

    A video arrives as a batch of frames, so sampling is all the node has to do
    to let a vision model actually LOOK at it instead of guessing its content.
    Returns the pairs plus the batch length, which the manifest needs to say how
    much of the clip was left out.
    """
    if video is None:
        return [], 0
    try:
        total = len(video)
    except Exception as e:
        print("[LLMPromptStudio] video input has no frames: %s" % e)
        return [], 0
    idx = _pick_frame_indices(total, stride, max_frames)
    items = []
    for n, i in enumerate(idx, 1):
        try:
            url = _image_to_data_url(video[i:i + 1], size_mode)
        except Exception as e:
            print("[LLMPromptStudio] video frame %d could not be encoded: %s" % (i, e))
            continue
        stamp = " at %s" % _timestamp(i, fps) if fps and fps > 0 else ""
        items.append(("<Video 1> frame %d of %d%s:" % (n, len(idx), stamp), url))
    if items:
        print("[LLMPromptStudio] video: %d frames in the batch, sending %d (1 out "
              "of %d, capped at %d, %s)"
              % (total, len(items), max(1, int(stride)), max_frames, size_mode))
    if len(items) > VIDEO_FRAMES_ADVISED:
        print("[LLMPromptStudio] %d frames is above the advised %d: expect a large "
              "request and a big vision-token bill."
              % (len(items), VIDEO_FRAMES_ADVISED))
    return items, total


def _video_manifest(connected: bool, role: str, sent: int, total: int,
                    fps: float) -> str:
    """State whether the video was actually SEEN, and what it is for.

    The whole point of the video input: the model must never describe a source it
    was not shown. Either it got frames - then it may describe what is ON them
    and nothing else - or it got none, and <Video 1> is a label it designates
    without ever saying what it contains.
    """
    rule = _VIDEO_ROLE_RULES.get(role)
    if not connected and not rule:
        return ""
    if sent > 0:
        span = ("They are evenly spaced over the whole clip (%d frames in total%s)"
                % (total, ", %.3g fps" % fps if fps and fps > 0 else ""))
        seen = (
            "You have been shown %d still frames sampled from it, labelled "
            "\"<Video 1> frame 1 of %d\" and so on. %s, so you have NOT seen what "
            "happens between them: describe only what is visible ON the frames, "
            "and never narrate an action, a cut or a sound you did not see. They "
            "are frames of ONE video, not separate reference pictures - never call "
            "them <Picture N>. These frames are your EVIDENCE, never your "
            "vocabulary: they exist only inside this request, the generator "
            "receives the whole video and knows nothing of your sample. So never "
            "write \"frame N\", never state a frame count or a total, and never put "
            "a \"<Video 1> frame N of M\" label in your output - name the video "
            "<Video 1> and a moment in it by its timestamp or by what happens "
            "there." % (sent, sent, span))
    elif connected:
        seen = ("No frame was sampled from it (the frame count is set to 0), so you "
                "have NOT seen it. Never describe its content: designate it as "
                "<Video 1> and reuse the user's own words about it.")
    else:
        seen = ("It is not wired into this node, so you have NOT seen it. Never "
                "describe its content: designate it as <Video 1> and reuse the "
                "user's own words about it.")
    if rule:
        task_type, definition, retention = rule
        what = ("Declare it in subject_definitions with: \"%s\" %s In "
                "retention_analysis, %s."
                % (definition,
                   "Add \"%s\" to the bracketed task types of summary." % task_type
                   if task_type else "", retention))
    else:
        what = ("Its role was left unset, so infer it from the user's request and "
                "state plainly what the target video keeps from it.")
    return ("[VIDEO INPUT] One video exists and it is labelled <Video 1>. %s %s "
            "Whatever the user did not state and the frames do not show simply is "
            "not yours to invent. If the format you are writing has no reference "
            "labels, carry the same intent in plain words instead of using "
            "<Video 1>." % (seen, what))


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
    # Several angles of one person are one subject, not one per socket: a format
    # using <Subject N> wants "<Subject 1> is the character from <Picture 1> and
    # <Picture 2>", and left unsaid the model dutifully invents a second character.
    same = ("" if count < 2 else
            " Pictures showing the SAME person or object are one single subject: if "
            "the format you are writing uses <Subject N>, define it once as "
            "\"<Subject 1> is the character from <Picture 1> and <Picture 2>\" "
            "instead of creating one subject per picture.")
    return ("[IMAGE INPUTS] %s %s: never cite <Picture %d> or any higher number, "
            "and never invent a picture you were not shown. Labels appearing in "
            "the examples above are formatting samples, not extra pictures.%s"
            % (head, rule, count + 1, same))


def _audio_manifest(connected: bool, role: str) -> str:
    """State that an audio reference exists and what it is for.

    Returns "" when there is nothing to declare, so the nine non-video cards
    never carry a paragraph about a track that does not exist. The negative case
    needs no line: <Audio N> only lives in the full-reference card, which already
    says the label exists solely when the user provides such a source.
    """
    rule = _AUDIO_ROLE_RULES.get(role)
    if not connected and not rule:
        return ""
    where = ("One audio is connected to this node" if connected
             else "One audio track is supplied to the video model further down the "
                  "graph (it is not wired into this node)")
    if rule:
        marker, task_type, meaning = rule
        what = ("Its role is %s: %s. In the full-reference format, declare it in "
                "subject_definitions, add \"%s\" to the bracketed task types of "
                "summary, give it its own retention_analysis line \"<Audio 1>: %s - "
                "...\" with no (Sx) in that section, and state the copy or reference "
                "relationship only in the section matching the audible layer."
                % (marker, meaning, task_type, marker))
    else:
        what = ("Its role was left unset, so infer it from the user's request and "
                "say plainly whether the track is reused or merely referenced.")
    return ("[AUDIO INPUT] %s and it is labelled <Audio 1>. %s If the format you are "
            "writing has no reference labels, carry the same intent in plain words "
            "instead of using <Audio 1>." % (where, what))


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
                               "raise top_p to 1.0 to use min_p alone. Does NOT work on a "
                               "vLLM server started with speculative decoding (draft model "
                               "/ n-gram): it answers 400 and generates nothing. Turn "
                               "enable_min_p off there (it keeps your value) and filter "
                               "with top_p / top_k instead."}),
                "repeat_penalty": ("FLOAT", {"default": 1.1, "min": 0.0, "max": 2.0, "step": 0.01,
                    "tooltip": "Penalises tokens already produced. 1.0 = neutral and "
                               "nothing is sent; enable_repeat_penalty (bottom of the "
                               "node) switches it off without touching this value."}),
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
        optional["audio"] = ("AUDIO", {
            "tooltip": "Optional audio reference. Connecting it tells the LLM that "
                       "an audio signal exists and is labelled <Audio 1>; what it "
                       "is FOR is set by audio_role. The node does not listen to "
                       "it - it declares it, so the prompt can carry the reuse or "
                       "reference relationship H3 expects."})
        optional["video"] = ("IMAGE", {
            "tooltip": "Optional source video, as the IMAGE batch a video loader "
                       "outputs. Frames are sampled from it and shown to the vision "
                       "model, so the prompt can talk about a video it has really "
                       "seen. Labelled <Video 1>; how many frames and what for is "
                       "set by the video_* widgets below."})
        # Kept LAST: widget values are serialized positionally, so a new widget
        # anywhere else would shift saved workflows by one slot. audio_role is
        # appended AFTER no_think_model for the same reason, and the video widgets
        # after both.
        optional["no_think_model"] = ("STRING", {"default": "",
            "tooltip": "No-think override: the model alias called INSTEAD when "
                       "thinking = off. DeepSeek-style servers read a request with "
                       "no thinking field as thinking ON, so naming the non-thinking "
                       "alias is what really turns reasoning off. Empty = auto (a "
                       "DeepSeek model becomes 'deepseek-chat' if the server serves "
                       "it)."})
        optional["audio_role"] = (AUDIO_ROLES, {
            "default": AUDIO_ROLES[0],
            "tooltip": "What the connected audio is FOR, in MiniMax H3's own "
                       "vocabulary. Picking anything but 'none' declares <Audio 1> "
                       "to the LLM even if nothing is wired into the audio socket, "
                       "which is what you want when the track is fed to the video "
                       "model further down the graph."})
        optional["video_stride"] = ("INT", {"default": 4, "min": 1, "max": 9999,
            "tooltip": "Take 1 frame out of N from the connected video. 1 = every "
                       "frame, 4 = one out of four. The result is then thinned down "
                       "to video_max_frames, so this mostly sets WHERE the samples "
                       "come from on a long clip."})
        optional["video_max_frames"] = ("INT", {"default": 8, "min": 0, "max": 999,
            "tooltip": "Hard cap on the frames actually sent, spread over the whole "
                       "clip. 0 = send NO frame: the video is still declared as "
                       "<Video 1> but the LLM never sees it, which is the mode for a "
                       "text-only model. Above ~16 the request gets heavy (roughly "
                       "700 vision tokens per frame at 512 px) - the node warns and "
                       "obeys."})
        optional["video_frame_size"] = (VIDEO_FRAME_SIZES, {
            "default": VIDEO_FRAME_SIZES[0],
            "tooltip": "Longest side each frame is downscaled to before sending. "
                       "Frames go by the dozen, so keep it small: 512 px reads fine, "
                       "256 px is enough to follow an action, 128 px only for "
                       "counting shots."})
        optional["video_fps"] = ("FLOAT", {"default": 0.0, "min": 0.0, "max": 240.0,
            "step": 0.1,
            "tooltip": "Frame rate of the connected video. Set it and every sampled "
                       "frame is labelled with its real timestamp, so the model "
                       "writes 'At MM:SS.mmm' on actual times instead of made-up "
                       "ones. 0 = unknown, frames are only numbered."})
        optional["video_role"] = (VIDEO_ROLES, {
            "default": VIDEO_ROLES[0],
            "tooltip": "What the video is FOR, in MiniMax H3's own vocabulary. "
                       "Picking anything but 'none' declares <Video 1> to the LLM "
                       "even with nothing wired into the video socket - what you "
                       "want when the clip goes straight to the video model further "
                       "down the graph."})
        # Also kept last for the positional reason above, which is why these two
        # switches sit here instead of next to the sliders they command.
        optional["enable_min_p"] = ("BOOLEAN", {"default": True,
            "label_on": "min_p: on", "label_off": "min_p: off",
            "tooltip": "Master switch for min_p. Off = the field is NEVER sent, "
                       "whatever the slider says, so you can park a value there "
                       "and still talk to a backend that rejects it (a vLLM "
                       "server with speculative decoding answers 400 on it). On = "
                       "sent as soon as the slider is above 0."})
        optional["enable_repeat_penalty"] = ("BOOLEAN", {"default": True,
            "label_on": "repeat penalty: on", "label_off": "repeat penalty: off",
            "tooltip": "Master switch for the repetition penalty. Off = neither "
                       "repetition_penalty (vLLM) nor repeat_penalty (llama.cpp) "
                       "is sent, whatever the slider says. On = sent as soon as "
                       "the slider leaves 1.0, which is the neutral value."})
        optional["unload_after"] = ("BOOLEAN", {"default": False,
            "label_on": "unload model: on", "label_off": "unload model: off",
            "tooltip": "LM Studio only: drop the model from memory as soon as the "
                       "answer is in, so the VRAM is free for the Flux/SDXL model "
                       "further down the graph. Uses LM Studio's own API "
                       "(/api/v1/models/unload, 0.3.30+), which lives next to the "
                       "base_url you typed. The next run loads the model again, so "
                       "leave it off while you iterate on a prompt: reloading costs "
                       "several seconds per run."})
        optional["context_length"] = ("INT", {"default": 0, "min": 0, "max": 1048576,
            "step": 1024,
            "tooltip": "LM Studio only: how many tokens the model reserves when the "
                       "node loads it. 0 = leave LM Studio's own setting alone. This "
                       "is the VRAM knob: the reservation is context_length x "
                       "context_slots and it sits NEXT to the weights, so a 17 GB "
                       "model asking 60k x 4 tokens runs a 24 GB card out of memory "
                       "in the middle of a prompt ('Channel Error' in the LM Studio "
                       "log, {\"error\":\"terminated\"} here). A prompt with a picture "
                       "and a few turns of history fits in ~8k. The size is fixed "
                       "when the model loads, so a copy already in memory at another "
                       "size is unloaded and loaded again."})
        optional["context_slots"] = ("INT", {"default": 0, "min": 0, "max": 64,
            "tooltip": "LM Studio only: how many requests it keeps room for in "
                       "parallel ('parallel'), each one costing a full "
                       "context_length. 0 = leave its own setting alone. ComfyUI "
                       "sends one request at a time, so 1 divides the reservation "
                       "by whatever LM Studio had picked - by 4, with its usual "
                       "default. Works on its own or next to context_length."})
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
                 audio=None, audio_role=AUDIO_ROLES[0], video=None,
                 video_stride=4, video_max_frames=8,
                 video_frame_size=VIDEO_FRAME_SIZES[0], video_fps=0.0,
                 video_role=VIDEO_ROLES[0], enable_min_p=True,
                 enable_repeat_penalty=True, unload_after=False,
                 context_length=0, context_slots=0, **pictures):

        # 1) encode the connected images first: how many there are is a fact the
        #    system prompt has to state, otherwise the model reads <Picture 3> in
        #    a card's example and cites a picture that was never sent. Same idea
        #    for the video, except the fact to state is how much of it was seen.
        slots = [image] + [pictures.get("image_%d" % i) for i in range(2, MAX_PICTURES + 1)]
        picture_urls = _collect_pictures(slots, image_analysis_size)
        frames, frame_total = _collect_video_frames(
            video, video_stride, video_max_frames, video_frame_size, video_fps)

        # 2) resolve the system prompt (fallback to preset if empty), then add
        #    the asset manifests and the user's global directives so they apply
        #    to every target model.
        sys_prompt = system_prompt.strip() or get_template(target_model)
        sys_prompt += "\n\n" + _picture_manifest(len(picture_urls))
        audio_note = _audio_manifest(audio is not None, audio_role)
        if audio_note:
            sys_prompt += "\n\n" + audio_note
            print("[LLMPromptStudio] audio declared as <Audio 1>: %s" % audio_role)
        video_note = _video_manifest(video is not None, video_role, len(frames),
                                     frame_total, video_fps)
        if video_note:
            sys_prompt += "\n\n" + video_note
            print("[LLMPromptStudio] video declared as <Video 1>: %s (%d frames sent)"
                  % (video_role, len(frames)))
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
        #    The video frames follow the pictures, each one carrying its own
        #    position in the clip, so the model reads them as one timeline rather
        #    than as extra reference images.
        if picture_urls or frames:
            user_content = [{"type": "text", "text": user_text}]
            for n, url in enumerate(picture_urls, 1):
                user_content.append({"type": "text", "text": "<Picture %d>:" % n})
                user_content.append({"type": "image_url", "image_url": {"url": url}})
            for label, url in frames:
                user_content.append({"type": "text", "text": label})
                user_content.append({"type": "image_url", "image_url": {"url": url}})
            if picture_urls:
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

        # 6) build the request payload. Only the OpenAI fields are always sent:
        #    top_k, min_p and the two penalty spellings are extensions, and a
        #    server that validates its request body answers 400 on the one it
        #    does not know. Sending them only when they actually DO something
        #    keeps a strict backend happy without costing anything - a neutral
        #    value is a no-op on the backends that accept it anyway. The two
        #    enable_* switches are the manual override of that rule: off means
        #    never sent, so a tuned value can stay on the slider while the field
        #    itself is kept out of a request the backend would refuse.
        payload = {
            "model": resolved_model,
            "messages": messages,
            "temperature": float(temperature),
            "top_p": float(top_p),
            "max_tokens": int(max_tokens),
            "seed": int(seed),
            "stream": False,
        }
        if int(top_k) > 0:
            payload["top_k"] = int(top_k)
        if enable_min_p and float(min_p) > 0.0:
            payload["min_p"] = float(min_p)
        if enable_repeat_penalty and abs(float(repeat_penalty) - 1.0) > 1e-9:
            payload["repetition_penalty"] = float(repeat_penalty)  # vLLM & co
            payload["repeat_penalty"] = float(repeat_penalty)      # llama.cpp
        if extra_template_kwargs is not None:
            # vLLM/SGLang pass this through to the chat template: enable_thinking
            # for Qwen3.x and GLM, thinking for DeepSeek V3.1+. A template simply
            # ignores the name it does not use.
            payload["chat_template_kwargs"] = extra_template_kwargs
        # top-level reasoning switch for DeepSeek-compatible servers and proxies
        payload.update(extra_payload)

        url = _endpoint(base_url, "/chat/completions")

        # 6bis) load -> prompt -> unload: when the model is to be dropped after
        #    the run it also has to be there before it, so the pair is explicit.
        #    A context size is asked for here too, since it can only be chosen
        #    while loading. Loading a big model can take minutes, far more than
        #    a chat timeout sized for generation, hence the floor of 5 minutes.
        ctx_len, ctx_slots = max(0, int(context_length)), max(0, int(context_slots))
        if unload_after or ctx_len or ctx_slots:
            _load_model(base_url, api_key, resolved_model, max(int(timeout), 300),
                        ctx_len, ctx_slots)

        # 7) call the server, retrying once without the extension fields
        try:
            result, err = _post_chat(url, payload, api_key, timeout)
            if err:
                print(err)
                return _ui_result(err, err)
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

        # 10) the whole prompt is in hand: only NOW may the model go. Every
        #     return above leaves it loaded on purpose - a run that failed is
        #     one you are about to launch again, and unloading first would make
        #     the next try pay a full reload of the model it never used.
        if unload_after:
            _unload_model(base_url, api_key, resolved_model, timeout)

        # ui preview shows the CLEANED prompt (no thinking); raw stays on output 2
        return _ui_result(cleaned, raw)


NODE_CLASS_MAPPINGS = {"LLMPromptStudio": LLMPromptStudio}
NODE_DISPLAY_NAME_MAPPINGS = {"LLMPromptStudio": "coco"}
