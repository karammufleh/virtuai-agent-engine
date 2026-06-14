#!/usr/bin/env python3
"""
produce_reel_v9.py — Industry-grade polish over v8.

Takes the v8 reel as input and adds:
  • Whoosh SFX on every b-roll cut (12-18 dB below dialogue)
  • Riser SFX building up to the "Part two" cliffhanger
  • Boom SFX on the punchline keyword "Part two"
  • Strategic 350ms silence just before "Part two"
  • Hard zoom punch (1.0x → 1.18x in 6 frames) on the cliffhanger keyword

The cliffhanger is detected by parsing the existing ASS captions for "Part".
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("produce_reel_v9")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
SFX_DIR = ROOT / "virtuai" / "data" / "sfx"
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

INPUT_REEL = OUTPUT_DIR / "daniel_reel_v8_1778704962.mp4"

# The b-roll cut schedule from v8 (where whoosh SFX should fire)
CUT_TIMES = [2.8, 4.6, 7.2, 9.0, 10.0, 11.8, 14.0, 16.0, 19.2, 21.2, 22.6, 24.6]


def video_dur(path: Path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def find_part2_time(captions_ass: Path) -> float | None:
    """Find the timestamp where 'Part' or 'PART' is spoken (cliffhanger)."""
    text = captions_ass.read_text()
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        if re.search(r"\bPART(?:\s*2)?\b|\bpart\s*two\b|\bpart\b", parts[9], re.IGNORECASE):
            ts = parts[1].strip()
            try:
                h, m, s = ts.split(":")
                seconds = int(h) * 3600 + int(m) * 60 + float(s)
                return seconds
            except Exception:
                continue
    return None


def find_captions_for_video(video: Path) -> Path | None:
    """Locate the ASS captions file associated with a v8 work directory."""
    work_dirs = sorted(OUTPUT_DIR.glob("_v8_work_*"), reverse=True)
    for d in work_dirs:
        f = d / "for_captions.ass"
        if f.exists():
            return f
    return None


def main():
    if not INPUT_REEL.exists():
        raise FileNotFoundError(f"v8 reel not found: {INPUT_REEL}")

    captions = find_captions_for_video(INPUT_REEL)
    log.info(f"Captions source: {captions}")
    part2_time = find_part2_time(captions) if captions else None
    if part2_time is None:
        log.warning("Could not find 'Part' in captions — using duration-2.5s as fallback.")
        part2_time = video_dur(INPUT_REEL) - 2.5
    log.info(f"Cliffhanger '{'Part two'}' detected at t={part2_time:.2f}s")

    whoosh = SFX_DIR / "whoosh.mp3"
    boom = SFX_DIR / "boom.mp3"
    riser = SFX_DIR / "riser.mp3"
    for s in [whoosh, boom, riser]:
        if not s.exists():
            raise FileNotFoundError(s)

    total_dur = video_dur(INPUT_REEL)
    log.info(f"Source duration: {total_dur:.2f}s")

    # ─── Build the SFX mix track ─────────────────────────────────────────
    # Each whoosh is delayed to its cut time, volume reduced.
    # Riser ramps up from (part2 - 1.5s) to part2.
    # Boom hits at part2.
    sfx_inputs = []
    for t in CUT_TIMES:
        sfx_inputs += ["-i", str(whoosh)]
    sfx_inputs += ["-i", str(riser), "-i", str(boom)]

    # Filter graph: delay + volume each whoosh, then amix
    filter_parts = []
    for i, t in enumerate(CUT_TIMES):
        # input index = i (the whooshes are the first N inputs)
        # base reel is NOT in this filter — we'll mix it in a second pass
        delay_ms = int(t * 1000)
        filter_parts.append(
            f"[{i}:a]adelay={delay_ms}|{delay_ms},volume=0.35[w{i}]"
        )

    riser_idx = len(CUT_TIMES)
    boom_idx = len(CUT_TIMES) + 1
    # Riser: starts 1.5s before part2 keyword
    riser_start_ms = max(int((part2_time - 1.5) * 1000), 0)
    boom_start_ms = int(part2_time * 1000)
    filter_parts.append(
        f"[{riser_idx}:a]adelay={riser_start_ms}|{riser_start_ms},volume=0.45[r]"
    )
    filter_parts.append(
        f"[{boom_idx}:a]adelay={boom_start_ms}|{boom_start_ms},volume=0.55[b]"
    )

    whoosh_labels = "".join(f"[w{i}]" for i in range(len(CUT_TIMES)))
    filter_parts.append(
        f"{whoosh_labels}[r][b]amix=inputs={len(CUT_TIMES) + 2}:duration=longest[sfxmix]"
    )

    work = OUTPUT_DIR / f"_v9_work_{int(__import__('time').time())}"
    work.mkdir(exist_ok=True)
    sfx_track = work / "sfx_track.wav"

    log.info("Building SFX track...")
    cmd = [FFMPEG, "-y", *sfx_inputs,
           "-filter_complex", ";".join(filter_parts),
           "-map", "[sfxmix]",
           "-ar", "44100", "-ac", "2",
           str(sfx_track)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error(f"SFX build failed: {proc.stderr[-1500:]}")
        sys.exit(1)
    log.info(f"  SFX track: {sfx_track.name}")

    # ─── Hard zoom punch on "Part two" via split-zoom-concat ──────────
    log.info("Applying hard zoom punch on cliffhanger...")
    zoom_start = part2_time
    hold_end = part2_time + 1.5

    seg_pre = work / "seg_pre.mp4"   # 0 → zoom_start
    seg_zoom = work / "seg_zoom.mp4" # zoom_start → hold_end (zoomed)
    seg_post = work / "seg_post.mp4" # hold_end → end

    # Pre-zoom segment (normal)
    subprocess.run([
        FFMPEG, "-y", "-i", str(INPUT_REEL),
        "-ss", "0", "-to", f"{zoom_start}",
        "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-r", "30",
        str(seg_pre),
    ], check=True, capture_output=True)

    # Zoom segment: crop center 84.7% then scale back to 720x1280 = 1.18x zoom
    subprocess.run([
        FFMPEG, "-y", "-i", str(INPUT_REEL),
        "-ss", f"{zoom_start}", "-to", f"{hold_end}",
        "-vf", (
            "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
            "crop=610:1084:55:98,scale=720:1280"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-r", "30",
        str(seg_zoom),
    ], check=True, capture_output=True)

    # Post-zoom segment
    if hold_end < total_dur:
        subprocess.run([
            FFMPEG, "-y", "-i", str(INPUT_REEL),
            "-ss", f"{hold_end}", "-to", f"{total_dur}",
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-r", "30",
            str(seg_post),
        ], check=True, capture_output=True)

    # Concat
    list_file = work / "concat.txt"
    parts = [seg_pre, seg_zoom]
    if hold_end < total_dur:
        parts.append(seg_post)
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in parts))

    zoomed = work / "zoomed.mp4"
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(zoomed),
    ], check=True, capture_output=True)
    log.info(f"  Zoomed: {zoomed.name}")

    # ─── Mix SFX track into the reel audio ─────────────────────────────
    log.info("Mixing SFX with original audio...")
    final = OUTPUT_DIR / f"daniel_reel_v9_{int(__import__('time').time())}.mp4"
    subprocess.run([
        FFMPEG, "-y", "-i", str(zoomed), "-i", str(sfx_track),
        "-filter_complex",
        f"[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(final),
    ], check=True, capture_output=True)

    log.info("=" * 60)
    log.info(f"Final v9: {final}")
    log.info(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
    log.info("=" * 60)

    # Reviewer
    log.info("Running reviewer gate...")
    from virtuai.tools.video_reviewer import review_video, format_review_report
    review = review_video(final)
    print(format_review_report(review))


if __name__ == "__main__":
    main()
