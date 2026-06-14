"""
kie_upload.py — KIE.ai File Stream Upload (replaces flaky tmpfiles.org).

KIE-hosted CDN, ~3-day TTL, same Bearer auth as the rest of the API.
The previous tmpfiles.org approach kept failing mid-job with
"Image fetch failed. Check access settings or use our File Upload API
instead." This is THAT API.
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
logger = logging.getLogger("virtuai.tools.kie_upload")

KIE_UPLOAD_URL = "https://kieai.redpandaai.co/api/file-stream-upload"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()

UPLOAD_PATH_DEFAULT = "virtuai/uploads"
MAX_RETRIES = 4


def upload(filepath: str | Path,
           *, upload_path: str = UPLOAD_PATH_DEFAULT,
           filename: str | None = None,
           timeout: int = 300) -> str:
    """Upload a local file via KIE's stream endpoint. Returns downloadUrl."""
    if not KIE_API_KEY:
        raise RuntimeError("KIE_API_KEY not set")
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(p)

    headers = {"Authorization": f"Bearer {KIE_API_KEY}"}
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with open(p, "rb") as f:
                files = {"file": (filename or p.name, f, "application/octet-stream")}
                data = {"uploadPath": upload_path}
                if filename:
                    data["fileName"] = filename
                resp = httpx.post(
                    KIE_UPLOAD_URL, headers=headers,
                    files=files, data=data, timeout=timeout,
                )
            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                raise RuntimeError(f"KIE upload not successful: {body}")
            url = (body.get("data") or {}).get("downloadUrl")
            if not url:
                raise RuntimeError(f"No downloadUrl in: {body}")
            logger.info(f"KIE upload ok: {p.name} -> {url}")
            return url
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError,
                httpx.RemoteProtocolError, httpx.HTTPStatusError) as e:
            last_err = e
            wait = (2 ** attempt) * 3
            logger.warning(f"KIE upload retry {attempt+1}/{MAX_RETRIES} after {wait}s: {e}")
            time.sleep(wait)
        except Exception as e:
            last_err = e
            wait = (2 ** attempt) * 3
            logger.warning(f"KIE upload error {attempt+1}/{MAX_RETRIES}: {e}")
            time.sleep(wait)
    raise RuntimeError(f"KIE upload failed after {MAX_RETRIES} attempts: {last_err}")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(upload(sys.argv[1]))
