#!/usr/bin/env python3
"""
produce_reel_kie.py — Production reel via Kling 3.0 (KIE.ai).

Chain: Script → F5-TTS voice → upload face refs → Kling 3.0 video
       → FFmpeg audio swap → Whisper captions → reel builder → final MP4.
"""
from __future__ import annotations

import logging
import os
import subprocess
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
log = logging.getLogger("produce_reel_kie")

FACE_DIR = ROOT / "virtuai" / "persona" / "face_dataset"
OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BACKEND_URL = "http://localhost:8765"
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"

# ── Script & scene ───────────────────────────────────────────────────────────

SCRIPT_TEXT = (
    "Everyone's building AI apps. Nobody's building AI systems. "
    "One app breaks, you're done. One system fails, it reroutes. "
    "That's the difference between a product and a business. "
    "Build systems."
)

HOOK_TEXT = "Nobody's building AI systems."

KLING_PROMPT = (
    "A confident young male entrepreneur, early 30s with short dark hair "
    "and light stubble, seated at a table in a sunlit modern café. "
    "Medium shot, eye level, shallow depth of field. He speaks directly "
    "to camera with natural hand gestures, warm golden hour light from "
    "a large window behind him. Blurred café patrons and plants in "
    "background. Cinematic color grading, 9:16 vertical framing. "
    "Subtle camera push-in over duration. Natural blinking and "
    "micro-expressions. No phone visible."
)

# ── helpers ──────────────────────────────────────────────────────────────────

def upload_file(filepath: Path) -> str:
    log.info(f"Uploading {filepath.name} to tmpfiles.org...")
    with open(filepath, "rb") as f:
        resp = httpx.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (filepath.name, f)},
            timeout=120,
        )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"tmpfiles upload failed: {data}")
    page_url = data["data"]["url"]
    direct_url = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)
    log.info(f"  → {direct_url}")
    return direct_url


def pick_face_refs(n: int = 4) -> list[Path]:
    pngs = sorted(FACE_DIR.glob("*.png"))
    if len(pngs) >= 16:
        picks = [pngs[0], pngs[5], pngs[10], pngs[15]]
    else:
        picks = pngs[:n]
    log.info(f"Face refs: {[p.name for p in picks]}")
    return picks


# ── Step 1: F5-TTS voice ────────────────────────────────────────────────────

