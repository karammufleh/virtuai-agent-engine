"""
Publisher Agent — pushes approved persona content to social-media platforms.

Sits at the END of the pipeline, after Guardian has APPROVE'd a post.
Two parallel tool tiers are exposed to the agent:

  TIER 1 — Composio dynamic tools (flexible, multi-platform):
    LINKEDIN_CREATE_LINKED_IN_POST, TWITTER_CREATION_OF_A_POST,
    INSTAGRAM_CREATE_MEDIA_CONTAINER + INSTAGRAM_CREATE_POST,
    FACEBOOK_CREATE_POST, MEDIUM_CREATE_POST

  TIER 2 — Direct API + simple cloud_tools wrappers (more reliable):
    YOUTUBE_DIRECT_UPLOAD (bypasses Composio's broken YT wrapper),
    publish_reel_to_youtube, publish_reel_to_instagram,
    publish_image_to_instagram, publish_post_to_linkedin
    (these are the same callables used by scripts/publish_v16.py and
    scripts/publish_images.py, exposed as @tool functions so the agent
    can invoke them directly when the Composio path is too verbose).

Two operating modes, picked automatically:

  - LIVE: COMPOSIO_API_KEY is set → connects to Composio's hosted execution,
    real posts go live. Pre-fetches the LinkedIn URN once and caches it on
    disk so the agent doesn't burn a Composio call per run.
  - DRY-RUN: env var unset → uses log-only stub tools, writes JSONL to
    virtuai/data/logs/composio_dry_run.jsonl, never touches the network.
    Safe for capstone demos and offline development.

Caching: virtuai/persona/composio_cache.json holds stable per-platform
identifiers (LinkedIn URN, etc.) keyed by user_id. Delete to force refresh.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from crewai import Agent, LLM

from virtuai.tools.composio_tools import (
    composio_tools,
    composio_tools_dry_run,
    is_configured,
    COMPOSIO_USER_ID,
)

logger = logging.getLogger("virtuai.agents.publisher")

ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = ROOT / "virtuai" / "persona" / "composio_cache.json"


PUBLISHER_BACKSTORY_BASE = """\
You are the final step in the VirtuAI content pipeline. By the time content
reaches you, the Guardian Agent has issued APPROVE / REVISE / BLOCK verdicts
per platform. Your job is to PUBLISH every APPROVE'd item to the correct
platform.

You have access to publishing tools — most through Composio's hosted SDK,
plus one direct-API tool for YouTube. The tool catalogue available to you
in this run includes:

  - LINKEDIN_CREATE_LINKED_IN_POST   →  LinkedIn post (Composio)
  - TWITTER_CREATION_OF_A_POST       →  X / Twitter post (Composio, paid-tier only)
  - INSTAGRAM_CREATE_MEDIA_CONTAINER →  Instagram step 1: build media (Composio)
  - INSTAGRAM_CREATE_POST            →  Instagram step 2: publish (Composio)
  - FACEBOOK_CREATE_POST             →  Facebook Page post (Composio)
  - YOUTUBE_DIRECT_UPLOAD            →  YouTube Shorts (DIRECT, not Composio)

Hard rules:
  1. Only publish items where Guardian's verdict is APPROVE. Skip REVISE/BLOCK.
  2. Pick exactly one tool per item. Don't cross-publish unless explicitly told.
  3. Use the post's `platform` field to decide which tool.
  4. Return a publish report: per platform, which tool was used and the
     platform's response (URL, share ID, etc.) or the error if it failed.
  5. Never re-publish. If you see a post that already has a published_at
     field, mark it skipped.

Platform quirks you MUST follow:

  LinkedIn (LINKEDIN_CREATE_LINKED_IN_POST):
    - The body field is `commentary` (NOT `text` or `content`). Max 3000 chars.
    - The `author` field MUST be a LinkedIn URN, not a username.

  YouTube (YOUTUBE_DIRECT_UPLOAD — IMPORTANT):
    - DO NOT call Composio's YOUTUBE_UPLOAD_VIDEO. It's intentionally absent
      from your tools because it omits the COPPA selfDeclaredMadeForKids field
      and YouTube silently abandons every upload.
    - Use YOUTUBE_DIRECT_UPLOAD instead. Its arguments are:
        video_path (str, absolute file path to the local mp4)
        title (str)
        description (str)
        tags (list of strings, optional)
        privacy_status (str: 'public' | 'unlisted' | 'private', default 'unlisted')
    - The tool returns a string like:
        'YOUTUBE_DIRECT_UPLOAD success: https://www.youtube.com/watch?v=<id>'

  Facebook (FACEBOOK_CREATE_POST):
    - Posts to a Facebook Page (NOT a personal profile). Required args:
        message (str, the text content)
        page_id (str, numeric Page ID — read from environment var FB_PAGE_ID)
    - The page_id for the Daniel Calder Page is set in the environment.
      If the env var is missing, mark Facebook as 'needs_setup' instead of
      attempting the call.

  Instagram (two-step flow):
    - First: INSTAGRAM_CREATE_MEDIA_CONTAINER with ig_user_id (from env IG_USER_ID),
      image_url (publicly accessible URL — local files do not work), and caption.
      Returns a 'creation_id'.
    - Then: INSTAGRAM_CREATE_POST with ig_user_id and that creation_id.
