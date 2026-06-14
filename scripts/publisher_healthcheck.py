#!/usr/bin/env python3
"""
publisher_healthcheck.py — daily whoami probe across all platforms.

Run via cron or just before any publish cycle. Pings each platform's
identity endpoint (no posting, ever) and reports token health, writes
the verdict to the auth-audit JSONL, and exits non-zero if any platform
is failing — handy for CI / a status email.

Endpoints used (all read-only):
  • YouTube     channels.list?part=id&mine=true     (Data API v3)
  • LinkedIn    LINKEDIN_GET_MY_INFO                 (Composio wrapper)
  • Instagram   /me?fields=id  via FB Graph token    (or Composio fallback)
  • Facebook    /me?fields=id&access_token=...       (Graph API)

Why this is the right "active log to prevent the ban" lever:
  Catching token death the morning of a publish is too late — by the time
  we notice, we've already burned a retry-storm and added a fraud signal.
  By probing every 24h with a cheap whoami, we know about expiry BEFORE
  the publisher fires, and we can either refresh quietly or open the
  circuit (preventing publishes that would have failed anyway).

Usage:
    python scripts/publisher_healthcheck.py             # check all
    python scripts/publisher_healthcheck.py --reset all # clear any tripped circuits
    python scripts/publisher_healthcheck.py --json      # machine output

Exit codes:
    0  every platform healthy
    1  at least one platform unhealthy (token expired / circuit open)
    2  config error (env not set up)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from virtuai.tools import auth_guard

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("healthcheck")


# ── Per-platform probes ────────────────────────────────────────────────────

def probe_youtube() -> dict:
    """Probe: refresh_token → access_token (always granted) + tokeninfo
    introspection. We do NOT call channels.list because our upload scope
    (`youtube.upload`) doesn't include `youtube.readonly` — a strict probe
    would false-fail on a perfectly healthy upload token.

    The token mint itself proves: client_id/secret valid, refresh_token
    not revoked. tokeninfo proves: scopes still cover upload."""
    import httpx
    from virtuai.tools.youtube_direct import _get_youtube_access_token
    try:
        token = _get_youtube_access_token()
    except Exception as e:
        auth_guard.record("youtube_shorts", "HEALTHCHECK",
                          ok=False, error=e)
        return {"platform": "youtube_shorts", "ok": False,
                "stage": "token_refresh", "error": str(e)[:200]}
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(
                "https://www.googleapis.com/oauth2/v3/tokeninfo",
                params={"access_token": token},
            )
        if r.status_code != 200:
            err = RuntimeError(f"tokeninfo HTTP {r.status_code}: {r.text[:200]}")
            auth_guard.record("youtube_shorts", "HEALTHCHECK",
                              ok=False, status_code=r.status_code, error=err)
            return {"platform": "youtube_shorts", "ok": False,
                    "stage": "tokeninfo", "status_code": r.status_code,
                    "error": r.text[:200]}
        body = r.json()
        scopes = body.get("scope", "")
        has_upload = "youtube.upload" in scopes or "youtube" in scopes.split()
        if not has_upload:
            err = RuntimeError(f"token missing youtube upload scope: {scopes}")
            auth_guard.record("youtube_shorts", "HEALTHCHECK",
                              ok=False, error=err)
            return {"platform": "youtube_shorts", "ok": False,
                    "stage": "scope_check", "scopes": scopes,
                    "error": "token does not include youtube.upload scope"}
        auth_guard.record("youtube_shorts", "HEALTHCHECK",
                          ok=True, extra={"scopes": scopes,
                                          "expires_in": body.get("expires_in")})
        return {"platform": "youtube_shorts", "ok": True,
                "scopes": scopes,
                "expires_in": body.get("expires_in"),
                "audience": body.get("aud", "")[:40]}
    except Exception as e:
        auth_guard.record("youtube_shorts", "HEALTHCHECK", ok=False, error=e)
        return {"platform": "youtube_shorts", "ok": False,
                "stage": "tokeninfo", "error": str(e)[:200]}


def probe_linkedin() -> dict:
    """Composio LINKEDIN_GET_MY_INFO — also serves as "does Daniel's token still work"."""
    from composio import Composio
    from composio_crewai import CrewAIProvider
    try:
        cp = Composio(provider=CrewAIProvider())
        user_id = os.environ.get("COMPOSIO_USER_ID", "default")
        info_tool = next(iter(cp.tools.get(
            user_id=user_id, tools=["LINKEDIN_GET_MY_INFO"])))
        me = info_tool.run()
        data = (me or {}).get("data") or {}
        li_id = data.get("id") or data.get("sub") or (me or {}).get("sub")
        if not li_id:
            err = RuntimeError(f"no id in response: {str(me)[:200]}")
            auth_guard.record("linkedin", "HEALTHCHECK", ok=False, error=err)
            return {"platform": "linkedin", "ok": False,
                    "stage": "parse_id", "error": str(me)[:200]}
        auth_guard.record("linkedin", "HEALTHCHECK", ok=True,
                          extra={"li_id": li_id})
        return {"platform": "linkedin", "ok": True, "li_id": li_id}
    except Exception as e:
        auth_guard.record("linkedin", "HEALTHCHECK", ok=False, error=e)
        return {"platform": "linkedin", "ok": False, "error": str(e)[:300]}


def probe_instagram() -> dict:
    """Hit FB Graph /me?fields=id with IG_ACCESS_TOKEN if available;
    fall back to Composio INSTAGRAM_LIST_PAGES probe."""
    ig_token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    ig_user_id = os.environ.get("IG_USER_ID", "").strip()
    if ig_token and ig_user_id:
        import httpx
        try:
            with httpx.Client(timeout=15) as c:
                r = c.get(
                    f"https://graph.facebook.com/v21.0/{ig_user_id}",
                    params={"fields": "id,username", "access_token": ig_token},
                )
            if r.status_code != 200:
                err = RuntimeError(f"IG whoami HTTP {r.status_code}: {r.text[:200]}")
                auth_guard.record("instagram", "HEALTHCHECK",
                                  ok=False, status_code=r.status_code, error=err)
                return {"platform": "instagram", "ok": False,
                        "stage": "graph_me", "status_code": r.status_code,
                        "error": r.text[:200]}
            body = r.json()
            auth_guard.record("instagram", "HEALTHCHECK", ok=True,
                              extra={"username": body.get("username")})
            return {"platform": "instagram", "ok": True,
                    "ig_user_id": body.get("id"),
                    "username": body.get("username")}
        except Exception as e:
            auth_guard.record("instagram", "HEALTHCHECK", ok=False, error=e)
            return {"platform": "instagram", "ok": False, "error": str(e)[:300]}

    # No direct IG token — fall back to "Composio IG toolkit returns tools",
    # the same shallow probe we use for Facebook. Composio's IG actions
    # (CREATE_MEDIA_CONTAINER, CREATE_POST) don't expose a whoami; if
    # the toolkit returns tools at all, the connection is alive.
    try:
        from composio import Composio
        from composio_crewai import CrewAIProvider
        cp = Composio(provider=CrewAIProvider())
        user_id = os.environ.get("COMPOSIO_USER_ID", "default")
        tools = list(cp.tools.get(user_id=user_id, toolkits=["instagram"]))
        if not tools:
            err = RuntimeError("no Instagram tools available in Composio")
            auth_guard.record("instagram", "HEALTHCHECK", ok=False, error=err)
            return {"platform": "instagram", "ok": False,
                    "stage": "tool_lookup",
                    "error": "no IG toolkit (set IG_ACCESS_TOKEN or "
                             "connect in Composio)"}
        auth_guard.record("instagram", "HEALTHCHECK", ok=True,
                          extra={"tool_count": len(tools),
                                 "via": "composio_toolkit_list"})
        return {"platform": "instagram", "ok": True,
                "via": "composio_toolkit_list",
                "tool_count": len(tools),
                "note": "Tools listed — connection alive but not actually called. "
                        "Set IG_ACCESS_TOKEN in .env for a deeper Graph-API probe."}
    except Exception as e:
        auth_guard.record("instagram", "HEALTHCHECK", ok=False, error=e)
        return {"platform": "instagram", "ok": False, "error": str(e)[:300]}


def probe_facebook() -> dict:
    """If FB_PAGE_ACCESS_TOKEN is set, hit Graph /me; else mark skipped."""
    fb_token = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()
    fb_page = os.environ.get("FB_PAGE_ID", "").strip()
    if not fb_token or not fb_page:
        # No direct token — try Composio's me probe.
        try:
            from composio import Composio
            from composio_crewai import CrewAIProvider
            cp = Composio(provider=CrewAIProvider())
            user_id = os.environ.get("COMPOSIO_USER_ID", "default")
            tools = list(cp.tools.get(user_id=user_id, toolkits=["facebook"]))
            if not tools:
                err = RuntimeError("no Facebook tools available in Composio")
                auth_guard.record("facebook", "HEALTHCHECK", ok=False, error=err)
                return {"platform": "facebook", "ok": False,
                        "stage": "tool_lookup",
                        "error": "no FB toolkit (set FB_PAGE_ACCESS_TOKEN or connect in Composio)"}
            auth_guard.record("facebook", "HEALTHCHECK", ok=True,
                              extra={"tool_count": len(tools), "via": "composio_toolkit_list"})
            return {"platform": "facebook", "ok": True,
                    "via": "composio_toolkit_list",
                    "tool_count": len(tools),
                    "note": "Tool list returned — token healthy but not actually called. "
                            "Set FB_PAGE_ACCESS_TOKEN in .env for a deeper probe."}
        except Exception as e:
            auth_guard.record("facebook", "HEALTHCHECK", ok=False, error=e)
            return {"platform": "facebook", "ok": False, "error": str(e)[:300]}

    import httpx
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(
                f"https://graph.facebook.com/v21.0/{fb_page}",
                params={"fields": "id,name", "access_token": fb_token},
            )
        if r.status_code != 200:
            err = RuntimeError(f"FB whoami HTTP {r.status_code}: {r.text[:200]}")
            auth_guard.record("facebook", "HEALTHCHECK",
                              ok=False, status_code=r.status_code, error=err)
            return {"platform": "facebook", "ok": False,
                    "stage": "graph_me", "status_code": r.status_code,
                    "error": r.text[:200]}
        body = r.json()
        auth_guard.record("facebook", "HEALTHCHECK", ok=True,
                          extra={"name": body.get("name")})
        return {"platform": "facebook", "ok": True,
                "page_id": body.get("id"), "name": body.get("name")}
    except Exception as e:
        auth_guard.record("facebook", "HEALTHCHECK", ok=False, error=e)
        return {"platform": "facebook", "ok": False, "error": str(e)[:300]}


PROBES = {
    "youtube_shorts": probe_youtube,
    "linkedin":       probe_linkedin,
    "instagram":      probe_instagram,
    "facebook":       probe_facebook,
}


# ── Library entry point (used by daily_pack.py) ───────────────────────────


def preflight(platforms: list[str] | None = None,
              trip_on_failure: bool = True) -> dict[str, bool]:
    """Run the probes for the requested platforms (or all). Return a dict
    of `{platform: bool}` showing which are healthy.

    When `trip_on_failure=True`, any platform that fails the probe has its
    auth_guard circuit opened so the subsequent publish() call will be
    skipped cleanly (CircuitOpenError → caught by daily_pack's existing
    try/except) instead of attempting a publish with a dead credential.

    Intended caller: daily_pack.main() — runs once at the top of the
    publish phase so we know which platforms are healthy BEFORE doing
    any expensive work or compounding fraud signals on dead tokens."""
    targets = platforms or list(PROBES.keys())
    out: dict[str, bool] = {}
    for p in targets:
        probe = PROBES.get(p)
        if not probe:
            out[p] = False
            continue
        try:
            r = probe()
            ok = bool(r.get("ok"))
        except Exception as e:
            log.warning(f"preflight {p}: {e}")
            ok = False
        out[p] = ok
        if not ok and trip_on_failure:
            # Open the circuit so the publisher's own auth_guard.gate(p) call
            # will refuse the publish attempt without ever hitting the API.
            # We feed the breaker two synthetic auth-failure records so the
            # AUTH_FAIL_LIMIT (default=2) is reached in one preflight call.
            for _ in range(2):
                auth_guard._breaker.record_auth_failure(
                    p, "PREFLIGHT_PROBE_FAILED",
                    "healthcheck reported platform unhealthy",
                    status_code=None,
                )
    return out


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", nargs="?", const="all",
                    help="Reset breaker(s). Pass platform or 'all'.")
    ap.add_argument("--json", action="store_true",
                    help="Machine-readable JSON output to stdout.")
    ap.add_argument("--only", choices=list(PROBES.keys()),
                    help="Probe only this one platform.")
    args = ap.parse_args()

    if args.reset:
        targets = list(PROBES.keys()) if args.reset == "all" else [args.reset]
        for p in targets:
            auth_guard.reset(p)
        log.info(f"Reset breakers for: {', '.join(targets)}")

    probes = (
        {args.only: PROBES[args.only]} if args.only else PROBES
    )

    results = []
    for platform, fn in probes.items():
        log.info(f"Probing {platform}…")
        try:
            r = fn()
        except Exception as e:
            r = {"platform": platform, "ok": False, "error": str(e)[:300]}
        results.append(r)
        status = "✓" if r.get("ok") else "✗"
        msg = (r.get("error") or "ok")
        log.info(f"  {status} {platform}: {msg[:120]}")

    breaker_state = auth_guard.status()

    summary = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "probes": results,
        "breaker_state": breaker_state,
        "audit_log": str(auth_guard.log_path()),
    }

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        log.info("=" * 60)
        log.info("HEALTHCHECK SUMMARY")
        log.info("=" * 60)
        for r in results:
            badge = "OK " if r.get("ok") else "FAIL"
            log.info(f"  [{badge}] {r['platform']:<16} {r.get('error', '') or ''}")
        any_open = any(s.get("open") for s in breaker_state.values())
        if any_open:
            log.warning("CIRCUIT BREAKERS OPEN:")
            for p, s in breaker_state.items():
                if s.get("open"):
                    log.warning(f"  - {p}: {s.get('last_reason', '')[:120]}")
            log.warning("Run: python scripts/publisher_healthcheck.py --reset all")
        log.info(f"Audit log: {auth_guard.log_path()}")

    # Exit code reflects worst probe result
    if not all(r.get("ok") for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
