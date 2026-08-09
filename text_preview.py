# -*- coding: utf-8 -*-
"""Text preview node with a token counter.

Shows a piece of text on the node (like any preview node) and tells you how
many tokens it is. Four ways to count, from "no install needed" to "exactly
what the model will see":

  * estimate  - pure arithmetic, no dependency, +/- 15%
  * CLIP      - the tokenizer of a connected CLIP: the real number for an
                image model, where the 75-token chunk is what matters
  * tiktoken  - the OpenAI BPE, if the package happens to be installed
  * server    - POST /tokenize on a vLLM server: the exact count for the very
                model that will read the prompt
"""

import json
import re
import urllib.error
import urllib.request

TOKENIZERS = [
    "auto",
    "estimate (no install)",
    "CLIP (connected)",
    "tiktoken o200k_base (GPT-4o / GPT-5)",
    "tiktoken cl100k_base (GPT-4 / 3.5)",
    "LLM server /tokenize (vLLM)",
]

# A CLIP chunk is 77 slots, 75 of which are usable text.
CLIP_CHUNK = 75

_TIKTOKEN_CACHE = {}


# --------------------------------------------------------------------------
# counters. Each returns (count, method_label) and raises on failure, so the
# caller can fall back to the estimate and say why in the report.
# --------------------------------------------------------------------------
def _estimate_tokens(text: str) -> int:
    """Dependency-free approximation.

    Two rules of thumb disagree depending on the text: ~4 characters per token
    holds for prose, ~1.33 tokens per word for keyword soup full of commas and
    parentheses. Taking the larger of the two keeps the estimate from lying low
    on tag-style prompts, which is the direction that actually costs you when
    you are sizing a context window.
    """
    if not text.strip():
        return 0
    chars = len(text)
    words = len(re.findall(r"\S+", text))
    return max(1, int(round(max(chars / 4.0, words * 1.33))))


def _tiktoken_tokens(text: str, encoding: str) -> int:
    enc = _TIKTOKEN_CACHE.get(encoding)
    if enc is None:
        import tiktoken  # optional dependency
        enc = tiktoken.get_encoding(encoding)
        _TIKTOKEN_CACHE[encoding] = enc
    # disallowed_special=() so a prompt containing <|endoftext|> counts as text
    # instead of raising.
    return len(enc.encode(text, disallowed_special=()))


def _token_id(entry):
    """One entry of a ComfyUI CLIP chunk -> its token id."""
    if isinstance(entry, (tuple, list)):
        return entry[0]
    if isinstance(entry, dict):
        return entry.get("token", entry.get("id"))
    return entry


def _chunk_core(chunk, pad=None):
    """A chunk minus its trailing padding.

    `pad` has to be told from the outside, read off a chunk that is known to be
    padded (the empty string). Guessing it from the chunk's own last entry
    breaks on a chunk filled to the brim, where that entry is the end marker
    and dropping it costs a real token.
    """
    ids = [_token_id(e) for e in chunk]
    if not ids:
        return ids
    if pad is None:
        pad = ids[-1]
    end = len(ids)
    while end > 0 and ids[end - 1] == pad:
        end -= 1
    return ids[:end]


def _clip_tokens(clip, text: str) -> int:
    """Count the real tokens of a connected CLIP, whatever its flavour.

    clip.tokenize() pads every chunk to 77 and wraps it in start/end markers,
    and which markers depends on the encoder (CLIP-L pads with its end token,
    CLIP-G pads with 0, T5 has no start token at all). Rather than hard-code
    that table, tokenize the empty string first: what comes back IS the
    overhead of one chunk, so subtracting it works for any encoder present and
    for any future one.
    """
    if clip is None:
        raise RuntimeError("no CLIP connected")

    empty = clip.tokenize("")
    full = clip.tokenize(text)

    best = 0
    for key, chunks in full.items():
        overhead, pad = 0, None
        base = empty.get(key) or []
        if base and len(base[0]):
            pad = _token_id(base[0][-1])
            overhead = len(_chunk_core(base[0], pad))
        total = 0
        for chunk in chunks:
            total += max(0, len(_chunk_core(chunk, pad)) - overhead)
        best = max(best, total)
    return best


