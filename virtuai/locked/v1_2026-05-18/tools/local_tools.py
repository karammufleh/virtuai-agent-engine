"""
local_tools.py — CrewAI tools backed by the local VLM backend.

All tools call the local FastAPI inference server (backend.py) — no external
APIs. Pre-pivot Gemini-based tools live under virtuai/tools/_legacy/ for history.

All tools follow the @tool decorator pattern used by CrewAI.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import datetime

from crewai.tools import tool

from virtuai.models.vlm_client import get_client

logger = logging.getLogger("virtuai.tools.local")

IMAGES_DIR = Path(__file__).parent.parent / "data" / "content_packages" / "images"


# ══════════════════════════════════════════════════════════════════════════════
# Image & Video Generation Tools (Visual Agent)
# ══════════════════════════════════════════════════════════════════════════════

@tool("generate_image_local")
def generate_image_local(prompt: str, platform: str) -> str:
    """
    Generate an image using the local Z-Image-Turbo model + persona LoRA via mflux.
    The model runs on Apple Silicon with MLX — no external API needed.
    Images are face-locked to the Daniel Calder persona via the trained LoRA.

    Args:
        prompt: Description of the image to generate (e.g. 'entrepreneur at desk with laptop, dark studio')
        platform: Target platform — linkedin, x, instagram, tiktok, youtube_shorts, medium

    Returns:
        JSON with image_path, prompt used, dimensions, and generation time.
    """
    client = get_client()

    try:
        result = client.generate_image(
            prompt=prompt,
            platform=platform,
        )
        return json.dumps({
            "status": "generated",
            "platform": platform,
            "image_path": result["image_path"],
            "prompt_used": result["prompt_used"],
            "model": result["model"],
            "dimensions": f"{result['width']}x{result['height']}",
            "generation_time_ms": result["generation_time_ms"],
        })
    except Exception as e:
        logger.error(f"Image generation failed for {platform}: {e}")
        return json.dumps({"status": "error", "platform": platform, "error": str(e)})


@tool("generate_video_local")
def generate_video_local(scene_descriptions: str, platform: str) -> str:
    """
    Generate a short video by creating a sequence of AI-generated images
    and stitching them together with transitions and text overlays.
    Uses Z-Image-Turbo for each frame and moviepy for video assembly.
    NOTE: This is a pre-Phase-3 placeholder. LivePortrait talking-head video
    will replace it once Phase 3 lands.

    Args:
        scene_descriptions: Pipe-separated (|) scene descriptions for each frame.
            Example: 'entrepreneur coding at desk | AI dashboard on screen | team celebrating launch'
        platform: Target platform — tiktok, youtube_shorts, instagram

    Returns:
        JSON with video_path, frame paths, duration, and generation time.
    """
    client = get_client()

    prompts = [s.strip() for s in scene_descriptions.split("|") if s.strip()]
    if not prompts:
        return json.dumps({"status": "error", "error": "No scene descriptions provided"})

    # Limit frames for reasonable generation time
    if len(prompts) > 5:
        prompts = prompts[:5]

    try:
        result = client.generate_video(
            prompts=prompts,
            platform=platform,
            duration_per_frame=3.0,
            add_text_overlay=True,
        )
        return json.dumps({
            "status": "generated",
            "platform": platform,
            "video_path": result["video_path"],
            "frame_paths": result["frame_paths"],
            "total_frames": result["total_frames"],
            "duration_seconds": result["duration_seconds"],
            "generation_time_ms": result["generation_time_ms"],
        })
    except Exception as e:
        logger.error(f"Video generation failed for {platform}: {e}")
        return json.dumps({"status": "error", "platform": platform, "error": str(e)})


@tool("generate_talking_head_local")
def generate_talking_head_local(script: str, platform: str) -> str:
    """
    Generate a Daniel Calder talking-head video for video platforms.

    Pipeline (all local):
        script (text) → F5-TTS (Daniel's cloned voice) → audio.wav
        audio.wav + Daniel face image → SadTalker → talking_head.mp4

    Use this for TikTok, Instagram Reels, YouTube Shorts, X video posts —
    any platform where Daniel speaking on camera is the right format.

    Args:
        script: The full spoken script. Should be:
                - 15s for TikTok (~38 words)
                - 30s for Instagram Reels (~75 words)
                - 45s for YouTube Shorts (~110 words)
                - hook (first 3s) → body → CTA (last 3s)
        platform: Target platform (tiktok, instagram_reels, youtube_shorts, x).

    Returns:
        JSON with video_path, audio_path, duration_s, generation_time_ms.
    """
    client = get_client()
    try:
        result = client.generate_talking_head(
            text=script,
            size=256,
            preprocess="full",
            still=False,
            cpu=True,
        )
        return json.dumps({
            "status": "generated",
            "platform": platform,
            "video_path": result["video_path"],
            "audio_path": result["audio_path"],
            "duration_s": result["duration_s"],
            "generation_time_ms": result["generation_time_ms"],
            "model": result["model"],
        })
    except Exception as e:
        logger.error(f"Talking-head generation failed for {platform}: {e}")
        return json.dumps({"status": "error", "platform": platform, "error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# Content Generation Tools (Creator Agent)
# ══════════════════════════════════════════════════════════════════════════════

@tool("generate_platform_content")
def generate_platform_content(platform: str, topic: str, format_type: str) -> str:
    """
    Generate content for a specific platform using the local fine-tuned VLM.

    The model has been fine-tuned on VirtuAI Mentor persona data, so it
    generates on-brand content natively without any external API calls.

    Args:
        platform: Target platform — linkedin, x, instagram, tiktok, youtube_shorts, medium
        topic: The content topic or angle (e.g. 'AI automation for founders')
        format_type: Content format — post, thread, script, article, carousel

    Returns:
        Generated content string, formatted for the target platform.
    """
    client = get_client()

    format_descriptions = {
        "post": "a complete social media post with hook, body, and CTA",
        "thread": "a Twitter/X thread with 4-6 numbered tweets, strong opener and closer",
        "script": "a video script for spoken delivery with hook in first 2 seconds",
        "article": "a long-form article section with headers and structured content",
        "carousel": "a 5-slide carousel concept with hook slide and CTA slide",
    }

    format_desc = format_descriptions.get(format_type, "a piece of content")

    prompt = (
        f"Write {format_desc} about '{topic}' for {platform.replace('_', ' ').upper()}.\n\n"
        f"Requirements:\n"
        f"- Strong scroll-stopping hook as the first line\n"
        f"- Actionable, direct, no fluff\n"
        f"- Platform-appropriate formatting and length\n"
        f"- End with exactly one CTA appropriate for {platform}\n"
        f"- Include platform-appropriate hashtags"
    )

    try:
        content = client.generate(
            prompt=prompt,
            platform=platform,
            max_tokens=600 if format_type in ("article", "thread") else 350,
            temperature=0.75,
        )
        return json.dumps({
            "platform": platform,
            "format": format_type,
            "topic": topic,
            "content": content,
            "status": "generated",
            "model": "local-vlm",
        })
    except Exception as e:
        logger.error(f"Content generation failed for {platform}: {e}")
        return json.dumps({"status": "error", "platform": platform, "error": str(e)})


@tool("analyze_image_for_content")
def analyze_image_for_content(image_path: str, platform: str) -> str:
    """
    Use the VLM's vision capability to analyze a generated image and produce
    a platform-specific caption and content angle.

    This is the core Vision Language Model feature — combines image understanding
    with the fine-tuned VirtuAI Mentor voice.

    Args:
        image_path: Absolute or relative path to the image file (PNG/JPEG)
        platform: Target platform for caption style (linkedin, x, instagram, etc.)

    Returns:
        JSON with image analysis, suggested content angle, and generated caption.
    """
    client = get_client()

    health = client.health()
    if not health.get("vision_capable", False):
        # Fallback: text-only description
        return json.dumps({
            "status": "vision_unavailable",
            "image_path": image_path,
            "platform": platform,
            "note": "VLM vision capability not available. Using text-only mode.",
            "caption": _generate_caption_text_only(platform),
        })

    prompt = (
        f"Analyze this image for the VirtuAI Mentor brand.\n\n"
        f"Provide:\n"
        f"1. Visual analysis: What does this image communicate? Does it match "
        f"the dark, futuristic, minimal aesthetic?\n"
        f"2. Content angle: What's the strongest narrative this image supports?\n"
        f"3. Caption: Write a complete {platform} caption in VirtuAI Mentor voice. "
        f"Hook first line. Strong CTA at end."
    )

    try:
        analysis = client.analyze_image(
            image_path=image_path,
            prompt=prompt,
            platform=platform,
            max_tokens=400,
        )
        return json.dumps({
            "status": "analyzed",
            "image_path": image_path,
            "platform": platform,
            "analysis": analysis,
        })
    except FileNotFoundError:
        return json.dumps({
            "status": "error",
            "error": f"Image not found: {image_path}",
        })
    except Exception as e:
        logger.error(f"Image analysis failed: {e}")
        return json.dumps({"status": "error", "error": str(e)})


def _generate_caption_text_only(platform: str) -> str:
    """Fallback caption when vision is not available."""
    client = get_client()
    prompt = (
        f"Write a {platform} caption for a dark-aesthetic, minimal luxury workspace photo "
        f"featuring a focused entrepreneur. VirtuAI Mentor voice. Hook + CTA."
    )
    try:
        return client.generate(prompt=prompt, platform=platform, max_tokens=200)
    except Exception:
        return "The work that builds empires doesn't look glamorous. It looks like this. Save this. 🎯"


# ══════════════════════════════════════════════════════════════════════════════
# Sentiment & Quality Analysis Tools (Reviewer Agent)
# ══════════════════════════════════════════════════════════════════════════════

@tool("analyze_sentiment_local")
def analyze_sentiment_local(text: str) -> str:
    """
    Analyze the sentiment, tone, and VirtuAI persona match of a piece of content
    using the local VLM backend.

    Args:
        text: The content text to analyze.

    Returns:
        JSON with sentiment, tone analysis, energy level, persona match, and issues.
    """
    client = get_client()

    try:
        result = client.analyze_sentiment(text)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Sentiment analysis failed: {e}")
        return json.dumps({
            "sentiment": "unknown",
            "confidence": 0.0,
            "tone": [],
            "energy_level": "unknown",
            "persona_match": False,
            "issues": [f"Analysis error: {str(e)}"],
        })


@tool("review_content_quality")
def review_content_quality(content: str, platform: str) -> str:
    """
    Full quality review of content: persona consistency, hook quality,
    CTA presence, formatting, banned phrases check.

    Args:
        content: The content to review.
        platform: The target platform (affects format expectations).

    Returns:
        JSON with verdict (PASS/REVISE), score, and specific feedback.
    """
    client = get_client()

    prompt = (
        f"Review this {platform} content for quality:\n\n\"{content}\"\n\n"
        f"Check:\n"
        f"1. Persona match — does it sound like VirtuAI Mentor? (direct, high-energy, no fluff)\n"
        f"2. Hook quality — would you stop scrolling for this first line?\n"
        f"3. CTA present and appropriate for {platform}?\n"
        f"4. Formatting correct for {platform}?\n"
        f"5. Any banned phrases used? (game-changer, revolutionary, in today's world, etc.)\n"
        f"6. Emoji compliance (max 2, none in LinkedIn/Medium)?\n\n"
        f"Return JSON with: verdict (PASS/REVISE), score (0-10), "
        f"issues (array), hook_score (0-10), cta_present (bool).\n"
        f"ONLY return valid JSON."
    )

    try:
        output = client.generate(prompt=prompt, max_tokens=300, temperature=0.1)
        # Parse JSON from output
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            # Try to extract from partial output
            data = {
                "verdict": "REVISE",
                "score": 5,
                "issues": ["Review parsing failed — manual check recommended"],
                "hook_score": 5,
                "cta_present": False,
            }

        return json.dumps(data)
    except Exception as e:
        logger.error(f"Quality review failed: {e}")
        return json.dumps({
            "verdict": "REVISE",
            "score": 0,
            "issues": [f"Review error: {str(e)}"],
        })


# ══════════════════════════════════════════════════════════════════════════════
# Safety & Guardian Tools (Guardian Agent)
# ══════════════════════════════════════════════════════════════════════════════

@tool("content_safety_check_local")
def content_safety_check_local(content: str, platform: str = "") -> str:
    """
    Run a comprehensive safety and ethics check on content using the local VLM.

    Checks for: forbidden topics (hate speech, violence, illegal activities,
    discrimination, pornography, terrorism, self-harm, doxxing), restricted
    topics needing disclaimers (financial/medical advice), false claims,
    and manipulation tactics.

    Args:
        content: The content to check.
        platform: Optional platform context for policy-specific checks.

    Returns:
        JSON with decision (APPROVE/REVISE/BLOCK), safety_score, issues, reasoning.
    """
    client = get_client()

    try:
        result = client.safety_check(content, platform=platform or None)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Safety check failed: {e}")
        # Default to REVISE on error (conservative)
        return json.dumps({
            "decision": "REVISE",
            "safety_score": 0.5,
            "issues": [f"Safety check error: {str(e)}"],
            "reasoning": "Error during safety check — manual review required.",
        })


@tool("check_persona_compliance_local")
def check_persona_compliance_local(content: str) -> str:
    """
    Check content for VirtuAI Mentor persona compliance.
    Flags banned phrases, weak language, and off-brand content.

    Args:
        content: The content to check for persona compliance.

    Returns:
        JSON with compliance verdict, violations found, and suggested fixes.
    """
    client = get_client()

    # Rule-based banned phrase check (fast, no LLM needed)
    banned_phrases = [
        "in today's fast-paced world",
        "it's important to note",
        "at the end of the day",
        "game-changer",
        "revolutionary",
        "unleash the power",
        "dive deep",
        "let's unpack",
        "i'm excited to share",
        "without further ado",
        "in this article we will",
        "as an ai language model",
    ]

    content_lower = content.lower()
    found_banned = [p for p in banned_phrases if p in content_lower]

    # LLM check for deeper compliance
    prompt = (
        f"Check this content for VirtuAI Mentor persona compliance:\n\n\"{content}\"\n\n"
        f"The persona is: direct, motivational, high-energy, no fluff, authority-driven.\n"
        f"Issues to flag:\n"
        f"- Weak/soft language (maybe, perhaps, might, could)\n"
        f"- Uncertain tone (I think, I believe, it seems)\n"
        f"- Missing hook (no scroll-stopping first line)\n"
        f"- Missing CTA (no action directive at end)\n"
        f"- Passive voice overuse\n\n"
        f"Return JSON: compliant (bool), violations (array of strings), "
        f"severity (low/medium/high), suggestions (array of fixes).\n"
        f"ONLY return valid JSON."
    )

    try:
        output = client.generate(prompt=prompt, max_tokens=250, temperature=0.1)
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            data = {"compliant": len(found_banned) == 0, "violations": found_banned, "severity": "medium", "suggestions": []}

        # Merge banned phrase findings
        if found_banned:
            data["violations"] = data.get("violations", []) + [f"Banned phrase: '{p}'" for p in found_banned]
            data["compliant"] = False

        return json.dumps(data)
    except Exception as e:
        return json.dumps({
            "compliant": len(found_banned) == 0,
            "violations": [f"Banned phrase: '{p}'" for p in found_banned],
            "severity": "medium" if found_banned else "low",
            "suggestions": [],
        })


# ══════════════════════════════════════════════════════════════════════════════
# Research Tools (Research Agent)
# ══════════════════════════════════════════════════════════════════════════════

@tool("analyze_platform_signals_local")
def analyze_platform_signals_local(platform: str, niche: str) -> str:
    """
    Use the VLM to generate platform signal analysis for content strategy.
    Analyzes what content formats and topics perform best on the given platform
    within the specified niche.

    Args:
        platform: The social platform to analyze (linkedin, x, instagram, etc.)
        niche: Content niche (e.g., 'AI in business', 'entrepreneurship')

    Returns:
        JSON with platform signals, top performing formats, and content recommendations.
    """
    client = get_client()

    prompt = (
        f"Analyze content performance signals for {platform.replace('_', ' ').upper()} "
        f"in the '{niche}' niche.\n\n"
        f"Based on typical platform behavior, provide:\n"
        f"1. Top 3 content formats performing well right now\n"
        f"2. Optimal post length/duration\n"
        f"3. Best posting topics in this niche\n"
        f"4. Engagement tactics that work on this platform\n"
        f"5. One contrarian angle that stands out\n\n"
        f"Return structured analysis as JSON with keys: "
        f"top_formats, optimal_length, trending_topics, engagement_tactics, contrarian_angle."
    )

    try:
        output = client.generate(prompt=prompt, platform=platform, max_tokens=400, temperature=0.5)
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        try:
            data = json.loads(output)
            return json.dumps({"platform": platform, "niche": niche, "signals": data})
        except json.JSONDecodeError:
            return json.dumps({"platform": platform, "niche": niche, "raw_analysis": output})
    except Exception as e:
        return json.dumps({"platform": platform, "error": str(e)})


@tool("search_trending_topics_local")
def search_trending_topics_local(niches: str) -> str:
    """
    Use the VLM to identify trending topic angles within specified content niches.
    Uses the model's training knowledge to surface relevant, timely angles.

    Args:
        niches: Comma-separated content niches (e.g., 'AI in business, entrepreneurship')

    Returns:
        JSON research brief with trending topics, relevance scores, and platform-specific angles.
    """
    client = get_client()

    prompt = (
        f"Generate a content research brief for these niches: {niches}\n\n"
        f"Identify 5 high-potential content topics with:\n"
        f"- Topic title and angle\n"
        f"- Relevance score (1-10)\n"
        f"- Why it's trending or relevant now\n"
        f"- Best platform for this topic\n"
        f"- Suggested hook line\n\n"
        f"Focus on: AI in business, automation, entrepreneurship, founder mindset, scaling.\n"
        f"Avoid: generic advice, outdated references, weak angles.\n\n"
        f"Return as JSON array of topic objects."
    )

    try:
        output = client.generate(prompt=prompt, max_tokens=500, temperature=0.6)
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        try:
            topics = json.loads(output)
            return json.dumps({"niches": niches, "trending_topics": topics, "source": "local-vlm"})
        except json.JSONDecodeError:
            return json.dumps({"niches": niches, "raw_research": output, "source": "local-vlm"})
    except Exception as e:
        return json.dumps({"niches": niches, "error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# Identity Verification Tools (Reviewer Agent — ArcFace gate)
# ══════════════════════════════════════════════════════════════════════════════

@tool("verify_face_identity")
def verify_face_identity(image_path: str) -> str:
    """
    Verify that a generated image matches Daniel Calder's face using ArcFace.
    Returns PASS (≥0.70), ACCEPTABLE (≥0.45), or FAIL (<0.45) with the
    cosine similarity score.

    Args:
        image_path: Path to the generated face image (PNG/JPEG).

    Returns:
        JSON with similarity score, verdict, and details.
    """
    from virtuai.persona.eval.face_similarity import FaceSimilarity

    try:
        fs = FaceSimilarity(lazy=True)
        result = fs.score(image_path)
        score = result.similarity

        if score >= 0.70:
            verdict = "PASS"
        elif score >= 0.45:
            verdict = "ACCEPTABLE"
        else:
            verdict = "FAIL"

        return json.dumps({
            "image_path": image_path,
            "similarity": round(score, 4),
            "nearest_ref_sim": round(result.nearest_reference_sim, 4),
            "faces_detected": result.n_faces_detected,
            "verdict": verdict,
            "threshold": 0.70,
        })
    except Exception as e:
        logger.error(f"ArcFace identity check failed: {e}")
        return json.dumps({
            "image_path": image_path,
            "verdict": "ERROR",
            "error": str(e),
        })


@tool("review_video_quality")
def review_video_quality(video_path: str) -> str:
    """
    Run programmatic quality checks on a generated reel video. Verifies
    audio continuity (no silence gaps > 1.8s indicating bad cuts),
    aspect ratio (9:16 for short-form), duration (8-60s window), and
    face consistency across frames (ArcFace mean pairwise similarity).

    Returns PASS if all checks pass, REVISE with specific issues if any fail.
    The reel must NOT be published if verdict is REVISE.

    Args:
        video_path: Absolute path to the generated reel video (MP4).

    Returns:
        JSON with verdict, score (0-1), list of issues, and stats.
    """
    from pathlib import Path
    from virtuai.tools.video_reviewer import review_video

    try:
        result = review_video(Path(video_path))
        return json.dumps({
            "video_path": video_path,
            "verdict": result["verdict"],
            "score": result["score"],
            "issues": result["issues"],
            "stats_summary": {
                "duration_sec": result["stats"]["duration"]["duration_sec"],
                "aspect": result["stats"]["aspect"]["aspect"],
                "longest_silence_sec": result["stats"]["audio"]["longest_gap_sec"],
                "face_mean_sim": result["stats"]["face"].get("mean_pairwise_sim"),
            },
        })
    except Exception as e:
        logger.error(f"Video review failed: {e}")
        return json.dumps({
            "video_path": video_path,
            "verdict": "ERROR",
            "error": str(e),
        })


# ══════════════════════════════════════════════════════════════════════════════
# Reel Production Tools (Visual Agent — Phase 2 pipeline)
# ══════════════════════════════════════════════════════════════════════════════

@tool("generate_captions")
def generate_captions(audio_path: str) -> str:
    """
    Generate CapCut-style word-by-word ASS captions from an audio file.
    Uses Whisper for word-level timestamps. Output is an .ass subtitle file
    ready to be burned into video by build_reel.

    Args:
        audio_path: Path to WAV or MP4 file containing speech.

    Returns:
        JSON with the path to the generated .ass file and word count.
    """
    from virtuai.tools.caption_generator import create_captions

    try:
        ass_path = create_captions(
            audio_path=audio_path,
            whisper_model="base",
            words_per_group=2,
        )
        return json.dumps({
            "status": "generated",
            "captions_path": str(ass_path),
        })
    except Exception as e:
        logger.error(f"Caption generation failed: {e}")
        return json.dumps({"status": "error", "error": str(e)})


@tool("build_reel")
def build_reel_tool(
    clips: str,
    captions_ass: str,
    output_path: str,
    hook_text: str = "",
) -> str:
    """
    Build a complete reel: stitch video clips, burn in captions, add hook overlay.
    Takes lip-synced clips (already contain audio) and produces a final reel.

    Args:
        clips: Comma-separated paths to video clips (in order).
        captions_ass: Path to ASS caption file from generate_captions.
        output_path: Where to write the final reel MP4.
        hook_text: Text overlay for first 3 seconds (the scroll-stopping hook).

    Returns:
        JSON with output path, duration, and file size.
    """
    from virtuai.tools.reel_builder import build_reel

    clip_list = [c.strip() for c in clips.split(",") if c.strip()]

    try:
        result_path = build_reel(
            clips=clip_list,
            captions_ass=captions_ass,
            output_path=output_path,
            hook_text=hook_text or None,
        )
        size_mb = result_path.stat().st_size / (1024 * 1024)
        return json.dumps({
            "status": "built",
            "reel_path": str(result_path),
            "size_mb": round(size_mb, 1),
        })
    except Exception as e:
        logger.error(f"Reel build failed: {e}")
        return json.dumps({"status": "error", "error": str(e)})
