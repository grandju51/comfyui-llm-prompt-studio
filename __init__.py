# -*- coding: utf-8 -*-
"""
ComfyUI - LLM Prompt Studio
Connect to LM Studio or vLLM (OpenAI-compatible) and generate optimized
image / video prompts from inside ComfyUI.
"""

import json
import urllib.error
import urllib.request

from .civitai_prompt import (
    NODE_CLASS_MAPPINGS as _CIVITAI_CLASSES,
    NODE_DISPLAY_NAME_MAPPINGS as _CIVITAI_NAMES,
)
from .nodes import (
    NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS,
    _list_models,
    _pick_chat_model,
)
from .prompt_templates import TEMPLATES

NODE_CLASS_MAPPINGS.update(_CIVITAI_CLASSES)
NODE_DISPLAY_NAME_MAPPINGS.update(_CIVITAI_NAMES)

# Tell ComfyUI where the front-end JS lives.
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]


# --------------------------------------------------------------------------
# Server routes used by the front-end JS:
#   GET /llm_prompt_studio/models?base_url=...&api_key=...   -> { "models": [...] }
#   GET /llm_prompt_studio/templates                          -> { name: template }
# --------------------------------------------------------------------------
try:
    import server  # ComfyUI's PromptServer module
    from aiohttp import web

    routes = server.PromptServer.instance.routes

    @routes.get("/llm_prompt_studio/models")
    async def _route_models(request):
        base_url = request.query.get("base_url", "http://localhost:1234/v1")
        api_key = request.query.get("api_key", "")
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            models = await loop.run_in_executor(None, _list_models, base_url, api_key)
            suggested = _pick_chat_model(models)
            return web.json_response({"models": models, "suggested": suggested})
        except Exception as e:
            return web.json_response({"models": [], "suggested": None, "error": str(e)})

    @routes.get("/llm_prompt_studio/templates")
    async def _route_templates(request):
        return web.json_response(TEMPLATES)

    print("[LLM Prompt Studio] routes registered.")

except Exception as e:  # pragma: no cover - keeps node usable without the server
    print("[LLM Prompt Studio] could not register web routes:", e)
