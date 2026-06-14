"""
echomimic_v2_render.py — Render a Daniel talking-head via the public
fffiloni/echomimic-v2 HuggingFace Space (free public compute, CPU-tier).

Why use the Space:
  - EchoMimic v2 is the SOTA open-source audio-driven half-body talker
    (CVPR 2025, AntGroup). Quality is significantly better than SadTalker.
  - It officially needs A100/RTX 4090 — won't run usefully on Apple Silicon.
  - The community Space gives us free public compute to render demos
    without renting a GPU.

Tradeoffs:
  - Space is CPU-only — render times are long (~30-60 min for a 5-10 sec clip).
  - Public — we wait in queue behind other users.
  - No quality knob; we use the Space's defaults.

Usage:
    python virtuai/persona/scripts/echomimic_v2_render.py \\
        --image virtuai/persona/face_dataset/daniel_hero.png \\
        --audio virtuai/persona/voice_clone/generated/daniel_1777308365861.wav

The output mp4 lands in virtuai/persona/talking_head/echomimic/.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PERSONA = ROOT / "virtuai" / "persona"
DEFAULT_IMAGE = PERSONA / "face_dataset" / "daniel_hero.png"
DEFAULT_AUDIO = PERSONA / "voice_clone" / "generated" / "daniel_1777308365861.wav"
OUTPUT_DIR = PERSONA / "talking_head" / "echomimic"

SPACE_ID = "fffiloni/echomimic-v2"


def render(
    image_path: Path,
    audio_path: Path,
    *,
    output_dir: Path = OUTPUT_DIR,
    preset: str = "Showcase",
    width: int = 768,
    height: int = 768,
    length: int = 100,        # ≈ 4 s at 24 fps — matches our 3.7 s audio with a small buffer
    steps: int = 6,           # default 20 → 6 trades a little quality for ~3× speed on CPU
    cfg: float = 2.5,
    fps: int = 24,
    context_frames: int = 12,
    context_overlap: int = 3,
    quantization: bool = True,  # int8 quant — major CPU speedup, mild quality cost
    seed: int = -1,
) -> Path:
    """Submit a render job to the EchoMimic v2 Space and wait for the mp4."""
    from gradio_client import Client, file as gradio_file

    if not image_path.exists():
        sys.exit(f"image_path not found: {image_path}")
    if not audio_path.exists():
        sys.exit(f"audio_path not found: {audio_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[echomimic-v2] connecting to Space '{SPACE_ID}'...")
    client = Client(SPACE_ID)
    print(f"[echomimic-v2] connected.")
    print(f"[echomimic-v2] inputs:")
    print(f"  image: {image_path}")
    print(f"  audio: {audio_path}")
    print(f"  preset={preset}, {width}x{height}, length={length}, steps={steps}")
    print(f"[echomimic-v2] dispatching... (Space is CPU-only — expect 30-60 min)")

    t0 = time.time()
    result = client.predict(
        image_input=gradio_file(str(image_path)),
        audio_input=gradio_file(str(audio_path)),
        # pose_input is non-interactive in the Space (server-side default)
        # but the API still requires it as a positional/named argument.
        pose_input="assets/halfbody_demo/pose/01",
        preset_name=preset,
        width=width,
        height=height,
        length=length,
        steps=steps,
        sample_rate=16000,
        cfg=cfg,
        fps=fps,
        context_frames=context_frames,
        context_overlap=context_overlap,
        quantization_input=quantization,
        seed=seed,
        api_name="/generate",
    )
    elapsed = time.time() - t0
    print(f"[echomimic-v2] returned in {elapsed/60:.1f} min")

    # Result is typically (video_path, seed_used). The video path points to
    # a temp file the gradio_client downloaded locally — copy it into our
    # persona/talking_head/echomimic/ for permanence.
    if isinstance(result, (list, tuple)):
        video_src = Path(result[0])
        seed_used = result[1] if len(result) > 1 else seed
    else:
        video_src = Path(result)
        seed_used = seed

    if not video_src.exists():
        sys.exit(f"Result video missing: {video_src}")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    final_path = output_dir / f"daniel_echomimic_{timestamp}.mp4"
    shutil.copy2(video_src, final_path)

    print(f"\n✓ video: {final_path}")
    print(f"  seed:  {seed_used}")
    print(f"  total: {elapsed/60:.1f} min")
    return final_path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--image", default=str(DEFAULT_IMAGE), help="Source face image")
    p.add_argument("--audio", default=str(DEFAULT_AUDIO), help="Driving audio (WAV)")
    p.add_argument("--length", type=int, default=240, help="Frames to generate (240 ≈ 10s @ 24fps)")
    p.add_argument("--steps", type=int, default=6, help="Diffusion steps (lower = faster)")
    p.add_argument("--seed", type=int, default=-1)
    p.add_argument("--no-quantization", action="store_true",
                   help="Disable quantization (slower but possibly higher quality)")
    args = p.parse_args()

    render(
        image_path=Path(args.image),
        audio_path=Path(args.audio),
        length=args.length,
        steps=args.steps,
        seed=args.seed,
        quantization=not args.no_quantization,
    )


if __name__ == "__main__":
    main()
