"""
eval_lora_checkpoint.py — Generate test images with the latest LoRA checkpoint
and run the consistency report. Capstone before/after delta in one command.

Pipeline:
  1. Discover the newest checkpoint zip across training_runs* dirs
  2. Extract its adapter.safetensors into _extracted/
  3. Generate N test images via mflux-generate with the persona LoRA
  4. Run the face/text consistency report against those generated images
  5. Print the before/after numbers side-by-side

Usage:
    python virtuai/persona/scripts/eval_lora_checkpoint.py
    python virtuai/persona/scripts/eval_lora_checkpoint.py --n 8 --steps 4 --size 512
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PERSONA = ROOT / "virtuai" / "persona"
EVAL_OUT = PERSONA / "eval" / "_lora_test_images"

# Test prompts that exercise the persona LoRA across varied scenes.
# Each starts with the trigger token from persona_anchor.json.
TEST_PROMPTS = [
    "a photo of dnlcldr man, looking at camera, soft natural studio light, neutral grey background, professional headshot",
    "a photo of dnlcldr man, three-quarter view, dramatic side lighting, dark moody background",
    "a photo of dnlcldr man, slight smile, warm window light, blurred coffee shop background",
    "a photo of dnlcldr man, candid expression, cinematic golden hour lighting, urban rooftop background",
    "a photo of dnlcldr man, eye contact with camera, even balanced light, simple white backdrop",
    "a photo of dnlcldr man, side profile, low key lighting, dramatic shadows, dark background",
]

NEGATIVE_PROMPT = (
    "blurry, low quality, distorted face, extra limbs, deformed, child, woman, "
    "cartoon, illustration, painting"
)


def find_newest_lora() -> Path:
    """Re-use the backend's discovery so we share extraction cache."""
    sys.path.insert(0, str(ROOT))
    # Avoid the F5-TTS dyld re-exec just to import _find_persona_lora
    import os
    if "DYLD_FALLBACK_LIBRARY_PATH" not in os.environ:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = "/opt/homebrew/opt/ffmpeg@7/lib"
    from virtuai.models.backend import _find_persona_lora  # noqa: E402
    p = _find_persona_lora()
    if p is None:
        sys.exit(
            "No LoRA checkpoint found. Run mflux-train --config virtuai/persona/training_config.json first."
        )
    return p


def generate_test_images(lora_path: Path, n: int, steps: int, size: int) -> list[Path]:
    EVAL_OUT.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = EVAL_OUT / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    prompts = TEST_PROMPTS[:n]
    generated: list[Path] = []
    for i, prompt in enumerate(prompts):
        out_file = out_dir / f"test_{i:02d}.png"
        # Use the dedicated z-image-turbo command — the generic `mflux-generate
        # --model z-image-turbo` path fails because the generic loader expects a
        # FLUX-style `text_encoder_2/` directory that doesn't exist in the
        # Tongyi-MAI/Z-Image-Turbo HF repo.
        cmd = [
            "mflux-generate-z-image-turbo",
            "-q", "8",
            "--lora-paths", str(lora_path),
            "--prompt", prompt,
            "--width", str(size),
            "--height", str(size),
            "--steps", str(steps),
            "--seed", str(42 + i),
            "--output", str(out_file),
        ]
        print(f"\n[gen {i+1}/{len(prompts)}] {prompt[:80]}…")
        t0 = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        elapsed = time.time() - t0
        if result.returncode != 0:
            print(f"  ✗ failed in {elapsed:.1f}s: {result.stderr[-300:]}")
            continue
        if out_file.exists():
            print(f"  ✓ {elapsed:.1f}s → {out_file.name}")
            generated.append(out_file)
    return generated


def score_with_eval(images_dir: Path) -> dict:
    """Use the existing FaceSimilarity to score the generated batch."""
    sys.path.insert(0, str(ROOT))
    from virtuai.persona.eval.face_similarity import FaceSimilarity
    fs = FaceSimilarity(lazy=True)
    return fs.score_directory(images_dir)


def print_delta(before: dict, after: dict) -> None:
    """Pretty-print the before/after table."""
    def get(d, k, default="-"):
        v = d.get(k)
        return f"{v:.3f}" if isinstance(v, float) else (str(v) if v is not None else default)

    print("\n" + "=" * 72)
    print("PERSONA CONSISTENCY DELTA")
    print("=" * 72)
    print(f"{'metric':<35} {'before (Imagen)':>15} {'after (LoRA)':>15}")
    print("-" * 72)
    for label, key in [
        ("images with face", "n_with_face"),
        ("mean similarity", "mean_similarity"),
        ("median similarity", "median_similarity"),
        ("max similarity", "max_similarity"),
        ("strong matches (≥0.65)", "above_strong_threshold_0.65"),
        ("acceptable (≥0.45)", "above_acceptable_threshold_0.45"),
        ("identity drift (<0.30)", "below_drift_threshold_0.30"),
    ]:
        print(f"{label:<35} {get(before, key):>15} {get(after, key):>15}")
    print("=" * 72)


def find_baseline_report() -> dict | None:
    """Pick the latest face section from the most recent consistency report."""
    reports = sorted(
        (PERSONA / "eval" / "_reports").glob("consistency_*.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not reports:
        return None
    data = json.loads(reports[-1].read_text(encoding="utf-8"))
    face = data.get("face", {})
    return face if "n_with_face" in face else None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=6, help="Number of test prompts")
    p.add_argument("--steps", type=int, default=4, help="mflux-generate steps")
    p.add_argument("--size", type=int, default=512, help="Output image size")
    args = p.parse_args()

    lora = find_newest_lora()
    print(f"Using LoRA: {lora}")
    print(f"  size: {lora.stat().st_size / 1e6:.1f} MB")

    images = generate_test_images(lora, args.n, args.steps, args.size)
    if not images:
        sys.exit("No images generated successfully — aborting eval.")

    images_dir = images[0].parent
    print(f"\n[eval] scoring {len(images)} images in {images_dir.name}…")
    after = score_with_eval(images_dir)

    after_summary = {k: v for k, v in after.items() if k != "scores"}
    print("\n[after — LoRA-conditioned generations]")
    print(json.dumps(after_summary, indent=2))

    before = find_baseline_report()
    if before:
        print_delta(before, after)
    else:
        print("\n(no baseline report found — run virtuai/persona/eval/run_consistency_report.py first to capture the 'before')")

    # Write a delta summary for the capstone writeup
    delta_path = images_dir / "delta_summary.json"
    delta_path.write_text(
        json.dumps({"lora": str(lora), "before": before, "after": after_summary}, indent=2),
        encoding="utf-8",
    )
    print(f"\n✓ delta saved → {delta_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
