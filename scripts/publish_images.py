#!/usr/bin/env python3
"""
publish_images.py — Push carousel and/or portrait posts to platforms.

Usage:
  python scripts/publish_images.py --run-dir virtuai/data/generated_images/posts/carousel_1778778079
  python scripts/publish_images.py --run-dir virtuai/data/generated_images/posts/portrait_...

Auto-detects portrait vs carousel from the run_dir contents.
Publishes:
  - Instagram (carousel via INSTAGRAM_CREATE_MEDIA_CONTAINER + INSTAGRAM_CREATE_POST,
    or single image)
  - LinkedIn (single image or first slide as cover)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from virtuai.tools import auth_guard

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("publish_images")


def upload_public(filepath: Path) -> str:
    """Now backed by KIE's file-stream-upload (more reliable)."""
    from virtuai.tools.kie_upload import upload as _kie_upload
    return _kie_upload(filepath)


def _composio_user():
    return os.environ.get("COMPOSIO_USER_ID", "default")


def _ig_user():
    uid = os.environ.get("IG_USER_ID", "").strip()
    if not uid:
        raise RuntimeError("IG_USER_ID not set in .env")
    return uid


def _cp_tools(*tool_slugs: str):
    from composio import Composio
    from composio_crewai import CrewAIProvider
    cp = Composio(provider=CrewAIProvider())
    return list(cp.tools.get(user_id=_composio_user(), tools=list(tool_slugs)))


# ── Instagram ───────────────────────────────────────────────────────────────

def publish_ig_single(image_path: Path, caption: str) -> dict:
    auth_guard.gate("instagram")
    log.info("Instagram single image post...")
    image_url = upload_public(image_path)
    log.info(f"  hosted: {image_url}")

    try:
        container_tool, publish_tool = _cp_tools(
            "INSTAGRAM_CREATE_MEDIA_CONTAINER", "INSTAGRAM_CREATE_POST"
        )
        container = container_tool.run(
            ig_user_id=_ig_user(),
            image_url=image_url,
            caption=caption[:2200],
        )
    except Exception as e:
        auth_guard.record("instagram", "INSTAGRAM_CREATE_MEDIA_CONTAINER",
                          ok=False, error=e)
        raise
    cdata = container.get("data") if isinstance(container, dict) else {}
    creation_id = (cdata or {}).get("id") or container.get("id")
    if not creation_id:
        err = RuntimeError(f"no creation_id: {container}")
        auth_guard.record("instagram", "INSTAGRAM_CREATE_MEDIA_CONTAINER",
                          ok=False, error=err)
        raise err
    time.sleep(15)
    try:
        result = publish_tool.run(ig_user_id=_ig_user(), creation_id=creation_id)
    except Exception as e:
        auth_guard.record("instagram", "INSTAGRAM_CREATE_POST",
                          ok=False, error=e)
        raise
    auth_guard.record("instagram", "INSTAGRAM_CREATE_POST",
                      ok=True, extra={"kind": "single"})
    log.info(f"  ✓ IG single: {result}")
    return {"platform": "instagram", "kind": "single", "result": result}


def publish_ig_carousel(slide_paths: list[Path], caption: str) -> dict:
    """
    True 5-slide Instagram carousel.

    Strategy:
      1. If IG_ACCESS_TOKEN is set, use the direct Meta Graph API
         (virtuai.tools.ig_carousel) — full swipe-through carousel.
      2. Else fall back to publishing the cover slide as a single image
         (Composio wrapper rejects the carousel parent).
    """
    from virtuai.tools.ig_carousel import publish_carousel, is_configured

    if is_configured():
        log.info(f"Instagram CAROUSEL via direct Meta API ({len(slide_paths)} slides)...")
        result = publish_carousel(slide_paths, caption)
        if result.get("ok"):
            return {"platform": "instagram", "kind": "carousel",
                    "media_id": result["media_id"],
                    "permalink": result.get("permalink"),
                    "result": result}
        log.warning(f"Direct carousel failed ({result.get('reason')}); "
                     f"falling back to cover-only post")

    # Fallback: single-image post of slide 1 (cover)
    log.info("Instagram carousel — fallback: cover slide as single image")
    return publish_ig_single(slide_paths[0], caption)


# ── LinkedIn ────────────────────────────────────────────────────────────────

