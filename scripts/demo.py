"""
scripts/demo.py — Single-command VirtuAI capstone demo.

Picks a fixed seed scenario, runs the 8-agent crew, and prints the
final output paths. Designed to be the one command a reviewer runs
during the live demo:

    python scripts/demo.py

What it does:
    1. Health-checks the local backend (:8765) and the API server (:9090).
    2. Builds the 8-agent crew with the locked persona.
    3. Triggers the daily pack (reel + portrait + carousel) via the
       existing FastAPI endpoint /run-pack so the run uses the production
       cloud path (Kling 3.0 + Nano Banana 2 + Suno).
    4. Polls until the pack finishes.
    5. Prints the produced asset paths and a one-line per-agent verdict
       trail from autopilot_history.json.

Inputs:
    .env (KIE_API_KEY required; COMPOSIO_API_KEY optional for live posts)

Outputs:
    virtuai/data/generated_videos/*.mp4
    virtuai/data/generated_images/*.png
    virtuai/data/content_packages/*.json
    virtuai/data/autopilot_history.json (new entry appended)

Flags:
    --no-publish    Run the crew but stop before Publisher (cloud renders
                    happen; nothing is pushed to Instagram/YouTube/LinkedIn).
    --kind reel     Generate just a reel (skips portrait + carousel).
    --kind portrait Generate just a portrait still.
    --kind carousel Generate just a 5-slide carousel.
    --kind pack     Default — generate all three.

Exit codes:
    0  Pack completed (or --no-publish stopped cleanly)
    1  Backend/API not running; pre-flight failed
    2  Run started but did not complete in the 15-min budget
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv()

API = "http://localhost:9090"
BACKEND = "http://localhost:8765"
PACK_TIMEOUT_SEC = 15 * 60
POLL_SEC = 8


def _hr(label: str) -> None:
    print(f"\n── {label} " + "─" * (60 - len(label) - 4))


def _check(url: str, name: str) -> bool:
    try:
        r = httpx.get(url, timeout=4)
        return r.status_code == 200
    except Exception:
        return False


def pre_flight() -> bool:
    _hr("Pre-flight")
    backend_ok = _check(f"{BACKEND}/healthz", "backend")
    api_ok = _check(f"{API}/healthz", "API")
    kie = bool(os.environ.get("KIE_API_KEY", "").strip())
    composio = bool(os.environ.get("COMPOSIO_API_KEY", "").strip())
    print(f"  Local backend (:8765)   : {'✓ up' if backend_ok else '✗ down (only required for the local-fallback tools)'}")
    print(f"  API server   (:9090)   : {'✓ up' if api_ok else '✗ down — REQUIRED'}")
    print(f"  KIE_API_KEY            : {'✓ set' if kie else '✗ missing — REQUIRED'}")
    print(f"  COMPOSIO_API_KEY       : {'✓ live publish mode' if composio else '○ dry-run publish (no live posts)'}")
    return api_ok and kie


def kick_off(kind: str, no_publish: bool) -> str | None:
    _hr(f"Trigger /run-pack (kind={kind}, publish={'no' if no_publish else 'yes'})")
    body = {
        "kind":    kind,
        "persona": "virtuai_mentor",
        "publish": not no_publish,
        "demo":    True,
    }
    try:
        r = httpx.post(f"{API}/run-pack", json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ✗ /run-pack failed: {e}")
        return None
    task_id = data.get("task_id") or data.get("id")
    print(f"  ✓ task_id: {task_id}")
    return task_id


def poll(task_id: str) -> dict | None:
    _hr("Polling")
    deadline = time.time() + PACK_TIMEOUT_SEC
    while time.time() < deadline:
        try:
            r = httpx.get(f"{API}/status/{task_id}", timeout=10)
            r.raise_for_status()
            s = r.json()
        except Exception as e:
            print(f"  poll error: {e}")
            time.sleep(POLL_SEC)
            continue
        state = (s.get("state") or s.get("status") or "").lower()
        stage = s.get("stage") or s.get("step") or "?"
        print(f"  state={state:<10} stage={stage}")
        if state in ("done", "succeeded", "completed", "success"):
            return s
        if state in ("failed", "error", "fail"):
            print(f"  ✗ run failed: {json.dumps(s)[:400]}")
            return None
        time.sleep(POLL_SEC)
    print(f"  ✗ timed out after {PACK_TIMEOUT_SEC}s")
    return None


def summarize(result: dict) -> None:
    _hr("Outputs")
    pack = result.get("pack") or result.get("artifacts") or {}
    for key in ("reel", "portrait", "carousel"):
        paths = pack.get(key)
        if not paths:
            continue
        if isinstance(paths, str):
            print(f"  {key:9} → {paths}")
        else:
            for p in (paths if isinstance(paths, list) else [paths]):
                print(f"  {key:9} → {p}")

    hist = ROOT / "virtuai/data/autopilot_history.json"
    if hist.exists():
        try:
            entries = json.loads(hist.read_text(encoding="utf-8"))
            last = entries[-1] if isinstance(entries, list) else entries.get("runs", [])[-1]
            _hr("Latest autopilot entry")
            for k in ("started_at", "topic", "instagram_id",
                      "youtube_url", "linkedin_urn", "analyzer_verdict"):
                if k in last:
                    print(f"  {k:18}: {last[k]}")
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="VirtuAI single-command demo")
    parser.add_argument("--kind", choices=["pack", "reel", "portrait", "carousel"],
                        default="pack", help="Which artifact(s) to generate.")
    parser.add_argument("--no-publish", action="store_true",
                        help="Render but skip the Publisher step.")
    args = parser.parse_args()

    print("┌─ VirtuAI Capstone Demo ─────────────────────────────────────────┐")
    print("│ Persona: virtuai_mentor (Daniel Calder)                         │")
    print("│ Path:    KIE.ai → Kling 3.0 + Nano Banana 2 + Suno              │")
    print("│ Publish: Composio (LinkedIn/IG) + YouTube direct                │")
    print("└─────────────────────────────────────────────────────────────────┘")

    if not pre_flight():
        print("\n✗ Pre-flight failed. Start the API server with:")
        print("    uvicorn scripts.api_server:app --host 0.0.0.0 --port 9090")
        print("  and ensure KIE_API_KEY is set in .env.")
        return 1

    task_id = kick_off(args.kind, args.no_publish)
    if not task_id:
        return 1
    result = poll(task_id)
    if result is None:
        return 2
    summarize(result)
    print("\n✓ Demo complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
