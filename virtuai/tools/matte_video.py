"""
matte_video.py — Per-frame AI matting via rembg for video.

Takes an MP4 talking-head clip and outputs:
  - frames/   : RGBA PNG sequence (Daniel + transparent background)
  - alpha.mov : ProRes 4444 video with proper alpha channel (FFmpeg-friendly)

Then a separate function can composite over any background MP4.

Public API:
    matte_clip(video_path, out_dir) -> Path  (alpha video)
    composite(alpha_video, bg_video, audio_video, out_path) -> Path
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger("virtuai.tools.matte_video")

import os as _os, shutil as _shutil
FFMPEG = _os.environ.get("FFMPEG_BIN") or _shutil.which("ffmpeg") or "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = _os.environ.get("FFPROBE_BIN") or _shutil.which("ffprobe") or "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"


def matte_clip(video_path: Path, work_dir: Path, fps: int = 30) -> Path:
    """
    Extract Daniel from a talking-head video. Returns path to an MP4
    with the alpha pre-multiplied as a luma key — FFmpeg can use it as
    a matte for compositing over any background.

    Strategy: extract frames → rembg per frame (RGBA) → recompose as
    QuickTime ProRes 4444 (carries alpha cleanly).
    """
    from rembg import remove, new_session

    work_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = work_dir / "frames_rgba"
    frames_dir.mkdir(exist_ok=True)

    logger.info(f"Extracting frames from {video_path.name} at {fps}fps...")
    subprocess.run([
        FFMPEG, "-y", "-i", str(video_path),
        "-r", str(fps),
        "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
        str(work_dir / "src_%04d.png"),
    ], check=True, capture_output=True)

    src_frames = sorted((work_dir).glob("src_*.png"))
    n = len(src_frames)
    logger.info(f"Matting {n} frames via rembg (u2net_human_seg)...")

    # Use the human-segmentation model — more accurate for people than default u2net
    session = new_session("u2net_human_seg")

    for i, frame in enumerate(src_frames):
        if i % 30 == 0:
            logger.info(f"  Frame {i+1}/{n}...")
        img = Image.open(frame).convert("RGB")
        out = remove(img, session=session)  # RGBA
        out_path = frames_dir / f"matte_{i:04d}.png"
        out.save(out_path)

    # Compose RGBA frames into a ProRes 4444 MOV (preserves alpha for FFmpeg)
    alpha_out = work_dir / "daniel_alpha.mov"
    logger.info(f"Encoding alpha video → {alpha_out.name}...")
    subprocess.run([
        FFMPEG, "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "matte_%04d.png"),
        "-i", str(video_path),  # for audio
        "-map", "0:v", "-map", "1:a",
        "-c:v", "prores_ks", "-profile:v", "4444",
        "-pix_fmt", "yuva444p10le",
        "-c:a", "aac", "-b:a", "192k",
        str(alpha_out),
    ], check=True, capture_output=True)

    logger.info(f"Alpha video: {alpha_out} ({alpha_out.stat().st_size/1024/1024:.1f} MB)")
    return alpha_out


def composite_over(
    alpha_video: Path,
    bg_segments: list[tuple[Path, float, float]],  # (bg_path, start, end)
    audio_source: Path,
    out_path: Path,
) -> Path:
    """
    Composite the alpha-matte talking head over a sequence of background
    clips. Backgrounds switch at the specified time intervals — gives the
    illusion of the speaker being filmed in different locations.

    Args:
        alpha_video: ProRes 4444 .mov from matte_clip()
        bg_segments: list of (background_video, start_sec, end_sec). The
            backgrounds cover the full duration; sections outside any
            segment use the first segment as default.
        audio_source: where to take the final audio track (usually the
            alpha video which already carries the dialogue audio).
        out_path: final mp4.
    """
    logger.info(f"Compositing alpha over {len(bg_segments)} background segments...")

    # Probe duration
    r = subprocess.run([FFPROBE, "-v", "quiet", "-print_format", "json",
                       "-show_format", str(alpha_video)],
                      capture_output=True, text=True)
    import json as _json
    total_dur = float(_json.loads(r.stdout)["format"]["duration"])

    # Build a single background track by concatenating bg clips at scheduled times.
    # Simplest approach: concat backgrounds end-to-end as a loop, trimmed to total_dur.
    work = out_path.parent / f"_composite_{out_path.stem}"
    work.mkdir(exist_ok=True)

    # Normalize each background to 720x1280 30fps, no audio
    norm_bgs = []
    for i, (bg, _, _) in enumerate(bg_segments):
        norm = work / f"bg_{i:02d}.mp4"
        subprocess.run([
            FFMPEG, "-y", "-i", str(bg),
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1",
            "-r", "30", "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            str(norm),
        ], check=True, capture_output=True)
        norm_bgs.append(norm)

    # Concat backgrounds (loop the list if needed)
    concat_file = work / "bg_concat.txt"
    bg_lines = []
    seg_dur = total_dur / len(norm_bgs)
    for nb in norm_bgs:
        bg_lines.append(f"file '{nb.resolve()}'")
    concat_file.write_text("\n".join(bg_lines) + "\n")

    bg_full = work / "bg_full.mp4"
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-t", f"{total_dur}",
        "-c", "copy",
        str(bg_full),
    ], check=True, capture_output=True)

    # Final composite: alpha overlaid on bg, audio from alpha video
    logger.info(f"Final overlay → {out_path.name}...")
    subprocess.run([
        FFMPEG, "-y",
        "-i", str(bg_full),
        "-i", str(alpha_video),
        "-filter_complex",
        # Slight blur on bg for natural separation, then overlay alpha
        "[0:v]boxblur=2:1[bgblur];"
        "[1:v]format=yuva444p10le[fg];"
        "[bgblur][fg]overlay=0:0[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        str(out_path),
    ], check=True, capture_output=True)

    logger.info(f"Composited: {out_path} ({out_path.stat().st_size/1024/1024:.1f} MB)")
    return out_path


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    alpha = matte_clip(Path(args.video), Path(args.out).parent / "_matte_work")
    print(f"Alpha video: {alpha}")
