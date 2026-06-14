"""
migrate_to_feed.py — One-time migration of existing single-post demo content
into the new <platform>/feed/<post_id>/ layout.

Before:
    virtuai/persona/demo/<platform>/{text.md, image.png|audio.wav+video.mp4, manifest.json}

After:
    virtuai/persona/demo/<platform>/feed/<2026-04-27_HHMMSS>__<topic-slug>/{...same files...}
    plus the platform's manifest.json now lives at the top level just listing the feed entries.

Idempotent — skips platforms that already have a feed/ subdirectory.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEMO_DIR = ROOT / "virtuai" / "persona" / "demo"


def slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:max_len].strip("-") or "post"


def migrate_platform(platform_dir: Path) -> str:
    feed_dir = platform_dir / "feed"
    if feed_dir.exists() and any(feed_dir.iterdir()):
        return "skipped (feed already exists)"

    # Existing per-post files
    text_file = platform_dir / "text.md"
    audio_file = platform_dir / "audio.wav"
    video_file = platform_dir / "video.mp4"
    video_improved = platform_dir / "video_improved.mp4"
    image_file = platform_dir / "image.png"
    manifest_file = platform_dir / "manifest.json"

    if not text_file.exists() or not manifest_file.exists():
        return "skipped (no original post)"

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    topic = manifest.get("topic") or "post"

    # Use a deterministic timestamp based on the manifest mtime so re-runs are stable
    mtime = datetime.fromtimestamp(manifest_file.stat().st_mtime).strftime("%Y-%m-%d_%H%M%S")
    post_id = f"{mtime}__{slugify(topic)}"

    feed_dir.mkdir(parents=True, exist_ok=True)
    post_dir = feed_dir / post_id
    post_dir.mkdir(parents=True, exist_ok=True)

    moved = []
    for src in [text_file, audio_file, video_file, video_improved, image_file]:
        if src.exists():
            dst = post_dir / src.name
            shutil.move(str(src), str(dst))
            moved.append(src.name)

    # Build per-post manifest, keep platform-level manifest as a feed index
    post_manifest = dict(manifest)
    post_manifest["post_id"] = post_id
    post_manifest["created_at"] = mtime
    # Update relative paths to point inside the post dir
    for k in ("text_path", "audio_path", "video_path", "image_path"):
        if k in post_manifest:
            old = Path(post_manifest[k])
            post_manifest[k] = str((post_dir / old.name).relative_to(ROOT))
    (post_dir / "manifest.json").write_text(
        json.dumps(post_manifest, indent=2), encoding="utf-8"
    )

    # Replace the platform-level manifest with a feed index
    feed_index = {
        "platform": manifest.get("platform", platform_dir.name),
        "is_video": manifest.get("is_video", False),
        "format": manifest.get("format"),
        "feed": [post_id],
    }
    manifest_file.write_text(json.dumps(feed_index, indent=2), encoding="utf-8")

    return f"migrated → feed/{post_id} ({', '.join(moved)})"


def main() -> None:
    print(f"Migrating posts under {DEMO_DIR.relative_to(ROOT)}")
    if not DEMO_DIR.exists():
        print("  no demo dir, nothing to do")
        return
    for p in sorted(DEMO_DIR.iterdir()):
        if not p.is_dir() or p.name.startswith("_"):
            continue
        result = migrate_platform(p)
        print(f"  {p.name}: {result}")


if __name__ == "__main__":
    main()
