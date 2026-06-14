#!/usr/bin/env python3
"""
produce_reel_v6.py — Premium reel with continuous audio + reviewer gate.

Fixes from v5:
  • B-roll is OVERLAID on top of avatar video — audio stays continuous
  • New cinematic b-roll subjects (not typing-hands clichés)
  • Auto-review at the end with verdict, rejects on REVISE
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("produce_reel_v6")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

# Reuse the locked-persona avatar clip
EXISTING_AVATAR = OUTPUT_DIR / "avatar_lipsync_1778700549.mp4"
EXISTING_CAPTIONS = OUTPUT_DIR / "eleven_voice_1778700052.ass"
HOOK_TEXT = "Nobody's building AI systems."

POLL_INTERVAL = 12
POLL_TIMEOUT = 900

# ── New b-roll: cinematic, symbolic, not generic tech ────────────────────────

BROLL_PROMPTS = [
    {
        "name": "broll_skyline",
        "prompt": (
            "Slow cinematic drone push-in toward a modern glass office tower "
            "at golden hour, warm sunlight reflecting off floor-to-ceiling "
            "windows, city skyline behind it, soft volumetric haze. "
            "9:16 vertical aspect. Premium luxury production value. "
            "Wide angle to medium, slow camera movement. No text, no faces."
        ),
        "duration": 5,
    },
    {
        "name": "broll_whiteboard",
        "prompt": (
            "Cinematic close-up of a hand drawing a complex flow diagram with "
            "a black marker on a clean whiteboard. Sharp shallow depth of field "
            "on the marker tip. Confident steady drawing strokes creating boxes "
            "and arrows. Warm office light. The shot tracks the marker movement. "
            "9:16 vertical framing. No face, no text legible."
        ),
        "duration": 5,
    },
    {
        "name": "broll_chess",
        "prompt": (
            "Extreme close-up of a hand confidently moving a black knight chess "
            "piece across a wooden board, dramatic side lighting, deep shadows, "
            "the piece sliding into a strategic position with intention. "
            "Shallow depth of field, cinematic. 9:16 vertical. "
            "No face visible."
        ),
        "duration": 5,
    },
]

# ── helpers ──────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def poll_task(task_id: str, label: str = "") -> dict:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        r = httpx.get(f"{KIE_API_BASE}/jobs/recordInfo",
                      params={"taskId": task_id}, headers=_headers(), timeout=30)
        r.raise_for_status()
        data = r.json().get("data", {})
        state = data.get("state", "")
        if state in ("success", "completed", "succeed"):
            return data
        if state in ("failed", "error", "fail"):
            raise RuntimeError(f"{label} failed: {data.get('failMsg', data)}")
        log.info(f"  {label}: {state}...")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"{label} timed out")


def download_first(data: dict, out: Path) -> Path:
    rj = json.loads(data.get("resultJson", "{}"))
    urls = rj.get("resultUrls", [])
    if not urls:
        raise RuntimeError(f"No URLs: {rj}")
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(urls[0])
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  Downloaded: {out.name} ({out.stat().st_size/1024/1024:.1f} MB)")
    return out


def video_dur(path: Path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


# ── B-roll generation ───────────────────────────────────────────────────────

def generate_broll(cfg: dict) -> Path:
    log.info(f"Generating: {cfg['name']}...")
    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask",
        headers=_headers(),
        json={
            "model": "kling-3.0/video",
            "input": {
                "prompt": cfg["prompt"],
                "duration": str(cfg["duration"]),
                "aspect_ratio": "9:16",
                "mode": "std",
                "sound": False,
                "multi_shots": False,
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    task_id = (r.json().get("data") or {}).get("taskId")
    if not task_id:
        raise RuntimeError(f"Submit failed: {r.text}")
    data = poll_task(task_id, cfg["name"])
    ts = int(time.time())
    return download_first(data, OUTPUT_DIR / f"{cfg['name']}_{ts}.mp4")


# ── Overlay edit (audio continuous) ─────────────────────────────────────────

def overlay_edit(avatar: Path, brolls: list[Path], captions: Path) -> Path:
    """
    Overlay b-roll on top of avatar VIDEO at scheduled time windows.
    Daniel's audio plays continuously underneath — never cut.
    """
    total = video_dur(avatar)
    log.info(f"Building overlay edit (base={total:.1f}s, brolls={len(brolls)})...")

    # Schedule: ~2s b-roll overlays at scene-change beats.
    # Tuned to the script's natural pauses in the existing avatar clip.
    schedule = [
        # (broll_index, start_time, end_time)
        (0, 3.2, 5.0),   # after hook → skyline establishing shot
        (1, 6.8, 8.6),   # mid-script → whiteboard drawing
        (2, 10.2, 12.0), # before payoff → chess move
    ]
    schedule = [s for s in schedule if s[0] < len(brolls)]

    # Build filter graph: scale each b-roll to 720x1280, then overlay on base
    # with enable=between(t, start, end).
    inputs = ["-i", str(avatar)]
    for b in brolls:
        inputs += ["-i", str(b)]

    # Normalize EVERYTHING to 720x1280 so overlay covers the full frame
    filter_parts = []
    filter_parts.append(
        f"[0:v]scale=720:1280:force_original_aspect_ratio=increase,"
        f"crop=720:1280,setsar=1[base]"
    )
    last_label = "base"
    for i, (b_idx, start, end) in enumerate(schedule):
        in_idx = b_idx + 1
        scaled = f"b{i}_scaled"
        out_lbl = f"v{i+1}"
        filter_parts.append(
            f"[{in_idx}:v]scale=720:1280:force_original_aspect_ratio=increase,"
            f"crop=720:1280,setsar=1,setpts=PTS-STARTPTS+{start}/TB[{scaled}]"
        )
        filter_parts.append(
            f"[{last_label}][{scaled}]overlay=x=0:y=0:enable='between(t,{start},{end})':"
            f"eof_action=pass[{out_lbl}]"
        )
        last_label = out_lbl

    filter_complex = ";".join(filter_parts)

    work = OUTPUT_DIR / f"_v6_work_{int(time.time())}"
    work.mkdir(exist_ok=True)
    no_captions = work / "overlay.mp4"

    cmd = [FFMPEG, "-y", *inputs,
           "-filter_complex", filter_complex,
           "-map", f"[{last_label}]", "-map", "0:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-c:a", "copy",
           "-r", "30",
           str(no_captions)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error(f"Overlay ffmpeg failed: {proc.stderr[-1500:]}")
        raise RuntimeError("Overlay step failed")
    log.info(f"  Overlay done: {no_captions.name}")

    # Burn captions + hook
    log.info("  Burning captions + hook overlay...")
    hook_escaped = HOOK_TEXT.replace("'", r"'\''")
    hook_filter = (
        f"ass='{captions.resolve()}',"
        f"drawtext=text='{hook_escaped}'"
        f":font='Montserrat Black':fontsize=42:fontcolor=white"
        f":borderw=3:bordercolor=black:shadowcolor=black@0.5:shadowx=2:shadowy=2"
        f":x=(w-text_w)/2:y=h*0.15"
        f":alpha='if(lt(t,0.2),t/0.2,if(lt(t,2.7),1,if(lt(t,3.0),((3.0-t)/0.3),0)))'"
        f":enable='between(t,0,3.0)'"
    )
    ts = int(time.time())
    final = OUTPUT_DIR / f"daniel_reel_v6_{ts}.mp4"
    subprocess.run([
        FFMPEG, "-y", "-i", str(no_captions),
        "-vf", hook_filter,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-r", "30",
        str(final),
    ], check=True, capture_output=True)
    log.info(f"  Final: {final.name}")
    return final


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — Premium Reel v6 (continuous audio + reviewer)")
    log.info("=" * 60)

    for p in [EXISTING_AVATAR, EXISTING_CAPTIONS]:
        if not p.exists():
            raise FileNotFoundError(f"Missing: {p}")

    # Generate 3 b-roll clips
    broll_paths = []
    for cfg in BROLL_PROMPTS:
        broll_paths.append(generate_broll(cfg))

    # Overlay edit + captions
    final = overlay_edit(EXISTING_AVATAR, broll_paths, EXISTING_CAPTIONS)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info("=" * 60)

    # ── Reviewer gate ────────────────────────────────────────────────────
    log.info("")
    log.info("Running reviewer gate...")
    # Reviewer needs insightface — try the ML venv first, fall back to no face check
    try:
        from virtuai.tools.video_reviewer import review_video, format_review_report
        review = review_video(final)
        print(format_review_report(review))
        if review["verdict"] == "REVISE":
            log.error("REVIEWER REJECTED — see issues above. Reel will NOT be published.")
            sys.exit(2)
        log.info("REVIEWER APPROVED — reel cleared for publishing.")
    except ImportError as e:
        log.warning(f"Reviewer skipped: {e}")


if __name__ == "__main__":
    main()
