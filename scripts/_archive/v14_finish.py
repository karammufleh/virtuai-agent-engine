#!/usr/bin/env python3
"""
v14 finish — resilient lipsync poller + post.

Polls the existing Kling lipsync task with SSL retry, downloads the result,
then runs post-production using the user's helpers.
"""
from __future__ import annotations

import ssl
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from virtuai.tools.kling_omni import _headers, KLING_API_BASE, LIPSYNC_PATH
from scripts.v14_resume import post_produce, video_dur, log, VOICE, MUSIC, OUTPUT_DIR

# Most recent successful lipsync submit (from v14_phase_d log)
LIPSYNC_TASK_ID = "883856199170138129"

MAX_RETRIES = 8
HTTP_TIMEOUT = 30


def kling_get_retry(url):
    """GET with backoff retry on SSL / connection / read errors."""
    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT, verify=ssl.create_default_context()) as c:
                return c.get(url, headers=_headers())
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError,
                ssl.SSLError, OSError) as e:
            wait = min(60, (2 ** attempt) * 2)
            log.warning(f"  Kling GET retry {attempt+1}/{MAX_RETRIES} after {wait}s: {type(e).__name__}: {e}")
            time.sleep(wait)
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries")


def poll_kling_resilient(task_id: str, timeout_sec: int = 1800) -> dict:
    url = f"{KLING_API_BASE}{LIPSYNC_PATH}/{task_id}"
    log.info(f"Polling Kling lipsync task {task_id}...")
    deadline = time.time() + timeout_sec
    last = ""
    while time.time() < deadline:
        try:
            r = kling_get_retry(url)
            if r.status_code != 200:
                log.warning(f"  Non-200 {r.status_code}: {r.text[:200]}")
                time.sleep(8)
                continue
            body = r.json()
            data = body.get("data", {})
            status = data.get("task_status", "")
            if status != last:
                log.info(f"  status={status}")
                last = status
            if status == "succeed":
                return data
            if status in ("failed",):
                raise RuntimeError(f"Lipsync failed: {data}")
        except Exception as e:
            log.warning(f"  poll transient: {e}")
        time.sleep(10)
    raise TimeoutError("Lipsync poll timed out")


def download_kling_video(data: dict, out: Path) -> Path:
    """Extract video URL from Kling response and download with retries."""
    works = data.get("task_result", {}).get("videos", [])
    if not works:
        raise RuntimeError(f"No videos in result: {data}")
    video_url = works[0].get("url")
    if not video_url:
        raise RuntimeError(f"No url in works[0]: {works[0]}")

    log.info(f"Downloading lipsync video: {video_url}")
    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=600, follow_redirects=True) as c:
                dl = c.get(video_url)
                dl.raise_for_status()
                out.write_bytes(dl.content)
            log.info(f"  ↓ {out.name} ({out.stat().st_size/1024/1024:.1f} MB)")
            return out
        except Exception as e:
            wait = min(60, (2 ** attempt) * 3)
            log.warning(f"  download retry {attempt+1}/{MAX_RETRIES} after {wait}s: {e}")
            time.sleep(wait)
    raise RuntimeError(f"Download failed after {MAX_RETRIES} retries")


def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("v14 FINISH — poll existing lipsync task + post")
    log.info("=" * 60)

    data = poll_kling_resilient(LIPSYNC_TASK_ID)
    synced = OUTPUT_DIR / f"v14_lipsync_{int(time.time())}.mp4"
    download_kling_video(data, synced)

    final = post_produce(synced, MUSIC if MUSIC.exists() else None)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info(f"Duration: {video_dur(final):.1f}s")
    log.info(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
