#!/usr/bin/env python3
"""
produce_reel_v12.py — Clean cut. No captions, no b-roll overlays.

User feedback on v11: video quality bad, lip-sync poor, scenes not realistic.
Root cause: scene-swap via Nano Banana 2 degrades Avatar Pro lip-sync, and
b-roll overlays during dialogue interrupt the talking head.

v12 = simplest possible reel:
  • Single talking head from canonical Daniel (best lip-sync source)
  • NO captions
  • NO b-roll overlays during talking
  • Music underneath at -22 dB
  • Subtle natural look (mild grain + gentle S-curve, no color cast)
  • Static @daniel.calder corner handle only

Pure post-production on existing v10 assets — no new KIE generations.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("produce_reel_v12")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

# Existing assets from v10 (canonical Daniel + 4-hour rule audio)
AVATAR_SOURCE = OUTPUT_DIR / "v10_avatar_1778707002.mp4"
MUSIC_SOURCE = OUTPUT_DIR / "v10_music_1778706570.mp3"


def video_dur(p):
    r = subprocess.run([FFPROBE, "-v", "quiet", "-print_format", "json",
                       "-show_format", str(p)], capture_output=True, text=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def main():
    if not AVATAR_SOURCE.exists():
        raise FileNotFoundError(AVATAR_SOURCE)

    work = OUTPUT_DIR / f"_v12_work_{int(time.time())}"
    work.mkdir(exist_ok=True)

    log.info("=" * 60)
    log.info("VirtuAI — Clean Reel v12 (no captions, no overlays)")
    log.info("=" * 60)

    # Step 1: Apply natural look (mild S-curve + subtle grain, no color cast)
    log.info("Step 1: Natural look pass...")
    natural = work / "natural.mp4"
    vf = (
        # 720x1280 normalize
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,"
        # Very mild contrast bump only — no saturation/color shift
        "eq=contrast=1.03:saturation=1.00:gamma=1.0,"
        # Light S-curve
        "curves=master='0/0 0.3/0.29 0.7/0.71 1/1',"
        # Subtle real-camera grain
        "noise=alls=8:allf=t+u,"
        # Very subtle vignette (real lens darkening)
        "vignette=PI/6"
    )
    subprocess.run([
        FFMPEG, "-y", "-i", str(AVATAR_SOURCE),
        "-vf", vf,
        "-r", "30",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy",
        str(natural),
    ], check=True, capture_output=True)
    log.info(f"  Natural: {natural.name}")

    # Step 2: Mix music + loudnorm dialogue, burn ONLY the corner handle
    dur = video_dur(natural)
    log.info(f"Step 2: Mix music ({dur:.1f}s) + loudnorm + handle...")

    if MUSIC_SOURCE.exists():
        mixed = work / "mixed.mp4"
        subprocess.run([
            FFMPEG, "-y", "-i", str(natural), "-i", str(MUSIC_SOURCE),
            "-filter_complex",
            f"[0:a]loudnorm=I=-14:LRA=11:tp=-1[dlg];"
            f"[1:a]volume=0.07,afade=t=in:st=0:d=1.5,afade=t=out:st={dur-1.5}:d=1.5[bg];"
            f"[dlg][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(mixed),
        ], check=True, capture_output=True)
        log.info(f"  Mixed with music")
        source_for_final = mixed
    else:
        # Loudnorm only
        normed = work / "normed.mp4"
        subprocess.run([
            FFMPEG, "-y", "-i", str(natural),
            "-af", "loudnorm=I=-14:LRA=11:tp=-1",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(normed),
        ], check=True, capture_output=True)
        source_for_final = normed

    # Step 3: Tiny handle watermark only — NO captions, NO hook overlay
    log.info("Step 3: Burn @daniel.calder handle...")
    ts = int(time.time())
    final = OUTPUT_DIR / f"daniel_reel_v12_{ts}.mp4"
    burn = (
        "drawtext=text='@daniel.calder'"
        ":font='Inter':fontsize=20:fontcolor=white@0.55"
        ":borderw=1:bordercolor=black@0.5"
        ":x=20:y=h-40"
    )
    subprocess.run([
        FFMPEG, "-y", "-i", str(source_for_final),
        "-vf", burn,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-r", "30",
        str(final),
    ], check=True, capture_output=True)

    log.info("=" * 60)
    log.info(f"Final: {final}")
    log.info(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
    log.info(f"Duration: {video_dur(final):.1f}s")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
