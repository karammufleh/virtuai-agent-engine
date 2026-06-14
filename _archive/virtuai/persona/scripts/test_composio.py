"""
test_composio.py — Smoke-test the Composio MCP integration without involving
the rest of the agent stack.

What it does, in order:

  1. Reports whether COMPOSIO_MCP_URL / COMPOSIO_API_KEY are set.
  2. If not set: instantiates the dry-run tool set, prints the names + descriptions.
  3. If set: opens an MCP connection to Composio, prints the live tools the
     server exposed, and (with --post) actually fires a tiny test action
     using the first tool. Useful sanity check before letting the agent loose.

Usage:
    python virtuai/persona/scripts/test_composio.py
    python virtuai/persona/scripts/test_composio.py --post   # really call one tool
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from virtuai.tools.composio_tools import (
    composio_tools,
    composio_tools_dry_run,
    is_configured,
    COMPOSIO_USER_ID,
)


def describe_tool(t) -> tuple[str, str]:
    """Pull a (name, short-description) pair from a CrewAI tool object."""
    name = getattr(t, "name", None) or getattr(t, "__name__", str(t))
    desc = (getattr(t, "description", "") or "").strip().split("\n")[0]
    return name, desc[:120]


def list_dry_run() -> None:
    print("\n[mode] DRY-RUN — credentials not set")
    print(f"[mode] Set COMPOSIO_API_KEY in .env to enable live publishing.\n")
    tools = composio_tools_dry_run()
    print(f"Stub tools available: {len(tools)}")
    for t in tools:
        name, desc = describe_tool(t)
        print(f"  • {name:35s} {desc}")
    print("\nLogs go to: virtuai/data/logs/composio_dry_run.jsonl")


def list_live(do_post: bool) -> None:
    print(f"\n[mode] LIVE — Composio SDK, user_id={COMPOSIO_USER_ID}")
    tools = composio_tools()
    print(f"Live tools available: {len(tools)}")
    for t in tools:
        name, desc = describe_tool(t)
        print(f"  • {name:35s} {desc}")

    if not do_post:
        print("\n(skipping the actual call — pass --post to fire a test action)")
        return

    if not tools:
        print(
            "\nNo tools available. Most common cause: the LinkedIn account "
            "you connected in Composio's Auth Configs UI is under a different "
            "user_id than the one we're querying. Check the Connected Accounts "
            "section in Composio and ensure the LinkedIn connection is bound "
            "to user_id='" + COMPOSIO_USER_ID + "'."
        )
        return

    # LinkedIn posting requires knowing the author URN. Two-step flow:
    #   1. LINKEDIN_GET_MY_INFO  -> returns the user's URN
    #   2. LINKEDIN_CREATE_LINKED_IN_POST(author=<urn>, commentary=<text>)
    by_name = {describe_tool(t)[0]: t for t in tools}
    info_tool = next((v for k, v in by_name.items() if "GET_MY_INFO" in k.upper()), None)
    post_tool = next((v for k, v in by_name.items() if "CREATE_LINKED_IN_POST" in k.upper()), None)

    if not info_tool or not post_tool:
        print("\n[test] skipping live post — required tools not exposed:")
        print(f"  GET_MY_INFO: {'✓' if info_tool else '✗'}")
        print(f"  CREATE_LINKED_IN_POST: {'✓' if post_tool else '✗'}")
        print("Make sure both action slugs are listed in DEFAULT_PUBLISHER_TOOLS.")
        return

    # Step 1: fetch the user's LinkedIn URN
    print(f"\n[test] step 1: {describe_tool(info_tool)[0]} (fetch author URN)…")
    try:
        info_result = info_tool.run()
        print(f"[test] info: {str(info_result)[:300]}")
    except Exception as e:
        print(f"[test] info error: {type(e).__name__}: {e}")
        return

    # Pull the URN from whatever shape the response came back as
    author_urn = _extract_linkedin_urn(info_result)
    if not author_urn:
        print(f"[test] could not parse author URN from response — check the dump above.")
        return
    print(f"[test] author URN: {author_urn}")

    # Step 2: actually post
    body = "Capstone smoke test — please ignore."
    print(f"\n[test] step 2: {describe_tool(post_tool)[0]} with author={author_urn[:30]}…")
    try:
        result = post_tool.run(author=author_urn, commentary=body)
        print(f"[test] result: {str(result)[:400]}")
    except Exception as e:
        print(f"[test] error: {type(e).__name__}: {e}")


def _extract_linkedin_urn(response) -> str:
    """LinkedIn GET_MY_INFO returns a dict-ish — find the URN inside it."""
    import json as _json
    s = response if isinstance(response, str) else _json.dumps(response, default=str)
    import re
    # Already a full URN somewhere?
    m = re.search(r"urn:li:(?:person|member):[A-Za-z0-9_\-]+", s)
    if m:
        return m.group(0)
    # OpenID userinfo style: bare `sub` field
    m = re.search(r'"sub"\s*:\s*"([^"]+)"', s)
    if m:
        return f"urn:li:person:{m.group(1)}"
    # Composio's LinkedIn payload: bare `id` field inside `data`
    m = re.search(r'"id"\s*:\s*"([A-Za-z0-9_\-]+)"', s)
    if m:
        return f"urn:li:person:{m.group(1)}"
    return ""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--post", action="store_true",
                   help="LIVE mode only: actually invoke the first tool. Use with care — "
                        "this consumes Composio quota and may publish to a real account.")
    args = p.parse_args()

    if is_configured():
        list_live(do_post=args.post)
    else:
        list_dry_run()


if __name__ == "__main__":
    main()
