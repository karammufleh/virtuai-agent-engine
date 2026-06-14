"""
scripts/post_inventory.py — Clear the unposted KIE-generated inventory.

For every carousel slide that's on disk but not yet on Instagram:
  1. Re-render WITHOUT the page-number badge (using the saved Nano Banana
     background under `_bg/slide_N_bg.png`).
  2. Build a short, role-appropriate IG caption from the slide's role +
     headline + subhead + the carousel's hashtags.
  3. Publish it to Instagram via the existing Composio helper
     (`publish_ig_single`).
  4. Sleep `--interval-sec` (default 3600 = 1 hour) before the next post.
  5. Log each result to a JSONL manifest.

Reels and portraits that haven't shipped to IG also enter the queue but
the current inventory has zero of those.

Defaults are SAFE: `--dry-run` mode is the default. Pass `--live` to
actually publish.

Usage:
    # Show the full queue without posting anything
    python scripts/post_inventory.py --dry-run

    # Go live — post one every hour
    python scripts/post_inventory.py --live

    # Faster pace for testing (e.g., 30 seconds between posts)
    python scripts/post_inventory.py --live --interval-sec 30

    # Stop the running queue at any time:
    pkill -f scripts/post_inventory.py
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

LOG_PATH = Path("/tmp/virtuai_post_inventory.jsonl")
MANIFEST_PATH = Path("/tmp/virtuai_inventory.json")
log = logging.getLogger("post-inventory")


# ────────────────────────────────────────────────────────────────────────────
# Slide re-render — strips the "01 / 05" page-number badge
# ────────────────────────────────────────────────────────────────────────────

def re_render_slide_clean(bg_path: Path, headline: str, subhead: str,
                          out_path: Path,
                          handle: str = "@daniel.calder") -> Path:
    """Composite a slide exactly like `render_slide()` but without the
    top-right page-number badge. The bottom-left handle stays."""
    from virtuai.tools.slide_renderer import (
        _fit_canvas, _add_bottom_gradient_fast, _draw_text_block, _draw_chrome,
    )
    from PIL import Image
    bg = Image.open(bg_path).convert("RGB")
    canvas = _fit_canvas(bg)
    canvas = _add_bottom_gradient_fast(canvas)
    _draw_text_block(canvas, headline=headline, subhead=subhead)
    # `_draw_chrome` skips the page badge when both index and total are None.
    _draw_chrome(canvas, None, None, handle=handle)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


# ────────────────────────────────────────────────────────────────────────────
# Per-slide caption builder — short, IG-style, no essay
# ────────────────────────────────────────────────────────────────────────────

def _tags(carousel_content: dict, max_tags: int = 5) -> str:
    raw = (carousel_content.get("hashtags") or [])[:max_tags]
    return " ".join(f"#{h.lstrip('#')}" for h in raw)


def caption_for_slide(slide_data: dict, carousel_content: dict) -> str:
    """
    Compose a short IG caption (≈ 200-400 chars) using the slide's role.
    Carousels emit roles cover/problem/insight/proof/payoff — we tune the
    voice slightly to each.
    """
    role     = (slide_data.get("role") or "").lower()
    headline = (slide_data.get("headline") or "").strip().strip('"')
    subhead  = (slide_data.get("subhead") or "").strip()
    hook     = (carousel_content.get("hook_summary") or "").strip()
    tags     = _tags(carousel_content)

    # Voice — one short body, blank line, hashtags. No emoji, no "follow me".
    if role == "problem":
        body = f"{subhead}\n\nThe number that mattered: {headline}."
    elif role == "insight":
        # Insight slides typically have a punchy short headline = the reframe.
        body = f"{headline}\n\n{subhead}"
    elif role == "proof":
        body = f"{subhead}\n\n{headline} — that's the actual math."
    elif role == "payoff":
        # Slide 5 is the aphorism — quote it and add a save line.
        body = f'"{headline}"\n\n{subhead}'
    else:
        # Fallback (cover or unknown role).
        body = f"{headline}\n\n{subhead}"

    # Optional context from the carousel hook if it isn't already in the body.
    extra = ""
    if hook and hook.lower() not in body.lower() and len(body) < 220:
        extra = f"\n\n— from: \"{hook}\""

    caption = f"{body}{extra}\n\n{tags}".strip()
    return caption[:2200]


# ────────────────────────────────────────────────────────────────────────────
# Inventory → queue
# ────────────────────────────────────────────────────────────────────────────

def build_queue(inventory: dict) -> list[dict]:
    """One queue entry per asset to post."""
    queue: list[dict] = []
    for s in inventory.get("slides", []):
        original = Path(s["image"])
        run_dir = original.parent
        bg_path = run_dir / "_bg" / f"slide_{s['slide_index']}_bg.png"
        clean_out = run_dir / f"slide_{s['slide_index']:02d}_clean.png"
        content_path = run_dir / "content.json"
        if not bg_path.exists():
            log.warning(f"skip (no bg): {bg_path}")
            continue
        if not content_path.exists():
            log.warning(f"skip (no content.json): {content_path}")
            continue
        queue.append({
            "kind":          "slide",
            "slide_index":   s["slide_index"],
            "of_total":      s.get("of_total", 5),
            "pack_ts":       s.get("pack_ts"),
            "topic":         s.get("topic"),
            "original":      str(original),
            "bg":            str(bg_path),
            "out":           str(clean_out),
            "content_path":  str(content_path),
        })
    # Reels + portraits inventory entries would be appended here if any
    # — current inventory shows 0, so the structure stays slide-only today.
    return queue


# ────────────────────────────────────────────────────────────────────────────
# Posting helpers
# ────────────────────────────────────────────────────────────────────────────

def post_slide_live(image_path: Path, caption: str) -> dict:
    """Publish a single image to Instagram via existing Composio helper."""
    # Lazy import — Composio may need to read env. We must NOT import this
    # during dry-run because Composio may pre-validate the connection.
    from scripts.publish_images import publish_ig_single
    return publish_ig_single(Path(image_path), caption)


# ────────────────────────────────────────────────────────────────────────────
# Main loop
# ────────────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--inventory", type=Path, default=MANIFEST_PATH,
                   help="Inventory JSON produced by the scanner.")
    p.add_argument("--interval-sec", type=int, default=3600,
                   help="Seconds between posts (default 3600 = 1 hour).")
    p.add_argument("--live", action="store_true",
                   help="Actually publish. Without this flag, runs DRY-RUN.")
    p.add_argument("--limit", type=int, default=0,
                   help="Optional cap on the number of posts. 0 = no cap.")
    p.add_argument("--log-file", type=Path, default=LOG_PATH,
                   help="Per-post JSONL log path.")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if not args.inventory.exists():
        log.error(f"inventory missing: {args.inventory}")
        return 2
    inventory = json.loads(args.inventory.read_text())
    queue = build_queue(inventory)
    if args.limit:
        queue = queue[: args.limit]

    mode = "LIVE PUBLISH" if args.live else "DRY-RUN (no API calls)"
    log.info("─" * 60)
    log.info(f"VirtuAI inventory poster — {mode}")
    log.info(f"queue size: {len(queue)}   interval: {args.interval_sec}s   log: {args.log_file}")
    log.info("─" * 60)

    # Step 1 — re-render all slides locally up-front (no network, no credits)
    # so we can show a complete preview before going live.
    for item in queue:
        try:
            content = json.loads(Path(item["content_path"]).read_text())
            slide_data = content["slides"][item["slide_index"] - 1]
            re_render_slide_clean(
                bg_path=Path(item["bg"]),
                headline=slide_data["headline"],
                subhead=slide_data["subhead"],
                out_path=Path(item["out"]),
            )
            item["caption"] = caption_for_slide(slide_data, content)
        except Exception as e:
            log.error(f"prep failed for {item['out']}: {e}")
            item["caption"] = ""
            item["prep_error"] = str(e)

    # Step 2 — preview
    log.info("queue preview:")
    for i, item in enumerate(queue, 1):
        cap_preview = (item.get("caption", "") or "").replace("\n", " ")[:80]
        log.info(f"  {i:2}. slide {item['slide_index']}/{item['of_total']}  "
                 f"topic={(item.get('topic') or '')[:42]:<42}  → {item['out'].split('/')[-1]}")
        log.info(f"      caption: \"{cap_preview}…\" ({len(item.get('caption',''))} chars)")

    # Step 3 — post (or skip in dry-run)
    for i, item in enumerate(queue, 1):
        if i > 1 and args.live:
            log.info(f"sleeping {args.interval_sec}s before post {i}/{len(queue)}…")
            time.sleep(args.interval_sec)

        result: dict
        if not args.live:
            result = {"mode": "dry_run", "would_post": item["out"],
                      "caption_chars": len(item.get("caption", ""))}
            log.info(f"[{i}/{len(queue)}] DRY-RUN slide {item['slide_index']}")
        else:
            log.info(f"[{i}/{len(queue)}] POSTING slide {item['slide_index']} → IG …")
            try:
                result = post_slide_live(Path(item["out"]), item["caption"])
                log.info(f"[{i}/{len(queue)}] ✓ posted: {result}")
            except Exception as e:
                log.error(f"[{i}/{len(queue)}] ✗ post failed: {e}")
                result = {"error": str(e)}

        with args.log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts":           time.time(),
                "pack_ts":      item.get("pack_ts"),
                "slide_index":  item["slide_index"],
                "image":        item["out"],
                "caption":      item.get("caption", ""),
                "result":       result,
                "mode":         "live" if args.live else "dry_run",
            }, default=str) + "\n")

    log.info("─" * 60)
    log.info(f"queue complete ({len(queue)} posts). manifest: {args.log_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
