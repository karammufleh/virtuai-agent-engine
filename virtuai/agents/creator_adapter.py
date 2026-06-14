"""
creator_adapter.py — convert the n8n Creator agent's output into the EXACT
content shapes the renderers expect, so daily_pack can render the Creator's
authored content verbatim instead of regenerating it.

The Creator agent emits one consolidated JSON (see virtuai/agents/creator_agent.py).
`adapt_creator_content` maps it to:

  {
    "reel_script":      <write_script-shaped dict> | None,
    "portrait_content": <write_portrait-shaped dict> | None,
    "carousel_content": <write_carousel-shaped dict> | None,
  }

Each piece is None when the Creator didn't supply usable content for it, so
daily_pack can fall back to generating that piece on the chosen topic.

Key mapping (the reason the old direct-pass crashed): the Creator's scene_plan
uses {voiceover, visual_description}, while the reel renderer expects
{audio_text, visual_prompt}.
"""
from __future__ import annotations

import json
import re
from typing import Optional


def _coerce(blob) -> dict:
    if isinstance(blob, dict):
        return blob
    if not blob:
        return {}
    s = re.sub(r"^```(?:json)?|```$", "", str(blob).strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
    return {}


def _adapt_reel(c: dict) -> Optional[dict]:
    scene_plan = c.get("scene_plan") or c.get("scenes") or []
    scenes = []
    for s in scene_plan:
        if not isinstance(s, dict):
            continue
        audio = (s.get("voiceover") or s.get("audio_text") or "").strip()
        visual = (s.get("visual_description") or s.get("visual_prompt") or "").strip()
        if len(audio) < 10:
            continue
        scenes.append({
            "audio_text": audio,
            "visual_prompt": visual or ("documentary candid medium shot of the same man "
                                        "in his early 30s, soft natural light, slight handheld motion"),
            "on_screen_text": (s.get("on_screen_text") or "").strip(),
        })
    if len(scenes) < 3:
        return None
    hook = (c.get("main_hook") or "").strip()
    topic = (hook or (c.get("caption") or "")[:60] or "AI automation in business").strip()[:80]
    return {
        "topic": topic,
        "hook_summary": hook or scenes[0]["audio_text"][:60],
        "scenes": scenes,
        "caption": c.get("caption") or "",
        "hashtags": c.get("hashtags") or [],
        "platform_versions": c.get("platform_versions") or {},
        "_source": "creator",
    }


def _adapt_portrait(c: dict) -> Optional[dict]:
    por = c.get("portrait") or {}
    headline = (por.get("headline") or "").strip()
    image_prompt = (por.get("image_prompt") or "").strip()
    if not (headline and image_prompt):
        return None
    return {
        "type": "portrait",
        "topic": (c.get("main_hook") or headline)[:80],
        "hook_summary": headline,
        "headline": headline,
        "subhead": (por.get("subhead") or "").strip(),
        "image_prompt": image_prompt,
        "post_caption_long": por.get("caption") or c.get("caption") or "",
        "hashtags": por.get("hashtags") or c.get("hashtags") or [],
        "_source": "creator",
    }


def _adapt_carousel(c: dict) -> Optional[dict]:
    car = c.get("carousel") or {}
    slides_in = car.get("slides") or []
    if len(slides_in) < 5:
        return None  # renderer requires exactly 5 — fall back to generation otherwise
    slides = []
    for i, sl in enumerate(slides_in[:5], 1):
        if not isinstance(sl, dict):
            return None
        slides.append({
            "id": i,
            "role": sl.get("role") or ("cover" if i == 1 else "recap" if i == 5 else "body"),
            "headline": (sl.get("headline") or "").strip(),
            "subhead": (sl.get("subhead") or "").strip(),
            "image_prompt": (sl.get("image_prompt") or "").strip(),
            "uses_persona": bool(sl.get("uses_persona", i in (1, 5))),
        })
    if any(not s["headline"] or not s["image_prompt"] for s in slides):
        return None
    return {
        "type": "carousel_5",
        "topic": (c.get("main_hook") or car.get("hook_summary") or slides[0]["headline"])[:80],
        "hook_summary": car.get("hook_summary") or slides[0]["headline"],
        "slides": slides,
        "post_caption_long": car.get("caption") or c.get("caption") or "",
        "hashtags": car.get("hashtags") or c.get("hashtags") or [],
        "_source": "creator",
    }


def adapt_creator_content(creator_output) -> dict:
    """Adapt the Creator agent's output to renderer-ready content. Returns a
    dict with reel_script / portrait_content / carousel_content (any may be
    None when the Creator did not supply usable content for that piece)."""
    c = _coerce(creator_output)
    return {
        "reel_script": _adapt_reel(c),
        "portrait_content": _adapt_portrait(c),
        "carousel_content": _adapt_carousel(c),
    }