def generate_voice(text: str) -> Path:
    log.info("Step 1/6: Generating Daniel's voice via F5-TTS...")
    resp = httpx.post(
        f"{BACKEND_URL}/generate-voice",
        json={"text": text, "speed": 1.0, "seed": -1, "nfe_step": 48},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    wav = Path(data["audio_path"])
    log.info(f"  Voice: {wav.name} ({data.get('duration_seconds', '?')}s)")
    return wav


# ── Step 2: Kling 3.0 video via KIE ─────────────────────────────────────────

def generate_kling3_video(face_urls: list[str]) -> dict:
    log.info("Step 2/6: Generating Kling 3.0 video via KIE.ai...")
    from virtuai.tools.kie_kling import generate_video

    ts = int(time.time())
    result = generate_video(
        prompt=KLING_PROMPT,
        image_urls=face_urls[:2],
        elements=[{
            "name": "daniel",
            "description": "young male entrepreneur, early 30s, short dark hair, light stubble",
            "element_input_urls": face_urls,
        }],
        duration=10,
        aspect_ratio="9:16",
        mode="std",
        sound=True,
        output_filename=f"kie_reel_base_{ts}.mp4",
    )
    log.info(f"  Video: {result['local_path']}")
    return result


# ── Step 3a: Crop to vertical ────────────────────────────────────────────────

def crop_to_vertical(video_path: Path) -> Path:
    probe = subprocess.run(
        [FFMPEG, "-i", str(video_path)],
        capture_output=True, text=True,
    )
    import re
    m = re.search(r"(\d{3,4})x(\d{3,4})", probe.stderr)
    if not m:
        return video_path
    w, h = int(m.group(1)), int(m.group(2))
    if w * 16 <= h * 9 + 10:
        log.info(f"  Already vertical ({w}x{h}), skipping crop")
        return video_path
    log.info(f"Step 3a: Cropping {w}x{h} to 9:16...")
    target_w = int(h * 9 / 16)
    out = video_path.with_name(video_path.stem + "_vert.mp4")
    subprocess.run([
        FFMPEG, "-y", "-i", str(video_path),
        "-vf", f"crop={target_w}:{h}:({w}-{target_w})/2:0,scale=720:1280",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-an",
        str(out),
    ], check=True, capture_output=True)
    log.info(f"  Cropped: {out.name}")
    return out


# ── Step 3b: FFmpeg audio swap ───────────────────────────────────────────────

def swap_audio(video_path: Path, audio_path: Path) -> Path:
    log.info("Step 3b/6: Swapping audio with Daniel's voice...")
    out = video_path.with_name(video_path.stem + "_voiced.mp4")
    cmd = [
        FFMPEG, "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    log.info(f"  Voiced video: {out.name} ({out.stat().st_size / 1024:.0f} KB)")
    return out


# ── Step 4: Whisper captions ─────────────────────────────────────────────────

def generate_captions(audio_path: Path) -> Path:
    log.info("Step 4/6: Generating Whisper captions...")
    from virtuai.tools.caption_generator import create_captions
    ass_path = create_captions(
        audio_path=str(audio_path),
        whisper_model="base",
        words_per_group=2,
    )
    log.info(f"  Captions: {ass_path}")
    return ass_path


# ── Step 5: Reel builder ────────────────────────────────────────────────────

def build_final_reel(clip_path: Path, captions_ass: Path) -> Path:
    log.info("Step 5/6: Building final reel with captions + hook...")
    from virtuai.tools.reel_builder import build_reel
    ts = int(time.time())
    out = OUTPUT_DIR / f"daniel_reel_kie_{ts}.mp4"
    result = build_reel(
        clips=[str(clip_path)],
        captions_ass=str(captions_ass),
        output_path=str(out),
        hook_text=HOOK_TEXT,
    )
    log.info(f"  Final reel: {result}")
    return result


# ── Step 6: ArcFace verification ────────────────────────────────────────────

def verify_identity(video_path: Path) -> dict:
    log.info("Step 6/6: ArcFace identity verification...")
    resp = httpx.post(
        f"{BACKEND_URL}/verify-face-identity",
        json={"image_path": str(video_path)},
        timeout=60,
    )
    if resp.status_code == 200:
        data = resp.json()
        score = data.get("similarity", 0)
        verdict = "PASS" if score >= 0.70 else "ACCEPTABLE" if score >= 0.45 else "FAIL"
        log.info(f"  ArcFace: {score:.3f} — {verdict}")
        return data
    log.warning(f"  ArcFace check skipped (backend returned {resp.status_code})")
    return {}


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — Kling 3.0 Production Reel (KIE.ai)")
    log.info("=" * 60)

    # Step 1: Voice
    wav_path = generate_voice(SCRIPT_TEXT)

    # Upload face references to catbox
    log.info("Uploading face references to catbox...")
    face_paths = pick_face_refs(4)
    face_urls = [upload_file(p) for p in face_paths]

    # Step 2: Kling 3.0 video
    video_result = generate_kling3_video(face_urls)
    video_path = Path(video_result["local_path"])

    # Step 3a: Crop to 9:16 if needed (Kling sometimes returns 1:1)
    video_path = crop_to_vertical(video_path)

    # Step 3b: Swap audio with Daniel's voice
    voiced_path = swap_audio(video_path, wav_path)

    # Step 4: Captions
    captions_path = generate_captions(wav_path)

    # Step 5: Build reel
    final_path = build_final_reel(voiced_path, Path(captions_path))

    # Step 6: Verify identity
    verify_identity(final_path)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s")
    log.info(f"Final reel: {final_path}")
    log.info(f"Size: {final_path.stat().st_size / (1024*1024):.1f} MB")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
