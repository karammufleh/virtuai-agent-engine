#!/usr/bin/env python3
"""
produce_reel_v13.py — Cinematic Kling + Lipsync. No Avatar Pro.

Architecture (per user direction):
  1. DeepSeek writes a viral, scene-by-scene script (script_writer.py)
  2. ElevenLabs generates the full audio in one shot (consistent voice)
  3. Kling 3.0 generates the CINEMATIC video using face element refs
     — multi_shots=True chains the scenes into one continuous clip
  4. Kling V1-6 lip_sync syncs that cinematic video to the ElevenLabs audio
  5. Natural look + music + handle — NO captions, NO b-roll overlays

No Avatar Pro. The video is real cinematic Kling shots of @daniel in
different real locations, lip-synced to a single consistent voice track.
"""
from __future__ import annotations

import concurrent.futures as cf
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
log = logging.getLogger("produce_reel_v13")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
SCRIPT_DIR = ROOT / "virtuai" / "data" / "scripts"
SCRIPT_DIR.mkdir(parents=True, exist_ok=True)

KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

CANONICAL_FACE = ROOT / "virtuai" / "persona" / "canonical_daniel.png"
ELEVENLABS_VOICE = "TX3LPaxmHKxFdv7VOQHJ"  # Liam

POLL_INTERVAL = 10
POLL_TIMEOUT = 1200


# ── KIE helpers ─────────────────────────────────────────────────────────────

def _headers():
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def submit_kie(model, input_data):
    r = httpx.post(f"{KIE_API_BASE}/jobs/createTask", headers=_headers(),
                   json={"model": model, "input": input_data}, timeout=30)
    r.raise_for_status()
    tid = (r.json().get("data") or {}).get("taskId")
    if not tid:
        raise RuntimeError(f"Submit failed for {model}: {r.text[:300]}")
    return tid


def poll_task(tid, label=""):
    deadline = time.time() + POLL_TIMEOUT
    last = ""
    while time.time() < deadline:
        r = httpx.get(f"{KIE_API_BASE}/jobs/recordInfo",
                      params={"taskId": tid}, headers=_headers(), timeout=30)
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
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(label)


def poll_suno(tid):
    deadline = time.time() + POLL_TIMEOUT
    last = ""
    while time.time() < deadline:
        r = httpx.get(f"{KIE_API_BASE}/generate/record-info",
                      params={"taskId": tid}, headers=_headers(), timeout=30)
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
        time.sleep(POLL_INTERVAL)
    raise TimeoutError("Suno")


def download_first(data, out):
    rj = json.loads(data.get("resultJson", "{}"))
    urls = rj.get("resultUrls", [])
    if not urls:
        raise RuntimeError(f"No URLs: {rj}")
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(urls[0])
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  ↓ {out.name} ({out.stat().st_size/1024/1024:.1f}MB)")
    return out


