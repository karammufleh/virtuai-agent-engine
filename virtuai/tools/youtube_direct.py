"""
youtube_direct.py — Direct YouTube Data API v3 video upload, bypassing
Composio's thin upload wrapper.

Why this exists:
  Composio's YOUTUBE_UPLOAD_VIDEO action only exposes 6 fields (title,
  description, tags, categoryId, privacyStatus, videoFilePath). It does NOT
  expose `selfDeclaredMadeForKids`, which YouTube made REQUIRED under COPPA
  in 2020. Without it, API uploads succeed (HTTP 200, video resource is
  created) but YouTube's processor silently abandons them — Studio shows
  "Processing abandoned, the video could not be processed."

  Manual web-UI uploads succeed because the UI forces the "Made for kids?"
  question through the wizard. Composio's wrapper omits the field. We
  ALSO can't pull Composio's access_token out — they redact it in their
  SDK output as a security feature. So we BYO OAuth: our own GCP project,
  our own OAuth client, our own refresh_token in .env.

How it works:
  1. Read YOUTUBE_OAUTH_{CLIENT_ID,CLIENT_SECRET,REFRESH_TOKEN} from .env
     (populated once by virtuai/persona/scripts/youtube_oauth_setup.py).
  2. Trade the refresh_token for a fresh access_token at Google's
     oauth2.googleapis.com/token endpoint.
  3. Call YouTube Data API v3 videos.insert directly using the resumable
     upload protocol with `selfDeclaredMadeForKids: False` in the resource.
  4. Return the same shape Composio returns so callers don't care which
     path was used.

Refresh tokens don't expire unless idle 6 months or password changes — so
this is one-time setup; every future run reads from .env automatically.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from virtuai.tools import auth_guard

# Load .env at import time so callers don't have to.
load_dotenv()

logger = logging.getLogger("virtuai.tools.youtube_direct")

YOUTUBE_UPLOAD_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&part=snippet,status"
)
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _get_youtube_access_token() -> str:
    """
    Trade the long-lived refresh_token (stored in .env) for a short-lived
    access_token. Google's refresh tokens don't expire unless the user
    revokes access or doesn't use them for 6 months.

    Raises a clear RuntimeError if any of the three required env vars
    are missing — points the user at the one-time setup script.
    """
    client_id = os.environ.get("YOUTUBE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("YOUTUBE_OAUTH_REFRESH_TOKEN", "").strip()
    missing = [
        name for name, value in [
            ("YOUTUBE_OAUTH_CLIENT_ID", client_id),
            ("YOUTUBE_OAUTH_CLIENT_SECRET", client_secret),
            ("YOUTUBE_OAUTH_REFRESH_TOKEN", refresh_token),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing YouTube OAuth env vars: {', '.join(missing)}. "
            "Run virtuai/persona/scripts/youtube_oauth_setup.py once to "
            "populate them."
        )

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Token refresh failed: {resp.status_code} {resp.text[:300]}"
        )
    body = resp.json()
    token = body.get("access_token", "")
    if not token:
        raise RuntimeError(f"No access_token in refresh response: {body}")
    return token


def upload_video(
    *,
    video_path: str | Path,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category_id: str = "22",                # People & Blogs
    privacy_status: str = "unlisted",       # public | unlisted | private
    made_for_kids: bool = False,            # COPPA — required field
) -> dict:
    """
    Upload a local video file to YouTube via the resumable upload protocol,
    using the OAuth2 token Composio has stored for this user.

    Returns the YouTube video resource as a dict, including the `id` field
    that uniquely identifies the uploaded video.

    The shape mirrors what Composio's YOUTUBE_UPLOAD_VIDEO returns, so
    downstream code can treat both paths uniformly.
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {path}")

    auth_guard.gate("youtube_shorts")  # halt early if breaker is open
    try:
        access_token = _get_youtube_access_token()
    except Exception as e:
        auth_guard.record(
            "youtube_shorts", "YOUTUBE_TOKEN_REFRESH",
            ok=False, error=e,
        )
        raise

    # ── Step 1: initiate resumable upload session ───────────────────────────
    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,  # ← the field Composio omits
            "embeddable": True,
            "publicStatsViewable": True,
        },
    }

    file_size = path.stat().st_size
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"

    init_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": mime,
        "X-Upload-Content-Length": str(file_size),
    }

    logger.info(f"YouTube direct: initiating resumable upload ({file_size} bytes)…")
    with httpx.Client(timeout=60) as client:
        init_resp = client.post(
            YOUTUBE_UPLOAD_URL,
            headers=init_headers,
            content=json.dumps(metadata),
        )
        if init_resp.status_code != 200:
            err = RuntimeError(
                f"Resumable upload init failed: {init_resp.status_code} "
                f"{init_resp.text[:400]}"
            )
            auth_guard.record(
                "youtube_shorts", "YOUTUBE_UPLOAD_INIT",
                ok=False, status_code=init_resp.status_code, error=err,
            )
            raise err

        upload_url = init_resp.headers.get("Location")
        if not upload_url:
            err = RuntimeError("No Location header in resumable upload response")
            auth_guard.record(
                "youtube_shorts", "YOUTUBE_UPLOAD_INIT",
                ok=False, status_code=init_resp.status_code, error=err,
            )
            raise err

        # ── Step 2: PUT the bytes ───────────────────────────────────────────
        logger.info(f"YouTube direct: uploading {path.name} → {upload_url[:80]}…")
        # httpx default timeout is too tight for big files; bump it
        with httpx.Client(timeout=600) as up_client, open(path, "rb") as f:
            put_resp = up_client.put(
                upload_url,
                content=f.read(),
                headers={
                    "Content-Type": mime,
                    "Content-Length": str(file_size),
                },
            )

    if put_resp.status_code not in (200, 201):
        err = RuntimeError(
            f"Resumable upload PUT failed: {put_resp.status_code} "
            f"{put_resp.text[:400]}"
        )
        auth_guard.record(
            "youtube_shorts", "YOUTUBE_UPLOAD_PUT",
            ok=False, status_code=put_resp.status_code, error=err,
        )
        raise err

    body = put_resp.json()
    video_id = body.get("id", "")
    logger.info(f"YouTube direct: upload complete, video_id={video_id}")
    auth_guard.record(
        "youtube_shorts", "YOUTUBE_UPLOAD_PUT",
        ok=True, status_code=put_resp.status_code,
        extra={"video_id": video_id},
    )
    # Mirror Composio's response shape so callers don't have to branch
    return {
        "data": {"response_data": body},
        "error": None,
        "successful": True,
    }


