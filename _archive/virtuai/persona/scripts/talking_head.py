"""
talking_head.py — Generate Daniel Calder talking-head videos via SadTalker.

Pipeline (per request):
    text → F5-TTS clone → audio.wav
    audio.wav + source_image → SadTalker → talking_head.mp4

SadTalker is installed in an isolated venv at ~/virtuai-sadtalker-venv because
its dependency pins (numpy 1.23, scipy 1.10, librosa 0.9, kornia 0.6) conflict
with our main venv. We invoke it via subprocess.

Usage:
    python talking_head.py --text "Build systems instead of trading time."
    python talking_head.py --audio path/to/voice.wav --source path/to/face.png
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PERSONA_DIR = ROOT / "virtuai" / "persona"
SADTALKER_DIR = PERSONA_DIR / "sadtalker"
SADTALKER_VENV_PY = Path("/Users/karammufleh/virtuai-sadtalker-venv/bin/python")

# Default Daniel face used by SadTalker — the LoRA-generated hero image
# (front-facing 512px studio portrait, max single-image ArcFace sim 0.71).
DEFAULT_SOURCE_IMAGE = PERSONA_DIR / "face_dataset" / "daniel_hero.png"
DEFAULT_RESULT_DIR = PERSONA_DIR / "talking_head" / "generated"
TRIMMED_AUDIO_DIR = PERSONA_DIR / "talking_head" / "audio"


def call_voice_endpoint(text: str, *, host: str = "http://localhost:8765") -> Path:
    """Call POST /generate-voice on the running backend → returns the audio path."""
    import json
    import urllib.request
    body = json.dumps({"text": text, "speed": 1.0, "seed": 42}).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/generate-voice",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    return Path(data["audio_path"])


def synth_audio_locally(text: str) -> Path:
    """Fallback if backend is not running — invoke clone_voice.py CLI directly."""
    import importlib.util
    clone_path = PERSONA_DIR / "scripts" / "clone_voice.py"
    spec = importlib.util.spec_from_file_location("clone_voice", clone_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {clone_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out_path = PERSONA_DIR / "voice_clone" / "generated" / f"daniel_{int(time.time())}.wav"
    mod.synthesize(text=text, out_path=out_path)
    return out_path


def run_sadtalker(
    audio_path: Path,
    source_image: Path,
    result_dir: Path,
    *,
    size: int = 256,
    preprocess: str = "full",
    still: bool = False,
    enhancer: str | None = None,
    cpu: bool = False,
) -> Path:
    """Invoke SadTalker via the isolated venv. Returns the output mp4 path."""
    if not SADTALKER_VENV_PY.exists():
        raise RuntimeError(f"SadTalker venv missing: {SADTALKER_VENV_PY}")
    if not (SADTALKER_DIR / "checkpoints" / "SadTalker_V0.0.2_512.safetensors").exists():
        raise RuntimeError(
            f"SadTalker checkpoints not found in {SADTALKER_DIR}/checkpoints/. "
            "Run scripts/download_models.sh equivalent."
        )

    result_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(SADTALKER_VENV_PY),
        "inference.py",
        "--driven_audio", str(audio_path),
        "--source_image", str(source_image),
        "--result_dir", str(result_dir),
        "--size", str(size),
        "--preprocess", preprocess,
    ]
    if still:
        cmd.append("--still")
    if enhancer:
        cmd.extend(["--enhancer", enhancer])
    if cpu:
        cmd.append("--cpu")

    print(f"[SadTalker] cmd: {' '.join(cmd)}")
    start = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(SADTALKER_DIR),
        capture_output=True,
        text=True,
        timeout=1800,  # 30 min ceiling
    )
    elapsed = time.time() - start
    print(f"[SadTalker] returned {proc.returncode} in {elapsed:.1f}s")

    if proc.returncode != 0:
        # Surface stderr; SadTalker writes most diagnostics there
        sys.stderr.write(proc.stderr or "")
        sys.stderr.write(proc.stdout or "")
        raise RuntimeError(f"SadTalker failed (exit {proc.returncode})")

    # SadTalker writes to result_dir/<run-id>/<stem>##<audio_stem>_enhanced.mp4
    # or .mp4 — find the newest mp4 under result_dir.
    mp4s = sorted(result_dir.rglob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not mp4s:
        raise RuntimeError("SadTalker exited 0 but produced no .mp4")
    return mp4s[-1]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--text", help="Text to synthesize via F5-TTS, then animate")
    p.add_argument("--audio", help="Skip TTS, use this wav directly")
    p.add_argument("--source", default=str(DEFAULT_SOURCE_IMAGE),
                   help=f"Source face image (default: {DEFAULT_SOURCE_IMAGE.name})")
    p.add_argument("--out", default=None, help="Final mp4 destination (optional copy)")
    p.add_argument("--size", type=int, default=256, choices=[256, 512])
    p.add_argument("--preprocess", default="full", choices=["crop", "extcrop", "resize", "full", "extfull"])
    p.add_argument("--still", action="store_true", help="Still mode — minimal head motion")
    p.add_argument("--enhancer", default=None, choices=[None, "gfpgan"])
    p.add_argument("--cpu", action="store_true", help="Force CPU (slower but no MPS surprises)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.text and not args.audio:
        sys.exit("Provide --text or --audio")

    if args.audio:
        audio_path = Path(args.audio)
        if not audio_path.exists():
            sys.exit(f"Audio not found: {audio_path}")
    else:
        # Prefer the running backend; fall back to local clone_voice
        try:
            audio_path = call_voice_endpoint(args.text)
            print(f"[voice] backend → {audio_path}")
        except Exception as e:
            print(f"[voice] backend unavailable ({e}); falling back to local synth")
            audio_path = synth_audio_locally(args.text)
            print(f"[voice] local → {audio_path}")

    source_image = Path(args.source)
    if not source_image.exists():
        sys.exit(f"Source image not found: {source_image}")

    DEFAULT_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    mp4 = run_sadtalker(
        audio_path=audio_path,
        source_image=source_image,
        result_dir=DEFAULT_RESULT_DIR,
        size=args.size,
        preprocess=args.preprocess,
        still=args.still,
        enhancer=args.enhancer,
        cpu=args.cpu,
    )
    print(f"\n✓ talking head: {mp4}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mp4, out_path)
        print(f"  copied → {out_path}")


if __name__ == "__main__":
    main()
