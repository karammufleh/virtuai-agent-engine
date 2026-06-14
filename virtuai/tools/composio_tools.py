"""
composio_tools.py — Native Composio SDK integration for the publisher agent.

We initially planned to use Composio's MCP server, but the new Composio platform
ships a first-class CrewAI provider (`composio_crewai.CrewAIProvider`) that is
simpler and gives us better tool schemas for free. No MCP layer required.

────────────────────────────────────────────────────────────────────────────
Setup once on Composio's side (you've done most of this):

  1. Sign up at https://app.composio.dev
  2. Toolkits → search LinkedIn → "Add to Project"
  3. Auth Configs → Create → OAuth 2.0 → Composio Managed (Recommended) →
     Create Auth Config → Connect Account → log in to LinkedIn
  4. Settings → API Keys → generate one (looks like ak_...)

────────────────────────────────────────────────────────────────────────────
Setup in this repo (~10 seconds):

    # Add to .env (NEVER commit this):
    COMPOSIO_API_KEY=ak_...
    COMPOSIO_USER_ID=default          # optional, defaults to "default"

If COMPOSIO_API_KEY is unset, the publisher agent runs in DRY-RUN mode —
it logs what it would have posted to JSONL, but never calls the network.
Useful for capstone defense demos where you don't want to burn quota.

────────────────────────────────────────────────────────────────────────────
Public API (used by publisher_agent.py and test_composio.py):

    is_configured()              -> bool
    composio_tools(...)          -> list of CrewAI-compatible tools
    composio_tools_dry_run()     -> list of stub tools (logs only, no network)
    get_publisher_tools()        -> (tools, mode) one-shot helper
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from dotenv import load_dotenv

# Load .env at import time so callers don't need to.
load_dotenv()

logger = logging.getLogger("virtuai.tools.composio")

COMPOSIO_API_KEY = os.environ.get("COMPOSIO_API_KEY", "").strip()
COMPOSIO_USER_ID = os.environ.get("COMPOSIO_USER_ID", "default").strip() or "default"

# The actions we expose to the publisher agent. Slugs come straight from
# Composio's toolkit pages — `LINKEDIN_CREATE_LINKED_IN_POST` is the actual
# slug shown for LinkedIn's "Create a LinkedIn post" action.
DEFAULT_PUBLISHER_TOOLS: list[str] = [
    # LinkedIn — posting requires knowing the author URN, which we get
    # from GET_MY_INFO once and could later cache. Both must be exposed.
    "LINKEDIN_CREATE_LINKED_IN_POST",
    "LINKEDIN_GET_MY_INFO",
    # Facebook — posts to a Page (replaced X / Twitter on 2026-05-21).
    # FB_PAGE_ID env var points to the Daniel Calder Page.
    # FACEBOOK_CREATE_POST (text), FACEBOOK_CREATE_PHOTO_POST (image),
    # FACEBOOK_CREATE_VIDEO_POST (reel) are all wired in scripts/.
    "FACEBOOK_CREATE_POST",
    "FACEBOOK_CREATE_PHOTO_POST",
    "FACEBOOK_CREATE_VIDEO_POST",
    # Instagram — TWO-STEP flow:
    #   1. INSTAGRAM_CREATE_MEDIA_CONTAINER (image_url + caption) → returns container id
    #   2. INSTAGRAM_CREATE_POST (ig_user_id + creation_id) → publishes
    # Plus INSTAGRAM_LIST_PAGES (or similar) is needed once to discover the
    # IG Business Account ID — like LinkedIn's GET_MY_INFO step.
    "INSTAGRAM_CREATE_MEDIA_CONTAINER",
    "INSTAGRAM_CREATE_POST",
    # YouTube — INTENTIONALLY OMITTED. Composio's YOUTUBE_UPLOAD_VIDEO
    # wrapper drops the COPPA-required `selfDeclaredMadeForKids` field,
    # which causes YouTube to silently abandon processing on every API
    # upload. We use virtuai/tools/youtube_direct.py instead — direct
    # Google Data API v3 with our own OAuth refresh token. The Publisher
    # Agent gets that direct tool injected separately via make_publisher().
    # "YOUTUBE_UPLOAD_VIDEO",
    # Medium — toolkit not currently active. Placeholder slug; if enabled
    # in Composio later, action will appear and demo_publisher will pick
    # it up automatically.
    "MEDIUM_CREATE_POST",
]


def is_configured() -> bool:
    """True iff env vars are set well enough to attempt a real connection."""
    return bool(COMPOSIO_API_KEY)


def composio_tools(
    *,
    tools: list[str] | None = None,
    toolkits: list[str] | None = None,
    user_id: str | None = None,
) -> list:
    """
    Return CrewAI-compatible tools backed by Composio's hosted execution.

    By default returns the DEFAULT_PUBLISHER_TOOLS slugs. Pass `tools=[...]`
    to scope to specific actions, or `toolkits=["LINKEDIN", ...]` to grab
    every action from one or more toolkits.

    Note: an action only works if the corresponding toolkit has a CONNECTED
    account under the user_id you pass. Connect accounts via Composio's
    Auth Configs UI — see the module docstring.

    Usage:
        from virtuai.tools.composio_tools import composio_tools
        tools = composio_tools(tools=["LINKEDIN_CREATE_LINKED_IN_POST"])
        agent = Agent(tools=tools, ...)
    """
    if not is_configured():
        raise RuntimeError(
            "COMPOSIO_API_KEY not set in environment. Either configure it "
            "(see module docstring) or use composio_tools_dry_run() instead."
        )

    # Late imports — keeps `import` time cheap for callers that just want dry-run.
    from composio import Composio
    from composio_crewai import CrewAIProvider

    composio = Composio(provider=CrewAIProvider())
    selected_tools = tools or (None if toolkits else DEFAULT_PUBLISHER_TOOLS)

    kwargs: dict[str, Any] = {"user_id": user_id or COMPOSIO_USER_ID}
    if selected_tools:
        kwargs["tools"] = selected_tools
    if toolkits:
        kwargs["toolkits"] = toolkits

    collection = composio.tools.get(**kwargs)
    out = list(collection)
    logger.info(f"Composio: loaded {len(out)} tool(s) for user_id={kwargs['user_id']}")
    return out


# ── Dry-run fallback ────────────────────────────────────────────────────────
# When credentials are missing, expose a tiny set of CrewAI @tool functions
# that pretend to publish. The agent gets a realistic interface for development
# without consuming Composio's quota or risking accidental posts.

class _DryRunPublisher:
    """A drop-in placeholder when no Composio credentials exist."""

    def __init__(self, log_dir: str | None = None):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        self._log_path = (
            (Path(log_dir) if log_dir else root / "virtuai" / "data" / "logs")
            / "composio_dry_run.jsonl"
        )
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def _record(self, action: str, payload: dict) -> str:
        import time
        entry = {"action": action, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), **payload}
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        msg = f"[DRY-RUN composio] {action}: {json.dumps(payload)[:200]}"
        logger.info(msg)
        return f"DRY-RUN OK — logged to {self._log_path.name}"

    def linkedin_create_post(self, text: str, image_url: str = "") -> str:
        return self._record("LINKEDIN_CREATE_LINKED_IN_POST", {"text": text, "image_url": image_url})

    def twitter_create_tweet(self, text: str, image_url: str = "") -> str:
        return self._record("TWITTER_CREATE_TWEET", {"text": text, "image_url": image_url})

    def instagram_create_media(self, caption: str, image_url: str = "") -> str:
        return self._record("INSTAGRAM_CREATE_MEDIA", {"caption": caption, "image_url": image_url})

    def youtube_upload_short(self, title: str, description: str, video_url: str) -> str:
        return self._record("YOUTUBE_UPLOAD_SHORT",
                            {"title": title, "description": description, "video_url": video_url})

    def medium_create_post(self, title: str, content: str) -> str:
        return self._record("MEDIUM_CREATE_POST", {"title": title, "content": content[:200] + "..."})


def composio_tools_dry_run() -> list:
    """Return CrewAI tools that log instead of posting. Used when env unset."""
    from crewai.tools import tool

    pub = _DryRunPublisher()

    @tool("LINKEDIN_CREATE_LINKED_IN_POST")
    def linkedin_create_post(text: str, image_url: str = "") -> str:
        """Publish a post to LinkedIn (DRY-RUN: logs only)."""
        return pub.linkedin_create_post(text, image_url)

    @tool("TWITTER_CREATE_TWEET")
    def twitter_create_tweet(text: str, image_url: str = "") -> str:
        """Publish a tweet to X / Twitter (DRY-RUN: logs only)."""
        return pub.twitter_create_tweet(text, image_url)

    @tool("INSTAGRAM_CREATE_MEDIA")
    def instagram_create_media(caption: str, image_url: str = "") -> str:
        """Publish a media post to Instagram (DRY-RUN: logs only)."""
        return pub.instagram_create_media(caption, image_url)

    @tool("YOUTUBE_UPLOAD_SHORT")
    def youtube_upload_short(title: str, description: str, video_url: str) -> str:
        """Upload a YouTube Short (DRY-RUN: logs only)."""
        return pub.youtube_upload_short(title, description, video_url)

    @tool("MEDIUM_CREATE_POST")
    def medium_create_post(title: str, content: str) -> str:
        """Publish a Medium article (DRY-RUN: logs only)."""
        return pub.medium_create_post(title, content)

    return [
        linkedin_create_post,
        twitter_create_tweet,
        instagram_create_media,
        youtube_upload_short,
        medium_create_post,
    ]


def get_publisher_tools() -> tuple[list, str]:
    """
    One-call entry point that does the right thing in either mode.
    Returns (tools, mode) where mode is 'live' or 'dry-run'.
    """
    if is_configured():
        return composio_tools(), "live"
    return composio_tools_dry_run(), "dry-run"
