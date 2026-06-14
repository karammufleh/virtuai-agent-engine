"""
Asset-download helper with a NARROWLY-SCOPED SSL workaround for KIE's
temporary CDN.

Default behavior: full SSL verification, always. The pipeline is
unchanged.

Opt-in workaround (requires BOTH conditions):
    1. environment variable `VIRTUAI_TRUST_KIE_CDN=true`
    2. URL host is in `_KIE_CDN_ALLOWLIST`

When (and only when) both are true, the helper disables `verify` for
that single httpx request and logs a WARNING naming the host. Every
other host — including `api.kie.ai` — still uses full verification.

This exists because KIE's temporary CDN (`tempfile.aiquickdraw.com`)
occasionally serves an incomplete certificate chain. Both `curl` and
Python (with the freshest `certifi`) reject it. The workaround is
strictly for downloading already-rendered assets; it never touches the
KIE API itself, Composio, or YouTube Direct.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("virtuai.utils.asset_download")

# Hostnames where relaxed verification is permitted iff the env var is set.
# This list MUST stay tiny and MUST NEVER include API endpoints.
_KIE_CDN_ALLOWLIST: frozenset[str] = frozenset({
    "tempfile.aiquickdraw.com",
})


# ── Hosts that NEVER get relaxed verification, even with the env var set ──
# Listed defensively so a future edit can't drop them into the allowlist.
_NEVER_RELAX_HOSTS: frozenset[str] = frozenset({
    "api.kie.ai",
    "kieai.redpandaai.co",         # KIE file-stream upload host
    "api.json2video.com",
    "api.creatomate.com",
    "api.shotstack.io",
    "api.elevenlabs.io",
    "api.openai.com",
    "api.anthropic.com",
})


def _trust_cdn_enabled() -> bool:
    """True iff the opt-in env var is explicitly enabled. Default: false."""
    return os.environ.get("VIRTUAI_TRUST_KIE_CDN", "false").lower() in (
        "1", "true", "yes", "on"
    )


def _host_from(url: str) -> str:
    """Extract the lower-cased hostname from a URL. Empty string on parse error."""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _should_relax_verify(url: str) -> bool:
    """
    Decision gate: should this ONE httpx call run with `verify=False`?

    Returns True iff BOTH:
      1. `VIRTUAI_TRUST_KIE_CDN=true` in the environment, AND
      2. The URL's hostname is in the KIE CDN allowlist.

    Returns False for everything else, including:
      - any API endpoint (api.kie.ai, etc.)
      - any unknown / arbitrary domain
      - URLs that fail to parse
    """
    if not _trust_cdn_enabled():
        return False
    host = _host_from(url)
    if not host:
        return False
    if host in _NEVER_RELAX_HOSTS:
        # Belt-and-braces safety net — never relax for API endpoints.
        return False
    return host in _KIE_CDN_ALLOWLIST


def download_generated_asset(
    url: str,
    output_path: Path | str,
    *,
    timeout: float = 600.0,
) -> Path:
    """
    Download a single rendered asset from `url` to `output_path` on disk.

    - SSL verification is ON by default.
    - If `_should_relax_verify(url)` returns True, verification is disabled
      for THIS one request and a WARNING is logged naming the host.
    - Errors (network, HTTP, SSL) are raised — never silently swallowed.

    Returns the resolved `Path` of the written file.
    """
    if not url:
        raise ValueError("download_generated_asset: empty URL")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    relax = _should_relax_verify(url)
    if relax:
        host = _host_from(url)
        logger.warning(
            "[KIE-CDN-WORKAROUND] SSL verification TEMPORARILY DISABLED for "
            "asset download from host '%s'. This is permitted only because "
            "VIRTUAI_TRUST_KIE_CDN=true AND the host is in the KIE CDN "
            "allowlist. The KIE API itself, Composio, and YouTube Direct "
            "are NOT affected and still use full SSL verification. "
            "Unset VIRTUAI_TRUST_KIE_CDN (the default) to restore full "
            "verification for this path.",
            host,
        )

    with httpx.Client(timeout=timeout, follow_redirects=True, verify=not relax) as c:
        resp = c.get(url)
        resp.raise_for_status()
        out.write_bytes(resp.content)
    return out
