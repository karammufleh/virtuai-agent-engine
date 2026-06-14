#!/usr/bin/env python3
"""
publish_v16.py — Push the approved v16 reel to platforms.

Defaults to YouTube Shorts (most reliable for video upload — direct API,
not Composio wrapper). Optionally also: X/Twitter (text + video upload),
LinkedIn (text post with video link), Instagram (Reels via Composio).

Run modes:
  python scripts/publish_v16.py                # YouTube unlisted (safe default)
  python scripts/publish_v16.py --public       # YouTube public
  python scripts/publish_v16.py --x            # also tweet a link
  python scripts/publish_v16.py --linkedin     # also LinkedIn post
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from virtuai.tools import auth_guard

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("publish_v16")

VIDEO = ROOT / "virtuai/data/generated_videos/daniel_reel_v16_1778775252.mp4"
SCRIPT_JSON = sorted(
    (ROOT / "virtuai/data/scripts").glob("v16_*.json"),
    key=lambda p: p.stat().st_mtime,
)[-1]


# ── Caption assembly from the script ───────────────────────────────────────

def build_caption(script: dict) -> dict:
    """Return platform-specific captions derived from the script."""
    topic = script["topic"]
    hook = script["hook_summary"]
    # Stitch the full spoken script as the long-form body
    body = " ".join(s["audio_text"].strip() for s in script["scenes"])
    aphorism = script.get("loop_back_line") or ""

    hashtags = "#AI #automation #founder #saas #buildinpublic"

    short_caption = f"{hook}\n\n{body}\n\n— {aphorism}\n\n{hashtags}"

    return {
        "title": topic[:95],                       # YouTube title cap
        "youtube_description": short_caption[:5000],
        "instagram_caption": short_caption[:2200],
        "x_thread_open": hook[:280],
        "x_thread_body": body[:280] if len(body) <= 280 else None,
        "linkedin_post": short_caption[:3000],
    }


# ── YouTube Shorts (direct upload, most reliable) ───────────────────────────

def publish_youtube(video: Path, caption_pkg: dict, public: bool) -> dict:
    log.info("Uploading to YouTube Shorts...")
    from virtuai.tools.youtube_direct import upload_video

    # Shorts requires #Shorts in title or description and a ≤60s vertical video.
    title = caption_pkg["title"]
    if "#Shorts" not in title:
        title = (title[:90] + " #Shorts").strip()

    description = caption_pkg["youtube_description"]
    if "#Shorts" not in description:
        description = "#Shorts\n\n" + description

    result = upload_video(
        video_path=video,
        title=title,
        description=description,
        tags=["AI", "automation", "founder", "saas", "shorts"],
        category_id="22",
        privacy_status="public" if public else "unlisted",
        made_for_kids=False,
    )
    # youtube_direct wraps the API body inside data.response_data
    body = (result.get("data") or {}).get("response_data") or {}
    vid = body.get("id") or result.get("id") or result.get("videoId")
    url = f"https://youtube.com/shorts/{vid}" if vid else None
    log.info(f"  ✓ YouTube: {url}")
    return {"platform": "youtube", "url": url, "id": vid, "raw": result}


# ── X / Twitter (text post — video upload is complex via API) ───────────────

# ── Facebook Page reel ──────────────────────────────────────────────────────
# Replaces the old publish_x (X/Twitter dropped 2026-05-21).

def publish_facebook_reel(video_path: "Path", caption: str, title: str | None = None) -> dict:
    """Post a video as a Facebook Page reel via Composio
    FACEBOOK_CREATE_VIDEO_POST. Uploads to KIE's tempfile CDN first so
    Facebook can pull the mp4 via a public URL — same pattern as the IG
    reel flow."""
    auth_guard.gate("facebook")
    log.info("Posting to Facebook Page (video)...")
    from virtuai.tools.kie_upload import upload as kie_upload
    video_url = kie_upload(video_path)
    log.info(f"  Video hosted: {video_url}")

    from composio import Composio
    from composio_crewai import CrewAIProvider
    cp = Composio(provider=CrewAIProvider())
    tools = cp.tools.get(
        user_id=os.environ.get("COMPOSIO_USER_ID", "default"),
        toolkits=["facebook"],
    )
    tool = next((t for t in tools if t.name == "FACEBOOK_CREATE_VIDEO_POST"), None)
    if tool is None:
        err = RuntimeError("FACEBOOK_CREATE_VIDEO_POST tool not available — "
                           "is Composio FB connected for this user?")
        auth_guard.record("facebook", "FACEBOOK_CREATE_VIDEO_POST",
                          ok=False, error=err)
        raise err
    try:
        result = tool._run(
            page_id=os.environ["FB_PAGE_ID"],
            file_url=video_url,
            title=(title or caption.split("\n")[0])[:90],
            description=caption[:5000],
        )
    except Exception as e:
        auth_guard.record("facebook", "FACEBOOK_CREATE_VIDEO_POST",
                          ok=False, error=e)
        raise
    auth_guard.record("facebook", "FACEBOOK_CREATE_VIDEO_POST",
                      ok=True, extra={"caption_chars": len(caption)})
    log.info(f"  ✓ Facebook reel: {result}")
    return {"platform": "facebook", "kind": "video", "result": result}


# ── LinkedIn (text post with link) ──────────────────────────────────────────

def publish_instagram(video_path: Path, caption: str) -> dict:
    """Post a Reel: upload video publicly → create container → publish."""
    auth_guard.gate("instagram")
    log.info("Posting to Instagram (Reel)...")

    # Step 1: upload video to a public URL (Instagram fetches it)
    from virtuai.tools.kie_upload import upload as _kie_upload
    video_url = _kie_upload(video_path)
    log.info(f"  Video hosted: {video_url}")

    from composio import Composio
    from composio_crewai import CrewAIProvider
    cp = Composio(provider=CrewAIProvider())
    user_id = os.environ.get("COMPOSIO_USER_ID", "default")
    ig_user_id = os.environ.get("IG_USER_ID", "").strip()
    if not ig_user_id:
        err = RuntimeError("IG_USER_ID not set in .env")
        auth_guard.record("instagram", "INSTAGRAM_CREATE_MEDIA_CONTAINER",
                          ok=False, error=err)
        raise err

    # Step 2: create media container for the Reel
    try:
        container_tool = next(iter(cp.tools.get(
            user_id=user_id, tools=["INSTAGRAM_CREATE_MEDIA_CONTAINER"])))
        container_result = container_tool.run(
            ig_user_id=ig_user_id,
            video_url=video_url,
            media_type="REELS",
            caption=caption[:2200],
        )
    except Exception as e:
        auth_guard.record("instagram", "INSTAGRAM_CREATE_MEDIA_CONTAINER",
                          ok=False, error=e)
        raise
    log.info(f"  Container result: {container_result}")

    # Extract creation_id from a few possible shapes
    cdata = container_result.get("data") if isinstance(container_result, dict) else {}
    creation_id = (
        (cdata or {}).get("id")
        or (cdata or {}).get("creation_id")
        or container_result.get("id")
    )
    if not creation_id:
        err = RuntimeError(f"No creation_id in container response: {container_result}")
        auth_guard.record("instagram", "INSTAGRAM_CREATE_MEDIA_CONTAINER",
                          ok=False, error=err)
        raise err
    log.info(f"  creation_id: {creation_id}")

    # Step 3: wait briefly for IG to ingest the video, then publish
    import time as _time
    _time.sleep(20)

    try:
        publish_tool = next(iter(cp.tools.get(
            user_id=user_id, tools=["INSTAGRAM_CREATE_POST"])))
        publish_result = publish_tool.run(
            ig_user_id=ig_user_id,
            creation_id=creation_id,
        )
    except Exception as e:
        auth_guard.record("instagram", "INSTAGRAM_CREATE_POST",
                          ok=False, error=e)
        raise
    auth_guard.record("instagram", "INSTAGRAM_CREATE_POST",
                      ok=True, extra={"creation_id": creation_id})
    log.info(f"  ✓ Instagram: {publish_result}")
    return {"platform": "instagram", "container_id": creation_id, "result": publish_result}


def publish_linkedin(text: str, link: str | None = None) -> dict:
    auth_guard.gate("linkedin")
    log.info("Posting to LinkedIn...")
    from composio import Composio
    from composio_crewai import CrewAIProvider
    cp = Composio(provider=CrewAIProvider())
    user_id = os.environ.get("COMPOSIO_USER_ID", "default")

    try:
        info_tool = next(iter(cp.tools.get(user_id=user_id, tools=["LINKEDIN_GET_MY_INFO"])))
        me = info_tool.run()
    except Exception as e:
        auth_guard.record("linkedin", "LINKEDIN_GET_MY_INFO", ok=False, error=e)
        raise
    data = (me or {}).get("data") or {}
    li_id = data.get("id") or data.get("sub") or (me or {}).get("sub")
    author_urn = data.get("urn") or (f"urn:li:person:{li_id}" if li_id else None)
    if not author_urn:
        err = RuntimeError(f"Couldn't determine LinkedIn URN from: {me}")
        auth_guard.record("linkedin", "LINKEDIN_GET_MY_INFO", ok=False, error=err)
        raise err
    log.info(f"  LinkedIn author URN: {author_urn}")

    try:
        post_tool = next(iter(cp.tools.get(
            user_id=user_id, tools=["LINKEDIN_CREATE_LINKED_IN_POST"])))
        full_text = text if not link else f"{text}\n\n▶ {link}"
        result = post_tool.run(
            author=author_urn,
            commentary=full_text[:3000],
            visibility="PUBLIC",
            lifecycleState="PUBLISHED",
        )
    except Exception as e:
        auth_guard.record("linkedin", "LINKEDIN_CREATE_LINKED_IN_POST",
                          ok=False, error=e)
        raise
    auth_guard.record("linkedin", "LINKEDIN_CREATE_LINKED_IN_POST", ok=True)
    log.info(f"  ✓ LinkedIn: {result}")
    return {"platform": "linkedin", "result": result}


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--public", action="store_true",
                    help="YouTube public (default: unlisted)")
    ap.add_argument("--x", action="store_true",
                    help="Also post to X")
    ap.add_argument("--linkedin", action="store_true",
                    help="Also post to LinkedIn")
    ap.add_argument("--video",
                    default=str(VIDEO),
                    help=f"Path to video (default: {VIDEO.name})")
    ap.add_argument("--script",
                    default=str(SCRIPT_JSON),
                    help="Path to script JSON for caption")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.exists():
        sys.exit(f"Video not found: {video}")

    script = json.loads(Path(args.script).read_text())
    caption_pkg = build_caption(script)

    log.info("=" * 60)
    log.info("VirtuAI — Publishing v16 reel")
    log.info("=" * 60)
    log.info(f"Video:    {video.name}")
    log.info(f"Topic:    {script['topic']}")
    log.info(f"Hook:     {script['hook_summary']}")
    log.info(f"YT mode:  {'PUBLIC' if args.public else 'UNLISTED'}")
    log.info("=" * 60)

    posted = []

    # YouTube (always — primary host)
    yt = publish_youtube(video, caption_pkg, public=args.public)
    posted.append(yt)
    yt_url = yt.get("url")

    if args.x:
        try:
            posted.append(publish_x(caption_pkg["x_thread_open"], yt_url))
        except Exception as e:
            log.error(f"X failed: {e}")

    if args.linkedin:
        try:
            posted.append(publish_linkedin(caption_pkg["linkedin_post"], yt_url))
        except Exception as e:
            log.error(f"LinkedIn failed: {e}")

    log.info("=" * 60)
    log.info("PUBLISHED:")
    for p in posted:
        log.info(f"  - {p.get('platform')}: {p.get('url') or p.get('result', '(no url)')}")
    log.info("=" * 60)

    # Save published manifest
    out = ROOT / "virtuai/data/content_packages" / f"published_v16_{int(__import__('time').time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "video": str(video),
        "script_topic": script["topic"],
        "captions": caption_pkg,
        "results": posted,
    }, indent=2, default=str))
    log.info(f"Manifest saved: {out}")


if __name__ == "__main__":
    main()