def publish_linkedin_with_image(cover_image: Path, text: str) -> dict:
    auth_guard.gate("linkedin")
    log.info("LinkedIn post with image...")
    try:
        info_tool = _cp_tools("LINKEDIN_GET_MY_INFO")[0]
        me = info_tool.run()
    except Exception as e:
        auth_guard.record("linkedin", "LINKEDIN_GET_MY_INFO", ok=False, error=e)
        raise
    data = (me or {}).get("data") or {}
    li_id = data.get("id") or data.get("sub") or (me or {}).get("sub")
    author_urn = data.get("urn") or (f"urn:li:person:{li_id}" if li_id else None)
    if not author_urn:
        err = RuntimeError(f"no LinkedIn URN: {me}")
        auth_guard.record("linkedin", "LINKEDIN_GET_MY_INFO", ok=False, error=err)
        raise err
    log.info(f"  URN: {author_urn}")

    image_url = upload_public(cover_image)
    log.info(f"  cover hosted: {image_url}")

    post_tool = _cp_tools("LINKEDIN_CREATE_LINKED_IN_POST")[0]
    # Composio expects images as an array of objects; let's try common shapes.
    try:
        result = post_tool.run(
            author=author_urn,
            commentary=text[:3000],
            visibility="PUBLIC",
            lifecycleState="PUBLISHED",
            images=[{"url": image_url, "alt_text": text[:120]}],
        )
    except Exception as e1:
        log.warning(f"  with images[] failed: {e1}; trying without image")
        try:
            result = post_tool.run(
                author=author_urn,
                commentary=text[:3000],
                visibility="PUBLIC",
                lifecycleState="PUBLISHED",
            )
        except Exception as e2:
            auth_guard.record("linkedin", "LINKEDIN_CREATE_LINKED_IN_POST",
                              ok=False, error=e2)
            raise
    auth_guard.record("linkedin", "LINKEDIN_CREATE_LINKED_IN_POST", ok=True)
    log.info(f"  ✓ LinkedIn: {result}")
    return {"platform": "linkedin", "result": result}


# ── Facebook Page image ────────────────────────────────────────────────────
# Added 2026-05-21 when X was replaced with Facebook as the 4th platform.

def publish_facebook_image(image_path: Path, caption: str) -> dict:
    """Post a single image to a Facebook Page via Composio
    FACEBOOK_CREATE_PHOTO_POST. Uploads the image to KIE's CDN first so
    FB can fetch it via a public URL — same pattern as IG single."""
    auth_guard.gate("facebook")
    log.info("Facebook Page photo post...")
    image_url = upload_public(image_path)
    log.info(f"  hosted: {image_url}")

    from composio import Composio
    from composio_crewai import CrewAIProvider
    cp = Composio(provider=CrewAIProvider())
    tools = cp.tools.get(
        user_id=os.environ.get("COMPOSIO_USER_ID", "default"),
        toolkits=["facebook"],
    )
    tool = next((t for t in tools if t.name == "FACEBOOK_CREATE_PHOTO_POST"), None)
    if tool is None:
        err = RuntimeError("FACEBOOK_CREATE_PHOTO_POST not available — "
                           "is Composio FB connected for this user?")
        auth_guard.record("facebook", "FACEBOOK_CREATE_PHOTO_POST",
                          ok=False, error=err)
        raise err
    try:
        result = tool._run(
            page_id=os.environ["FB_PAGE_ID"],
            url=image_url,
            message=caption[:5000],
        )
    except Exception as e:
        auth_guard.record("facebook", "FACEBOOK_CREATE_PHOTO_POST",
                          ok=False, error=e)
        raise
    auth_guard.record("facebook", "FACEBOOK_CREATE_PHOTO_POST", ok=True)
    log.info(f"  ✓ Facebook photo: {result}")
    return {"platform": "facebook", "kind": "photo", "result": result}


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--skip-ig", action="store_true")
    ap.add_argument("--skip-linkedin", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    content = json.loads((run_dir / "content.json").read_text())
    captions = json.loads((run_dir / "captions.json").read_text())

    log.info("=" * 60)
    log.info(f"Publishing {content['type']} from {run_dir}")
    log.info(f"Topic: {content['topic']}")
    log.info("=" * 60)

    results = {}

    if content["type"] == "carousel_5":
        slide_paths = sorted(run_dir.glob("slide_*.png"))
        slide_paths = [p for p in slide_paths if "_bg" not in p.name]
        log.info(f"Found {len(slide_paths)} slide files")

        if not args.skip_ig:
            try:
                results["instagram"] = publish_ig_carousel(slide_paths, captions["instagram"])
            except Exception as e:
                log.error(f"IG failed: {e}")
                results["instagram"] = {"error": str(e)}

        if not args.skip_linkedin:
            try:
                results["linkedin"] = publish_linkedin_with_image(slide_paths[0], captions["linkedin"])
            except Exception as e:
                log.error(f"LinkedIn failed: {e}")
                results["linkedin"] = {"error": str(e)}

    elif content["type"] == "portrait_quote":
        portrait_path = run_dir / "portrait.png"
        if not args.skip_ig:
            try:
                results["instagram"] = publish_ig_single(portrait_path, captions["instagram"])
            except Exception as e:
                log.error(f"IG failed: {e}")
                results["instagram"] = {"error": str(e)}
        if not args.skip_linkedin:
            try:
                results["linkedin"] = publish_linkedin_with_image(portrait_path, captions["linkedin"])
            except Exception as e:
                log.error(f"LinkedIn failed: {e}")
                results["linkedin"] = {"error": str(e)}

    out = run_dir / f"published_{int(time.time())}.json"
    out.write_text(json.dumps(results, indent=2, default=str))

    log.info("=" * 60)
    log.info("PUBLISHED:")
    for k, v in results.items():
        log.info(f"  {k}: {v}")
    log.info(f"Manifest: {out}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