"""


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _extract_linkedin_urn(response) -> str:
    """LinkedIn GET_MY_INFO returns dict-ish — find the URN inside it."""
    s = response if isinstance(response, str) else json.dumps(response, default=str)
    m = re.search(r"urn:li:(?:person|member):[A-Za-z0-9_\-]+", s)
    if m:
        return m.group(0)
    m = re.search(r'"sub"\s*:\s*"([^"]+)"', s)
    if m:
        return f"urn:li:person:{m.group(1)}"
    m = re.search(r'"id"\s*:\s*"([A-Za-z0-9_\-]+)"', s)
    if m:
        return f"urn:li:person:{m.group(1)}"
    return ""


def _resolve_linkedin_urn(tools: list, user_id: str) -> str | None:
    """
    Return Daniel's LinkedIn URN. Cache it on disk per user_id so we don't
    burn a Composio call every pipeline run. Falls back to None if the
    GET_MY_INFO tool isn't connected (e.g., LinkedIn not auth'd in Composio).
    """
    cache = _load_cache()
    cached = cache.get("linkedin_urn", {}).get(user_id)
    if cached:
        logger.info(f"LinkedIn URN cache hit for user_id={user_id}")
        return cached

    info_tool = next(
        (t for t in tools if "GET_MY_INFO" in (getattr(t, "name", "") or "").upper()),
        None,
    )
    if info_tool is None:
        logger.info("LINKEDIN_GET_MY_INFO not exposed — skipping URN prefetch")
        return None

    try:
        response = info_tool.run()
    except Exception as e:
        logger.warning(f"LINKEDIN_GET_MY_INFO failed: {e}")
        return None

    urn = _extract_linkedin_urn(response)
    if not urn:
        logger.warning(f"Could not parse URN from response: {response!r}")
        return None

    cache.setdefault("linkedin_urn", {})[user_id] = urn
    _save_cache(cache)
    logger.info(f"Cached LinkedIn URN for user_id={user_id}: {urn}")
    return urn


def make_publisher(llm: LLM) -> Agent:
    """
    Build a Publisher CrewAI agent. Auto-picks live vs dry-run mode based on
    whether COMPOSIO_API_KEY is set.

    In live mode, pre-fetches platform-specific identifiers (LinkedIn URN)
    once and bakes them into the agent's backstory so the agent doesn't have
    to do discovery calls itself.

    The agent's tool list is Composio's tools (LinkedIn / Twitter / Instagram
    / Medium) PLUS our direct YouTube tool — Composio's YOUTUBE_UPLOAD_VIDEO
    is intentionally excluded because it omits the COPPA flag and YouTube
    rejects every upload at processing.
    """
    # Lazy-import the YouTube direct tool so dry-run mode doesn't require
    # the OAuth env vars to be set.
    from virtuai.tools.youtube_direct import get_youtube_direct_tool
    from virtuai.tools.cloud_tools import (
        publish_reel_to_youtube,
        publish_reel_to_instagram,
        publish_image_to_instagram,
        publish_post_to_linkedin,
    )

    if is_configured():
        tools = composio_tools()
        # Append our direct + simple wrappers — same callable shape, the
        # agent treats them like any other tool. These are easier to invoke
        # than the raw Composio actions for routine publishing.
        tools.append(get_youtube_direct_tool())
        tools.extend([
            publish_reel_to_youtube,
            publish_reel_to_instagram,
            publish_image_to_instagram,
            publish_post_to_linkedin,
        ])
        backstory = PUBLISHER_BACKSTORY_BASE

        # Pre-fetch LinkedIn URN if the action is exposed; bake it into
        # the prompt so the agent uses the right value without thinking.
        urn = _resolve_linkedin_urn(tools, COMPOSIO_USER_ID)
        if urn:
            backstory += (
                f"    - Use this exact author URN for every LinkedIn post:\n"
                f"        author = \"{urn}\"\n"
                f"      It is your account's URN — do NOT try to fetch a different one.\n"
            )
        else:
            backstory += (
                "    - The LinkedIn URN is not cached. Before posting, call\n"
                "      LINKEDIN_GET_MY_INFO once and read `data.id` from the response;\n"
                "      construct the author as `urn:li:person:<that id>`.\n"
            )

        return Agent(
            role="Content Publisher",
            goal=(
                "Publish each APPROVE'd post to its target platform using the correct "
                "Composio tool, then return a publish report."
            ),
            backstory=backstory,
            tools=tools,
            llm=llm,
            verbose=True,
            allow_delegation=False,
        )

    # Dry-run: stub tools that log to JSONL instead of publishing.
    dry_tools = composio_tools_dry_run()
    dry_tools.append(get_youtube_direct_tool())  # direct YT works in dry-run too
    dry_tools.extend([
        publish_reel_to_youtube,
        publish_reel_to_instagram,
        publish_image_to_instagram,
        publish_post_to_linkedin,
    ])
    return Agent(
        role="Content Publisher (DRY-RUN)",
        goal=(
            "Simulate publishing each APPROVE'd post — log what WOULD be "
            "published without actually calling any external API. Used for "
            "capstone development and demos."
        ),
        backstory=PUBLISHER_BACKSTORY_BASE + (
            "\n\n[DRY-RUN MODE] Composio credentials are not configured in this "
            "environment. Your Composio tools log intent to JSONL but never call "
            "the network. YOUTUBE_DIRECT_UPLOAD is still live — it doesn't go "
            "through Composio."
        ),
        tools=dry_tools,
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )
