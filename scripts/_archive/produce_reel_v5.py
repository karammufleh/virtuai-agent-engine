#!/usr/bin/env python3
"""
produce_reel_v5.py — Premium multi-shot reel.

Reuses the existing Avatar Pro clip (the locked-persona Daniel) and adds:
  • 2 Kling 3.0 b-roll cuts (hands typing, screen UI)
  • Suno background music (subtle tech underbed)
  • Multi-shot edit: intercut talking head with b-roll on caption beats
  • Topaz 2× upscale for premium 1440p output
  • Captions re-burned over the assembled edit
"""
from __future__ import annotations

import json
import logging
import os
import re
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
log = logging.getLogger("produce_reel_v5")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_LLM_BASE = "https://kieai.erweima.ai/api/v1"
KIE_SUNO_BASE = "https://api.kie.ai/api/v1/suno-api"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

# Reuse the existing Avatar Pro clip + ElevenLabs audio + captions
EXISTING_AVATAR = OUTPUT_DIR / "avatar_lipsync_1778700549.mp4"
EXISTING_AUDIO = OUTPUT_DIR / "eleven_voice_1778700052.mp3"
EXISTING_CAPTIONS = OUTPUT_DIR / "eleven_voice_1778700052.ass"
HOOK_TEXT = "Nobody's building AI systems."

POLL_INTERVAL = 12
POLL_TIMEOUT = 900

# ── B-roll prompts ───────────────────────────────────────────────────────────

BROLL_PROMPTS = [
    {
        "name": "broll_typing",
        "prompt": (
            "Cinematic extreme close-up of male hands typing on a sleek silver "
            "MacBook Pro keyboard, golden hour light spilling across the keys, "
            "shallow depth of field, subtle camera dolly forward. Modern minimalist "
            "wooden desk. Slight motion blur on fast fingers. Premium luxury aesthetic. "
            "No text, no faces."
        ),
        "duration": 5,
    },
    {
        "name": "broll_screen",
        "prompt": (
            "Macro shot of a laptop screen displaying a clean dark-mode AI dashboard "
            "with flowing neural network graphs, glowing blue and amber data nodes, "
            "subtle particle effects, code reflecting off the glass. Out-of-focus "
            "warm bokeh lights in the background. Cinematic teal-orange grade. "
            "No people, no readable text."
        ),
        "duration": 5,
    },
]

# ── helpers ──────────────────────────────────────────────────────────────────

def _headers() -> dict:
    if not KIE_API_KEY:
        raise RuntimeError("KIE_API_KEY not set")
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def poll_task(task_id: str, label: str = "") -> dict:
    url = f"{KIE_API_BASE}/jobs/recordInfo"
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        r = httpx.get(url, params={"taskId": task_id}, headers=_headers(), timeout=30)
        r.raise_for_status()
        data = r.json().get("data", {})
        state = data.get("state", "")
        if state in ("success", "completed", "succeed"):
            return data
        if state in ("failed", "error"):
            raise RuntimeError(f"{label} task {task_id} failed: {data.get('failMsg', data)}")
        log.info(f"  {label}: {state}...")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"{label} task {task_id} timed out")


def download_first_result(data: dict, out_path: Path) -> Path:
    result_str = data.get("resultJson", "")
    rj = json.loads(result_str)
    urls = rj.get("resultUrls") or rj.get("urls") or []
    if not urls:
        raise RuntimeError(f"No URLs in: {rj}")
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(urls[0])
        dl.raise_for_status()
        out_path.write_bytes(dl.content)
    log.info(f"  Downloaded: {out_path.name} ({out_path.stat().st_size/1024/1024:.1f} MB)")
    return out_path


