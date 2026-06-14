#!/usr/bin/env python3
"""
produce_reel_v8.py — Industry-level reel with parallel generation + cliffhanger.

Improvements over v7:
  • New Part 2 ends with cliffhanger ("seventh agent... part two")
  • Parallel KIE job submission (b-rolls + voice submit concurrently)
  • Motion-rich b-roll prompts (camera moves, not static)
  • Subtle zoompan on Daniel segments (Ken-Burns) for visual life
  • Updated reviewer with motion + pacing + cliffhanger checks
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
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
log = logging.getLogger("produce_reel_v8")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

PART1_AVATAR = OUTPUT_DIR / "avatar_lipsync_1778700549.mp4"
CANONICAL_FACE = ROOT / "virtuai" / "persona" / "canonical_daniel.png"

ELEVENLABS_VOICE = "TX3LPaxmHKxFdv7VOQHJ"  # Liam

# New Part 2 with cliffhanger ending
PART2_SCRIPT = (
    "Six AI agents. Content, outreach, analytics. Forty bucks a month. "
    "They publish, qualify, and report while I sleep. "
    "But there's a seventh — and I'm not ready to show you yet. Part two."
)

HOOK_TEXT = "Nobody's building AI systems."

POLL_INTERVAL = 10
POLL_TIMEOUT = 900

# ── B-roll prompts with CAMERA MOTION baked in ──────────────────────────────

BROLL_PROMPTS = [
    {
        "name": "broll_pdf_chatbot_v2",
        "line": "slapping a chatbot on a PDF",
        "prompt": (
            "Cinematic top-down tracking shot moving slowly across a stack of "
            "white documents on a dark wood desk. A small glowing blue chat "
            "speech-bubble icon hovers above and slowly descends, stamping itself "
            "onto the top page like a fragile sticker. Camera continues its slow "
            "lateral move past the stack. Shallow depth of field, dramatic side "
            "lighting, slight slow-motion. 9:16 vertical. Minimalist conceptual "
            "aesthetic. No text. The bubble looks fragile and incongruous against "
            "the formal documents."
        ),
        "duration": 5,
    },
    {
        "name": "broll_blueprint_v2",
        "line": "app, not architecture",
        "prompt": (
            "Cinematic slow camera push-in over a detailed architectural "
            "blueprint on rich blue paper. As the camera moves, white schematic "
            "lines self-draw across the surface — floor plans, elevations, "
            "structural details materializing in real-time. Warm desk-lamp light, "
            "shallow depth of field. 9:16 vertical. Premium engineering aesthetic. "
            "No face. The camera move communicates depth and scale of the design."
        ),
        "duration": 5,
    },
    {
        "name": "broll_neural_brain_v2",
        "line": "build the brain",
        "prompt": (
            "Cinematic camera orbit around a glowing 3D neural network shaped "
            "like a luminous brain. Hundreds of cyan and amber nodes connected "
            "by pulsing lines of light, rotating slowly in dark space. Particle "
            "effects, depth, lens flares. As the camera orbits, new connections "
            "spark between nodes. Cinematic teal-orange color grade, premium "
            "sci-fi aesthetic. 9:16 vertical. Organic and technical at once."
        ),
        "duration": 5,
    },
    {
        "name": "broll_agents_dashboard_v2",
        "line": "six AI agents working",
        "prompt": (
            "Cinematic slow camera push-in toward a dark cinematic workspace "
            "where six floating holographic dashboard panels arrange in a 3x2 "
            "grid. Each panel shows live AI agent activity: emails flying out, "
            "analytics graphs animating upward, social posts drafting, calendar "
            "auto-booking, code being written, customer chat responses. Subtle "
            "blue and cyan light, glowing data particles flowing between panels. "
            "9:16 vertical. Premium tech-noir aesthetic. As the camera moves, "
            "the numbers and graphs on the panels visibly update."
        ),
        "duration": 5,
    },
    {
        "name": "broll_night_office",
        "line": "while I sleep",
        "prompt": (
            "Cinematic establishing shot of a modern empty office at deep night, "
            "city skyline through large windows in soft focus. A single laptop "
            "on a clean desk glows with its screen on, casting blue light. "
            "Subtle camera slow push-in toward the screen. Dust motes drift in "
            "the light. The screen subtly flickers as work happens autonomously. "
            "9:16 vertical. Cinematic, contemplative, premium quality. No people. "
            "Conveys: 'the system is working while you sleep.'"
        ),
        "duration": 5,
    },
    {
        "name": "broll_locked_door",
        "line": "seventh — not ready to show",
        "prompt": (
            "Cinematic close-up of a heavy metal vault door with a glowing "
            "amber number 7 etched into its center, slight steam rising from "
            "below. The camera slowly dollies forward toward the door. Dark "
            "cinematic side lighting, deep shadows, premium mysterious "
            "aesthetic. The number 7 pulses subtly. 9:16 vertical. No people. "
            "The door stays closed — conveys 'not ready yet, coming soon.'"
        ),
        "duration": 5,
    },
]

# ── helpers ──────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def submit_kie(model: str, input_data: dict) -> str:
    """Submit a KIE job, return the taskId."""
    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask",
        headers=_headers(),
        json={"model": model, "input": input_data},
        timeout=30,
    )
    r.raise_for_status()
    task_id = (r.json().get("data") or {}).get("taskId")
    if not task_id:
        raise RuntimeError(f"Submit failed for {model}: {r.text[:300]}")
    return task_id


def poll_task(task_id: str, label: str = "") -> dict:
    deadline = time.time() + POLL_TIMEOUT
    last_state = ""
    while time.time() < deadline:
        r = httpx.get(
            f"{KIE_API_BASE}/jobs/recordInfo",
            params={"taskId": task_id}, headers=_headers(), timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        state = data.get("state", "")
        if state != last_state:
            log.info(f"  {label}: {state}...")
            last_state = state
        if state in ("success", "completed", "succeed"):
            return data
        if state in ("failed", "error", "fail"):
            raise RuntimeError(f"{label} failed: {data.get('failMsg', data)}")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"{label} timed out")


def poll_suno(task_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT
    last_status = ""
    while time.time() < deadline:
        r = httpx.get(
            f"{KIE_API_BASE}/generate/record-info",
            params={"taskId": task_id}, headers=_headers(), timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        status = data.get("status", "")
        if status != last_status:
            log.info(f"  Suno: {status}...")
            last_status = status
        if status == "SUCCESS":
            return data
        if status in ("FAILED", "ERROR", "CALLBACK_EXCEPTION"):
            raise RuntimeError(f"Suno failed: {data}")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError("Suno timed out")


def download_first(data: dict, out: Path) -> Path:
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


# ── Parallel KIE workflow ───────────────────────────────────────────────────

def submit_voice() -> str:
    return submit_kie("elevenlabs/text-to-speech-turbo-2-5", {
        "text": PART2_SCRIPT,
        "voice": ELEVENLABS_VOICE,
        "stability": 0.55,
        "similarity_boost": 0.78,
        "style": 0.45,
        "speed": 1.0,
    })


def submit_broll(cfg: dict) -> str:
    return submit_kie("kling-3.0/video", {
        "prompt": cfg["prompt"],
        "duration": str(cfg["duration"]),
        "aspect_ratio": "9:16",
        "mode": "std",
        "sound": False,
        "multi_shots": False,
    })


def submit_suno() -> str:
    body = {
        "prompt": (
            "Subtle minimalist tech entrepreneur underscore for a 25-second reel. "
            "Lo-fi ambient with soft synth pad, distant glitchy percussion, "
            "rising tension toward the end, contemplative but forward-moving, "
            "sub-bass present. No vocals. Instrumental only."
        ),
        "customMode": False,
        "instrumental": True,
        "model": "V3_5",
        "callBackUrl": "https://example.com/cb",
    }
    r = httpx.post(f"{KIE_API_BASE}/generate", headers=_headers(),
                   json=body, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Suno submit failed: {r.text[:200]}")
    task_id = (r.json().get("data") or {}).get("taskId")
    if not task_id:
        raise RuntimeError(f"Suno: no taskId in {r.text[:200]}")
    return task_id


def fetch_voice(task_id: str) -> Path:
    data = poll_task(task_id, "ElevenLabs")
    ts = int(time.time())
    return download_first(data, OUTPUT_DIR / f"eleven_voice_v8_{ts}.mp3")


def fetch_broll(task_id: str, name: str) -> Path:
    data = poll_task(task_id, name)
    ts = int(time.time())
    return download_first(data, OUTPUT_DIR / f"{name}_{ts}.mp4")


def fetch_suno(task_id: str) -> Path | None:
    try:
        data = poll_suno(task_id)
    except Exception as e:
        log.warning(f"Suno fetch failed: {e}")
        return None
    resp_inner = data.get("response", {})
    suno_data = resp_inner.get("sunoData", []) if isinstance(resp_inner, dict) else []
    audio_url = ""
    if suno_data and isinstance(suno_data, list):
        audio_url = suno_data[0].get("audioUrl") or suno_data[0].get("streamAudioUrl", "")
    if not audio_url:
        log.warning(f"Suno: no audioUrl")
        return None
    ts = int(time.time())
    out = OUTPUT_DIR / f"bg_music_v8_{ts}.mp3"
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(audio_url)
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  ↓ {out.name}")
    return out


# ── Avatar Pro (sequential, depends on voice) ───────────────────────────────

def generate_avatar_part2(audio_path: Path) -> Path:
    log.info("Avatar Pro for part 2...")
    face_url = upload_file(CANONICAL_FACE)
    audio_url = upload_file(audio_path)
    task_id = submit_kie("kling/ai-avatar-pro", {
        "image_url": face_url,
        "audio_url": audio_url,
        "prompt": (
            "A confident young entrepreneur speaking directly to camera with "
            "intimate energy, slight subtle camera push-in feel. Natural micro-"
            "expressions, hand gestures from the side, slight head tilts. "
            "Modern office, warm light. Holds steady eye contact. Toward the end, "
            "a slight smirk suggesting he has more to say."
        ),
    })
    data = poll_task(task_id, "Avatar")
    ts = int(time.time())
    return download_first(data, OUTPUT_DIR / f"avatar_part2_v8_{ts}.mp4")


# ── Stitching with Ken-Burns motion on static segments ──────────────────────

def concat_parts(p1: Path, p2: Path) -> Path:
    log.info("Concat part 1 + part 2 with subtle zoompan on Daniel...")
    work = OUTPUT_DIR / f"_v8_work_{int(time.time())}"
    work.mkdir(exist_ok=True)

    # Apply gentle Ken-Burns (zoompan) on each avatar segment for subtle motion.
    # zoompan: zoom from 1.0 to 1.08 over the clip duration.
    def kenburns(src: Path, dst: Path, zoom_end: float = 1.08, direction: str = "in"):
        fps = 30
        dur = video_dur(src)
        n_frames = int(dur * fps)
        if direction == "in":
            zoom_expr = f"min(zoom+0.0005,{zoom_end})"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        else:
            zoom_expr = f"max({zoom_end}-(on/{n_frames})*({zoom_end}-1),1)"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        vf = (
            f"scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,"
            f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
            f"d=1:s=720x1280:fps={fps}"
        )
        subprocess.run([
            FFMPEG, "-y", "-i", str(src),
            "-vf", vf,
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(dst),
        ], check=True, capture_output=True)

    p1_kb = work / "p1_kb.mp4"
    p2_kb = work / "p2_kb.mp4"
    kenburns(p1, p1_kb, zoom_end=1.06, direction="in")
    kenburns(p2, p2_kb, zoom_end=1.08, direction="in")

    list_file = work / "concat.txt"
    list_file.write_text(f"file '{p1_kb.resolve()}'\nfile '{p2_kb.resolve()}'\n")
    out = work / "combined.mp4"
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(out),
    ], check=True, capture_output=True)
    log.info(f"  Combined: {video_dur(out):.1f}s")
    return out


def overlay_brolls(base: Path, brolls: list[Path], schedule: list[tuple]) -> Path:
    log.info(f"Overlaying {len(schedule)} b-roll cuts...")
    inputs = ["-i", str(base)]
    for b in brolls:
        inputs += ["-i", str(b)]
    filter_parts = [
        "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1[base]"
    ]
    last = "base"
    for i, (b_idx, start, end) in enumerate(schedule):
        in_idx = b_idx + 1
        scaled = f"b{i}_scaled"
        out_lbl = f"v{i+1}"
        filter_parts.append(
            f"[{in_idx}:v]scale=720:1280:force_original_aspect_ratio=increase,"
            f"crop=720:1280,setsar=1,setpts=PTS-STARTPTS+{start}/TB[{scaled}]"
        )
        filter_parts.append(
            f"[{last}][{scaled}]overlay=x=0:y=0:enable='between(t,{start},{end})':"
            f"eof_action=pass[{out_lbl}]"
        )
        last = out_lbl
    work = base.parent
    out = work / "overlaid.mp4"
    subprocess.run([
        FFMPEG, "-y", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{last}]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy", "-r", "30",
        str(out),
    ], check=True, capture_output=True)
    return out


def make_captions(video: Path) -> Path:
    log.info("Captions from combined audio...")
    work = video.parent
    audio = work / "for_captions.wav"
    subprocess.run([
        FFMPEG, "-y", "-i", str(video),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio),
    ], check=True, capture_output=True)
    from virtuai.tools.caption_generator import create_captions
    return Path(create_captions(audio_path=str(audio), whisper_model="base", words_per_group=2))


def finalize(video: Path, captions: Path, music: Path | None) -> Path:
    log.info("Mix music + burn captions + hook + end card...")
    work = video.parent

    # Music mix
    if music and music.exists():
        mixed = work / "mixed.mp4"
        dur = video_dur(video)
        subprocess.run([
            FFMPEG, "-y", "-i", str(video), "-i", str(music),
            "-filter_complex",
            f"[1:a]volume=0.09,afade=t=in:st=0:d=1.5,afade=t=out:st={dur-1.5}:d=1.5[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(mixed),
        ], check=True, capture_output=True)
        video = mixed

    hook_escaped = HOOK_TEXT.replace("'", r"'\''")
    dur = video_dur(video)
    # Two text overlays: opening hook + end-card "PART 2 →"
    hook_filter = (
        f"ass='{captions.resolve()}',"
        f"drawtext=text='{hook_escaped}'"
        f":font='Montserrat Black':fontsize=44:fontcolor=white"
        f":borderw=3:bordercolor=black:shadowcolor=black@0.5:shadowx=2:shadowy=2"
        f":x=(w-text_w)/2:y=h*0.12"
        f":alpha='if(lt(t,0.2),t/0.2,if(lt(t,2.7),1,if(lt(t,3.0),((3.0-t)/0.3),0)))'"
        f":enable='between(t,0,3.0)',"
        f"drawtext=text='PART 2 →'"
        f":font='Montserrat Black':fontsize=56:fontcolor=#FFD700"
        f":borderw=4:bordercolor=black:shadowcolor=black@0.6:shadowx=3:shadowy=3"
        f":x=(w-text_w)/2:y=h*0.78"
        f":alpha='if(lt(t,{dur-2.5}),0,if(lt(t,{dur-2.0}),(t-{dur-2.5})/0.5,1))'"
        f":enable='between(t,{dur-2.5},{dur})'"
    )
    ts = int(time.time())
    final = OUTPUT_DIR / f"daniel_reel_v8_{ts}.mp4"
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
    return final


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — Industry-Level Reel v8 (parallel + cliffhanger)")
    log.info("=" * 60)

    for p in [PART1_AVATAR, CANONICAL_FACE]:
        if not p.exists():
            raise FileNotFoundError(f"Missing: {p}")

    # Phase 1: Submit ALL non-avatar jobs in parallel
    log.info("Phase 1: submitting parallel jobs (voice + 6 brolls + music)...")
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        voice_fut = ex.submit(submit_voice)
        broll_futs = [(cfg["name"], ex.submit(submit_broll, cfg)) for cfg in BROLL_PROMPTS]
        suno_fut = ex.submit(submit_suno)

        voice_task = voice_fut.result()
        broll_tasks = [(name, f.result()) for name, f in broll_futs]
        try:
            suno_task = suno_fut.result()
        except Exception as e:
            log.warning(f"Suno submit failed: {e}")
            suno_task = None

    log.info(f"  Submitted: voice={voice_task[:8]}, brolls={len(broll_tasks)}, suno={'yes' if suno_task else 'no'}")

    # Phase 2: Fetch voice first (Avatar Pro depends on it)
    log.info("Phase 2: fetching voice...")
    p2_audio = fetch_voice(voice_task)

    # Phase 3: Avatar Pro + (in parallel) fetch the remaining jobs
    log.info("Phase 3: Avatar Pro + parallel fetch of brolls & music...")
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        avatar_fut = ex.submit(generate_avatar_part2, p2_audio)
        broll_futs = [(name, ex.submit(fetch_broll, tid, name)) for name, tid in broll_tasks]
        suno_fut = ex.submit(fetch_suno, suno_task) if suno_task else None

        p2_avatar = avatar_fut.result()
        broll_paths = [f.result() for _, f in broll_futs]
        music_path = suno_fut.result() if suno_fut else None

    # Phase 4: Concat + b-roll overlays + captions + music
    log.info("Phase 4: stitch + overlay + caption + music...")
    combined = concat_parts(PART1_AVATAR, p2_avatar)
    p1_dur = video_dur(PART1_AVATAR)
    total_dur = video_dur(combined)
    log.info(f"  Part 1: {p1_dur:.1f}s | Total: {total_dur:.1f}s")

    # Schedule b-roll cuts aligned to specific spoken lines
    schedule = [
        (0, 2.8, 4.6),                                # PDF/chatbot
        (1, 7.2, 9.0),                                # blueprint
        (2, 10.0, 11.8),                              # neural brain
        (3, p1_dur + 0.4, p1_dur + 2.4),              # 6-agent dashboard
        (4, p1_dur + 5.6, p1_dur + 7.6),              # night office "while I sleep"
        (5, max(p1_dur + 9.0, total_dur - 3.0),
            min(p1_dur + 11.0, total_dur - 1.0)),     # locked door "seventh"
    ]
    schedule = [s for s in schedule if s[0] < len(broll_paths)]
    overlaid = overlay_brolls(combined, broll_paths, schedule)

    captions = make_captions(overlaid)
    final = finalize(overlaid, captions, music_path)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info("=" * 60)

    # Reviewer
    log.info("")
    log.info("Reviewer gate...")
    from virtuai.tools.video_reviewer import review_video, format_review_report
    review = review_video(final)
    print(format_review_report(review))
    if review["verdict"] == "REVISE":
        log.error("REVIEWER REJECTED.")
        sys.exit(2)
    log.info("APPROVED.")


if __name__ == "__main__":
    main()
