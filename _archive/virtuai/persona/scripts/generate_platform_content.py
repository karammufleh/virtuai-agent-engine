"""
generate_platform_content.py — One-shot generator for all 6 platforms.

Pipeline (per platform):
  1. Pick a topic (rotated for diversity)
  2. Generate platform-formatted text via the backend's /generate (Phi-3.5 LoRA)
  3. For video platforms (tiktok, instagram_reels, youtube_shorts):
     also call /generate-voice to render an F5-TTS audio clip from the spoken script
  4. Write to virtuai/persona/demo/<platform>/ as JSON + audio.wav
  5. Bypass topic-memory dedup since this is a one-shot capstone batch

This is the script-generation step. The video-rendering step (Wav2Lip /
SadTalker / VideoReTalking / etc.) consumes the audio + face image afterwards.

Usage:
    python virtuai/persona/scripts/generate_platform_content.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import urllib.request

ROOT = Path(__file__).resolve().parents[3]
PERSONA = ROOT / "virtuai" / "persona"
DEMO_DIR = PERSONA / "demo"
BACKEND = "http://localhost:8765"


# Diverse topics that fit Daniel's persona (AI-native entrepreneur, leverage,
# self-improvement). Each platform gets one specifically suited to it.
PLATFORM_PLAN: dict[str, dict] = {
    "linkedin": {
        "format": "long-form professional post (no spoken script)",
        "topic": "the unfair advantage AI gives to operators who actually deploy it vs ones who only talk about it",
        "is_video": False,
        "max_tokens": 500,
    },
    "x": {
        "format": "punchy 280-char tweet with one strong line + one CTA",
        "topic": "why 'I'll start when I'm ready' is the most expensive lie in entrepreneurship",
        "is_video": False,
        "max_tokens": 200,
    },
    "instagram": {
        "format": "Instagram caption with hook + body + CTA + 5 hashtags",
        "topic": "three systems that compounded my output 10x without working more hours",
        "is_video": False,
        "max_tokens": 300,
    },
    "medium": {
        "format": "Medium article opening section: hook, thesis, first 3 paragraphs",
        "topic": "the difference between leverage and busywork, and the 90-second test that exposes which one you're doing",
        "is_video": False,
        "max_tokens": 600,
    },
    "tiktok": {
        "format": "15-second spoken script for TikTok talking-head video, ~38 words, hook → body → CTA, NO hashtags or markdown — pure spoken text",
        "topic": "stop chasing motivation. Build systems that work even when you don't feel like it",
        "is_video": True,
        "spoken_word_target": 38,
        "max_tokens": 180,
    },
    "instagram_reels": {
        "format": "30-second spoken script for Instagram Reels, ~75 words, hook → body → CTA, NO hashtags or markdown — pure spoken text",
        "topic": "the three questions I ask myself before saying yes to any new commitment",
        "is_video": True,
        "spoken_word_target": 75,
        "max_tokens": 220,
    },
    "youtube_shorts": {
        "format": "45-second spoken script for YouTube Shorts, ~110 words, hook → body → CTA, NO hashtags or markdown — pure spoken text",
        "topic": "what I would tell my 22-year-old self about leverage, time, and AI",
        "is_video": True,
        "spoken_word_target": 110,
        "max_tokens": 320,
    },
}


def _post(endpoint: str, payload: dict, timeout: float = 600.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BACKEND}{endpoint}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _check_backend() -> None:
    try:
        with urllib.request.urlopen(f"{BACKEND}/health", timeout=3) as r:
            health = json.load(r)
        if health.get("status") != "ok":
            sys.exit(f"Backend not healthy: {health}")
        print(f"[backend] OK — text:{health.get('text_model')} voice_loaded:{health.get('voice_loaded')}")
    except Exception as e:
        sys.exit(f"Backend unreachable at {BACKEND}: {e}")


def generate_text(platform: str, plan: dict) -> str:
    prompt = (
        f"Write {plan['format']} about: {plan['topic']}.\n\n"
        "Voice: direct, motivational, no fluff, exactly one CTA.\n"
        "Banned phrases: 'in today's fast-paced world', 'game-changer', 'revolutionary', "
        "'at the end of the day', 'dive deep', 'let's unpack'.\n"
        "Strong scroll-stopping hook as the first line."
    )
    if plan.get("spoken_word_target"):
        prompt += (
            f"\n\nThis will be SPOKEN ALOUD by an AI avatar — write conversational "
            f"sentences, no bullet points, no parenthetical asides, no hashtags. "
            f"Target ~{plan['spoken_word_target']} words."
        )
    print(f"[gen text] {platform}: dispatching...")
    t0 = time.time()
    res = _post(
        "/generate",
        {
            "prompt": prompt,
            "platform": platform,
            "max_tokens": plan["max_tokens"],
            "temperature": 0.7,  # was 0.8 — tightened to reduce persona drift
        },
        timeout=300,
    )
    elapsed = time.time() - t0
    content = res["content"].strip()
    print(f"  ✓ {len(content.split())} words in {elapsed:.1f}s")
    return content


def generate_voice(text: str, platform: str) -> dict:
    """Render the spoken script via F5-TTS clone."""
    print(f"[gen voice] {platform}: dispatching ({len(text.split())} words)...")
    t0 = time.time()
    res = _post(
        "/generate-voice",
        {"text": text, "speed": 1.0, "seed": 42, "nfe_step": 32},
        timeout=900,  # F5-TTS can be slow on long clips
    )
    elapsed = time.time() - t0
    print(f"  ✓ {res['duration_s']:.2f}s of audio in {elapsed:.1f}s")
    return res


def main() -> None:
    _check_backend()
    DEMO_DIR.mkdir(parents=True, exist_ok=True)

    summary: dict[str, dict] = {}
    for platform, plan in PLATFORM_PLAN.items():
        out_dir = DEMO_DIR / platform
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n────── {platform.upper()} ──────")
        text = generate_text(platform, plan)
        (out_dir / "text.md").write_text(text + "\n", encoding="utf-8")

        record = {
            "platform": platform,
            "topic": plan["topic"],
            "format": plan["format"],
            "is_video": plan["is_video"],
            "text": text,
            "text_path": str((out_dir / "text.md").relative_to(ROOT)),
        }

        if plan["is_video"]:
            voice_res = generate_voice(text, platform)
            audio_src = Path(voice_res["audio_path"])
            audio_dst = out_dir / "audio.wav"
            audio_dst.write_bytes(audio_src.read_bytes())
            record["audio_path"] = str(audio_dst.relative_to(ROOT))
            record["audio_duration_s"] = voice_res["duration_s"]
            record["audio_sample_rate"] = voice_res["sample_rate"]

        (out_dir / "manifest.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        summary[platform] = record

    # Top-level summary
    summary_path = DEMO_DIR / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "platforms": summary,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n✓ Summary → {summary_path}")
    print(f"  text-only platforms: {[k for k, v in summary.items() if not v['is_video']]}")
    print(f"  video platforms:     {[k for k, v in summary.items() if v['is_video']]}")


if __name__ == "__main__":
    main()