def upload_to_tmpfiles(filepath):
    with open(filepath, "rb") as f:
        r = httpx.post("https://tmpfiles.org/api/v1/upload",
                       files={"file": (filepath.name, f)}, timeout=180)
    r.raise_for_status()
    return r.json()["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


def video_dur(p):
    r = subprocess.run([FFPROBE, "-v", "quiet", "-print_format", "json",
                       "-show_format", str(p)], capture_output=True, text=True)
    return float(json.loads(r.stdout)["format"]["duration"])


# ── Step 1: ElevenLabs full audio ───────────────────────────────────────────

def generate_full_audio(script_text: str) -> Path:
    log.info("ElevenLabs full-script audio...")
    tid = submit_kie("elevenlabs/text-to-speech-turbo-2-5", {
        "text": script_text, "voice": ELEVENLABS_VOICE,
        "stability": 0.55, "similarity_boost": 0.78, "style": 0.42,
        "speed": 1.05,
    })
    d = poll_task(tid, "ElevenLabs")
    out = OUTPUT_DIR / f"v13_voice_{int(time.time())}.mp3"
    return download_first(d, out)


# ── Step 2: Kling 3.0 cinematic video (multi-shot, face-locked) ─────────────

def generate_kling_cinematic(script: dict, face_urls: list[str], target_duration: int) -> Path:
    log.info("Kling 3.0 cinematic multi-shot video...")

    # multi_shots=True requires `multi_prompt` as an array of OBJECTS
    # `{prompt: string<=500, duration: int}`. Max 5 shots, sum of per-shot
    # durations must equal total `duration`. Each shot can reference @daniel.
    scenes = script["scenes"][:5]  # cap at 5
    n_shots = len(scenes)
    total_dur = max(10, min(15, int(target_duration)))
    per_shot = max(1, total_dur // n_shots)
    durations = [per_shot] * n_shots
    # absorb the remainder into the last shot
    durations[-1] = total_dur - sum(durations[:-1])

    def _ensure_daniel(p: str) -> str:
        """Kling 3.0 requires the @element name to appear in each shot prompt."""
        return p if "@daniel" in p else f"@daniel — {p}"

    multi_prompt = [
        {"prompt": _ensure_daniel(sc["visual_prompt"])[:480], "duration": int(durations[i])}
        for i, sc in enumerate(scenes)
    ]
    log.info(f"  multi_prompt: {n_shots} shots, durations={durations}")
    for i, mp in enumerate(multi_prompt):
        log.info(f"    shot {i+1} ({mp['duration']}s): {mp['prompt'][:90]}...")

    dur = total_dur

    tid = submit_kie("kling-3.0/video", {
        "multi_prompt": multi_prompt,
        "image_urls": face_urls[:1],
        "duration": str(dur),
        "aspect_ratio": "9:16",
        "mode": "std",
        "sound": False,
        "multi_shots": True,
        "kling_elements": [{
            "name": "daniel",
            "description": (
                "A 28-year-old man with short dark wavy hair, light stubble, "
                "wearing a dark polo shirt"
            ),
            "element_input_urls": face_urls,
        }],
    })
    d = poll_task(tid, "Kling 3.0")
    out = OUTPUT_DIR / f"v13_kling_{int(time.time())}.mp4"
    return download_first(d, out)


# ── Step 3: Kling V1-6 direct API lipsync ───────────────────────────────────

def lip_sync_kling(video_path: Path, audio_path: Path) -> Path:
    log.info("Kling lip-sync (video + audio → synced video)...")
    from virtuai.tools.kling_omni import lip_sync

    video_url = upload_to_tmpfiles(video_path)
    audio_url = upload_to_tmpfiles(audio_path)
    log.info(f"  video → {video_url}")
    log.info(f"  audio → {audio_url}")

    ts = int(time.time())
    result = lip_sync(
        video_url=video_url,
        audio_url=audio_url,
        output_filename=f"v13_lipsync_{ts}.mp4",
    )
    return Path(result["local_path"])


# ── Step 4: Suno music ──────────────────────────────────────────────────────

def generate_music() -> Path | None:
    log.info("Suno music...")
    body = {
        "prompt": ("Calm intimate acoustic underscore, soft piano with warm "
                   "ambient pad, slight room tone, contemplative and confident. "
                   "No vocals. Instrumental only."),
        "customMode": False, "instrumental": True, "model": "V3_5",
        "callBackUrl": "https://example.com/cb",
    }
    r = httpx.post(f"{KIE_API_BASE}/generate", headers=_headers(),
                   json=body, timeout=30)
    if r.status_code != 200:
        log.warning(f"Suno submit: {r.text[:200]}")
        return None
    tid = (r.json().get("data") or {}).get("taskId")
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
    out = OUTPUT_DIR / f"v13_music_{int(time.time())}.mp3"
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(audio_url)
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  ↓ {out.name}")
    return out


# ── Step 5: Post-production (natural look + music + handle, NO captions) ────

def post_produce(video: Path, music: Path | None) -> Path:
    log.info("Natural look + music + handle (no captions)...")
    work = OUTPUT_DIR / f"_v13_work_{int(time.time())}"
    work.mkdir(exist_ok=True)

    natural = work / "natural.mp4"
    vf = (
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,"
        "eq=contrast=1.03:saturation=1.00:gamma=1.0,"
        "curves=master='0/0 0.3/0.29 0.7/0.71 1/1',"
        "noise=alls=8:allf=t+u,"
        "vignette=PI/6"
    )
    subprocess.run([
        FFMPEG, "-y", "-i", str(video),
        "-vf", vf,
        "-r", "30",
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
            f"[1:a]volume=0.07,afade=t=in:st=0:d=1.5,afade=t=out:st={dur-1.5}:d=1.5[bg];"
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
    final = OUTPUT_DIR / f"daniel_reel_v13_{ts}.mp4"
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
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — Cinematic Kling + Lipsync Reel v13")
    log.info("=" * 60)

    if not CANONICAL_FACE.exists():
        raise FileNotFoundError(CANONICAL_FACE)

    # Step 1: viral script
    log.info("Step 1: DeepSeek viral script...")
    from virtuai.tools.script_writer import write_script, full_audio_text
    script = write_script(topic=None, n_scenes=5)
    script_path = SCRIPT_DIR / f"v13_{int(time.time())}.json"
    script_path.write_text(json.dumps(script, indent=2))
    log.info(f"  Saved script: {script_path.name}")
    log.info(f"  Topic: {script['topic']}")
    log.info(f"  Hook:  {script['hook_summary']}")

    full_text = full_audio_text(script)

    # Step 2-4 in parallel: voice + music + cinematic Kling
    log.info("Phase 2: parallel voice + music + Kling cinematic...")
    canonical_url = upload_to_tmpfiles(CANONICAL_FACE)
    face_urls = [canonical_url, canonical_url]

    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        voice_fut = ex.submit(generate_full_audio, full_text)
        music_fut = ex.submit(generate_music)
        kling_fut = ex.submit(
            generate_kling_cinematic,
            script, face_urls,
            int(min(script.get("estimated_seconds", 15), 15)),
        )

        audio_path = voice_fut.result()
        music_path = music_fut.result()
        kling_video = kling_fut.result()

    # Step 5: lipsync
    synced = lip_sync_kling(kling_video, audio_path)

    # Step 6: post
    final = post_produce(synced, music_path)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info(f"Duration: {video_dur(final):.1f}s")
    log.info(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
