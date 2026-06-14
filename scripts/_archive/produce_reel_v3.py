#!/usr/bin/env python3
"""
produce_reel_v3.py — Kling 3.0 native pipeline.

Everything through KIE.ai:
  1. DeepSeek LLM writes the Kling 3.0 prompt
  2. Kling 3.0 generates video + native voice + face refs
  3. Whisper (local) generates word-level captions
  4. FFmpeg burns captions + hook overlay

No F5-TTS, no lip-sync, no audio swap. Kling handles it all.
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
log = logging.getLogger("produce_reel_v3")

FACE_DIR = ROOT / "virtuai" / "persona" / "face_dataset"
OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_LLM_BASE = "https://kieai.erweima.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"

POLL_INTERVAL = 15
POLL_TIMEOUT = 600

# ── Content config ───────────────────────────────────────────────────────────

TOPIC = "AI systems vs AI apps"
HOOK = "Nobody's building AI systems."
DURATION = 10
ASPECT_RATIO = "9:16"

PERSONA_BRIEF = (
    "Daniel Calder — a 28-year-old AI entrepreneur and systems thinker. "
    "He speaks in a direct, confident, slightly provocative tone. "
    "He wears dark casual clothes (navy zip-up, dark tee). "
    "Short dark wavy hair, light stubble, brown eyes."
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _kie_headers() -> dict:
    if not KIE_API_KEY:
        raise RuntimeError("KIE_API_KEY not set in .env")
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def upload_file(filepath: Path) -> str:
    log.info(f"  Uploading {filepath.name}...")
    with open(filepath, "rb") as f:
        resp = httpx.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (filepath.name, f)},
            timeout=120,
        )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Upload failed: {data}")
    return data["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


def pick_face_refs(n: int = 4) -> list[Path]:
    pngs = sorted(FACE_DIR.glob("*.png"))
    if len(pngs) >= 16:
        return [pngs[0], pngs[5], pngs[10], pngs[15]]
    return pngs[:n]


def poll_kie_task(task_id: str) -> dict:
    url = f"{KIE_API_BASE}/jobs/recordInfo"
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        r = httpx.get(url, params={"taskId": task_id}, headers=_kie_headers(), timeout=30)
        r.raise_for_status()
        data = r.json().get("data", {})
        state = data.get("state", "")
        if state in ("success", "completed", "succeed"):
            return data
        if state in ("failed", "error"):
            raise RuntimeError(f"Task {task_id} failed: {data.get('failMsg', data)}")
        log.info(f"  Polling: state={state}...")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Task {task_id} timed out after {POLL_TIMEOUT}s")


# ── Step 1: LLM writes the Kling prompt ─────────────────────────────────────

def write_kling_prompt(topic: str, hook: str) -> str:
    log.info("Step 1/4: Writing Kling 3.0 prompt via DeepSeek...")

    system_msg = (
        "You write scene prompts for Kling 3.0 AI video generation. "
        "Rules:\n"
        "- The video features a character referenced as @daniel\n"
        "- Describe the scene, setting, lighting, camera angle, and action\n"
        "- The character MUST be speaking directly to camera\n"
        "- Include specific body language and natural micro-expressions\n"
        "- Specify cinematic details: DOF, color grading, camera movement\n"
        "- The character should be in a real-world environment, not a studio\n"
        "- Keep under 200 words (element refs consume prompt space)\n"
        "- Do NOT include dialogue text — Kling generates its own speech\n"
        "- Output ONLY the prompt, no explanation"
    )

    user_msg = (
        f"Write a Kling 3.0 scene prompt for a {DURATION}-second vertical (9:16) "
        f"video reel.\n\n"
        f"Persona: {PERSONA_BRIEF}\n\n"
        f"Topic: {topic}\n"
        f"Hook/opening energy: \"{hook}\"\n"
        f"Mood: confident, direct, slightly provocative — like a founder "
        f"dropping hard truth on a podcast clip\n\n"
        f"The character is @daniel. Use that exact reference in the prompt."
    )

    resp = httpx.post(
        f"{KIE_LLM_BASE}/chat/completions",
        headers=_kie_headers(),
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": 400,
            "temperature": 0.7,
        },
        timeout=30,
    )
    resp.raise_for_status()
    prompt = resp.json()["choices"][0]["message"]["content"].strip()
    prompt = prompt.strip('"').strip("```").strip()
    log.info(f"  Prompt ({len(prompt.split())} words): {prompt[:150]}...")
    return prompt


# ── Step 2: Kling 3.0 video generation ──────────────────────────────────────

def generate_video(prompt: str, face_urls: list[str]) -> Path:
    log.info("Step 2/4: Generating Kling 3.0 video...")

    body = {
        "model": "kling-3.0/video",
        "input": {
            "prompt": prompt,
            "image_urls": face_urls[:2],
            "duration": str(DURATION),
            "aspect_ratio": ASPECT_RATIO,
            "mode": "std",
            "sound": True,
            "multi_shots": False,
            "kling_elements": [{
                "name": "daniel",
                "description": PERSONA_BRIEF,
                "element_input_urls": face_urls,
            }],
        },
    }

    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask",
        headers=_kie_headers(),
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    resp = r.json()
    data_block = resp.get("data") or {}
    task_id = data_block.get("taskId") or data_block.get("task_id")
    if not task_id:
        raise RuntimeError(f"No taskId: {resp}")
    log.info(f"  Task submitted: {task_id}")

    data = poll_kie_task(task_id)

    result_str = data.get("resultJson", "")
    video_url = ""
    if result_str:
        rj = json.loads(result_str)
        urls = rj.get("resultUrls", [])
        if urls:
            video_url = urls[0]
    if not video_url:
        raise RuntimeError(f"No video URL: {data}")

    ts = int(time.time())
    out = OUTPUT_DIR / f"kling3_native_{ts}.mp4"
    log.info(f"  Downloading video...")
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(video_url)
        dl.raise_for_status()
        out.write_bytes(dl.content)

    credits = data.get("creditsConsumed", "?")
    cost_time = data.get("costTime", "?")
    log.info(f"  Video saved: {out.name} ({out.stat().st_size/1024/1024:.1f} MB, {cost_time}s, {credits} credits)")
    return out


# ── Step 3: Crop to 9:16 if needed ──────────────────────────────────────────

def ensure_vertical(video_path: Path) -> Path:
    probe = subprocess.run([FFMPEG, "-i", str(video_path)], capture_output=True, text=True)
    m = re.search(r"(\d{3,4})x(\d{3,4})", probe.stderr)
    if not m:
        return video_path
    w, h = int(m.group(1)), int(m.group(2))
    if h > w:
        log.info(f"  Already vertical ({w}x{h})")
        return video_path
    log.info(f"  Cropping {w}x{h} to 9:16...")
    tw = int(h * 9 / 16)
    out = video_path.with_name(video_path.stem + "_vert.mp4")
    subprocess.run([
        FFMPEG, "-y", "-i", str(video_path),
        "-vf", f"crop={tw}:{h}:({w}-{tw})/2:0,scale=720:1280",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy",
        str(out),
    ], check=True, capture_output=True)
    return out


# ── Step 4: Captions + final reel ───────────────────────────────────────────

def add_captions(video_path: Path) -> Path:
    log.info("Step 3/4: Extracting audio + generating captions...")

    audio_tmp = video_path.with_suffix(".wav")
    subprocess.run([
        FFMPEG, "-y", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio_tmp),
    ], check=True, capture_output=True)

    from virtuai.tools.caption_generator import create_captions
    ass_path = create_captions(
        audio_path=str(audio_tmp),
        whisper_model="base",
        words_per_group=2,
    )
    log.info(f"  Captions: {ass_path}")
    return Path(ass_path)


def build_final_reel(video_path: Path, captions_ass: Path) -> Path:
    log.info("Step 4/4: Building final reel...")
    from virtuai.tools.reel_builder import build_reel
    ts = int(time.time())
    out = OUTPUT_DIR / f"daniel_reel_v3_{ts}.mp4"
    result = build_reel(
        clips=[str(video_path)],
        captions_ass=str(captions_ass),
        output_path=str(out),
        hook_text=HOOK,
    )
    log.info(f"  Final: {result} ({Path(str(result)).stat().st_size/1024/1024:.1f} MB)")
    return Path(str(result))


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — Kling 3.0 Native Pipeline v3")
    log.info("=" * 60)

    # Upload face references
    log.info("Uploading face references...")
    face_paths = pick_face_refs(4)
    face_urls = [upload_file(p) for p in face_paths]
    log.info(f"  {len(face_urls)} refs uploaded")

    # Step 1: LLM writes prompt
    prompt = write_kling_prompt(TOPIC, HOOK)

    # Step 2: Kling 3.0 generates everything
    video_path = generate_video(prompt, face_urls)

    # Ensure 9:16
    video_path = ensure_vertical(video_path)

    # Step 3: Captions
    captions = add_captions(video_path)

    # Step 4: Final reel
    final = build_final_reel(video_path, captions)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
