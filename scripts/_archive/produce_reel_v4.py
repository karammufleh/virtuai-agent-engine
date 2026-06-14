#!/usr/bin/env python3
"""
produce_reel_v4.py — Best quality pipeline.

  1. DeepSeek LLM writes the script
  2. ElevenLabs TTS generates premium voice audio (via KIE)
  3. Kling AI Avatar Pro lip-syncs Daniel's face to the audio (via KIE)
  4. Whisper generates word-level captions
  5. FFmpeg burns captions + hook overlay
"""
from __future__ import annotations

import json
import logging
import os
import re
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
log = logging.getLogger("produce_reel_v4")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FACE_IMAGE = ROOT / "virtuai" / "persona" / "canonical_daniel.png"

KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_LLM_BASE = "https://kieai.erweima.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"

ELEVENLABS_VOICE = "TX3LPaxmHKxFdv7VOQHJ"  # Liam — Energetic, Social Media Creator

POLL_INTERVAL = 12
POLL_TIMEOUT = 600

# ── Content ──────────────────────────────────────────────────────────────────

TOPIC = "AI systems vs AI apps"
HOOK = "Nobody's building AI systems."

PERSONA_BRIEF = (
    "Daniel Calder — a 28-year-old AI entrepreneur and systems thinker. "
    "Direct, confident, slightly provocative. Speaks like a founder "
    "dropping hard truths on a podcast clip."
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _headers() -> dict:
    if not KIE_API_KEY:
        raise RuntimeError("KIE_API_KEY not set in .env")
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def upload_file(filepath: Path) -> str:
    log.info(f"  Uploading {filepath.name}...")
    with open(filepath, "rb") as f:
        resp = httpx.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (filepath.name, f)},
            timeout=120,
        )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Upload failed: {data}")
    url = data["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)
    log.info(f"    → {url}")
    return url


def poll_task(task_id: str, label: str = "") -> dict:
    url = f"{KIE_API_BASE}/jobs/recordInfo"
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        r = httpx.get(url, params={"taskId": task_id}, headers=_headers(), timeout=30)
        r.raise_for_status()
        data = r.json().get("data", {})
        state = data.get("state", "")
        if state in ("success", "completed", "succeed"):
            return data
        if state in ("failed", "error"):
            raise RuntimeError(f"{label} task {task_id} failed: {data.get('failMsg', data)}")
        log.info(f"  {label} polling: {state}...")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"{label} task {task_id} timed out")


def download_result(data: dict, out_path: Path) -> Path:
    result_str = data.get("resultJson", "")
    if not result_str:
        raise RuntimeError(f"No resultJson: {data}")
    rj = json.loads(result_str)
    urls = rj.get("resultUrls", [])
    if not urls:
        raise RuntimeError(f"No resultUrls: {rj}")
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(urls[0])
        dl.raise_for_status()
        out_path.write_bytes(dl.content)
    log.info(f"  Downloaded: {out_path.name} ({out_path.stat().st_size/1024/1024:.1f} MB)")
    return out_path


# ── Step 1: DeepSeek writes the script ──────────────────────────────────────

def write_script() -> str:
    log.info("Step 1/5: Writing script via DeepSeek...")

    resp = httpx.post(
        f"{KIE_LLM_BASE}/chat/completions",
        headers=_headers(),
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": (
                    "You write short viral reel scripts for a tech entrepreneur persona. "
                    "Rules:\n"
                    "- Exactly 25-35 words (10 seconds of speech)\n"
                    "- First sentence is a provocative hook\n"
                    "- End with a punchy 2-3 word CTA\n"
                    "- No hashtags, no emojis\n"
                    "- Speak like a founder, not a teacher\n"
                    "- Output ONLY the script text, nothing else"
                )},
                {"role": "user", "content": (
                    f"Topic: {TOPIC}\n"
                    f"Hook energy: \"{HOOK}\"\n"
                    f"Persona: {PERSONA_BRIEF}\n\n"
                    "Write the script."
                )},
            ],
            "max_tokens": 100,
            "temperature": 0.8,
        },
        timeout=30,
    )
    resp.raise_for_status()
    script = resp.json()["choices"][0]["message"]["content"].strip().strip('"')
    log.info(f"  Script ({len(script.split())} words): {script}")
    return script


