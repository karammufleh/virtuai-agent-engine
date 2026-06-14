#!/usr/bin/env python3
"""
produce_reel_v15.py — Kling 3.0 NATIVE single-pass (no lipsync re-render).

The complaints about v14 (laggy motion, inconsistent persona, bad quality)
all trace back to the Kling V1-6 LIPSYNC re-render step. v15 eliminates it:

  • Kling 3.0 multi_shots in ONE render generates video + native speech +
    native lipsync — jointly synthesized, perfect by construction
  • kling_elements with canonical_daniel locks the face across all shots
  • sound=True → Kling speaks the dialogue in its own voice
  • Audio + video share the same generation pass → no compositing seams,
    no lipsync re-render, no motion lag

If you want Liam's voice instead of Kling's native voice, set
ELEVENLABS_API_KEY in .env and v15 will run the Voice Changer step at
the end (speech-to-speech preserves timing → lipsync intact).
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

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("produce_reel_v15")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
SCRIPT_DIR = ROOT / "virtuai" / "data" / "scripts"
SCRIPT_DIR.mkdir(parents=True, exist_ok=True)

KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

CANONICAL_FACE = ROOT / "virtuai" / "persona" / "canonical_daniel.png"
ELEVENLABS_VOICE_ID = "TX3LPaxmHKxFdv7VOQHJ"  # Liam (only used if voice-changer enabled)
N_SHOTS = 3            # 3 shots × 5s = 15s reel (Kling 3.0 cap)
SHOT_DURATION = 5

POLL_INTERVAL = 12
POLL_TIMEOUT = 1800
HTTP_TIMEOUT = 90
MAX_RETRIES = 5


def _headers():
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def http_get_retry(url, params=None):
    for attempt in range(MAX_RETRIES):
        try:
            return httpx.get(url, params=params, headers=_headers(), timeout=HTTP_TIMEOUT)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError,
                httpx.RemoteProtocolError) as e:
            wait = (2 ** attempt) * 2
            log.warning(f"  GET retry {attempt+1}/{MAX_RETRIES} after {wait}s: {e}")
            time.sleep(wait)
    return httpx.get(url, params=params, headers=_headers(), timeout=HTTP_TIMEOUT)


def http_post_retry(url, json_body):
    for attempt in range(MAX_RETRIES):
        try:
            return httpx.post(url, headers=_headers(), json=json_body, timeout=HTTP_TIMEOUT)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError,
                httpx.RemoteProtocolError) as e:
            wait = (2 ** attempt) * 2
            log.warning(f"  POST retry {attempt+1}/{MAX_RETRIES} after {wait}s: {e}")
            time.sleep(wait)
    return httpx.post(url, headers=_headers(), json=json_body, timeout=HTTP_TIMEOUT)


def upload_to_tmpfiles(filepath):
    for attempt in range(MAX_RETRIES):
        try:
            with open(filepath, "rb") as f:
                r = httpx.post("https://tmpfiles.org/api/v1/upload",
                               files={"file": (filepath.name, f)}, timeout=300)
            r.raise_for_status()
            return r.json()["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)
        except Exception as e:
            time.sleep((2 ** attempt) * 3)
    raise RuntimeError("Upload failed")


def video_dur(p):
    r = subprocess.run([FFPROBE, "-v", "quiet", "-print_format", "json",
                       "-show_format", str(p)], capture_output=True, text=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def submit_kie(model, input_data):
    r = http_post_retry(f"{KIE_API_BASE}/jobs/createTask",
                        {"model": model, "input": input_data})
    r.raise_for_status()
    tid = (r.json().get("data") or {}).get("taskId")
    if not tid:
        raise RuntimeError(f"Submit failed for {model}: {r.text[:500]}")
    return tid


def poll_task(tid, label):
    deadline = time.time() + POLL_TIMEOUT
    last = ""
    while time.time() < deadline:
        try:
            r = http_get_retry(f"{KIE_API_BASE}/jobs/recordInfo", params={"taskId": tid})
            r.raise_for_status()
            d = r.json().get("data", {})
            state = d.get("state", "")
            if state != last:
                log.info(f"  {label}: {state}...")
                last = state
            if state in ("success", "completed", "succeed"):
                return d
            if state in ("failed", "error", "fail"):
                raise RuntimeError(f"{label} failed: {d.get('failMsg', d)}")
        except (httpx.ReadTimeout, httpx.HTTPError) as e:
            log.warning(f"  {label} transient: {e}")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(label)


def poll_suno(tid):
    deadline = time.time() + POLL_TIMEOUT
    last = ""
    while time.time() < deadline:
        try:
            r = http_get_retry(f"{KIE_API_BASE}/generate/record-info", params={"taskId": tid})
            r.raise_for_status()
            d = r.json().get("data", {})
            status = d.get("status", "")
            if status != last:
                log.info(f"  Suno: {status}...")
                last = status
            if status == "SUCCESS":
                return d
            if status in ("FAILED", "ERROR", "CALLBACK_EXCEPTION"):
                raise RuntimeError(f"Suno failed: {d}")
        except (httpx.ReadTimeout, httpx.HTTPError) as e:
            log.warning(f"  Suno transient: {e}")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError("Suno")


def download_first(data, out):
    rj = json.loads(data.get("resultJson", "{}"))
    urls = rj.get("resultUrls", [])
    if not urls:
        raise RuntimeError(f"No URLs: {rj}")
    with httpx.Client(timeout=600, follow_redirects=True) as c:
        dl = c.get(urls[0])
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  ↓ {out.name} ({out.stat().st_size/1024/1024:.1f}MB)")
    return out


# ── Kling 3.0 single-render multi-shot ──────────────────────────────────────

import re as _re

def _compress_visual_for_kling(rich_visual_prompt: str) -> str:
    """
    Claude produces rich 5-layer visual prompts (~500 chars). Kling's
    multi_prompt items cap at ~500 chars TOTAL including dialogue. Strip
    the [1]..[5] markers, keep the most actionable nouns/adjectives.
    """
    # Drop the [N] LAYER: headers
    p = _re.sub(r"\[\d+\]\s*[A-Z][A-Z\s+&]+:\s*", "", rich_visual_prompt)
    # Collapse whitespace
    p = _re.sub(r"\s+", " ", p).strip()
    # Strip leading "@daniel" because we use kling_elements instead
    p = p.replace("@daniel ", "").replace("@daniel,", "").replace("@daniel.", "")
    return p


def kling_multi_shot(script: dict, face_url: str) -> Path:
    """One Kling 3.0 render covering all shots, with native speech."""
    log.info("Kling 3.0 multi_shots render (sound=True, native lipsync)...")

    shots = script["scenes"][:N_SHOTS]
    total_dur = SHOT_DURATION * len(shots)
    multi_prompt = []
    for sc in shots:
        dialogue = sc["audio_text"].strip().replace('"', '\\"')
        compressed = _compress_visual_for_kling(sc["visual_prompt"])

        # Budget: ~500 char Kling cap. Reserve 160 for dialogue + scaffold.
        # → ~340 chars for visual.
        visual_part = compressed[:330]
        # Dialogue can be long — Claude may write 200+ chars. Trim to 200.
        dlg_part = dialogue[:200]
        full = (
            f"{visual_part} Says: \"{dlg_part}\". Photo-real, alive background."
        )
        full = full[:495]
        log.info(f"  shot len={len(full)} | {full[:120]}…")
        multi_prompt.append({"prompt": full, "duration": SHOT_DURATION})

    tid = submit_kie("kling-3.0/video", {
        "multi_shots": True,
        "multi_prompt": multi_prompt,
        "image_urls": [face_url],          # exactly 1 required with multi_shots
        "duration": str(total_dur),
        "aspect_ratio": "9:16",
        "mode": "pro",
        "sound": True,                     # KEY — native speech + lipsync
        "kling_elements": [{
            "name": "daniel",
            "description": (
                "A 28-year-old man with short dark wavy hair, light stubble, "
                "wearing a dark polo shirt"
            ),
            # Kling requires 2-4 reference images per element
            "element_input_urls": [face_url, face_url],
        }],
    })
    d = poll_task(tid, "kling-multi-shot")
    out = OUTPUT_DIR / f"v15_kling_multi_{int(time.time())}.mp4"
    return download_first(d, out)


# ── Suno music ──────────────────────────────────────────────────────────────

def submit_suno():
    body = {
        "prompt": ("Calm confident underscore for a founder reel — soft acoustic "
                   "piano with warm ambient pad, very subtle rhythmic pulse, "
                   "contemplative but driving. No vocals. Instrumental only."),
        "customMode": False, "instrumental": True, "model": "V3_5",
        "callBackUrl": "https://example.com/cb",
    }
    r = http_post_retry(f"{KIE_API_BASE}/generate", body)
    if r.status_code != 200:
        return None
    return (r.json().get("data") or {}).get("taskId")


def fetch_suno(tid):
    if not tid:
        return None
    try:
        d = poll_suno(tid)
    except Exception as e:
        log.warning(f"Suno: {e}")
        return None
    resp = d.get("response", {})
    sd = resp.get("sunoData", []) if isinstance(resp, dict) else []
    if not sd:
        return None
    audio_url = sd[0].get("audioUrl") or sd[0].get("streamAudioUrl", "")
    if not audio_url:
        return None
    out = OUTPUT_DIR / f"v15_music_{int(time.time())}.mp3"
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(audio_url)
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  ↓ {out.name}")
    return out


# ── ElevenLabs Voice Changer (optional, requires ELEVENLABS_API_KEY) ────────

def voice_change_to_liam(video_path: Path) -> Path:
    """Extract audio → ElevenLabs speech-to-speech (→ Liam) → remux."""
    if not ELEVENLABS_API_KEY:
        log.info("  (Voice Changer skipped — ELEVENLABS_API_KEY not set)")
        return video_path

    log.info("ElevenLabs Voice Changer → Liam (preserves timing)...")
    work = OUTPUT_DIR / f"_v15_vc_{int(time.time())}"
    work.mkdir(exist_ok=True)

    in_audio = work / "in.mp3"
    subprocess.run([
        FFMPEG, "-y", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-b:a", "192k",
        str(in_audio),
    ], check=True, capture_output=True)

    # ElevenLabs direct API: POST /v1/speech-to-speech/{voice_id}
    url = f"https://api.elevenlabs.io/v1/speech-to-speech/{ELEVENLABS_VOICE_ID}"
    out_audio = work / "out.mp3"
    with open(in_audio, "rb") as f:
        files = {"audio": ("in.mp3", f, "audio/mpeg")}
        data = {
            "model_id": "eleven_english_sts_v2",
            "voice_settings": json.dumps({
                "stability": 0.45, "similarity_boost": 0.85, "style": 0.4
            }),
            "remove_background_noise": "false",
        }
        headers = {"xi-api-key": ELEVENLABS_API_KEY,
                   "accept": "audio/mpeg"}
        r = httpx.post(url, headers=headers, files=files, data=data,
                       timeout=300, params={"output_format": "mp3_44100_192"})
    if r.status_code != 200:
        log.warning(f"  Voice Changer failed ({r.status_code}): {r.text[:300]}")
        return video_path
    out_audio.write_bytes(r.content)
    log.info(f"  Voice swapped → {out_audio.name}")

    # Remux: replace video's audio with Liam audio
    out = work / "voice_swapped.mp4"
    subprocess.run([
        FFMPEG, "-y", "-i", str(video_path), "-i", str(out_audio),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out),
    ], check=True, capture_output=True)
    return out


# ── Post-production (no captions) ───────────────────────────────────────────

def post_produce(video: Path, music: Path | None) -> Path:
    log.info("Post: natural look + music + handle...")
    work = OUTPUT_DIR / f"_v15_post_{int(time.time())}"
    work.mkdir(exist_ok=True)

    natural = work / "natural.mp4"
    vf = (
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,"
        "eq=contrast=1.02:saturation=1.00:gamma=1.0,"
        "noise=alls=6:allf=t+u"
    )
    subprocess.run([
        FFMPEG, "-y", "-i", str(video),
        "-vf", vf, "-r", "30",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy",
        str(natural),
    ], check=True, capture_output=True)

    dur = video_dur(natural)
    if music and music.exists():
        mixed = work / "mixed.mp4"
        subprocess.run([
            FFMPEG, "-y", "-i", str(natural), "-i", str(music),
            "-filter_complex",
            f"[0:a]loudnorm=I=-14:LRA=11:tp=-1[dlg];"
            f"[1:a]volume=0.05,afade=t=in:st=0:d=1.5,afade=t=out:st={dur-1.5}:d=1.5[bg];"
            f"[dlg][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(mixed),
        ], check=True, capture_output=True)
        source = mixed
    else:
        normed = work / "normed.mp4"
        subprocess.run([
            FFMPEG, "-y", "-i", str(natural),
            "-af", "loudnorm=I=-14:LRA=11:tp=-1",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(normed),
        ], check=True, capture_output=True)
        source = normed

    burn = (
        "drawtext=text='@daniel.calder'"
        ":font='Inter':fontsize=20:fontcolor=white@0.55"
        ":borderw=1:bordercolor=black@0.5"
        ":x=20:y=h-40"
    )
    ts = int(time.time())
    final = OUTPUT_DIR / f"daniel_reel_v15_{ts}.mp4"
    subprocess.run([
        FFMPEG, "-y", "-i", str(source),
        "-vf", burn,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-r", "30",
        str(final),
    ], check=True, capture_output=True)
    return final


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    import concurrent.futures as cf
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — v15 Kling 3.0 NATIVE (no lipsync re-render)")
    log.info(f"  Voice changer: {'ENABLED' if ELEVENLABS_API_KEY else 'disabled (no key)'}")
    log.info("=" * 60)

    if not CANONICAL_FACE.exists():
        raise FileNotFoundError(CANONICAL_FACE)

    log.info("Step 1: DeepSeek script (niche locked)...")
    from virtuai.tools.script_writer import write_script
    script = write_script(topic=None, n_scenes=N_SHOTS)
    script_path = SCRIPT_DIR / f"v15_{int(time.time())}.json"
    script_path.write_text(json.dumps(script, indent=2))
    log.info(f"  Topic: {script['topic']}")
    log.info(f"  Hook:  {script['hook_summary']}")

    log.info("Step 2: Upload canonical face...")
    face_url = upload_to_tmpfiles(CANONICAL_FACE)

    log.info("Step 3: Kling multi-shot + Suno in parallel...")
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        kling_fut = ex.submit(kling_multi_shot, script, face_url)
        suno_fut = ex.submit(submit_suno)
        suno_task = suno_fut.result()
        kling_video = kling_fut.result()

    music = fetch_suno(suno_task) if suno_task else None

    log.info(f"Step 4: Voice Changer (Liam) — {'ENABLED' if ELEVENLABS_API_KEY else 'skipped'}")
    voice_changed = voice_change_to_liam(kling_video)

    log.info("Step 5: Post production...")
    final = post_produce(voice_changed, music)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info(f"Duration: {video_dur(final):.1f}s")
    log.info(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
