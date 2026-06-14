"""
kie_kling.py — Kling 3.0 video generation via KIE.ai unified API.

Features used:
  - Kling 3.0 multi-shot (up to 15s per shot, native audio + lipsync)
  - Element references for face consistency (2-4 images per element)
  - Single API gateway (video + LLM)

Public API:
    generate_video(prompt, image_urls, duration, ...) -> dict
    poll_task(task_id) -> dict
    get_result_url(task_id) -> str
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("virtuai.tools.kie_kling")

KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

POLL_INTERVAL_SEC = 6
POLL_TIMEOUT_SEC = 600


def _headers() -> dict:
    if not KIE_API_KEY:
        raise RuntimeError("KIE_API_KEY not set in .env")
    return {
        "Authorization": f"Bearer {KIE_API_KEY}",
        "Content-Type": "application/json",
    }


def _poll_task(task_id: str) -> dict:
    url = f"{KIE_API_BASE}/jobs/recordInfo"
    deadline = time.time() + POLL_TIMEOUT_SEC
    while time.time() < deadline:
        r = httpx.get(url, params={"taskId": task_id}, headers=_headers(), timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"KIE poll failed: {r.status_code} {r.text[:300]}")
        body = r.json()
        data = body.get("data", {})
        state = data.get("state", "")
        if state in ("completed", "succeed", "success"):
            return data
        if state in ("failed", "error"):
            msg = data.get("failMsg", data.get("failCode", "(no message)"))
            raise RuntimeError(f"KIE task {task_id} failed: {msg}")
        logger.info(f"KIE: task {task_id} state={state}, waiting...")
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"KIE task {task_id} did not complete in {POLL_TIMEOUT_SEC}s")


def _download(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        out_path.write_bytes(r.content)
    logger.info(f"Saved {out_path} ({out_path.stat().st_size} bytes)")


def generate_video(
    *,
    prompt: str,
    image_urls: list[str] | None = None,
    elements: list[dict] | None = None,
    duration: int = 10,
    aspect_ratio: str = "9:16",
    mode: str = "std",
    sound: bool = True,
    output_filename: str | None = None,
) -> dict:
    """
    Generate a video with Kling 3.0 via KIE.ai.

    Args:
        prompt: Scene description.
        image_urls: Start/end frame URLs for character reference.
        elements: Kling element references for face/voice consistency.
            Example: [{"name": "daniel", "description": "young male entrepreneur",
                       "element_input_urls": ["url1", "url2"]}]
        duration: 3-15 seconds.
        aspect_ratio: "9:16" for reels, "16:9" for wide, "1:1" for square.
        mode: "std", "pro", or "4K".
        sound: Enable native audio generation.
        output_filename: Custom output filename.

    Returns:
        {"ok": True, "local_path": "...", "video_url": "...", "task_id": "..."}
    """
    body: dict = {
        "model": "kling-3.0/video",
        "input": {
            "prompt": prompt,
            "duration": str(duration),
            "aspect_ratio": aspect_ratio,
            "mode": mode,
            "sound": sound,
            "multi_shots": False,
        },
    }

    if image_urls:
        body["input"]["image_urls"] = image_urls

    if elements:
        body["input"]["kling_elements"] = elements

    logger.info(
        f"KIE Kling 3.0: dur={duration}s, mode={mode}, sound={sound}, "
        f"images={len(image_urls or [])}, elements={len(elements or [])}"
    )

    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask",
        headers=_headers(),
        json=body,
        timeout=30,
    )

    resp = r.json()
    if resp.get("code") not in (0, 200, None):
        raise RuntimeError(f"KIE submit error: {resp}")

    task_id = (
        resp.get("data", {}).get("task_id")
        or resp.get("data", {}).get("taskId")
    )
    if not task_id:
        raise RuntimeError(f"No task_id in response: {resp}")

    logger.info(f"KIE: task_id={task_id}")

    data = _poll_task(task_id)

    import json as _json
    video_url = ""
    result_json_str = data.get("resultJson", "")
    if result_json_str:
        try:
            rj = _json.loads(result_json_str)
            urls = rj.get("resultUrls", [])
            if urls:
                video_url = urls[0]
        except _json.JSONDecodeError:
            pass

    if not video_url:
        raise RuntimeError(f"No video URL in completed task: {data}")

    ts = int(time.time())
    out_name = output_filename or f"kie_kling3_{ts}.mp4"
    out_path = OUTPUT_DIR / out_name
    _download(video_url, out_path)

    return {
        "ok": True,
        "local_path": str(out_path),
        "video_url": video_url,
        "task_id": task_id,
        "duration": duration,
    }


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser(description="KIE Kling 3.0 smoke test")
    p.add_argument("--prompt", required=True)
    p.add_argument("--duration", type=int, default=5)
    p.add_argument("--images", nargs="*", help="Image URLs for start/end frame")
    args = p.parse_args()

    result = generate_video(
        prompt=args.prompt,
        image_urls=args.images,
        duration=args.duration,
    )
    print(f"\nVideo: {result['local_path']}")
    print(f"Task ID: {result['task_id']}")