# ── Step 2: ElevenLabs TTS ──────────────────────────────────────────────────

def generate_voice(script: str) -> Path:
    log.info("Step 2/5: Generating voice via ElevenLabs TTS...")

    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask",
        headers=_headers(),
        json={
            "model": "elevenlabs/text-to-speech-turbo-2-5",
            "input": {
                "text": script,
                "voice": ELEVENLABS_VOICE,
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.4,
                "speed": 1.0,
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    resp = r.json()
    task_id = (resp.get("data") or {}).get("taskId") or (resp.get("data") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"ElevenLabs submit failed: {resp}")
    log.info(f"  Task: {task_id}")

    data = poll_task(task_id, "ElevenLabs")

    ts = int(time.time())
    audio_path = OUTPUT_DIR / f"eleven_voice_{ts}.mp3"
    download_result(data, audio_path)
    return audio_path


# ── Step 3: Kling AI Avatar Pro (lip sync) ──────────────────────────────────

def generate_avatar_video(face_url: str, audio_url: str) -> Path:
    log.info("Step 3/5: Generating lip-synced video via Kling AI Avatar Pro...")

    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask",
        headers=_headers(),
        json={
            "model": "kling/ai-avatar-pro",
            "input": {
                "image_url": face_url,
                "audio_url": audio_url,
                "prompt": (
                    "A confident young entrepreneur speaking directly to camera "
                    "in a modern office. Natural head movements, expressive hand "
                    "gestures, subtle micro-expressions. Eye contact with camera."
                ),
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    resp = r.json()
    task_id = (resp.get("data") or {}).get("taskId") or (resp.get("data") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"Avatar submit failed: {resp}")
    log.info(f"  Task: {task_id}")

    data = poll_task(task_id, "Avatar")

    ts = int(time.time())
    video_path = OUTPUT_DIR / f"avatar_lipsync_{ts}.mp4"
    download_result(data, video_path)
    return video_path


# ── Step 4: Whisper captions ────────────────────────────────────────────────

def generate_captions(audio_path: Path) -> Path:
    log.info("Step 4/5: Generating Whisper captions...")

    wav_tmp = audio_path.with_suffix(".wav")
    subprocess.run([
        FFMPEG, "-y", "-i", str(audio_path),
        "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(wav_tmp),
    ], check=True, capture_output=True)

    from virtuai.tools.caption_generator import create_captions
    ass_path = create_captions(
        audio_path=str(wav_tmp),
        whisper_model="base",
        words_per_group=2,
    )
    log.info(f"  Captions: {ass_path}")
    return Path(ass_path)


# ── Step 5: Final reel ──────────────────────────────────────────────────────

def build_reel(video_path: Path, captions_ass: Path) -> Path:
    log.info("Step 5/5: Building final reel...")
    from virtuai.tools.reel_builder import build_reel as _build
    ts = int(time.time())
    out = OUTPUT_DIR / f"daniel_reel_v4_{ts}.mp4"
    result = _build(
        clips=[str(video_path)],
        captions_ass=str(captions_ass),
        output_path=str(out),
        hook_text=HOOK,
    )
    log.info(f"  Final: {result} ({Path(str(result)).stat().st_size/1024/1024:.1f} MB)")
    return Path(str(result))


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — Premium Pipeline v4")
    log.info("  Voice:    ElevenLabs TTS (Liam)")
    log.info("  Lip sync: Kling AI Avatar Pro")
    log.info("  LLM:      DeepSeek (script)")
    log.info("  Captions: Whisper (local)")
    log.info("=" * 60)

    # Step 1: Script
    script = write_script()

    # Step 2: ElevenLabs voice
    audio_path = generate_voice(script)

    # Upload face image + audio for Kling
    log.info("Uploading assets...")
    face_url = upload_file(FACE_IMAGE)
    audio_url = upload_file(audio_path)

    # Step 3: Kling Avatar Pro lip sync
    video_path = generate_avatar_video(face_url, audio_url)

    # Step 4: Captions
    captions = generate_captions(audio_path)

    # Step 5: Final reel
    final = build_reel(video_path, captions)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info(f"Script: \"{script}\"")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
