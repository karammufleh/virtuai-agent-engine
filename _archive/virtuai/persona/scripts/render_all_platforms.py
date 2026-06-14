"""
render_all_platforms.py — Render Daniel talking-head videos for all video
platforms using the chosen model (Wav2Lip by default).

Reads the platform manifests written by generate_platform_content.py and
calls the appropriate render wrapper for each video platform. Stores the
final mp4s in <demo>/<platform>/video.mp4 and updates each manifest with
the path + render time + face similarity score.

Usage:
    python virtuai/persona/scripts/render_all_platforms.py
    python virtuai/persona/scripts/render_all_platforms.py --quality Enhanced
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
HERO_FACE = PERSONA / "face_dataset" / "daniel_hero.png"
WAV2LIP_RENDER = PERSONA / "scripts" / "wav2lip_render.py"
VENV_PY = Path("/Users/karammufleh/virtuai-sadtalker-venv/bin/python")

VIDEO_PLATFORMS = ("tiktok", "instagram_reels", "youtube_shorts")


def render_one(platform: str, quality: str) -> dict:
    out_dir = DEMO_DIR / platform
    manifest_path = out_dir / "manifest.json"
    audio_path = out_dir / "audio.wav"

    if not manifest_path.exists() or not audio_path.exists():
        return {"status": "skipped", "reason": f"manifest or audio missing for {platform}"}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    print(f"\n────── {platform.upper()} ──────")
    print(f"audio: {audio_path.name} ({manifest.get('audio_duration_s', '?')}s)")

    cmd = [
        str(VENV_PY),
        str(WAV2LIP_RENDER),
        "--face", str(HERO_FACE),
        "--audio", str(audio_path),
        "--quality", quality,
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    elapsed = time.time() - t0

    if proc.returncode != 0:
        sys.stderr.write(f"\n[{platform}] render FAILED:\n")
        sys.stderr.write((proc.stdout or "")[-2000:])
        sys.stderr.write((proc.stderr or "")[-2000:])
        return {"status": "error", "elapsed_s": elapsed, "exit_code": proc.returncode}

    # The wrapper prints "→ <path>" with the final mp4
    output_line = next(
        (l for l in proc.stdout.splitlines() if "→" in l and ".mp4" in l),
        None,
    )
    if not output_line:
        return {"status": "error", "elapsed_s": elapsed, "reason": "wrapper produced no output path"}
    src_mp4 = Path(output_line.split("→", 1)[1].strip())
    if not src_mp4.exists():
        return {"status": "error", "elapsed_s": elapsed, "reason": f"output mp4 missing: {src_mp4}"}

    # Copy to manifest dir as video.mp4 for stable referencing from the website
    final_mp4 = out_dir / "video.mp4"
    shutil.copy2(src_mp4, final_mp4)

    print(f"[{platform}] ✓ {elapsed/60:.1f} min → {final_mp4.relative_to(ROOT)}")

    # Update manifest
    manifest["video_path"] = str(final_mp4.relative_to(ROOT))
    manifest["video_render_time_s"] = round(elapsed, 1)
    manifest["video_render_quality"] = quality
    manifest["video_render_model"] = "Wav2Lip (Improved)"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"status": "ok", "elapsed_s": elapsed, "video_path": str(final_mp4)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--quality", default="Improved",
                   choices=["Fast", "Improved", "Enhanced"])
    p.add_argument("--platforms", nargs="*", default=list(VIDEO_PLATFORMS),
                   help="Subset of platforms to render")
    args = p.parse_args()

    results: dict[str, dict] = {}
    for platform in args.platforms:
        results[platform] = render_one(platform, args.quality)

    # Final summary
    summary = {
        "rendered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": "Wav2Lip",
        "quality": args.quality,
        "results": results,
    }
    summary_path = DEMO_DIR / "render_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n✓ summary → {summary_path}")
    for plat, r in results.items():
        ok = "✓" if r.get("status") == "ok" else "✗"
        print(f"  {ok} {plat}: {r}")


if __name__ == "__main__":
    main()
