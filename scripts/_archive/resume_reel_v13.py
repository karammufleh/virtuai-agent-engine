#!/usr/bin/env python3
"""resume_reel_v13.py — Resume v13 from in-flight task IDs."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# In-flight tasks from the killed v13 run at 13:27
KLING_TASK = "9b2c4b10a0603acac9e4e62716a5a71a"
SUNO_TASK = "345c7ec339f186e80439748839604131"
VOICE_FILE = ROOT / "virtuai/data/generated_videos/v13_voice_1778754444.mp3"

# Reuse v13 helpers
from scripts.produce_reel_v13 import (
    poll_task, poll_suno, download_first, lip_sync_kling,
    post_produce, KIE_API_BASE, _headers, OUTPUT_DIR,
)

print(f"Resuming v13...")
print(f"  Kling task: {KLING_TASK}")
print(f"  Suno task:  {SUNO_TASK}")
print(f"  Voice:      {VOICE_FILE.name}")

# 1. Wait for Kling 3.0 to finish
print("\nWaiting for Kling 3.0...")
kling_data = poll_task(KLING_TASK, "Kling")
ts = int(time.time())
kling_video = OUTPUT_DIR / f"v13_kling_{ts}.mp4"
download_first(kling_data, kling_video)

# 2. Wait for Suno
print("\nWaiting for Suno...")
try:
    suno_data = poll_suno(SUNO_TASK)
    resp = suno_data.get("response", {})
    sd = resp.get("sunoData", []) if isinstance(resp, dict) else []
    audio_url = sd[0].get("audioUrl", "") if sd else ""
    if audio_url:
        music_path = OUTPUT_DIR / f"v13_music_{int(time.time())}.mp3"
        with httpx.Client(timeout=300, follow_redirects=True) as c:
            r = c.get(audio_url)
            r.raise_for_status()
            music_path.write_bytes(r.content)
        print(f"  Music: {music_path.name}")
    else:
        music_path = None
except Exception as e:
    print(f"  Music skipped: {e}")
    music_path = None

# 3. Lip sync
print("\nLip syncing...")
synced = lip_sync_kling(kling_video, VOICE_FILE)

# 4. Post-produce
print("\nPost-producing...")
final = post_produce(synced, music_path)
print(f"\nFinal: {final}")
print(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