def video_duration(path: Path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


# ── Step 1: Kling 3.0 b-roll generation ─────────────────────────────────────

def generate_broll(prompt_cfg: dict) -> Path:
    log.info(f"Generating b-roll: {prompt_cfg['name']}...")
    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask",
        headers=_headers(),
        json={
            "model": "kling-3.0/video",
            "input": {
                "prompt": prompt_cfg["prompt"],
                "duration": str(prompt_cfg["duration"]),
                "aspect_ratio": "9:16",
                "mode": "std",
                "sound": False,
                "multi_shots": False,
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    resp = r.json()
    task_id = (resp.get("data") or {}).get("taskId")
    if not task_id:
        raise RuntimeError(f"B-roll submit failed: {resp}")

    data = poll_task(task_id, f"B-roll {prompt_cfg['name']}")
    ts = int(time.time())
    out = OUTPUT_DIR / f"{prompt_cfg['name']}_{ts}.mp4"
    return download_first_result(data, out)


# ── Step 2: Suno background music ───────────────────────────────────────────

def generate_music(duration_sec: int) -> Path:
    log.info("Generating Suno background music...")
    body = {
        "prompt": (
            "Subtle minimalist electronic underbed for a tech entrepreneur reel. "
            "Lo-fi ambient with soft synth pad, distant glitchy percussion, "
            "low-key contemplative mood. No vocals. Instrumental only."
        ),
        "style": "ambient electronic, lo-fi, minimal",
        "title": "Daniel Reel BG",
        "customMode": True,
        "instrumental": True,
        "model": "V4_5",
    }
    r = httpx.post(
        f"{KIE_SUNO_BASE}/generate",
        headers=_headers(),
        json=body,
        timeout=30,
    )
    if r.status_code != 200:
        log.warning(f"  Suno submit returned {r.status_code}: {r.text[:300]}")
        raise RuntimeError(f"Suno submit failed: {r.text[:300]}")
    resp = r.json()
    task_id = (resp.get("data") or {}).get("taskId") or (resp.get("data") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"Suno: no taskId in {resp}")
    log.info(f"  Suno task: {task_id}")
    data = poll_task(task_id, "Suno")

    # Suno returns multiple tracks in resultJson
    result_str = data.get("resultJson", "")
    rj = json.loads(result_str) if result_str else {}
    # Try various result shapes
    candidates = (
        rj.get("data")
        or rj.get("audios")
        or rj.get("clips")
        or rj.get("resultUrls")
        or []
    )
    audio_url = ""
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            audio_url = first.get("audio_url") or first.get("audioUrl") or first.get("url", "")
        elif isinstance(first, str):
            audio_url = first
    if not audio_url:
        raise RuntimeError(f"Suno: no audio_url in {rj}")

    ts = int(time.time())
    out = OUTPUT_DIR / f"bg_music_{ts}.mp3"
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(audio_url)
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  Music: {out.name}")
    return out


# ── Step 3: Multi-shot edit ─────────────────────────────────────────────────

def build_multishot_edit(
    avatar_clip: Path,
    broll_a: Path,
    broll_b: Path,
    music: Path | None,
    captions_ass: Path,
) -> Path:
    """
    Edit structure (13.6s total to match avatar duration):
      [0.0 - 3.5s ] Avatar Pro talking — hook line
      [3.5 - 5.5s ] B-roll A: hands typing
      [5.5 - 9.0s ] Avatar Pro talking
      [9.0 - 11.0s] B-roll B: screen
      [11.0 - 13.6s] Avatar Pro talking — payoff
    """
    total = video_duration(avatar_clip)
    log.info(f"Building multi-shot edit (avatar={total:.1f}s)...")

    # Segment timings — cut on natural beats from the captions
    seg_a_end = 3.5    # hook
    seg_b_end = 5.5    # b-roll typing
    seg_c_end = 9.0    # mid talking
    seg_d_end = 11.0   # b-roll screen
    # seg_e ends at total

    work = OUTPUT_DIR / f"_v5_work_{int(time.time())}"
    work.mkdir(parents=True, exist_ok=True)

    # Extract avatar segments (with original audio preserved)
    seg_a = work / "seg_a.mp4"
    seg_c = work / "seg_c.mp4"
    seg_e = work / "seg_e.mp4"

    for out, start, end in [
        (seg_a, 0.0, seg_a_end),
        (seg_c, seg_b_end, seg_c_end),
        (seg_e, seg_d_end, total),
    ]:
        subprocess.run([
            FFMPEG, "-y", "-ss", f"{start}", "-to", f"{end}",
            "-i", str(avatar_clip),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
            "-r", "30",
            str(out),
        ], check=True, capture_output=True)

    # Process b-roll: crop to 9:16, trim to slot length, mute audio
    seg_b = work / "seg_b.mp4"
    seg_d = work / "seg_d.mp4"

    for out, src, length in [
        (seg_b, broll_a, seg_b_end - seg_a_end),
        (seg_d, broll_b, seg_d_end - seg_c_end),
    ]:
        subprocess.run([
            FFMPEG, "-y", "-i", str(src),
            "-t", f"{length}",
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
            "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-an",
            str(out),
        ], check=True, capture_output=True)

    # Re-encode b-roll with silent audio so concat is uniform
    seg_b_a = work / "seg_b_a.mp4"
    seg_d_a = work / "seg_d_a.mp4"
    for out, src, length in [(seg_b_a, seg_b, seg_b_end - seg_a_end),
                              (seg_d_a, seg_d, seg_d_end - seg_c_end)]:
        subprocess.run([
            FFMPEG, "-y", "-i", str(src),
            "-f", "lavfi", "-t", f"{length}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(out),
        ], check=True, capture_output=True)

    # Concat
    concat_list = work / "concat.txt"
    with open(concat_list, "w") as f:
        for seg in [seg_a, seg_b_a, seg_c, seg_d_a, seg_e]:
            f.write(f"file '{seg.resolve()}'\n")

    stitched = work / "stitched.mp4"
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(stitched),
    ], check=True, capture_output=True)
    log.info(f"  Stitched: {stitched.name}")

    # Mix background music underneath (if present)
    if music and music.exists():
        log.info("  Mixing background music...")
        mixed = work / "mixed.mp4"
        subprocess.run([
            FFMPEG, "-y", "-i", str(stitched), "-i", str(music),
            "-filter_complex",
            "[1:a]volume=0.10,afade=t=in:st=0:d=0.5,afade=t=out:st=12.6:d=1.0[bg];"
            "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            str(mixed),
        ], check=True, capture_output=True)
        stitched = mixed

    # Burn captions + hook overlay
    log.info("  Burning captions + hook overlay...")
    ts = int(time.time())
    pre_final = OUTPUT_DIR / f"daniel_reel_v5_pre_{ts}.mp4"
    hook_escaped = HOOK_TEXT.replace("'", r"'\''")
    hook_filter = (
        f"ass='{captions_ass.resolve()}',"
        f"drawtext=text='{hook_escaped}'"
        f":font='Montserrat Black':fontsize=42:fontcolor=white"
        f":borderw=3:bordercolor=black:shadowcolor=black@0.5:shadowx=2:shadowy=2"
        f":x=(w-text_w)/2:y=h*0.15"
        f":alpha='if(lt(t,0.2),t/0.2,if(lt(t,2.7),1,if(lt(t,3.0),((3.0-t)/0.3),0)))'"
        f":enable='between(t,0,3.0)'"
    )
    subprocess.run([
        FFMPEG, "-y", "-i", str(stitched),
        "-vf", hook_filter,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-r", "30",
        str(pre_final),
    ], check=True, capture_output=True)
    log.info(f"  Pre-final: {pre_final.name}")
    return pre_final


# ── Step 4: Topaz upscale ───────────────────────────────────────────────────

def upload_file(filepath: Path) -> str:
    with open(filepath, "rb") as f:
        resp = httpx.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (filepath.name, f)},
            timeout=180,
        )
    resp.raise_for_status()
    data = resp.json()
    return data["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


def topaz_upscale(video_path: Path) -> Path:
    log.info("Upscaling via Topaz 2x...")
    video_url = upload_file(video_path)
    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask",
        headers=_headers(),
        json={
            "model": "topaz/video-upscale",
            "input": {
                "video_url": video_url,
                "upscale_factor": "2",
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    resp = r.json()
    task_id = (resp.get("data") or {}).get("taskId")
    if not task_id:
        raise RuntimeError(f"Topaz submit failed: {resp}")
    data = poll_task(task_id, "Topaz")
    ts = int(time.time())
    out = OUTPUT_DIR / f"daniel_reel_v5_FINAL_{ts}.mp4"
    return download_first_result(data, out)


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — Premium Multi-shot Reel v5")
    log.info("=" * 60)

    # Verify existing assets
    for p in [EXISTING_AVATAR, EXISTING_AUDIO, EXISTING_CAPTIONS]:
        if not p.exists():
            raise FileNotFoundError(f"Missing: {p}")

    # ── B-roll (parallel would be nice, do sequentially for simplicity)
    broll_paths = []
    for cfg in BROLL_PROMPTS:
        broll_paths.append(generate_broll(cfg))

    # ── Music (graceful fallback if Suno fails)
    try:
        music_path = generate_music(15)
    except Exception as e:
        log.warning(f"Suno music failed: {e}. Continuing without music.")
        music_path = None

    # ── Multi-shot edit + captions
    pre_final = build_multishot_edit(
        avatar_clip=EXISTING_AVATAR,
        broll_a=broll_paths[0],
        broll_b=broll_paths[1],
        music=music_path,
        captions_ass=EXISTING_CAPTIONS,
    )

    # ── Topaz upscale (optional, gracefully skip on failure)
    try:
        final = topaz_upscale(pre_final)
    except Exception as e:
        log.warning(f"Topaz upscale failed: {e}. Using pre-upscale version.")
        final = pre_final

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