# ── CrewAI tool wrapper ─────────────────────────────────────────────────────
# Lets the Publisher Agent invoke our direct YouTube upload alongside its
# Composio tools. The agent treats it like any other tool — same call shape
# (kwargs in, string out), same error handling. The decorator name doubles
# as the slug the agent's backstory references.

def get_youtube_direct_tool():
    """
    Build a CrewAI-compatible tool that wraps upload_video(). Returned as a
    factory so importing this module doesn't force CrewAI to be importable
    in code paths that only need the raw upload_video() function.
    """
    from crewai.tools import tool

    @tool("YOUTUBE_DIRECT_UPLOAD")
    def youtube_direct_upload(
        video_path: str,
        title: str,
        description: str,
        tags: list[str] | None = None,
        privacy_status: str = "unlisted",
    ) -> str:
        """
        Upload a local video file to YouTube using the project's BYO OAuth
        path (bypasses Composio's YOUTUBE_UPLOAD_VIDEO wrapper, which omits
        the COPPA-required selfDeclaredMadeForKids field and causes uploads
        to be silently rejected at processing).

        Args:
            video_path: absolute filesystem path to a local .mp4 file
            title: video title (max 100 chars)
            description: video description text
            tags: list of keyword tag strings (optional)
            privacy_status: 'public', 'unlisted', or 'private' (default unlisted)

        Returns:
            A short success/error string the agent can include in its publish
            report, e.g. 'Published: https://www.youtube.com/watch?v=<id>'
        """
        try:
            result = upload_video(
                video_path=video_path,
                title=title,
                description=description,
                tags=tags or [],
                privacy_status=privacy_status,
                made_for_kids=False,
            )
            video_id = (
                result.get("data", {})
                .get("response_data", {})
                .get("id", "")
            )
            if video_id:
                return (
                    f"YOUTUBE_DIRECT_UPLOAD success: "
                    f"https://www.youtube.com/watch?v={video_id} "
                    f"(privacy={privacy_status})"
                )
            return f"YOUTUBE_DIRECT_UPLOAD ambiguous response: {str(result)[:200]}"
        except Exception as e:
            return f"YOUTUBE_DIRECT_UPLOAD error: {type(e).__name__}: {e}"

    return youtube_direct_upload
