"""
demo_publisher.py — End-to-end demo for the Publisher agent only.

Exercises the Composio-backed Publisher in isolation by feeding it a synthetic
Guardian report with one APPROVE per platform. Useful when the upstream crew
(research → strategy → creator → visual → reviewer → guardian) is slow or
flaky and you just want to verify:

  1. The Publisher is wired correctly (tools loaded, mode is LIVE/DRY-RUN).
  2. LinkedIn posting works (the only platform actually auth'd in Composio).
  3. Other platforms are recorded as tool_unavailable, NOT fabricated.

Usage:
    python virtuai/persona/scripts/demo_publisher.py
    python virtuai/persona/scripts/demo_publisher.py --skip-linkedin   # don't actually post

Note on mode:
  This script doesn't depend on the local FastAPI backend. It uses CrewAI's
  default OpenAI-compatible LLM if OPENAI_API_KEY is set, OR a tiny stub LLM
  that just emits a publish-report string (so the agent's tools still get
  invoked even without a real reasoning backend). Pick whichever your env
  supports — both verify the same wiring.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# Synthetic Guardian output the Publisher will see. One APPROVE per platform.
DEMO_GUARDIAN_REPORT = {
    "linkedin": {
        "verdict": "APPROVE",
        "content": (
            "VirtuAI capstone — pipeline verification post.\n\n"
            "This is a system-generated test confirming our autonomous content "
            "publisher is connected and working. Built with CrewAI + Composio + "
            "local Phi-3.5-mini reasoning. No human in the loop for this post.\n\n"
            "If you're reading this on LinkedIn, the integration is live."
        ),
        "image_url": "",
    },
    "x": {
        "verdict": "APPROVE",
        "content": (
            "VirtuAI capstone integration test — autonomous publisher firing on "
            "all platforms. CrewAI + Composio + local LLM. If you see this on "
            "X, the API path is live."
        ),
        "image_url": "",
    },
    "instagram": {
        "verdict": "APPROVE",
        "content": (
            "VirtuAI capstone — autonomous content publisher demo. "
            "Multi-agent pipeline: research → strategy → creator → visual → "
            "reviewer → guardian → publisher. End-to-end verification."
        ),
        # Meta requires a publicly accessible URL; local file paths won't work.
        # picsum.photos with a seed returns a stable square image. Override
        # via IG_DEMO_IMAGE_URL in env to use one of our pipeline-rendered
        # images uploaded to a CDN/imgur/R2 for production.
        "image_url": os.environ.get(
            "IG_DEMO_IMAGE_URL",
            "https://picsum.photos/seed/virtuai/1080/1080",
        ),
    },
    "youtube_shorts": {
        "verdict": "APPROVE",
        "title": "VirtuAI Capstone — Publisher Integration Test",
        "description": (
            "Demo upload from the autonomous content pipeline. CrewAI + Composio "
            "+ local Phi-3.5-mini. End-to-end verification."
        ),
        "video_url": "",
    },
    "medium": {
        "verdict": "APPROVE",
        "title": "VirtuAI Capstone — Publisher Integration Test",
        "content": (
            "## What this is\n\n"
            "An autonomous publish-test from the VirtuAI content pipeline. The "
            "post you're reading was authored by a CrewAI multi-agent system, "
            "approved by a Guardian safety agent, and published via Composio's "
            "hosted Medium tool — no human pressed 'publish'.\n\n"
            "## How it works\n\n"
            "Six upstream agents (research, strategy, creator, visual, reviewer, "
            "guardian) reason over local Phi-3.5-mini. If Guardian approves, the "
            "Publisher agent picks the right Composio action per platform and "
            "fires it. Fully audited end-to-end."
        ),
    },
    "facebook": {
        "verdict": "APPROVE",
        "content": (
            "VirtuAI capstone — autonomous publisher demo on Facebook. "
            "Multi-agent pipeline: research → strategy → creator → visual → "
            "reviewer → guardian → publisher. End-to-end verification."
        ),
    },
}


def make_demo_llm():
    """
    Pick the cheapest LLM that lets CrewAI run a single agent task.
    Order of preference:
      1. Local VirtuAI backend at :8765 (if it's up — fastest, no quota)
      2. OpenAI gpt-4o-mini (if OPENAI_API_KEY set)
      3. None — caller will short-circuit and call tools directly.
    """
    from crewai import LLM

    # Try local backend first
    try:
        import httpx
        if httpx.get("http://localhost:8765/health", timeout=2).status_code == 200:
            print("[llm] using local VirtuAI backend (Phi-3.5-mini)")
            return LLM(
                model="openai/phi-3.5-mini",
                base_url="http://localhost:8765/v1",
                api_key="local",
                timeout=180,
            )
    except Exception:
        pass

    if os.environ.get("OPENAI_API_KEY"):
        print("[llm] using OpenAI gpt-4o-mini")
        return LLM(model="openai/gpt-4o-mini", temperature=0.3)

    print("[llm] no LLM available — will call publisher tools directly")
    return None


def direct_tool_demo() -> dict:
    """
    Bypass CrewAI: call the Composio tools directly with the Guardian report
    payload. Verifies the actual publish path without depending on an LLM
    making the right tool calls.
    """
    from virtuai.tools.composio_tools import composio_tools, is_configured

    report: dict = {}
    if not is_configured():
        print("[mode] DRY-RUN — Composio not configured")
        from virtuai.tools.composio_tools import composio_tools_dry_run
        tools = composio_tools_dry_run()
    else:
        print("[mode] LIVE — Composio configured")
        tools = composio_tools()

    by_name = {getattr(t, "name", "").upper(): t for t in tools}
    print(f"[tools] {len(tools)} available: {sorted(by_name)}")

    # Resolve LinkedIn URN once (cached on disk)
    linkedin_urn = None
    if is_configured():
        from virtuai.agents.publisher_agent import _resolve_linkedin_urn
        from virtuai.tools.composio_tools import COMPOSIO_USER_ID
        linkedin_urn = _resolve_linkedin_urn(tools, COMPOSIO_USER_ID)
        print(f"[linkedin] author URN: {linkedin_urn or '(unresolved)'}")

    # ── platform-by-platform dispatch ───────────────────────────────────────
    # Each entry: (guardian_platform_key, primary_tool_slug). Some platforms
    # need a second tool too (Instagram is two-step), handled inline.
    # YouTube uses None because we bypass Composio for that platform — the
    # direct API call doesn't need a Composio tool to be present.
    platform_routes = [
        ("linkedin",        "LINKEDIN_CREATE_LINKED_IN_POST"),
        ("x",               "TWITTER_CREATION_OF_A_POST"),
        ("instagram",       "INSTAGRAM_CREATE_POST"),
        ("youtube_shorts",  None),  # bypasses Composio — uses youtube_direct
        ("medium",          "MEDIUM_CREATE_POST"),
        ("facebook",        "FACEBOOK_CREATE_POST"),
    ]

    # Resolve the Facebook Page ID once (set by the user during OAuth setup).
    fb_page_id = os.environ.get("FB_PAGE_ID", "").strip()

    # Resolve once: the IG Business Account ID. Composio doesn't expose a
    # one-shot discovery action, so we read it from env (set IG_USER_ID).
    ig_user_id = os.environ.get("IG_USER_ID", "").strip()

    # Local video file for YouTube. Defaults to one of our pre-rendered shorts.
    yt_video_path = os.environ.get(
        "YOUTUBE_DEMO_VIDEO",
        str(ROOT / "virtuai" / "data" / "generated_videos" / "video_youtube_shorts_2.mp4"),
    )
    yt_video_path = yt_video_path if Path(yt_video_path).exists() else ""

    for platform, tool_slug in platform_routes:
        item = DEMO_GUARDIAN_REPORT.get(platform)
        if not item or item.get("verdict") != "APPROVE":
            report[platform] = {"action": "skipped", "reason": "non-APPROVE verdict"}
            continue

        # Skip the Composio-tool guard for platforms that bypass Composio
        # entirely (currently just YouTube — uses youtube_direct).
        tool = by_name.get(tool_slug) if tool_slug else None
        if tool_slug is not None and tool is None:
            report[platform] = {
                "action": "tool_unavailable",
                "reason": f"{tool_slug} not connected in Composio",
            }
            print(f"[{platform}] tool_unavailable — {tool_slug} not in tool list")
            continue

        try:
            # ── LinkedIn ─────────────────────────────────────────
            if platform == "linkedin":
                if not linkedin_urn:
                    report[platform] = {"action": "error", "reason": "no LinkedIn URN"}
                    continue
                print(f"\n[{platform}] calling {tool_slug}…")
                result = tool.run(author=linkedin_urn, commentary=item["content"])

            # ── Twitter / X ──────────────────────────────────────
            elif platform == "x":
                print(f"\n[{platform}] calling {tool_slug}…")
                result = tool.run(text=item["content"])

            # ── Instagram (two-step: container + publish) ────────
            elif platform == "instagram":
                if not ig_user_id:
                    report[platform] = {
                        "action": "needs_setup",
                        "reason": "IG_USER_ID env var not set. Find your IG Business "
                                  "Account ID via Facebook Graph API Explorer: "
                                  "me/accounts → <page>?fields=instagram_business_account",
                    }
                    print(f"[{platform}] needs_setup — IG_USER_ID env var missing")
                    continue
                if not item.get("image_url"):
                    report[platform] = {
                        "action": "needs_setup",
                        "reason": "Instagram requires a publicly accessible image_url",
                    }
                    print(f"[{platform}] needs_setup — no public image_url")
                    continue
                container_tool = by_name.get("INSTAGRAM_CREATE_MEDIA_CONTAINER")
                if container_tool is None:
                    report[platform] = {
                        "action": "tool_unavailable",
                        "reason": "INSTAGRAM_CREATE_MEDIA_CONTAINER not connected",
                    }
                    continue
                print(f"\n[{platform}] step 1: INSTAGRAM_CREATE_MEDIA_CONTAINER…")
                container_resp = container_tool.run(
                    ig_user_id=ig_user_id,
                    image_url=item["image_url"],
                    caption=item["content"],
                )
                # Parse container ID from response
                cid = None
                if isinstance(container_resp, dict):
                    cid = (container_resp.get("data") or {}).get("id")
                if not cid:
                    import re as _re
                    m = _re.search(r'"id"\s*:\s*"([^"]+)"', str(container_resp))
                    cid = m.group(1) if m else None
                if not cid:
                    report[platform] = {
                        "action": "error",
                        "reason": f"could not parse container id: {str(container_resp)[:200]}",
                    }
                    continue
                print(f"[{platform}] step 2: INSTAGRAM_CREATE_POST creation_id={cid}…")
                result = tool.run(ig_user_id=ig_user_id, creation_id=cid)

            # ── YouTube ──────────────────────────────────────────
            elif platform == "youtube_shorts":
                if not yt_video_path:
                    report[platform] = {
                        "action": "needs_setup",
                        "reason": "No local video file. Set YOUTUBE_DEMO_VIDEO env var to a path.",
                    }
                    print(f"[{platform}] needs_setup — no video file")
                    continue
                # IMPORTANT: bypass Composio's YOUTUBE_UPLOAD_VIDEO wrapper.
                # Their wrapper omits the COPPA-required `selfDeclaredMadeForKids`
                # field, which causes YouTube's processor to silently abandon
                # uploads (Studio shows "Processing abandoned"). We use Composio
                # only for token storage; the actual upload goes direct to
                # YouTube Data API v3 via youtube_direct.upload_video().
                from virtuai.tools.youtube_direct import upload_video as yt_upload
                # Default privacy = unlisted (safe for testing). Override with
                # YOUTUBE_PRIVACY=public in the env to push uploads live.
                yt_privacy = os.environ.get("YOUTUBE_PRIVACY", "unlisted").strip()
                print(f"\n[{platform}] direct YouTube upload (privacy={yt_privacy}) — {Path(yt_video_path).name}")
                result = yt_upload(
                    video_path=yt_video_path,
                    title=item["title"],
                    description=item["description"],
                    tags=["VirtuAI", "Capstone", "Test"],
                    category_id="22",
                    privacy_status=yt_privacy,
                    made_for_kids=False,  # the field Composio drops
                )

            # ── Medium (placeholder) ─────────────────────────────
            elif platform == "medium":
                print(f"\n[{platform}] calling {tool_slug}…")
                result = tool.run(title=item["title"], content=item["content"])

            # ── Facebook ─────────────────────────────────────────
            elif platform == "facebook":
                if not fb_page_id:
                    report[platform] = {
                        "action": "needs_setup",
                        "reason": "FB_PAGE_ID env var not set — find your Daniel Calder "
                                  "Page numeric ID and add it to .env.",
                    }
                    print(f"[{platform}] needs_setup — FB_PAGE_ID env var missing")
                    continue
                print(f"\n[{platform}] calling {tool_slug}…")
                result = tool.run(message=item["content"], page_id=fb_page_id)

            else:
                report[platform] = {"action": "error", "reason": "unknown platform"}
                continue

            response_str = str(result)[:400]
            print(f"[{platform}] response: {response_str}")
            report[platform] = {
                "action": "published",
                "tool_used": tool_slug,
                "response": response_str,
            }
        except Exception as e:
            print(f"[{platform}] error: {type(e).__name__}: {e}")
            report[platform] = {
                "action": "error",
                "tool_used": tool_slug,
                "error": f"{type(e).__name__}: {e}",
            }

    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--skip-linkedin",
        action="store_true",
        help="Don't actually post to LinkedIn (still tests other platforms).",
    )
    args = p.parse_args()

    if args.skip_linkedin:
        DEMO_GUARDIAN_REPORT["linkedin"]["verdict"] = "REVISE"
        print("[note] LinkedIn flagged REVISE — will be skipped\n")

    # LinkedIn rejects duplicate content. Append a per-run timestamp so each
    # demo invocation produces unique copy.
    run_tag = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for k, v in DEMO_GUARDIAN_REPORT.items():
        if "content" in v:
            v["content"] = f"{v['content']}\n\n— run {run_tag}"

    print("=" * 60)
    print("  VirtuAI Publisher — End-to-End Demo")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    report = direct_tool_demo()

    # Save report
    out_dir = ROOT / "virtuai" / "data" / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"demo_publisher_{ts}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print("  Publisher Demo Report")
    print("=" * 60)
    for platform, result in report.items():
        action = result.get("action", "?")
        marker = {
            "published": "✓",
            "skipped": "−",
            "tool_unavailable": "·",
            "needs_setup": "○",
            "error": "✗",
        }.get(action, "?")
        print(f"  {marker} {platform:16s} {action}")
        if action == "error":
            print(f"      → {result.get('error', '')[:100]}")
        elif action == "needs_setup":
            print(f"      → {result.get('reason', '')[:100]}")
        elif action == "published":
            r = result.get("response", "")
            # Try to surface a URL or share id from the response
            for needle in ("urn:li:share:", "https://", "id\":\""):
                if needle in r:
                    snippet = r[r.find(needle):r.find(needle) + 100]
                    print(f"      → {snippet}")
                    break
    print(f"\n  Full report: {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
