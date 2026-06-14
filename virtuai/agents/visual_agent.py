"""
Visual Agent — Renders the actual visual artifacts the Creator wrote.
Fourth agent in the VirtuAI pipeline.

CLOUD-ONLY PATH (production):
  - generate_cinematic_reel : Kling 3.0 multi-shot native lipsync (30s)
  - render_image_post       : Nano Banana 2 + PIL slide_renderer
                              (carousel 5 slides OR portrait)

The local Phase-1 tools (Z-Image-Turbo + Daniel LoRA, F5-TTS, SadTalker/
Wav2Lip, ffmpeg reel-builder) still live in virtuai.tools.local_tools and
remain available as MANUAL options for scripts like scripts/publish_v16.py
— but they're no longer exposed to this agent in the automated pipeline.
"""

import json
from pathlib import Path

from crewai import Agent, LLM

from virtuai.tools.cloud_tools import (
    generate_cinematic_reel,
    render_image_post,
)


PERSONA_ANCHOR_PATH = Path(__file__).resolve().parents[1] / "persona" / "persona_anchor.json"


def _load_persona_anchor() -> dict:
    if not PERSONA_ANCHOR_PATH.exists():
        return {}
    try:
        return json.loads(PERSONA_ANCHOR_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def create_visual_agent(llm: LLM, persona: dict) -> Agent:
    anchor = _load_persona_anchor()
    identity = anchor.get("identity", {})
    face = identity.get("face", {})
    persona_face_summary = (
        f"{identity.get('age_range', '26-30')} {identity.get('gender', 'male')}, "
        f"{identity.get('ethnicity_appearance', '')}, "
        f"{face.get('hair', '')}, {face.get('facial_hair', '')}, "
        f"{face.get('eyes', '')}, {face.get('skin', '')}"
    ).strip()

    return Agent(
        role="Visual Content Designer",
        goal=(
            "Take the Creator's written artifacts (reel script JSON / "
            "portrait content JSON / carousel content JSON) and render the "
            "actual MP4s and PNGs the Publisher will push, using the cloud "
            "render path."
        ),
        backstory=(
            "You're the renderer for VirtuAI's daily pack. Cloud-only path:\n\n"
            "TOOL: generate_cinematic_reel(script_json)\n"
            "  Input: JSON from write_viral_script (Creator).\n"
            "  Process: Two parallel Kling 3.0 multi-shot renders (face-\n"
            "  locked via kling_elements with canonical_daniel.png),\n"
            "  concatenated with audio resync, native voice+lipsync, then\n"
            "  Suno underbed. Output: final MP4 path.\n\n"
            "TOOL: render_image_post(content_json, kind='auto')\n"
            "  Input: JSON from write_portrait_content or write_carousel_content.\n"
            "  Process: Nano Banana 2 background generation (persona slides\n"
            "  use canonical_daniel; concept slides are environment-only),\n"
            "  then PIL slide_renderer for typography. Output: 1080x1350\n"
            "  PNG file paths (1 for portrait, 5 for carousel).\n\n"
            f"LOCKED PERSONA — {persona_face_summary}\n"
            "Canonical face passed to Kling/Nano: virtuai/persona/canonical_daniel.png\n\n"
            "POST-RENDER CHECKS — after each call, confirm:\n"
            "  - asset_url is non-empty AND local_path resolves to a file > 0 bytes\n"
            "  - image_prompt / video_prompt match the script topic (not generic)\n"
            "  - 9:16 aspect for reels, 1080×1350 for portraits/carousels\n"
            "If a check fails, do NOT silently move on — flag it in `problems`\n"
            "so Reviewer/Guardian see the issue before publishing.\n\n"
            "STRUCTURED OUTPUT — emit ONE JSON object matching this schema:\n"
            "{\n"
            '  "reference_images_used":    ["virtuai/persona/canonical_daniel.png", ...],\n'
            '  "generated_images":         ["/abs/path/to/portrait.png", ...],\n'
            '  "generated_videos":         ["/abs/path/to/reel.mp4"],\n'
            '  "image_model_used":         "nano-banana-2",\n'
            '  "video_model_used":         "kling-3.0/video",\n'
            '  "visual_consistency_notes": "<face / outfit / brand-tone notes>",\n'
            '  "problems":                 ["<each issue you spotted>"],\n'
            '  "recommendation":           "approve" | "retry" | "revise_prompt"\n'
            "}"
        ),
        llm=llm,
        tools=[
            generate_cinematic_reel,
            render_image_post,
        ],
        verbose=True,
        allow_delegation=False,
    )
