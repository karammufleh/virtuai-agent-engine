#!/usr/bin/env python3
"""v14 Phase D only — uses existing clips. Concat → lipsync → post."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Reuse the user's robust resume module
from scripts.v14_resume import (
    concat_clips, attach_silent_audio, lip_sync, post_produce,
    video_dur, log,
    VOICE, MUSIC, OUTPUT_DIR,
)
import time

EXISTING_CLIPS = [
    OUTPUT_DIR / "v14_clip_0_1778755977.mp4",
    OUTPUT_DIR / "v14_clip_1_1778755975.mp4",
    OUTPUT_DIR / "v14_clip_2_1778755982.mp4",
    OUTPUT_DIR / "v14_clip_3_1778755975.mp4",
]

if __name__ == "__main__":
    t0 = time.time()
    log.info("=" * 60)
    log.info("v14 PHASE D ONLY — using existing 4 Kling i2v clips")
    log.info("=" * 60)

    for c in EXISTING_CLIPS:
        if not c.exists():
            raise FileNotFoundError(c)
    log.info(f"All 4 clips present: {[c.name for c in EXISTING_CLIPS]}")

    stitched = concat_clips(EXISTING_CLIPS)
    total_dur = video_dur(stitched)
    with_silent = attach_silent_audio(stitched, total_dur)
    synced = lip_sync(with_silent, VOICE)
    final = post_produce(synced, MUSIC if MUSIC.exists() else None)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info(f"Duration: {video_dur(final):.1f}s")
    log.info(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
    log.info("=" * 60)
