"""
ig_carousel.py — Direct Meta Graph API carousel publisher.

WHY: Composio's `INSTAGRAM_CREATE_MEDIA_CONTAINER` wrapper hardcodes
`image_url` as required, even for `media_type=CAROUSEL` parents — but
Meta's actual API explicitly accepts the parent CAROUSEL container
*without* an image. This module bypasses Composio for the parent call
only, so we can publish proper 5-slide swipe carousels.

REQUIREMENTS:
  - .env: IG_USER_ID (already present)
  - .env: IG_ACCESS_TOKEN — a long-lived Instagram Graph access token.
    Get it from Composio dashboard → Connected Accounts → Instagram →
    Access Token, OR from Meta for Developers → your app → Tools →
    Graph API Explorer.

Behavior:
  - If IG_ACCESS_TOKEN is set → full 5-slide swipe carousel.
  - If not set → returns a `needs_token` marker so the caller falls
    back to publishing the cover slide as a single image.

Public:
  publish_carousel(slide_paths, caption) -> dict
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
logger = logging.getLogger("virtuai.tools.ig_carousel")

from virtuai.tools import auth_guard

GRAPH_BASE = "https://graph.facebook.com/v19.0"
IG_USER_ID = os.environ.get("IG_USER_ID", "").strip()
IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()

POLL_INTERVAL = 5
POLL_TIMEOUT = 180


def is_configured() -> bool:
    return bool(IG_USER_ID and IG_ACCESS_TOKEN)


def _create_child_container(image_url: str) -> str:
    """Create an Instagram child container for a single carousel image."""
    r = httpx.post(
        f"{GRAPH_BASE}/{IG_USER_ID}/media",
        params={"access_token": IG_ACCESS_TOKEN},
        data={"image_url": image_url, "is_carousel_item": "true"},
        timeout=60,
    )
    r.raise_for_status()
    body = r.json()
    if "id" not in body:
        raise RuntimeError(f"child container failed: {body}")
    return body["id"]


def _create_parent_container(child_ids: list[str], caption: str) -> str:
    r = httpx.post(
        f"{GRAPH_BASE}/{IG_USER_ID}/media",
        params={"access_token": IG_ACCESS_TOKEN},
        data={
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption[:2200],
        },
        timeout=60,
    )
    r.raise_for_status()
    body = r.json()
    if "id" not in body:
        raise RuntimeError(f"parent container failed: {body}")
    return body["id"]


def _wait_finished(creation_id: str) -> bool:
    """Poll the container until status_code == FINISHED (or fail/timeout)."""
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        r = httpx.get(
            f"{GRAPH_BASE}/{creation_id}",
            params={"fields": "status_code", "access_token": IG_ACCESS_TOKEN},
            timeout=30,
        )
        try:
            r.raise_for_status()
            status = r.json().get("status_code", "UNKNOWN")
        except Exception as e:
            logger.warning(f"poll {creation_id}: {e}")
            status = "ERROR"
        logger.info(f"  container {creation_id}: {status}")
        if status == "FINISHED":
            return True
        if status == "ERROR" or status == "EXPIRED":
            return False
        time.sleep(POLL_INTERVAL)
    return False


def _publish(creation_id: str) -> dict:
    r = httpx.post(
        f"{GRAPH_BASE}/{IG_USER_ID}/media_publish",
        params={"access_token": IG_ACCESS_TOKEN},
        data={"creation_id": creation_id},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def publish_carousel(slide_paths: list[Path], caption: str) -> dict:
    """
    Publish a 2-10 slide carousel to Instagram.

    Args:
        slide_paths: 2-10 local PNGs (the slide_renderer output).
        caption: full caption text (≤ 2200 chars).

    Returns:
        {"ok": True, "media_id": "...", "permalink": "...", ...}
        OR {"ok": False, "reason": "needs_token" | "<error>"}
    """
    if not is_configured():
        return {"ok": False, "reason": "needs_token",
                "hint": "Set IG_ACCESS_TOKEN in .env to enable true carousel"}
    if not (2 <= len(slide_paths) <= 10):
        return {"ok": False, "reason": "need 2-10 slides"}

    # auth_guard: halt if IG circuit is open (e.g. token rot).
    auth_guard.gate("instagram")

    # Upload each slide to KIE's CDN so Meta can fetch them
    from virtuai.tools.kie_upload import upload as kie_upload
    image_urls = []
    for p in slide_paths:
        url = kie_upload(p)
        logger.info(f"slide hosted: {p.name} → {url}")
        image_urls.append(url)

    # Create child containers
    try:
        child_ids = []
        for url in image_urls:
            cid = _create_child_container(url)
            child_ids.append(cid)
            logger.info(f"child container: {cid}")

        # Create parent CAROUSEL container
        parent_id = _create_parent_container(child_ids, caption)
        logger.info(f"parent container: {parent_id}")
    except httpx.HTTPStatusError as e:
        auth_guard.record("instagram", "IG_GRAPH_CAROUSEL_CONTAINER",
                          ok=False, status_code=e.response.status_code, error=e)
        raise
    except Exception as e:
        auth_guard.record("instagram", "IG_GRAPH_CAROUSEL_CONTAINER",
                          ok=False, error=e)
        raise

    # Wait for Meta to ingest
    if not _wait_finished(parent_id):
        err = RuntimeError("container did not reach FINISHED")
        auth_guard.record("instagram", "IG_GRAPH_CAROUSEL_POLL",
                          ok=False, error=err)
        return {"ok": False, "reason": "container did not reach FINISHED"}

    # Publish
    try:
        result = _publish(parent_id)
    except httpx.HTTPStatusError as e:
        auth_guard.record("instagram", "IG_GRAPH_MEDIA_PUBLISH",
                          ok=False, status_code=e.response.status_code, error=e)
        raise
    except Exception as e:
        auth_guard.record("instagram", "IG_GRAPH_MEDIA_PUBLISH",
                          ok=False, error=e)
        raise
    media_id = result.get("id", "")
    auth_guard.record("instagram", "IG_GRAPH_MEDIA_PUBLISH",
                      ok=True, extra={"media_id": media_id,
                                      "slide_count": len(slide_paths)})

    # Fetch permalink for convenience
    permalink = ""
    try:
        r = httpx.get(
            f"{GRAPH_BASE}/{media_id}",
            params={"fields": "permalink", "access_token": IG_ACCESS_TOKEN},
            timeout=30,
        )
        r.raise_for_status()
        permalink = r.json().get("permalink", "")
    except Exception:
        pass

    return {
        "ok": True,
        "media_id": media_id,
        "permalink": permalink,
        "parent_container": parent_id,
        "children": child_ids,
    }


if __name__ == "__main__":
    import sys
    import json as _json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if len(sys.argv) < 3:
        print("usage: python -m virtuai.tools.ig_carousel <run_dir> <caption>")
        sys.exit(1)
    run_dir = Path(sys.argv[1])
    caption_path = Path(sys.argv[2])
    caption = caption_path.read_text() if caption_path.exists() else sys.argv[2]
    slides = sorted(run_dir.glob("slide_*.png"))
    slides = [s for s in slides if "_bg" not in s.name]
    print(_json.dumps(publish_carousel(slides, caption), indent=2, default=str))
