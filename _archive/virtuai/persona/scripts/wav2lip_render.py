"""
wav2lip_render.py — Render a Daniel talking-head via Easy-Wav2Lip on MPS.

Why Wav2Lip:
  - Best-in-class lip sync — that's literally what it was built for (ACM MM 2020).
  - Easy-Wav2Lip fork has explicit Apple Silicon MPS support; ~1 min per clip.
  - Free, fully local, pretrained checkpoints downloaded on first run.

What it does (and doesn't):
  ✓ Sharp, accurate lip movements that match the audio
  ✓ Preserves the source identity (Daniel) frame-by-frame
  ✗ No head motion or expression generation — input is animated only at the lips
  ✗ Best with a video input; on a still image you'll get a static body with moving lips

For the most realistic pipeline, stack: SadTalker (head motion) → Wav2Lip (lip sync polish).
This script handles the standalone case (Wav2Lip directly on a still or video).

Usage:
    python virtuai/persona/scripts/wav2lip_render.py \
        --face virtuai/persona/face_dataset/daniel_hero.png \
        --audio virtuai/persona/voice_clone/generated/daniel_1777308365861.wav

    # Or stack on top of a SadTalker output for best result:
    python virtuai/persona/scripts/wav2lip_render.py \
        --face virtuai/persona/talking_head/generated/2026_04_27_19.46.27.mp4 \
        --audio virtuai/persona/voice_clone/generated/daniel_1777308365861.wav
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PERSONA = ROOT / "virtuai" / "persona"
WAV2LIP_DIR = PERSONA / "wav2lip"
# Reuse the SadTalker venv — it already has torch 2.11 (MPS), librosa, opencv,
# gfpgan, basicsr, facexlib, face_alignment + we added gdown, batch-face,
# moviepy. The dedicated virtuai-wav2lip-venv was abandoned because Easy-Wav2Lip's
# requirements.txt pins torch 2.1 from the CUDA wheel index — useless on Mac
# and the dlib==19.24.2 build hangs.
WAV2LIP_VENV_PY = Path("/Users/karammufleh/virtuai-sadtalker-venv/bin/python")

DEFAULT_FACE = PERSONA / "face_dataset" / "daniel_hero.png"
DEFAULT_AUDIO = PERSONA / "voice_clone" / "generated" / "daniel_1777308365861.wav"
OUTPUT_DIR = PERSONA / "talking_head" / "wav2lip"


def render(
    face_path: Path,
    audio_path: Path,
    *,
    output_dir: Path = OUTPUT_DIR,
    quality: str = "Improved",   # 'Fast', 'Improved', or 'Enhanced' (with GFPGAN)
    nosmooth: bool = True,        # skip face smoothing — better for still images
) -> Path:
    """Run Easy-Wav2Lip's run.py on (face, audio) and return the produced mp4."""
    if not WAV2LIP_VENV_PY.exists():
        sys.exit(f"Wav2Lip venv missing: {WAV2LIP_VENV_PY}")
    if not (WAV2LIP_DIR / "run.py").exists():
        sys.exit(f"Wav2Lip repo missing run.py: {WAV2LIP_DIR}")
    if not face_path.exists():
        sys.exit(f"face not found: {face_path}")
    if not audio_path.exists():
        sys.exit(f"audio not found: {audio_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"daniel_wav2lip_{timestamp}.mp4"

    # Easy-Wav2Lip's run.py expects a VIDEO input. If face_path is a still
    # image, loop it into an mp4 matching the audio's length.
    if face_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        import soundfile as sf
        ffmpeg = "/opt/homebrew/bin/ffmpeg"
        if not Path(ffmpeg).exists():
            ffmpeg = "ffmpeg"
        audio_duration = sf.info(str(audio_path)).duration
        # Pad slightly past audio length so the video isn't cut short.
        loop_duration = max(audio_duration + 0.3, 1.0)
        looped_video = output_dir / f"_input_loop_{timestamp}.mp4"
        loop_cmd = [
            ffmpeg, "-y", "-loop", "1",
            "-i", str(face_path),
            "-t", f"{loop_duration:.2f}",
            "-r", "24",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # ensure even dims for h264
            "-loglevel", "error",
            str(looped_video),
        ]
        print(f"[wav2lip] looping {face_path.name} → {loop_duration:.2f}s mp4")
        r = subprocess.run(loop_cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not looped_video.exists():
            sys.exit(f"Failed to loop still into video:\n{r.stderr}")
        face_path = looped_video

    # Easy-Wav2Lip's run.py reads config.ini for paths and runs from the
    # wav2lip/ dir, so we need ABSOLUTE paths or relative-from-wav2lip-dir.
    abs_face = face_path.resolve()
    abs_audio = audio_path.resolve()
    config_text = f"""[OPTIONS]
video_file = {abs_face}
vocal_file = {abs_audio}
quality = {quality}
output_height = full resolution
wav2lip_version = Wav2Lip
use_previous_tracking_data = False
nosmooth = {nosmooth}
preview_window = Off

[PADDING]
U = 0
D = 10
L = 0
R = 0

[MASK]
size = 2.5
feathering = 2
mouth_tracking = False
debug_mask = False

[OTHER]
batch_process = False
output_suffix = _w2l
include_settings_in_suffix = False
preview_input = False
preview_settings = False
frame_to_preview = 100
"""
    config_path = WAV2LIP_DIR / "config.ini"
    config_path.write_text(config_text, encoding="utf-8")

    cmd = [str(WAV2LIP_VENV_PY), "run.py"]
    print(f"[wav2lip] cmd: {' '.join(cmd)}")
    print(f"[wav2lip] face:  {face_path.name}")
    print(f"[wav2lip] audio: {audio_path.name}")
    print(f"[wav2lip] quality: {quality}, nosmooth: {nosmooth}")

    t0 = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(WAV2LIP_DIR),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    elapsed = time.time() - t0

    # Always surface FULL run.py output — Easy-Wav2Lip catches all exceptions
    # silently and prints "Processing failed! :(", so we need every line.
    if proc.stdout:
        print("--- run.py stdout (full) ---")
        for line in proc.stdout.splitlines():
            print(f"  {line}")
    if proc.stderr:
        print("--- run.py stderr (full) ---")
        for line in proc.stderr.splitlines():
            print(f"  {line}")
    # Detect the silent-failure case
    if "Processing failed" in (proc.stdout or ""):
        sys.exit("Wav2Lip silently failed — see stdout above for the real cause")
    if proc.returncode != 0:
        sys.exit(f"Wav2Lip exit {proc.returncode}")

    # Easy-Wav2Lip's run.py writes to: <input_video_folder>/<input_stem>_<audio_stem>_w2l.mp4
    # (when input filename != audio filename) or <input_stem>_w2l.mp4 otherwise.
    # We search the input video's directory broadly.
    input_dir = face_path.parent
    candidates = sorted(
        [p for p in input_dir.glob("*.mp4")
         if "_w2l" in p.name or p.name.endswith("_GAN.mp4") or "_Improved" in p.name or "_Enhanced" in p.name or "_Fast" in p.name],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not candidates:
        # Last-ditch: any new mp4 in the input dir or wav2lip dir
        all_recent = sorted(
            list(input_dir.glob("*.mp4")) + list(WAV2LIP_DIR.rglob("*.mp4")),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        # Filter out the input loop file
        candidates = [p for p in all_recent if p != face_path][:1]
    if not candidates:
        sys.exit(f"Wav2Lip exit 0 but no output mp4 found near {input_dir}\n"
                 "Run.py output above should indicate where it wrote.")

    src = candidates[0]
    shutil.move(str(src), str(out_path))
    print(f"\n✓ Wav2Lip render done in {elapsed:.1f}s")
    print(f"  → {out_path}")
    return out_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--face", default=str(DEFAULT_FACE),
                   help="Source face — image OR video. Default: daniel_hero.png")
    p.add_argument("--audio", default=str(DEFAULT_AUDIO),
                   help="Driving audio (WAV)")
    p.add_argument("--quality", default="Improved",
                   choices=["Fast", "Improved", "Enhanced"],
                   help="Easy-Wav2Lip quality preset; Enhanced runs GFPGAN refinement")
    p.add_argument("--smooth", action="store_true",
                   help="Enable face smoothing (default off — better for stills)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    render(
        face_path=Path(args.face),
        audio_path=Path(args.audio),
        quality=args.quality,
        nosmooth=not args.smooth,
    )


if __name__ == "__main__":
    main()
