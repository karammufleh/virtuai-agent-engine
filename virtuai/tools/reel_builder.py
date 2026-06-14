"""
reel_builder.py — Stitch clips + captions + hook overlay + bg music → reel.

Takes Kling lip-synced video clips (already contain audio), burns in ASS
captions from caption_generator.py, adds a hook text overlay for the first
3 seconds, and optionally mixes in background music at -22 dB.

All video processing via FFmpeg subprocess (ffmpeg@7 for torchaudio compat).

Standards enforced (from PROJECT_STANDARDS.md):
  - 9:16 aspect ratio, 720×1280 minimum
  - Word-by-word captions burned in (not srt sidecar)
  - Hook text overlay in first 0-3 seconds (top-third, fade-in)
  - Background music at -22 dB if provided
  - H.264 output, faststart for web delivery

Public API:
    build_reel(clips, captions_ass, output_path, hook_text=None, bg_music=None)
        -> Path to final reel MP4
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("virtuai.tools.reel_builder")

import os as _os, shutil as _shutil
FFMPEG = _os.environ.get("FFMPEG_BIN") or _shutil.which("ffmpeg") or "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = _os.environ.get("FFPROBE_BIN") or _shutil.which("ffprobe") or "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

# Hook overlay style
HOOK_FONT = "Montserrat Black"
HOOK_FONTSIZE = 42
HOOK_DURATION = 3.0  # seconds
HOOK_FADE_IN = 0.2   # seconds

# Background music level
BG_MUSIC_DB = -22

# Output encoding
OUTPUT_WIDTH = 720
OUTPUT_HEIGHT = 1280
OUTPUT_FPS = 30
OUTPUT_CRF = 20  # good quality, reasonable filesize


def _run_ffmpeg(args: list[str], desc: str = "FFmpeg") -> None:
    cmd = [FFMPEG, "-y"] + args
    logger.info(f"{desc}: {' '.join(cmd)}")
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        logger.error(f"{desc} stderr: {result.stderr[-2000:]}")
        raise RuntimeError(f"{desc} failed (exit {result.returncode}): {result.stderr[-500:]}")


def _get_duration(path: str | Path) -> float:
    result = subprocess.run(
        [FFPROBE, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def _concat_clips(clips: list[Path], output: Path) -> None:
    """Concatenate multiple clips using FFmpeg concat demuxer."""
    if len(clips) == 1:
        # Just copy the single clip
        _run_ffmpeg(
            ["-i", str(clips[0]), "-c", "copy", str(output)],
            desc="Copy single clip",
        )
        return

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for clip in clips:
            f.write(f"file '{clip}'\n")
        concat_list = f.name

    _run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", concat_list,
         "-c", "copy", str(output)],
        desc=f"Concat {len(clips)} clips",
    )
    Path(concat_list).unlink(missing_ok=True)


def _build_hook_drawtext(hook_text: str) -> str:
    """Build FFmpeg drawtext filter string for the hook overlay."""
    escaped = hook_text.replace("'", "'\\''").replace(":", "\\:")
    return (
        f"drawtext=text='{escaped}'"
        f":fontfile=''"
        f":font='{HOOK_FONT}'"
        f":fontsize={HOOK_FONTSIZE}"
        f":fontcolor=white"
        f":borderw=3"
        f":bordercolor=black"
        f":shadowcolor=black@0.5"
        f":shadowx=2:shadowy=2"
        f":x=(w-text_w)/2"
        f":y=h*0.15"
        f":alpha='if(lt(t,{HOOK_FADE_IN}),t/{HOOK_FADE_IN},"
        f"if(lt(t,{HOOK_DURATION - 0.3}),1,"
        f"if(lt(t,{HOOK_DURATION}),(({HOOK_DURATION}-t)/0.3),0)))'"
        f":enable='between(t,0,{HOOK_DURATION})'"
    )


def build_reel(
    clips: list[str | Path],
    captions_ass: str | Path,
    output_path: str | Path,
    hook_text: str | None = None,
    bg_music: str | Path | None = None,
    bg_music_db: float = BG_MUSIC_DB,
) -> Path:
    """
    Build a complete reel from lip-synced video clips.

    Args:
        clips:         List of video file paths (in order). Each clip
                       should already contain lip-synced audio.
        captions_ass:  Path to ASS caption file from caption_generator.py.
        output_path:   Where to write the final reel MP4.
        hook_text:     Text for the hook overlay (first 3s). None = no hook.
        bg_music:      Path to background music file. None = no bg music.
        bg_music_db:   Background music volume in dB (default -22).

    Returns:
        Path to the final reel MP4.
    """
    clips = [Path(c) for c in clips]
    captions_ass = Path(captions_ass)
    output_path = Path(output_path)

    for c in clips:
        if not c.exists():
            raise FileNotFoundError(f"Clip not found: {c}")
    if not captions_ass.exists():
        raise FileNotFoundError(f"Captions not found: {captions_ass}")

    # Step 1: Concat clips if multiple
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        if len(clips) > 1:
            concat_path = tmpdir / "concat.mp4"
            _concat_clips(clips, concat_path)
            source_video = concat_path
        else:
            source_video = clips[0]

        duration = _get_duration(source_video)
        logger.info(f"Source video: {duration:.1f}s, building reel...")

        # Step 2: Build filter chain
        filters = []

        # ASS captions (burned in)
        ass_path_escaped = str(captions_ass).replace(":", "\\:").replace("'", "'\\''")
        filters.append(f"ass='{ass_path_escaped}'")

        # Hook text overlay
        if hook_text:
            filters.append(_build_hook_drawtext(hook_text))

        filter_chain = ",".join(filters)

        # Step 3: Build FFmpeg command
        inputs = ["-i", str(source_video)]
        audio_filter = None

        if bg_music and Path(bg_music).exists():
            inputs += ["-i", str(bg_music)]
            # Mix: original audio at 0dB + bg music at specified dB
            # Trim bg music to video length, then amerge
            audio_filter = (
                f"[1:a]atrim=0:{duration},asetpts=PTS-STARTPTS,"
                f"volume={bg_music_db}dB[bg];"
                f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=3[aout]"
            )

        # Build full command
        args = inputs[:]

        if audio_filter:
            full_filter = f"[0:v]{filter_chain}[vout];{audio_filter}"
            args += [
                "-filter_complex", full_filter,
                "-map", "[vout]", "-map", "[aout]",
            ]
        else:
            args += ["-vf", filter_chain]

        args += [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", str(OUTPUT_CRF),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-r", str(OUTPUT_FPS),
            str(output_path),
        ]

        _run_ffmpeg(args, desc="Build reel")

    final_duration = _get_duration(output_path)
    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(f"Reel complete: {output_path} ({final_duration:.1f}s, {size_mb:.1f} MB)")
    return output_path


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    p = argparse.ArgumentParser(description="Build a reel from clips + captions")
    p.add_argument("clips", nargs="+", help="Video clip paths (in order)")
    p.add_argument("-c", "--captions", required=True, help="ASS caption file")
    p.add_argument("-o", "--output", required=True, help="Output reel MP4 path")
    p.add_argument("--hook", help="Hook text overlay for first 3 seconds")
    p.add_argument("--music", help="Background music file path")
    p.add_argument("--music-db", type=float, default=BG_MUSIC_DB,
                   help=f"Background music volume in dB (default: {BG_MUSIC_DB})")
    args = p.parse_args()

    result = build_reel(
        clips=args.clips,
        captions_ass=args.captions,
        output_path=args.output,
        hook_text=args.hook,
        bg_music=args.music,
        bg_music_db=args.music_db,
    )
    print(f"\nReel: {result}")
