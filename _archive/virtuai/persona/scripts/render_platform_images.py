"""
render_platform_images.py — Generate persona-locked images for the 4 image platforms.

For each non-video platform (linkedin, x, instagram, medium) generate a Daniel-
LoRA-conditioned still image at the platform's preferred aspect ratio, save
it next to the platform's text manifest, and update the manifest with the path.

Uses mflux-generate-z-image-turbo + the trained persona LoRA. The prompt for
each platform is platform-specific scene + Daniel's locked image_prefix from
persona_anchor.json.

Usage:
    python virtuai/persona/scripts/render_platform_images.py
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
DEMO_DIR = PERSONA / "demo"
ANCHOR = PERSONA / "persona_anchor.json"


# Platform-specific scene prompts. Each prompt starts with the trigger token
# from persona_anchor.json and is followed by a platform-appropriate scene.
PLATFORM_SCENES: dict[str, dict] = {
    "linkedin": {
        # Professional headshot, neutral background — fits LinkedIn's expected look
        "scene": "professional headshot, sharp suit jacket, neutral grey background, soft studio lighting, looking confidently at camera",
        "width": 1024,
        "height": 1024,
        "seed": 100,
    },
    "x": {
        # Casual confident shot for an X post — lifestyle vibe
        "scene": "candid lifestyle photo, sitting at a clean modern desk with laptop, soft window light, slight smile, looking off camera",
        "width": 1024,
        "height": 1024,
        "seed": 200,
    },
    "instagram": {
        # Square aesthetic shot — moody studio
        "scene": "moody studio portrait, dark navy backdrop, dramatic side lighting, casual black t-shirt, looking thoughtfully off camera",
        "width": 1024,
        "height": 1024,
        "seed": 300,
    },
    "medium": {
        # Wide editorial header image
        "scene": "editorial author photo, sitting in a minimal home office, large window with soft natural light, books on shelves blurred behind, slight smile",
        "width": 1024,
        "height": 1024,
        "seed": 400,
    },
}


def load_anchor() -> dict:
    return json.loads(ANCHOR.read_text(encoding="utf-8"))


def build_prompt(anchor: dict, scene: str) -> str:
    prefix = anchor.get("prompts", {}).get("image_prefix", "a photo of dnlcldr man,")
    return f"{prefix} {scene}, cinematic, sharp focus, high detail"


def find_lora() -> Path:
    sys.path.insert(0, str(ROOT))
    import os
    os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/opt/ffmpeg@7/lib")
    from virtuai.models.backend import _find_persona_lora
    p = _find_persona_lora()
    if p is None:
        sys.exit("No persona LoRA found")
    return p


def render_one(platform: str, plan: dict, anchor: dict, lora: Path, steps: int) -> dict:
    out_dir = DEMO_DIR / platform
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / "image.png"

    prompt = build_prompt(anchor, plan["scene"])
    print(f"\n────── {platform.upper()} ──────")
    print(f"prompt: {prompt[:100]}...")
    print(f"size: {plan['width']}x{plan['height']}, steps={steps}, seed={plan['seed']}")

    cmd = [
        "/Users/karammufleh/virtuai-venv/bin/mflux-generate-z-image-turbo",
        "-q", "8",
        "--lora-paths", str(lora),
        "--prompt", prompt,
        "--width", str(plan["width"]),
        "--height", str(plan["height"]),
        "--steps", str(steps),
        "--seed", str(plan["seed"]),
        "--output", str(final_path),
    ]

    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    elapsed = time.time() - t0

    if proc.returncode != 0 or not final_path.exists():
        sys.stderr.write(f"[{platform}] image gen FAILED:\n")
        sys.stderr.write((proc.stderr or "")[-1500:])
        return {"status": "error", "elapsed_s": elapsed, "exit_code": proc.returncode}

    print(f"[{platform}] ✓ {elapsed/60:.1f} min → {final_path.relative_to(ROOT)}")

    # Update manifest
    manifest_path = out_dir / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"platform": platform}
    )
    manifest["image_path"] = str(final_path.relative_to(ROOT))
    manifest["image_prompt"] = prompt
    manifest["image_seed"] = plan["seed"]
    manifest["image_render_time_s"] = round(elapsed, 1)
    manifest["image_model"] = "z-image-turbo + dnlcldr LoRA"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"status": "ok", "elapsed_s": elapsed, "image_path": str(final_path)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--steps", type=int, default=4, help="Diffusion steps (4 = turbo default)")
    p.add_argument("--platforms", nargs="*", default=list(PLATFORM_SCENES.keys()))
    args = p.parse_args()

    anchor = load_anchor()
    lora = find_lora()
    print(f"Using LoRA: {lora}")

    results: dict[str, dict] = {}
    for platform in args.platforms:
        if platform not in PLATFORM_SCENES:
            print(f"  ✗ unknown platform '{platform}', skipping")
            continue
        results[platform] = render_one(platform, PLATFORM_SCENES[platform], anchor, lora, args.steps)

    summary_path = DEMO_DIR / "image_render_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "rendered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "model": "z-image-turbo + dnlcldr LoRA",
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n✓ summary → {summary_path}")


if __name__ == "__main__":
    main()
