"""
scripts/publish_pack.py — Publish a single just-generated pack to Instagram.

Posts the reel + portrait + 5 individual carousel slides (page numbers
stripped, short IG-style captions). One post at a time with a short gap
between each to be polite to IG's rate limits.

Defaults are SAFE: prints a preview and exits unless you pass --live.
LinkedIn / YouTube are intentionally skipped — they have known auth issues
in this environment (no connected LinkedIn account; YouTube refresh token
expired). Only Instagram fires.

Usage:
    # Preview what would be posted, no API calls:
    python scripts/publish_pack.py virtuai/data/content_packages/daily_pack_1779278497.json

    # Go live:
    python scripts/publish_pack.py virtuai/data/content_packages/daily_pack_1779278497.json --live

    # Different gap between posts:
    python scripts/publish_pack.py <manifest> --live --gap-sec 60
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOG_PATH = Path("/tmp/virtuai_publish_pack.jsonl")
log = logging.getLogger("publish-pack")


# ────────────────────────────────────────────────────────────────────────────
# Caption builders — short, real-IG voice
# ────────────────────────────────────────────────────────────────────────────

def _tags(content: dict, n: int = 5) -> str:
    return " ".join(f"#{h.lstrip('#')}" for h in (content.get("hashtags") or [])[:n])


def reel_caption(reel_block: dict, manifest_ts: int | None = None) -> str:
    """Build a short reel IG caption.

    The manifest only stores `reel.topic` — the full script is in
    `virtuai/data/scripts/pack_reel_<ts>.json`. We find the latest one
    older than `manifest_ts` (or the newest overall if no ts) and pull
    `hook_summary` + the closing scene from it.
    """
    topic = (reel_block.get("topic") or "").strip()
    hook, closing = "", ""

    # Locate the matching reel script on disk
    scripts_dir = Path("virtuai/data/scripts")
    if scripts_dir.exists():
        candidates = sorted(scripts_dir.glob("pack_reel_*.json"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
        # Prefer the most recent script whose ts ≤ manifest_ts (if known)
        chosen = None
        for c in candidates:
            try:
                cts = int(c.stem.split("_")[-1])
            except ValueError:
                cts = 0
            if manifest_ts is None or cts <= manifest_ts:
                chosen = c
                break
        if chosen is None and candidates:
            chosen = candidates[0]
        if chosen:
            try:
                s = json.loads(chosen.read_text())
                hook = (s.get("hook_summary") or "").strip()
                scenes = s.get("scenes") or []
                if scenes and isinstance(scenes[-1], dict):
                    closing = (scenes[-1].get("audio_text") or "").strip()
            except Exception:
                pass

    fallback_tags = "#ai #automation #founders #buildinpublic #operators"
    body = hook or topic
    if closing and closing.lower() not in body.lower() and len(body) < 220:
        # Keep closing tight — first sentence only
        first_sentence = closing.split(". ")[0].rstrip(".") + "."
        body = f"{body}\n\n{first_sentence}"
    return f"{body}\n\n{fallback_tags}".strip()[:2200]


def portrait_caption_from_run(portrait_block: dict) -> str | None:
    """Prefer the already-built IG caption from captions.json in the run dir."""
    img = (portrait_block.get("asset") or {}).get("image")
    if not img:
        return None
    cap_path = Path(img).parent / "captions.json"
    if not cap_path.exists():
        return None
    try:
        caps = json.loads(cap_path.read_text())
        # The new short-form IG caption (from image_content_writer fix)
        return caps.get("instagram")
    except Exception:
        return None


def slide_caption(slide_data: dict, content: dict) -> str:
    """Short, role-aware IG caption for one carousel slide."""
    role     = (slide_data.get("role") or "").lower()
    headline = (slide_data.get("headline") or "").strip().strip('"')
    subhead  = (slide_data.get("subhead") or "").strip()
    hook     = (content.get("hook_summary") or "").strip()
    tags     = _tags(content)

    if role == "problem":
        body = f"{subhead}\n\nThe number that mattered: {headline}."
    elif role == "insight":
        body = f"{headline}\n\n{subhead}"
    elif role == "proof":
        body = f"{subhead}\n\n{headline} — that's the actual math."
    elif role == "payoff":
        body = f'"{headline}"\n\n{subhead}'
    else:  # cover / unknown
        body = f"{headline}\n\n{subhead}"

    extra = ""
    if hook and hook.lower() not in body.lower() and len(body) < 220:
        extra = f"\n\n— from: \"{hook}\""

    return f"{body}{extra}\n\n{tags}".strip()[:2200]


# ────────────────────────────────────────────────────────────────────────────
# Carousel slide re-render — strips the page-number badge
# ────────────────────────────────────────────────────────────────────────────

def re_render_slide_clean(bg_path: Path, headline: str, subhead: str,
                          out_path: Path,
                          handle: str = "@daniel.calder") -> Path:
    """Re-render a slide WITHOUT the top-right page-number chrome."""
    from virtuai.tools.slide_renderer import (
        _fit_canvas, _add_bottom_gradient_fast, _draw_text_block, _draw_chrome,
    )
    from PIL import Image
    bg = Image.open(bg_path).convert("RGB")
    canvas = _fit_canvas(bg)
    canvas = _add_bottom_gradient_fast(canvas)
    _draw_text_block(canvas, headline=headline, subhead=subhead)
    _draw_chrome(canvas, None, None, handle=handle)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


# ────────────────────────────────────────────────────────────────────────────
# Queue builder — one entry per IG post
# ────────────────────────────────────────────────────────────────────────────

def build_queue(manifest: dict) -> list[dict]:
    queue: list[dict] = []

    # 1. REEL
    reel = manifest.get("reel") or {}
    asset = reel.get("asset") or {}
    reel_path = asset.get("video_ig") or asset.get("video_master")
    if reel_path and Path(reel_path).exists():
        queue.append({
            "kind":    "reel",
            "path":    reel_path,
            "caption": reel_caption(reel, manifest_ts=manifest.get("ts")),
        })

    # 2. PORTRAIT
    portrait = manifest.get("portrait") or {}
    asset = portrait.get("asset") or {}
    p_path = asset.get("image")
    if p_path and Path(p_path).exists():
        cap = portrait_caption_from_run(portrait) or "—"
        queue.append({
            "kind":    "portrait",
            "path":    p_path,
            "caption": cap,
        })

    # 3. CAROUSEL — 5 individual slides
    car = manifest.get("carousel") or {}
    asset = car.get("asset") or {}
    slides = asset.get("slides") or []
    if slides:
        run_dir = Path(slides[0]).parent
        content_path = run_dir / "content.json"
        if not content_path.exists():
            log.warning(f"carousel skipped — no content.json at {content_path}")
        else:
            content = json.loads(content_path.read_text())
            for i, slide_path in enumerate(slides, start=1):
                slide_data = content["slides"][i - 1]
                bg_path = run_dir / "_bg" / f"slide_{i}_bg.png"
                clean_out = run_dir / f"slide_{i:02d}_clean.png"
                if not bg_path.exists():
                    log.warning(f"slide {i} skipped — no bg at {bg_path}")
                    continue
                re_render_slide_clean(bg_path,
                                       headline=slide_data["headline"],
                                       subhead=slide_data["subhead"],
                                       out_path=clean_out)
                queue.append({
                    "kind":    f"slide_{i}",
                    "path":    str(clean_out),
                    "caption": slide_caption(slide_data, content),
                })
    return queue


# ────────────────────────────────────────────────────────────────────────────
# Posting
# ────────────────────────────────────────────────────────────────────────────

def post_to_ig(kind: str, path: Path, caption: str) -> dict:
    """Post via the underlying plain-function helpers (not the @tool-wrapped
    versions in cloud_tools.py)."""
    if kind == "reel":
        from scripts.publish_v16 import publish_instagram
        return publish_instagram(Path(path), caption)
    # everything else is a single image
    from scripts.publish_images import publish_ig_single
    return publish_ig_single(Path(path), caption)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("manifest", type=Path,
                   help="content_packages/daily_pack_<ts>.json")
    p.add_argument("--live", action="store_true",
                   help="Actually publish. Without this, dry-run only.")
    p.add_argument("--gap-sec", type=int, default=30,
                   help="Seconds between IG posts (default 30).")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s [%(levelname)s] %(message)s")

    if not args.manifest.exists():
        log.error(f"manifest not found: {args.manifest}")
        return 2
    manifest = json.loads(args.manifest.read_text())
    queue = build_queue(manifest)

    log.info("─" * 60)
    log.info(f"publish-pack  mode={'LIVE' if args.live else 'DRY-RUN'}  "
             f"gap={args.gap_sec}s  queue={len(queue)}")
    log.info("─" * 60)

    for i, item in enumerate(queue, start=1):
        cap_preview = (item["caption"] or "").replace("\n", " ")[:80]
        log.info(f"  {i}/{len(queue)}  {item['kind']:10}  {item['path']}")
        log.info(f"           caption ({len(item['caption'])} chars): \"{cap_preview}…\"")

    if not args.live:
        log.info("\n(dry-run — no API calls made. add --live to actually publish.)")
        return 0

    for i, item in enumerate(queue, start=1):
        if i > 1:
            log.info(f"sleeping {args.gap_sec}s before post {i}/{len(queue)}…")
            time.sleep(args.gap_sec)
        log.info(f"[{i}/{len(queue)}] posting {item['kind']} → IG …")
        try:
            r = post_to_ig(item["kind"], Path(item["path"]), item["caption"])
            log.info(f"[{i}/{len(queue)}] ✓ result: {str(r)[:160]}")
        except Exception as e:
            log.error(f"[{i}/{len(queue)}] ✗ failed: {e}")
            r = {"error": str(e)}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": time.time(),
                "kind": item["kind"],
                "path": item["path"],
                "caption": item["caption"],
                "result": r,
            }, default=str) + "\n")
    log.info("─" * 60)
    log.info(f"published {len(queue)} items. manifest: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
