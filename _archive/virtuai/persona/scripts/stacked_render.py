"""
stacked_render.py — Render Daniel talking-head with REAL head motion + sharp lip sync.

Two-stage pipeline:
  1. SadTalker (motion-enabled, no --still) takes face image + audio →
     a video with natural head turns, blinks, micro-expressions, but slightly
     soft lip sync.
  2. Wav2Lip refines THAT video with the same audio → keeps SadTalker's head
     motion + replaces the mouth region with crisp lip movements.

This is what closes the gap toward HeyGen-tier output on Apple Silicon. Pure
Wav2Lip = lips only (looks frozen from the neck up). Pure SadTalker = motion
but soft lips. Stack = both.

Time budget on M-series at size 256 (no GFPGAN): roughly 5-8× realtime per
stage. So a 43-sec TikTok audio takes ~10-20 min total.

Usage:
    python virtuai/persona/scripts/stacked_render.py \\
        --image virtuai/persona/face_dataset/daniel_hero.png \\
        --audio virtuai/persona/demo/tiktok/feed/<post_id>/audio.wav \\
        --out  virtuai/persona/demo/tiktok/feed/<post_id>/video.mp4
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
SADTALKER_DIR = PERSONA / "sadtalker"
SADTALKER_VENV_PY = "/Users/karammufleh/virtuai-sadtalker-venv/bin/python"
WAV2LIP_RENDER = PERSONA / "scripts" / "wav2lip_render.py"
INTERMEDIATES_DIR = PERSONA / "talking_head" / "_stacked_intermediate"


def run_sadtalker(image: Path, audio: Path, *, size: int, enhancer: str | None) -> Path:
    """Run SadTalker with HEAD MOTION enabled (no --still). Returns the produced mp4."""
    INTERMEDIATES_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    result_dir = INTERMEDIATES_DIR / f"sadtalker_{timestamp}"
    result_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        SADTALKER_VENV_PY,
        "inference.py",
        "--driven_audio", str(audio),
        "--source_image", str(image),
        "--result_dir", str(result_dir),
        "--size", str(size),
        "--preprocess", "full",
        "--cpu",
    ]
    # NOTE: NO --still flag — that's the whole point. We want head motion.
    if enhancer:
        cmd.extend(["--enhancer", enhancer])

    print(f"[stage 1: SadTalker (motion-enabled, size={size})]")
    print(f"  audio: {audio.name}, image: {image.name}, enhancer: {enhancer or 'off'}")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(SADTALKER_DIR), capture_output=True, text=True, timeout=10800)
    elapsed = time.time() - t0
    if proc.returncode != 0:
        sys.stderr.write((proc.stdout or "")[-2000:])
        sys.stderr.write((proc.stderr or "")[-2000:])
        sys.exit(f"SadTalker exit {proc.returncode}")

    mp4s = sorted(result_dir.rglob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not mp4s:
        sys.exit("SadTalker exit 0 but no mp4 produced")
    out = mp4s[-1]
    print(f"  ✓ stage 1 done in {elapsed/60:.1f} min → {out.relative_to(ROOT)}")
    return out


def run_wav2lip_refine(motion_video: Path, audio: Path) -> Path:
    """Run Wav2Lip on the SadTalker motion video to sharpen the lip sync."""
    cmd = [
        SADTALKER_VENV_PY,
        str(WAV2LIP_RENDER),
        "--face", str(motion_video),
        "--audio", str(audio),
        "--quality", "Improved",
    ]
    print(f"[stage 2: Wav2Lip refinement]")
    print(f"  driving video: {motion_video.name}")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    elapsed = time.time() - t0
    if proc.returncode != 0:
        sys.stderr.write((proc.stdout or "")[-2000:])
        sys.stderr.write((proc.stderr or "")[-2000:])
        sys.exit(f"Wav2Lip exit {proc.returncode}")
    line = next((l for l in proc.stdout.splitlines() if "→" in l and ".mp4" in l), None)
    if not line:
        sys.exit("Wav2Lip wrapper produced no output path")
    out = Path(line.split("→", 1)[1].strip())
    print(f"  ✓ stage 2 done in {elapsed:.1f}s → {out.relative_to(ROOT)}")
    return out


def stacked(image: Path, audio: Path, *, size: int = 256, enhancer: str | None = None) -> Path:
    image = image.resolve()  # SadTalker runs from sadtalker/ cwd — needs absolute path
    audio = audio.resolve()
    if not image.exists():
        sys.exit(f"image not found: {image}")
    if not audio.exists():
        sys.exit(f"audio not found: {audio}")

    motion_video = run_sadtalker(image, audio, size=size, enhancer=enhancer)
    refined = run_wav2lip_refine(motion_video, audio)
    return refined


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True, help="Source face image (still png)")
    p.add_argument("--audio", required=True, help="Driving audio (wav)")
    p.add_argument("--out", help="Final mp4 destination (will copy refined output here)")
    p.add_argument("--size", type=int, default=256, choices=[256, 512],
                   help="SadTalker render size — 256 fastest, 512 sharper but ~4× slower")
    p.add_argument("--enhancer", default=None, choices=[None, "gfpgan"],
                   help="Optional GFPGAN inside SadTalker (slow). Wav2Lip refinement happens regardless.")
    args = p.parse_args()

    refined = stacked(Path(args.image), Path(args.audio), size=args.size, enhancer=args.enhancer)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(refined, out_path)
        print(f"\n✓ final video → {out_path}")
    else:
        print(f"\n✓ final video → {refined}")


if __name__ == "__main__":
    main()
