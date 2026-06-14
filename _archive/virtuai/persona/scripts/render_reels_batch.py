"""
render_reels_batch.py — Apply the reel editor to the existing video posts
in all 3 video platforms (TikTok, Instagram Reels, YouTube Shorts).

For each platform's NEWEST post in the feed:
  1. Source: the existing audio.wav + video.mp4 (talking head from Wav2Lip)
  2. Run reel_editor.py to produce a 9:16 reel with cuts/zooms/captions
  3. Save as `reel.mp4` next to `video.mp4`
  4. Back up the original talking-head as `video_talkinghead.mp4`
  5. Update manifest.json — `video.mp4` is now the reel; legacy is preserved

The website auto-picks up the new video.mp4 on next page load (no template change).

Usage:
    python virtuai/persona/scripts/render_reels_batch.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PERSONA = ROOT / "virtuai" / "persona"
DEMO_DIR = PERSONA / "demo"
REEL_EDITOR = PERSONA / "scripts" / "reel_editor.py"

VIDEO_PLATFORMS = ("tiktok", "instagram_reels", "youtube_shorts")


def newest_post_dir(platform: str) -> Path | None:
    feed = DEMO_DIR / platform / "feed"
    if not feed.is_dir():
        return None
    posts = sorted([p for p in feed.iterdir() if p.is_dir()], reverse=True)
    return posts[0] if posts else None


def render_reel_for_post(post_dir: Path) -> dict:
    audio = post_dir / "audio.wav"
    talking_head = post_dir / "video.mp4"
    if not audio.exists() or not talking_head.exists():
        return {"status": "skip", "reason": f"audio or video missing in {post_dir}"}

    backup = post_dir / "video_talkinghead.mp4"
    reel_out = post_dir / "video.mp4"  # we will overwrite

    # Back up the original talking-head if not already
    if not backup.exists() and talking_head.exists():
        shutil.copy2(talking_head, backup)

    # Render to a temp path, then move into place when done
    tmp_out = post_dir / "_reel_pending.mp4"
    cmd = [
        "/Users/karammufleh/virtuai-venv/bin/python",
        str(REEL_EDITOR),
        "--audio", str(talking_head.parent / "audio.wav"),
        "--talking-head", str(backup),  # always edit on the backup so we can re-run
        "--out", str(tmp_out),
    ]

    print(f"  rendering reel...")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    elapsed = time.time() - t0

    if proc.returncode != 0 or not tmp_out.exists():
        sys.stderr.write((proc.stdout or "")[-2000:])
        sys.stderr.write((proc.stderr or "")[-2000:])
        return {"status": "error", "elapsed_s": elapsed, "exit_code": proc.returncode}

    tmp_out.replace(reel_out)
    print(f"  ✓ {elapsed:.1f}s — reel.mp4 written ({reel_out.stat().st_size//1024} KB)")

    # Update per-post manifest with reel info
    mf_path = post_dir / "manifest.json"
    if mf_path.exists():
        mf = json.loads(mf_path.read_text(encoding="utf-8"))
        mf["video_path"] = str(reel_out.relative_to(ROOT))
        mf["video_render_model"] = "Reel editor: Whisper + ffmpeg (9:16, jump cuts, zoompan, captions, B-roll cutaways)"
        mf["video_talkinghead_path"] = str(backup.relative_to(ROOT))
        mf["video_render_time_s"] = round(elapsed, 1)
        mf_path.write_text(json.dumps(mf, indent=2), encoding="utf-8")

    return {"status": "ok", "elapsed_s": elapsed, "video_path": str(reel_out)}


def main() -> None:
    overall_t0 = time.time()
    results: dict[str, dict] = {}
    for platform in VIDEO_PLATFORMS:
        print(f"\n══════════════════════ {platform.upper()} ══════════════════════")
        post = newest_post_dir(platform)
        if post is None:
            print(f"  no posts found")
            results[platform] = {"status": "skip", "reason": "no posts"}
            continue
        print(f"  post: {post.name}")
        results[platform] = render_reel_for_post(post)

    summary_path = DEMO_DIR / "reel_batch_summary.json"
    summary_path.write_text(
        json.dumps(
            {"rendered_at": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results},
            indent=2,
        ),
        encoding="utf-8",
    )

    elapsed = time.time() - overall_t0
    print(f"\n✓ batch done in {elapsed/60:.1f} min")
    for plat, r in results.items():
        sym = "✓" if r.get("status") == "ok" else "✗"
        print(f"  {sym} {plat}: {r}")


if __name__ == "__main__":
    main()
