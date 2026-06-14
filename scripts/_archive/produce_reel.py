#!/usr/bin/env python3
"""
produce_reel.py — Full end-to-end reel production chain.

Chain: Script → F5-TTS voice → Gemini Kling prompt → Kling multi-image2video
       → Kling lip-sync → Whisper captions → reel builder → final MP4.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("produce_reel")

FACE_DIR = ROOT / "virtuai" / "persona" / "face_dataset"
VOICE_REF = ROOT / "virtuai" / "persona" / "voice_sample" / "daniel_voice_ref.wav"
OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BACKEND_URL = "http://localhost:8765"

# ── 1. Script ─────────────────────────────────────────────────────────────────

SCRIPT_TEXT = (
    "Stop trying to scale. Start systemizing. "
    "I replaced three roles with AI agents "
    "for forty dollars a month. Not chatbots. "
    "Real autonomous systems. "
    "Save this before it gets buried."
)

HOOK_TEXT = "Stop trying to scale."

SCENE = "modern minimalist home office with large window"
ACTION = "seated at a clean desk, speaking directly to camera, gesturing with one hand"
MOOD = "confident, direct, slightly provocative"

# ── helpers ───────────────────────────────────────────────────────────────────

def pick_reference_images(n: int = 4) -> list[Path]:
    pngs = sorted(FACE_DIR.glob("*.png"))
    picks = [pngs[0], pngs[5], pngs[10], pngs[15]] if len(pngs) > 15 else pngs[:n]
    log.info(f"Selected {len(picks)} reference images: {[p.name for p in picks]}")
    return picks


def upload_to_catbox(filepath: Path) -> str:
    log.info(f"Uploading {filepath.name} ({filepath.stat().st_size} bytes) to catbox.moe...")
    with open(filepath, "rb") as f:
        resp = httpx.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (filepath.name, f)},
            timeout=120,
        )
    resp.raise_for_status()
    url = resp.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"catbox upload failed: {url}")
    log.info(f"catbox URL: {url}")
    return url


# ── 2. F5-TTS voice generation ───────────────────────────────────────────────

def generate_voice(text: str) -> Path:
    log.info("Generating Daniel's voice via F5-TTS backend...")
    resp = httpx.post(
        f"{BACKEND_URL}/generate-voice",
        json={"text": text, "speed": 1.0, "seed": -1, "nfe_step": 48},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    wav_path = Path(data["audio_path"])
    duration = data.get("duration_seconds", "?")
    log.info(f"Voice generated: {wav_path.name} ({duration}s)")
    return wav_path


# ── 3. Gemini Kling prompt ────────────────────────────────────────────────────

def generate_kling_prompt() -> str:
    from virtuai.tools.prompt_writer import write_kling_prompt
    prompt = write_kling_prompt(
        scene=SCENE,
        action=ACTION,
        mood=MOOD,
        duration_seconds=10,
    )
    log.info(f"Kling prompt ({len(prompt.split())} words): {prompt[:120]}...")
    return prompt


# ── 4-5. Kling video + lip-sync ──────────────────────────────────────────────

def generate_video(ref_images: list[Path], prompt: str) -> dict:
    from virtuai.tools.kling_omni import multi_image_to_video
    ts = int(time.time())
    result = multi_image_to_video(
        image_paths=ref_images,
        prompt=prompt,
        duration=10,
        model_name="kling-v1-6",
        aspect_ratio="9:16",
        mode="std",
        output_filename=f"reel_base_{ts}.mp4",
    )
    log.info(f"Base video: {result['local_path']}")
    return result


def run_lip_sync(video_url: str, audio_url: str) -> dict:
    from virtuai.tools.kling_omni import lip_sync
    ts = int(time.time())
    result = lip_sync(
        video_url=video_url,
        audio_url=audio_url,
        output_filename=f"reel_lipsync_{ts}.mp4",
    )
    log.info(f"Lip-synced video: {result['local_path']}")
    return result


# ── 6. Captions ───────────────────────────────────────────────────────────────

def generate_captions(audio_path: Path) -> Path:
    from virtuai.tools.caption_generator import create_captions
    ass_path = create_captions(
        audio_path=str(audio_path),
        whisper_model="base",
        words_per_group=2,
    )
    log.info(f"Captions: {ass_path}")
    return ass_path


# ── 7. Reel builder ──────────────────────────────────────────────────────────

def build_final_reel(clip_path: Path, captions_ass: Path) -> Path:
    from virtuai.tools.reel_builder import build_reel
    ts = int(time.time())
    out = OUTPUT_DIR / f"daniel_reel_final_{ts}.mp4"
    result = build_reel(
        clips=[str(clip_path)],
        captions_ass=str(captions_ass),
        output_path=str(out),
        hook_text=HOOK_TEXT,
    )
    log.info(f"Final reel: {result} ({result.stat().st_size / 1024:.0f} KB)")
    return result


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — Full Reel Production Chain")
    log.info("=" * 60)

    # Step 1: Generate voice
    wav_path = generate_voice(SCRIPT_TEXT)

    # Step 2: Upload audio to catbox for Kling
    audio_url = upload_to_catbox(wav_path)

    # Step 3: Generate Kling prompt via Gemini
    kling_prompt = generate_kling_prompt()

    # Step 4: Pick reference images and generate base video
    ref_images = pick_reference_images(4)
    video_result = generate_video(ref_images, kling_prompt)

    # Step 5: Lip-sync with Daniel's voice
    lipsync_result = run_lip_sync(
        video_url=video_result["kling_video_url"],
        audio_url=audio_url,
    )

    # Step 6: Generate captions from the voice audio
    captions_path = generate_captions(wav_path)

    # Step 7: Build final reel
    lipsync_path = Path(lipsync_result["local_path"])
    final_path = build_final_reel(lipsync_path, captions_path)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s")
    log.info(f"Final reel: {final_path}")
    log.info(f"Size: {final_path.stat().st_size / (1024*1024):.1f} MB")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
