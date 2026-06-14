#!/usr/bin/env python3
"""Resume v13 from the Kling step using existing voice + music + script."""
import json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.produce_reel_v13 import (
    generate_kling_cinematic, lip_sync_kling, post_produce,
    upload_to_tmpfiles, video_dur, log,
)

SCRIPT = ROOT / "virtuai/data/scripts/v13_1778753825.json"
VOICE = ROOT / "virtuai/data/generated_videos/v13_voice_1778753847.mp3"
MUSIC = ROOT / "virtuai/data/generated_videos/v13_music_1778754012.mp3"
CANONICAL = ROOT / "virtuai/persona/canonical_daniel.png"

if __name__ == "__main__":
    t0 = time.time()
    script = json.loads(SCRIPT.read_text())
    log.info(f"Resuming v13 — topic: {script['topic']}")

    canonical_url = upload_to_tmpfiles(CANONICAL)
    face_urls = [canonical_url, canonical_url]

    kling_video = generate_kling_cinematic(
        script, face_urls,
        int(min(script.get("estimated_seconds", 15), 15)),
    )

    synced = lip_sync_kling(kling_video, VOICE)

    final = post_produce(synced, MUSIC if MUSIC.exists() else None)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info(f"Duration: {video_dur(final):.1f}s")
    log.info("=" * 60)
