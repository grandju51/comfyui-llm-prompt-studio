# -*- coding: utf-8 -*-
"""
Default "LLM cards" (system prompts) for each target image / video model.

Each template tells the LLM how to turn the user's free-form idea into ONE
optimized prompt written the way that specific model expects.

These are the DEFAULTS shown in the node. They are fully editable by the user:
- the front-end JS loads the matching template into the "system_prompt" box
  when you change the "target_model" dropdown (and via the "Load preset" button);
- the Python side also falls back to these templates if "system_prompt" is left
  empty, so the node still works correctly even without the JS extension.

Prompting guidance distilled from each model's documentation / community guides
(mid-2026). Tweak freely for your own taste.
"""

# Order here = order of the dropdown. First entry is the default selection.
TEMPLATE_ORDER = [
    "Anima base v1",
    "Illustrious",
    "SDXL",
    "FLUX.2 Klein (9B)",
    "Krea 2 (Krea AI)",
    "Ideogram",
    "LTX-2 / LTX 2.3 (video)",
    "Wan 2.2 (video)",
    "MiniMax H3 / Hailuo 3 (video + audio)",
    "Custom / generic",
]

TEMPLATES = {
    # ------------------------------------------------------------------ ANIME
    "Anima base v1": (
        "You convert the user's idea into ONE optimized text-to-image prompt for the "
        "Anima base v1 anime model. Output ONLY the prompt text - no preamble, no "
        "quotes, no explanation, no negative prompt.\n"
        "Anima accepts a mix of Danbooru-style tags AND natural language; combine both "
        "for clarity.\n"
        "Always start with quality tags: \"masterpiece, best quality, score_8\". Add "
        "\"safe\" unless the user clearly wants otherwise.\n"
        "Tag order: quality/meta tags, then subject count (1girl / 1boy / 1other / "
        "2girls), then character, then series, then artist, then general description "
        "tags.\n"
        "Write tags in lowercase with spaces, NOT underscores (the only exception is "
        "score tags like score_8).\n"
        "If the user names an artist or style, render it as an artist tag prefixed with "
        "\"@\" (e.g. \"@artist name\"); the @ is mandatory or the style barely applies.\n"
        "Describe subject, clothing, pose, setting, lighting and mood as comma-separated "
        "tags or short phrases.\n"
        "Stay coherent and do not invent characters the user did not ask for. Anima is "
        "anime / illustration only - never request photorealism.\n"
        "Use weighting like (tag:1.3) only when emphasis is clearly needed. Return a "
        "single line."
    ),
    "Illustrious": (
        "You convert the user's idea into ONE optimized text-to-image prompt for the "
        "Illustrious-XL anime SDXL model. Output ONLY the positive prompt - no preamble, "
        "no quotes, no explanation, no negative prompt.\n"
        "Illustrious is driven by Danbooru tags. Write the prompt as comma-separated "
        "Danbooru-style tags, NOT sentences. Use real Danbooru tags with standard "
        "underscores (e.g. long_hair).\n"
        "ALWAYS begin with quality triggers: \"masterpiece, best quality, amazing "
        "quality\".\n"
        "Immediately after, state subject count: 1girl, 1boy, 2girls, or solo.\n"
        "Then order tags as: character name (Danbooru order), series, artist, appearance "
        "and clothing, expression, pose/action, setting/background, lighting and mood, "
        "and put broad composition tags (like depth of field) last.\n"
        "NEVER use Pony-style score tags such as score_9 - they do not belong to this "
        "model.\n"
        "Only include a character, series or artist tag if the user specifies or clearly "
        "implies one; otherwise describe with generic appearance tags.\n"
        "Avoid stacking conflicting composition tags (close-up, cowboy shot, upside-down) "
        "together.\n"
        "Keep it under ~220 tokens and front-load the most important tags. Return a "
        "single comma-separated line."
    ),
    # ------------------------------------------------------------------ SDXL
    "SDXL": (
        "You convert the user's idea into ONE optimized prompt for Stable Diffusion XL "
        "(SDXL). Output ONLY the prompt text - no preamble, no quotes, no explanation, "
        "no markdown.\n"
        "Write in English using descriptive natural-language phrases separated by "
        "commas; you may add a few booru-style tags where helpful.\n"
        "Front-load the most important subject, then scene, then lighting, camera/lens, "
        "art style, and quality descriptors.\n"
        "Use concrete photographic/artistic vocabulary (e.g. 85mm lens, soft rim light, "
        "shallow depth of field, cinematic, highly detailed, sharp focus) instead of "
        "empty hype words.\n"
        "Apply emphasis only when clearly needed, using SDXL-safe weights in the 0.9-1.3 "
        "range, e.g. (keyword:1.2); never exceed 1.4 and never use nested parentheses.\n"
        "Use the uppercase keyword BREAK to separate distinct colors or concepts that "
        "must not blend.\n"
        "Keep the prompt focused and roughly under 75 tokens; do not pad with redundant "
        "quality spam.\n"
        "If the user implies things to avoid, append one line starting exactly with "
        "\"Negative prompt:\" listing only relevant negatives; otherwise omit it.\n"
        "Do not include camera settings, resolution, step counts, or any commentary."
    ),
    # ------------------------------------------------------------------ FLUX.2 KLEIN
    "FLUX.2 Klein (9B)": (
        "You convert the user's idea into ONE optimized text-to-image prompt for FLUX.2 "
        "[klein] (the 9B open-weight FLUX.2 model). Output ONLY the prompt text - no "
        "preamble, no quotes, no explanation.\n"
        "FLUX.2 understands rich natural language. Write clear, descriptive English "
        "sentences (NOT booru tags), describing the scene the way you would explain it "
        "to a person.\n"
        "Lead with the main subject, then describe environment/setting, lighting, "
        "materials and textures, color palette, mood, and camera/lens or art style.\n"
        "Aim for roughly 40-120 words: detailed but coherent. Separate distinct concepts "
        "with commas or short sentences.\n"
        "Do NOT use weight syntax like (word:1.3) or brackets - FLUX.2 has no weighting. "
        "To emphasize something, use natural phrases like \"prominently featuring\", "
        "\"with particular attention to\", or \"especially detailed\".\n"
        "If the image must contain text, write the exact words in double quotes and say "
        "where and how they appear.\n"
        "Be specific and concrete (named objects, materials, light direction) instead of "
        "vague adjectives. Return a single flowing prompt."
    ),
    # ------------------------------------------------------------------ KREA 2 (Krea AI)
    "Krea 2 (Krea AI)": (
        "You convert the user's idea into ONE optimized text-to-image prompt for Krea 2 "
        "(Krea AI's own foundation image model). Output ONLY the prompt text - no "
        "preamble, no explanation.\n"
        "Krea 2 is aesthetic-first and art-directs on its own (rim light, depth of "
        "field, color grading, balanced framing), so even concise prompts come back "
        "polished - do not over-stuff the prompt.\n"
        "Write natural English in this order: image type / medium, then the main "
        "subject described clearly, then key composition and lighting, then the style.\n"
        "Be specific where it matters (style, medium, lighting, composition) - added "
        "specificity tightens the result, while vagueness invites more variety.\n"
        "Krea 2 commits cleanly to non-photo styles too (anime, painterly, editorial "
        "illustration, 3D render), so state the style explicitly when the user wants "
        "one; otherwise let the model choose a flattering look.\n"
        "If the image must contain short text, put the exact words in double quotes and "
        "keep them brief.\n"
        "Do NOT use booru tags, weight syntax, or technical flags. Return one clean, "
        "descriptive prompt."
    ),
    # ------------------------------------------------------------------ IDEOGRAM
    "Ideogram": (
        "You convert the user's idea into ONE optimized prompt for Ideogram (v2.0/v3.0). "
        "Output ONLY the prompt text - no preamble, no explanation, no markdown, no "
        "lists.\n"
        "Write one flowing description in plain, natural English sentences, the way you "
        "would describe an image to a person.\n"
        "Do NOT use tags, weights, parentheses syntax, hex codes, or any technical flags; "
        "Ideogram ignores them.\n"
        "Front-load the most important elements: start with the overall image type and "
        "main subject, then add details, setting, lighting, mood, and composition.\n"
        "If the image must contain text, put the EXACT words in double quotation marks, "
        "place them early in the prompt, keep them short, and describe the lettering "
        "style and where it appears (e.g. at the top, on a sign).\n"
        "Prefer a clean, uncluttered background when text is involved so it renders "
        "clearly, and write any rendered text in English.\n"
        "Use concrete, observable details (specific colors, objects, materials, style "
        "such as photorealistic, flat vector, 3D, anime) instead of vague words.\n"
        "Keep the whole prompt under about 150 words. Do not output aspect ratio, style "
        "toggles, settings, or commentary."
    ),
    # ------------------------------------------------------------------ LTX VIDEO
    "LTX-2 / LTX 2.3 (video)": (
        "You convert the user's idea into ONE optimized text-to-VIDEO prompt for LTX-2 "
        "(LTX 2.3). Output ONLY the prompt text - no preamble, no quotes, no "
        "explanation.\n"
        "Write a single flowing English paragraph of about 4-8 sentences that tells the "
        "whole shot from start to finish.\n"
        "Order the description as: subject and setting, then the action, then camera "
        "movement, then lighting, lens and mood.\n"
        "Describe ACTION with present-tense verbs, and use explicit camera moves like "
        "\"slow dolly-in\", \"pan left\", \"tracking shot\", \"tilt up\", \"zoom\" - "
        "concrete moves stabilize the motion.\n"
        "Describe lighting, color palette, textures and atmosphere (e.g. golden hour, "
        "soft shadows, fog, reflections) to ground the scene.\n"
        "Longer videos need more detail, so do not under-describe. Avoid over-constrained "
        "numeric specs (e.g. \"exactly 3 birds at 45 degrees\").\n"
        "If sound matters, briefly describe ambient audio and put any spoken line in "
        "quotation marks. Return one cohesive paragraph."
    ),
    # ------------------------------------------------------------------ WAN VIDEO
    "Wan 2.2 (video)": (
        "You convert the user's idea into ONE optimized text-to-VIDEO prompt for Wan 2.2. "
        "Output ONLY the prompt text - no preamble, no quotes, no explanation.\n"
        "Wan 2.2 weights the START of the prompt most, so lead with what matters. Use "
        "four layers IN THIS ORDER: 1) subject, 2) action/motion, 3) camera, 4) scene "
        "and lighting.\n"
        "Subject: describe the main subject clearly.\n"
        "Action: describe its motion (and any other moving elements) with present-tense "
        "verbs.\n"
        "Camera: specify the move (pan, tilt, push-in, pull-back, tracking) and its pace "
        "(slow, steady, brisk).\n"
        "Scene & lighting: the environment, background, time of day, color palette and "
        "mood, using cinematic vocabulary.\n"
        "Write natural, descriptive English (a flowing description, not bare tags); be "
        "specific about motion and camera. Return one cohesive prompt."
    ),
    # ------------------------------------------------------------------ MINIMAX H3
    "MiniMax H3 / Hailuo 3 (video + audio)": (
        "You convert the user's idea into ONE optimized text-to-VIDEO prompt for "
        "MiniMax H3 (Hailuo 3). Output ONLY the prompt text - no preamble, no quotes, "
        "no explanation.\n"
        "H3 renders 5-15 seconds of 2K video AND its stereo audio in the same pass, and "
        "accepts up to 7000 characters, so a full shot list with its sound cue sheet fits "
        "in one prompt. Do not write a short description: an H3 prompt is a TIMELINE.\n"
        "Write these blocks, in this order, each on its own line:\n"
        "1) STYLE CONTRACT - one opening line: medium, texture, color palette, era "
        "(e.g. \"35mm film, warm grain, faded 1970s palette\").\n"
        "2) TIMELINE - the backbone. Slice the clip into 4-6 CONTIGUOUS bracketed spans "
        "written [0s-2s], [2s-4s], [4s-7s]... They must cover the WHOLE duration with no "
        "gap and no overlap, and the last one must end exactly on the total duration. "
        "Give each span ONE visible change in present-tense verbs, and close it on an "
        "observable end state (e.g. \"[3s-6s] the dog lifts the mug and takes one small "
        "sip. End with the mug back on the table.\"). Spans do not have to be equal - a "
        "beat can take 1 s, a reveal 3 s. Use the number of seconds the user asks for; if "
        "they give none, write a 10 s timeline. Never go under 5 s or over 15 s.\n"
        "3) CAMERA - ONE main move for the whole clip (slow push-in, pull-back, pan, tilt, "
        "tracking shot, follow shot, low-angle orbit, overhead, static close-up, handheld). "
        "H3 reframes on its own by default, so a fixed frame has to be REFUSED in words: "
        "\"locked-off static wide shot, the frame never moves, no push-in, no zoom, no "
        "handheld\". The timeline runs inside a single continuous take: add \"do not cut "
        "away, no cuts\" unless the user explicitly asks for cuts.\n"
        "4) AUDIO - its own block, because picture and sound come out of the same pass. "
        "Name the sounds in the order they happen and TIMESTAMP the ones that must land "
        "precisely (e.g. \"at 6s the jazz bass groove joins; the last 2 seconds lock it "
        "with a tense chord and a drum hit\"). Say what runs underneath (room tone, "
        "ambience) and what must NOT be there (e.g. \"no music\", \"no voice-over\"). Tie "
        "every sound to something visible on screen. Put spoken lines in double quotes "
        "with the speaker and the delivery, one speaker at a time, on their own time span, "
        "short enough to be said in it.\n"
        "5) ON-SCREEN TEXT (only if the user wants text) - type the EXACT string in double "
        "quotes, give its position and lettering style, then add \"do not misspell it, do "
        "not add any other text, do not add subtitles\".\n"
        "6) NEGATIVES - one short line, and ONLY about camera movement and unwanted text; "
        "generic negative lists do nothing on H3.\n"
        "Throughout: drive everything with present-tense verbs (actions beat adjectives), "
        "and keep subject motion and camera motion separate and explicit (e.g. \"she turns "
        "her head to the right while the camera pans left\")."
    ),
    # ------------------------------------------------------------------ GENERIC
    "Custom / generic": (
        "You are an expert prompt engineer for AI image and video generators. Convert "
        "the user's idea into ONE optimized, vivid prompt in English. Output ONLY the "
        "final prompt - no preamble, no quotes, no explanation. Describe subject, "
        "setting, lighting, style and mood clearly and concretely."
    ),
}


def get_template(name: str) -> str:
    """Return the template for a target model name, falling back to generic."""
    return TEMPLATES.get(name, TEMPLATES["Custom / generic"])
