#!/usr/bin/env python3
"""
produce_reel_v7.py — Extended reel with meaningful b-roll + music + reviewer.

Pipeline:
  1. Generate Part-2 audio (ElevenLabs Liam)
  2. Generate Part-2 avatar (Kling Avatar Pro, canonical Daniel)
  3. Generate 4 b-roll clips, each tied to a specific dialogue line
  4. Generate Suno background music
  5. Concat part-1 + part-2 (continuous audio)
  6. Overlay b-roll on top at scene-change beats
  7. Generate captions for the combined audio
  8. Burn captions + hook overlay
  9. Mix music underneath at -22dB
  10. Reviewer gate
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
log = logging.getLogger("produce_reel_v7")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

# Reuse part-1 assets
PART1_AVATAR = OUTPUT_DIR / "avatar_lipsync_1778700549.mp4"
PART1_AUDIO = OUTPUT_DIR / "eleven_voice_1778700052.mp3"
CANONICAL_FACE = ROOT / "virtuai" / "persona" / "canonical_daniel.png"

ELEVENLABS_VOICE = "TX3LPaxmHKxFdv7VOQHJ"  # Liam

PART2_SCRIPT = (
    "I run six AI agents that handle my outreach, content, and analytics. "
    "Forty dollars a month. They don't sleep, don't ask for raises, "
    "and don't miss a beat. Build your team."
)

HOOK_TEXT = "Nobody's building AI systems."

POLL_INTERVAL = 12
POLL_TIMEOUT = 900

# ── B-roll: each tied to a specific dialogue line ────────────────────────────

BROLL_PROMPTS = [
    {
        "name": "broll_pdf_chatbot",
        "line": "slapping a chatbot on a PDF",
        "prompt": (
            "Cinematic top-down shot of a stack of paper documents on a clean "
            "desk. A simple speech bubble icon, made of glowing blue light, "
            "descends and stamps itself onto the top page like a sticker. "
            "Shallow depth of field, dramatic side light, slight slow motion. "
            "9:16 vertical. No text legible. Conceptual minimalist aesthetic. "
            "The bubble looks fragile and out-of-place on the documents."
        ),
        "duration": 5,
    },
    {
        "name": "broll_blueprint",
        "line": "app, not architecture",
        "prompt": (
            "Cinematic close-up of a detailed architectural blueprint being "
            "drawn on rich blue paper with a silver mechanical pencil. "
            "Precise geometric lines, schematics, building floors materializing "
            "as the pencil moves. Warm desk-lamp light, shallow depth of field, "
            "the hand moves with confident expertise. 9:16 vertical. No face. "
            "Premium engineering aesthetic, the camera slowly pulls back to "
            "reveal the scope of the design."
        ),
        "duration": 5,
    },
    {
        "name": "broll_neural_brain",
        "line": "build the brain",
        "prompt": (
            "Cinematic abstract 3D visualization of a glowing neural network, "
            "shaped like a luminous brain. Hundreds of glowing nodes connected "
            "by pulsing lines of cyan and amber light, rotating slowly in dark "
            "space. Particle effects, depth and parallax. Cinematic teal-orange "
            "color grade, premium sci-fi aesthetic. 9:16 vertical. The brain "
            "form is recognizable but stylized, organic and technical at once."
        ),
        "duration": 5,
    },
    {
        "name": "broll_six_agents",
        "line": "six AI agents",
        "prompt": (
            "Cinematic shot of six floating holographic dashboard panels "
            "arranged in a 3x2 grid in dark space, each panel showing different "
            "AI agent activity: email queue updating, analytics graphs rising, "
            "social media post drafts, calendar booking, code being written, "
            "customer chats. Subtle blue and cyan light, particles between "
            "panels suggesting data flow. 9:16 vertical. No face. Premium "
            "tech-noir aesthetic, slight slow camera push-in."
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
        r = httpx.get(
            f"{KIE_API_BASE}/jobs/recordInfo",
            params={"taskId": task_id}, headers=_headers(), timeout=30,
        )
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


def poll_suno(task_id: str) -> dict:
    """Suno has a different polling endpoint and state values."""
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        r = httpx.get(
            f"{KIE_API_BASE}/generate/record-info",
            params={"taskId": task_id}, headers=_headers(), timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        status = data.get("status", "")
        if status == "SUCCESS":
            return data
        if status in ("FAILED", "ERROR", "CALLBACK_EXCEPTION"):
            raise RuntimeError(f"Suno failed: {data}")
        log.info(f"  Suno: {status}...")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Suno timed out")


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


def upload_file(filepath: Path) -> str:
    with open(filepath, "rb") as f:
        resp = httpx.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (filepath.name, f)},
            timeout=180,
        )
    resp.raise_for_status()
    return resp.json()["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


def video_dur(path: Path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


# ── Step 1: ElevenLabs voice for part 2 ─────────────────────────────────────

def generate_voice_part2() -> Path:
    log.info("Step 1: ElevenLabs TTS for part 2...")
    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask",
        headers=_headers(),
        json={
            "model": "elevenlabs/text-to-speech-turbo-2-5",
            "input": {
                "text": PART2_SCRIPT,
                "voice": ELEVENLABS_VOICE,
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.4,
                "speed": 1.0,
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    task_id = (r.json().get("data") or {}).get("taskId")
    if not task_id:
        raise RuntimeError(f"ElevenLabs submit failed: {r.text}")
    data = poll_task(task_id, "ElevenLabs")
    ts = int(time.time())
    out = OUTPUT_DIR / f"eleven_voice_part2_{ts}.mp3"
    return download_first(data, out)


# ── Step 2: Avatar Pro for part 2 ────────────────────────────────────────────

def generate_avatar_part2(audio_path: Path) -> Path:
    log.info("Step 2: Kling Avatar Pro for part 2...")
    face_url = upload_file(CANONICAL_FACE)
    audio_url = upload_file(audio_path)
    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask",
        headers=_headers(),
        json={
            "model": "kling/ai-avatar-pro",
            "input": {
                "image_url": face_url,
                "audio_url": audio_url,
                "prompt": (
                    "A confident young entrepreneur speaking directly to camera, "
                    "natural head movements and hand gestures, modern office, "
                    "eye contact with camera, subtle micro-expressions."
                ),
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    task_id = (r.json().get("data") or {}).get("taskId")
    if not task_id:
        raise RuntimeError(f"Avatar submit failed: {r.text}")
    data = poll_task(task_id, "Avatar")
    ts = int(time.time())
    return download_first(data, OUTPUT_DIR / f"avatar_part2_{ts}.mp4")


# ── Step 3: B-roll generation ───────────────────────────────────────────────

def generate_broll(cfg: dict) -> Path:
    log.info(f"B-roll: {cfg['name']} ('{cfg['line']}')")
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
        raise RuntimeError(f"B-roll submit failed: {r.text}")
    data = poll_task(task_id, cfg["name"])
    ts = int(time.time())
    return download_first(data, OUTPUT_DIR / f"{cfg['name']}_{ts}.mp4")


# ── Step 4: Suno background music ───────────────────────────────────────────

def generate_music() -> Path | None:
    log.info("Step 4: Suno background music...")
    body = {
        "prompt": (
            "Subtle minimalist tech entrepreneur underscore. Lo-fi ambient "
            "with soft synth pad, distant glitchy percussion, contemplative "
            "but driving forward, sub-bass. No vocals. Instrumental only."
        ),
        "customMode": False,
        "instrumental": True,
        "model": "V3_5",
        "callBackUrl": "https://example.com/cb",
    }
    r = httpx.post(
        f"{KIE_API_BASE}/generate",
        headers=_headers(), json=body, timeout=30,
    )
    if r.status_code != 200:
        log.warning(f"Suno submit returned {r.status_code}: {r.text[:200]}")
        return None
    task_id = (r.json().get("data") or {}).get("taskId")
    if not task_id:
        log.warning(f"Suno: no taskId in {r.text[:200]}")
        return None
    log.info(f"  Suno task: {task_id}")
    try:
        data = poll_suno(task_id)
    except Exception as e:
        log.warning(f"Suno poll failed: {e}")
        return None

    # Audio URL is at data.response.sunoData[0].audioUrl
    audio_url = ""
    resp_inner = data.get("response", {})
    suno_data = resp_inner.get("sunoData", []) if isinstance(resp_inner, dict) else []
    if suno_data and isinstance(suno_data, list):
        audio_url = suno_data[0].get("audioUrl") or suno_data[0].get("streamAudioUrl", "")
    if not audio_url:
        log.warning(f"Suno: no audioUrl in {data}")
        return None

    ts = int(time.time())
    out = OUTPUT_DIR / f"bg_music_{ts}.mp3"
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(audio_url)
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  Music: {out.name}")
    return out


# ── Step 5: Concat parts ────────────────────────────────────────────────────

def concat_parts(part1: Path, part2: Path) -> Path:
    log.info("Step 5: Concat part 1 + part 2 (continuous audio)...")
    work = OUTPUT_DIR / f"_v7_work_{int(time.time())}"
    work.mkdir(exist_ok=True)

    # Re-encode both at 720x1280, 30fps so concat is uniform
    p1_norm = work / "p1.mp4"
    p2_norm = work / "p2.mp4"
    for src, dst in [(part1, p1_norm), (part2, p2_norm)]:
        subprocess.run([
            FFMPEG, "-y", "-i", str(src),
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1",
            "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(dst),
        ], check=True, capture_output=True)

    list_file = work / "concat.txt"
    list_file.write_text(f"file '{p1_norm.resolve()}'\nfile '{p2_norm.resolve()}'\n")

    out = work / "combined.mp4"
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(out),
    ], check=True, capture_output=True)

    # Extract combined audio for caption generation
    audio_out = work / "combined.wav"
    subprocess.run([
        FFMPEG, "-y", "-i", str(out),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio_out),
    ], check=True, capture_output=True)

    log.info(f"  Combined: {video_dur(out):.1f}s")
    return out


# ── Step 6: B-roll overlays ─────────────────────────────────────────────────

def overlay_brolls(base: Path, brolls: list[Path], schedule: list[tuple]) -> Path:
    log.info(f"Step 6: Overlaying {len(schedule)} b-roll cuts...")
    inputs = ["-i", str(base)]
    for b in brolls:
        inputs += ["-i", str(b)]

    filter_parts = [
        "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1[base]"
    ]
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

    work = base.parent
    out = work / "overlaid.mp4"
    subprocess.run([
        FFMPEG, "-y", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{last_label}]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy", "-r", "30",
        str(out),
    ], check=True, capture_output=True)
    log.info(f"  Overlaid: {out.name}")
    return out


# ── Step 7: Captions ────────────────────────────────────────────────────────

def make_captions(video: Path) -> Path:
    log.info("Step 7: Generating captions for combined audio...")
    work = video.parent
    audio = work / "for_captions.wav"
    subprocess.run([
        FFMPEG, "-y", "-i", str(video),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio),
    ], check=True, capture_output=True)

    from virtuai.tools.caption_generator import create_captions
    ass_path = create_captions(
        audio_path=str(audio),
        whisper_model="base",
        words_per_group=2,
    )
    log.info(f"  Captions: {Path(ass_path).name}")
    return Path(ass_path)


# ── Step 8 + 9: Burn captions + mix music ───────────────────────────────────

def finalize(video: Path, captions: Path, music: Path | None) -> Path:
    log.info("Step 8: Mixing music + burning captions + hook...")
    work = video.parent

    # Mix music underneath if available
    if music and music.exists():
        log.info("  Mixing background music at -20dB...")
        mixed = work / "mixed.mp4"
        dur = video_dur(video)
        subprocess.run([
            FFMPEG, "-y", "-i", str(video), "-i", str(music),
            "-filter_complex",
            f"[1:a]volume=0.10,afade=t=in:st=0:d=1.0,afade=t=out:st={dur-1}:d=1.0[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            str(mixed),
        ], check=True, capture_output=True)
        video = mixed

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
    final = OUTPUT_DIR / f"daniel_reel_v7_{ts}.mp4"
    subprocess.run([
        FFMPEG, "-y", "-i", str(video),
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
    log.info("VirtuAI — Premium Extended Reel v7")
    log.info("=" * 60)

    for p in [PART1_AVATAR, PART1_AUDIO, CANONICAL_FACE]:
        if not p.exists():
            raise FileNotFoundError(f"Missing: {p}")

    # Step 1: Part 2 voice
    p2_audio = generate_voice_part2()

    # Step 2: Part 2 avatar
    p2_avatar = generate_avatar_part2(p2_audio)

    # Step 3: B-roll (4 clips)
    broll_paths = []
    for cfg in BROLL_PROMPTS:
        broll_paths.append(generate_broll(cfg))

    # Step 4: Music (optional, gracefully skips on failure)
    music_path = generate_music()

    # Step 5: Concat part 1 + part 2
    combined = concat_parts(PART1_AVATAR, p2_avatar)
    p1_dur = video_dur(PART1_AVATAR)
    log.info(f"  Part 1: {p1_dur:.1f}s, total: {video_dur(combined):.1f}s")

    # Step 6: Overlay schedule (each b-roll tied to its line's approx timing)
    # B-roll index, start, end
    schedule = [
        (0, 2.8, 4.6),                                 # "PDF chatbot" during line 2
        (1, 7.2, 9.0),                                 # blueprint during "app/architecture"
        (2, 10.0, 11.8),                               # neural brain during "build the brain"
        (3, p1_dur + 0.4, p1_dur + 2.6),               # six agents at start of part 2
    ]
    overlaid = overlay_brolls(combined, broll_paths, schedule)

    # Step 7: Captions for combined audio
    captions = make_captions(overlaid)

    # Step 8+9: Final
    final = finalize(overlaid, captions, music_path)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info("=" * 60)

    # ── Reviewer gate ────────────────────────────────────────────────────
    log.info("")
    log.info("Running reviewer gate...")
    from virtuai.tools.video_reviewer import review_video, format_review_report
    review = review_video(final)
    print(format_review_report(review))
    if review["verdict"] == "REVISE":
        log.error("REVIEWER REJECTED — see issues above.")
        sys.exit(2)
    log.info("REVIEWER APPROVED — reel cleared for publishing.")


if __name__ == "__main__":
    main()