def _server_tokens(text: str, base_url: str, api_key: str, model: str,
                   timeout: int = 20) -> int:
    """Ask the LLM server itself. vLLM serves /tokenize outside of /v1."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("base_url is empty")

    urls = [base + "/tokenize"]
    if base.endswith("/v1"):
        urls.append(base[: -len("/v1")] + "/tokenize")

    payload = {"prompt": text}
    if model and model.strip():
        payload["model"] = model.strip()

    headers = {"Content-Type": "application/json"}
    if api_key and api_key.strip():
        headers["Authorization"] = "Bearer " + api_key.strip()

    last = None
    for url in urls:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # try the next spelling of the URL
            last = e
            continue
        if isinstance(data, dict):
            if isinstance(data.get("count"), int):
                return data["count"]
            toks = data.get("tokens")
            if isinstance(toks, list):
                return len(toks)
        last = RuntimeError("unexpected answer: " + json.dumps(data)[:200])
    raise RuntimeError(str(last) if last else "no /tokenize endpoint")


def _has_tiktoken() -> bool:
    try:
        import tiktoken  # noqa: F401
        return True
    except Exception:
        return False


def _count(text, method, clip, base_url, api_key, model):
    """Return (count, label, warning)."""
    if method == "auto":
        if clip is not None:
            method = "CLIP (connected)"
        elif _has_tiktoken():
            method = "tiktoken o200k_base (GPT-4o / GPT-5)"
        else:
            method = "estimate (no install)"

    try:
        if method == "CLIP (connected)":
            return _clip_tokens(clip, text), "CLIP", None
        if method.startswith("tiktoken o200k"):
            return _tiktoken_tokens(text, "o200k_base"), "tiktoken o200k_base", None
        if method.startswith("tiktoken cl100k"):
            return _tiktoken_tokens(text, "cl100k_base"), "tiktoken cl100k_base", None
        if method.startswith("LLM server"):
            return (_server_tokens(text, base_url, api_key, model),
                    "server /tokenize", None)
    except ImportError:
        return (_estimate_tokens(text), "estimate",
                "tiktoken is not installed (pip install tiktoken) - estimated instead")
    except Exception as e:
        return (_estimate_tokens(text), "estimate",
                "%s failed (%s) - estimated instead" % (method.split(" (")[0], e))

    return _estimate_tokens(text), "estimate", None


def _report(text, count, label, warning, token_limit, clip_chunks):
    lines = []
    unit = "token" if count == 1 else "tokens"
    head = "%d %s  (%s)" % (count, unit, label)
    if token_limit > 0:
        over = count - token_limit
        head += "  -  %s" % ("%d OVER the %d limit" % (over, token_limit)
                             if over > 0 else "%d left of %d" % (-over, token_limit))
    lines.append(head)

    words = len(re.findall(r"\S+", text))
    lines.append("%d chars - %d words - %d lines"
                 % (len(text), words, text.count("\n") + 1 if text else 0))

    if clip_chunks and count > CLIP_CHUNK:
        n = (count + CLIP_CHUNK - 1) // CLIP_CHUNK
        lines.append("%d chunks of %d - past token %d the encoder starts a new "
                     "chunk, which weakens the whole prompt"
                     % (n, CLIP_CHUNK, CLIP_CHUNK))

    if warning:
        lines.append("! " + warning)
    return "\n".join(lines)


class LLMTextTokenPreview:
    """Preview a text on the node and count its tokens."""

    CATEGORY = "LLM Prompt Studio"
    FUNCTION = "preview"
    OUTPUT_NODE = True
    RETURN_TYPES = ("STRING", "INT", "STRING")
    RETURN_NAMES = ("text", "tokens", "report")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "The text to preview. Connect a STRING output here, "
                               "or type into the box to count something by hand.",
                }),
                "tokenizer": (TOKENIZERS, {
                    "default": "auto",
                    "tooltip": "How to count. 'auto' = the connected CLIP if there "
                               "is one, else tiktoken if installed, else the "
                               "estimate. The estimate needs nothing installed and "
                               "lands within ~15%.",
                }),
                "token_limit": ("INT", {
                    "default": 0, "min": 0, "max": 1000000,
                    "tooltip": "Budget to compare against: the report says how many "
                               "tokens are left or how far over you are. 0 = off. "
                               "75 is the CLIP chunk, 77 the raw CLIP slot count.",
                }),
            },
            "optional": {
                "clip": ("CLIP", {
                    "tooltip": "Connect the CLIP of your image model to count the "
                               "tokens it will actually see (75 per chunk).",
                }),
                "base_url": ("STRING", {
                    "default": "http://localhost:1234/v1",
                    "tooltip": "Only for 'LLM server /tokenize': the same address as "
                               "the generator node. vLLM serves /tokenize; LM Studio "
                               "does not, and falls back to the estimate.",
                }),
                "api_key": ("STRING", {"default": "lm-studio"}),
                "model": ("STRING", {
                    "default": "",
                    "tooltip": "Only for 'LLM server /tokenize'. Empty = the server "
                               "picks its loaded model.",
                }),
            },
        }

    # No IS_CHANGED: forcing a re-run would also invalidate everything wired to
    # the passthrough output, and re-sampling a whole graph to redraw a text box
    # is a bad trade. Unchanged inputs mean an unchanged count anyway.

    def preview(self, text, tokenizer, token_limit, clip=None,
                base_url="", api_key="", model=""):
        text = "" if text is None else str(text)
        count, label, warning = _count(text, tokenizer, clip, base_url, api_key, model)
        report = _report(text, count, label, warning, int(token_limit),
                         clip_chunks=(label == "CLIP"))
        return {
            "ui": {"text": [text], "info": [report]},
            "result": (text, count, report),
        }


class LLMTokenCount:
    """Just the number. No tokenizer to pick, nothing to install, nothing to
    connect: the count is the arithmetic estimate, within ~15%."""

    CATEGORY = "LLM Prompt Studio"
    FUNCTION = "count"
    OUTPUT_NODE = True
    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("text", "tokens")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "The text to measure. Connect a STRING output here, "
                               "or type into the box.",
                }),
            },
        }

    def count(self, text):
        text = "" if text is None else str(text)
        n = _estimate_tokens(text)
        info = "~%d %s" % (n, "token" if n == 1 else "tokens")
        # No "text" in the ui payload: this node shows the count only.
        return {"ui": {"info": [info]}, "result": (text, n)}


NODE_CLASS_MAPPINGS = {
    "LLMTextTokenPreview": LLMTextTokenPreview,
    "LLMTokenCount": LLMTokenCount,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "LLMTextTokenPreview": "Text Preview + Token Count",
    "LLMTokenCount": "Token Count (simple)",
}
