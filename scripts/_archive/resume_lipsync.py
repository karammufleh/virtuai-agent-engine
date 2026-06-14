#!/usr/bin/env python3
"""Resume v13 lip-sync from in-flight Kling task."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LIPSYNC_TASK_ID = "883849769331466273"
KLING_VIDEO = ROOT / "virtuai/data/generated_videos/v13_kling_1778754705.mp4"
MUSIC = ROOT / "virtuai/data/generated_videos/v13_music_1778754716.mp3"

print(f"Polling Kling lip-sync task {LIPSYNC_TASK_ID}...")

from virtuai.tools.kling_omni import _poll_task, _download_video, OUTPUT_DIR
from scripts.produce_reel_v13 import post_produce, video_dur

# Retry the poll if SSL hits transient errors
for attempt in range(8):
    try:
        result = _poll_task("/v1/videos/lip-sync", LIPSYNC_TASK_ID)
        break
    except Exception as e:
        print(f"  Poll attempt {attempt+1} failed: {e}")
        time.sleep(20)
else:
    print("All poll attempts failed.")
    sys.exit(1)

# Result structure: data.task_result.videos[0].url
videos = result.get("task_result", {}).get("videos", [])
if not videos:
    print(f"No videos in result: {result}")
    sys.exit(1)
video_url = videos[0]["url"]
print(f"Lip-synced video URL: {video_url}")

ts = int(time.time())
out_path = OUTPUT_DIR / f"v13_lipsync_{ts}.mp4"
_download_video(video_url, out_path)
print(f"\nLip-synced video: {out_path}")

# Find music if exists
music_files = sorted(ROOT.glob("virtuai/data/generated_videos/v13_music_*.mp3"), reverse=True)
music_path = music_files[0] if music_files else None
print(f"Music: {music_path}")

# Post-produce
final = post_produce(out_path, music_path)
print(f"\n=== FINAL ===")
print(f"  File: {final}")
print(f"  Size: {final.stat().st_size/1024/1024:.1f} MB")
print(f"  Duration: {video_dur(final):.1f}s")
