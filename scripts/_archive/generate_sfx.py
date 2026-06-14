#!/usr/bin/env python3
"""Generate SFX library via KIE ElevenLabs Sound Effects."""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
OUTPUT_DIR = ROOT / "virtuai" / "data" / "sfx"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SFX = [
    {
        "name": "whoosh",
        "text": "Whoosh transition",
        "duration_seconds": 1.0,
    },
    {
        "name": "boom",
        "text": "Cinematic bass impact boom",
        "duration_seconds": 1.0,
    },
    {
        "name": "riser",
        "text": "Tension riser",
        "duration_seconds": 2.0,
    },
]

def _headers():
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}

def submit(cfg):
    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask",
        headers=_headers(),
        json={
            "model": "elevenlabs/sound-effect-v2",
            "input": {
                "text": cfg["text"],
                "duration_seconds": cfg["duration_seconds"],
                "output_format": "mp3_44100_128",
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    tid = (r.json().get("data") or {}).get("taskId")
    if not tid:
        raise RuntimeError(f"Submit failed: {r.text}")
    return tid

def poll(tid, label):
    deadline = time.time() + 300
    while time.time() < deadline:
        r = httpx.get(f"{KIE_API_BASE}/jobs/recordInfo",
                      params={"taskId": tid}, headers=_headers(), timeout=30)
        r.raise_for_status()
        data = r.json().get("data", {})
        state = data.get("state", "")
        if state in ("success", "completed", "succeed"):
            return data
        if state in ("failed", "error", "fail"):
            raise RuntimeError(f"{label} failed: {data}")
        time.sleep(8)
    raise TimeoutError(label)

def download(data, out):
    rj = json.loads(data.get("resultJson", "{}"))
    urls = rj.get("resultUrls", [])
    if not urls:
        raise RuntimeError(f"No URLs: {rj}")
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        dl = c.get(urls[0])
        dl.raise_for_status()
        out.write_bytes(dl.content)
    print(f"  ✓ {out.name} ({out.stat().st_size} bytes)")

def main():
    print(f"Generating {len(SFX)} SFX in parallel via KIE...")
    with cf.ThreadPoolExecutor(max_workers=len(SFX)) as ex:
        # Submit all
        tasks = [(cfg["name"], ex.submit(submit, cfg)) for cfg in SFX]
        task_ids = [(name, f.result()) for name, f in tasks]
        for name, tid in task_ids:
            print(f"  → submitted: {name} = {tid[:8]}")

        # Fetch all in parallel
        fetch_futs = [(name, ex.submit(poll, tid, name)) for name, tid in task_ids]
        for name, fut in fetch_futs:
            data = fut.result()
            out = OUTPUT_DIR / f"{name}.mp3"
            download(data, out)

    print(f"\nSFX library: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
