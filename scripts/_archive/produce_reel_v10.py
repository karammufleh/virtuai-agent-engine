#!/usr/bin/env python3
"""
produce_reel_v10.py — Fresh topic + industry-grade post-production.

Topic: The 4-hour rule — universal, contrarian, loop-back ending.

Tier 1 + critical Tier 2 fixes from competitor analysis:
  • No "PART 2 →" card / no zoom punch / no boom on ending
  • Loop-back close: final line completes opening line
  • Locked-off Daniel (no continuous Ken Burns)
  • Micro post-zoom PUNCHES (1.0 → 1.10x, 4 frames, hold 800ms) on 3 keyword beats
  • M31-style orange-teal grade (FFmpeg eq + colorbalance + curves)
  • Film grain overlay to mask plastic AI skin
  • Caption size bumped to 72px (was 56px) + vertical center
  • Loudnorm to -14 LUFS
  • ElevenLabs speed 1.15x for tighter pacing
  • Parallel KIE job submission

Architecture: parallel submit → fetch → stitch → grade → captions → SFX → review
"""
from __future__ import annotations

import concurrent.futures as cf
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
log = logging.getLogger("produce_reel_v10")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
SFX_DIR = ROOT / "virtuai" / "data" / "sfx"
KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

CANONICAL_FACE = ROOT / "virtuai" / "persona" / "canonical_daniel.png"
ELEVENLABS_VOICE = "TX3LPaxmHKxFdv7VOQHJ"  # Liam

SCRIPT = (
    "There's a four-hour rule that quietly built my career. "
    "Every day, I block four hours before 10 AM. No meetings, no email, no Slack. "
    "Two years in: three products shipped, one book written, income tripled. "
    "Most people grind twelve hours. Four hours of focus beats them every time. "
    "Four hours. That's the rule."
)
HOOK = "The four-hour rule."

POLL_INTERVAL = 10
POLL_TIMEOUT = 900

# ── B-roll: tied to specific lines in the script ────────────────────────────

BROLL_PROMPTS = [
    {
        "name": "broll_dawn_desk",
        "line": "block four hours before 10 AM",
        "prompt": (
            "Cinematic static shot of a clean minimalist wooden desk at dawn, "
            "soft golden light streaming through a window from camera-right. "
            "On the desk: a single ceramic mug of black coffee with steam rising "
            "slowly, an open leather notebook with a pen, a closed silver laptop. "
            "Shallow depth of field, dust motes in the light beam. No people. "
            "9:16 vertical. Premium calm focused aesthetic. Camera does not "
            "move. The steam from the coffee rises in slow real-time motion."
        ),
        "duration": 5,
    },
    {
        "name": "broll_clock_spinning",
        "line": "grind twelve hours",
        "prompt": (
            "Cinematic time-lapse of a wall clock spinning rapidly from morning "
            "to night, sunlight rotating across a wood floor with the cast "
            "shadow lengthening and shifting hue from warm to cool blue. "
            "Static camera, the clock hands blur into circles of motion. "
            "Side light through window. 9:16 vertical. No people. Conveys: "
            "time slipping away."
        ),
        "duration": 5,
    },
    {
        "name": "broll_proof_montage",
        "line": "shipped, written, tripled",
        "prompt": (
            "Cinematic close-up overhead shot of a clean white desk surface. "
            "Three objects laid out left to right with intentional spacing: "
            "a glowing smartphone showing an app icon (slight pulse), a "
            "hardcover book with a textured cover (no readable title), and a "
            "small paper chart with a line clearly trending upward. Subtle slow "
            "dolly forward over the three objects. Warm desk-lamp light, shallow "
            "depth of field, premium product-shot aesthetic. 9:16 vertical. "
            "No people, no text. Conveys: tangible proof."
        ),
        "duration": 5,
    },
    {
        "name": "broll_focused_typing",
        "line": "four hours of focus beats them",
        "prompt": (
            "Cinematic over-the-shoulder shot of a person typing rapidly on a "
            "silver laptop in a quiet office, viewed from camera-left at a "
            "slight low angle. Their face is out of frame — only the back of "
            "their head, neck, and hands visible. Single warm desk lamp lighting "
            "the keyboard from above. Background completely dark and out of "
            "focus. The hands move with practiced confident rhythm. 9:16 "
            "vertical. No identifying face. Conveys: deep focus, flow state."
        ),
        "duration": 5,
    },
]

# Post-zoom keyword PUNCH timings (computed after captions).
# Format: (caption_keyword_regex, zoom_factor, hold_seconds)
PUNCH_TARGETS = [
    (r"\bfour[-\s]?hour\b", 1.08, 0.6),
    (r"\btripled\b", 1.12, 0.5),
    (r"\bfocus\b", 1.10, 0.5),
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _headers():
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def submit_kie(model, input_data):
    r = httpx.post(
        f"{KIE_API_BASE}/jobs/createTask", headers=_headers(),
        json={"model": model, "input": input_data}, timeout=30,
    )
    r.raise_for_status()
    tid = (r.json().get("data") or {}).get("taskId")
    if not tid:
        raise RuntimeError(f"Submit failed for {model}: {r.text[:300]}")
    return tid


def poll_task(task_id, label=""):
    deadline = time.time() + POLL_TIMEOUT
    last = ""
    while time.time() < deadline:
        r = httpx.get(f"{KIE_API_BASE}/jobs/recordInfo",
                      params={"taskId": task_id}, headers=_headers(), timeout=30)
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


def poll_suno(task_id):
    deadline = time.time() + POLL_TIMEOUT
    last = ""
    while time.time() < deadline:
        r = httpx.get(f"{KIE_API_BASE}/generate/record-info",
                      params={"taskId": task_id}, headers=_headers(), timeout=30)
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


def upload_file(filepath):
    with open(filepath, "rb") as f:
        r = httpx.post("https://tmpfiles.org/api/v1/upload",
                       files={"file": (filepath.name, f)}, timeout=180)
    r.raise_for_status()
    return r.json()["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


def video_dur(p):
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", str(p)],
        capture_output=True, text=True)
    return float(json.loads(r.stdout)["format"]["duration"])


# ── Phase 1: parallel submit ────────────────────────────────────────────────

def submit_voice():
    return submit_kie("elevenlabs/text-to-speech-turbo-2-5", {
        "text": SCRIPT, "voice": ELEVENLABS_VOICE,
        "stability": 0.5, "similarity_boost": 0.78, "style": 0.4,
        "speed": 1.15,  # tighter pacing to match top creators (180-220 WPM)
    })


def submit_broll(cfg):
    return submit_kie("kling-3.0/video", {
        "prompt": cfg["prompt"], "duration": str(cfg["duration"]),
        "aspect_ratio": "9:16", "mode": "std", "sound": False,
        "multi_shots": False,
    })


def submit_suno():
    body = {
        "prompt": ("Calm minimalist focus underscore, lo-fi ambient piano with "
                   "warm pad, subtle vinyl crackle, gentle tempo, contemplative "
                   "and confident. No vocals. Instrumental."),
        "customMode": False, "instrumental": True, "model": "V3_5",
        "callBackUrl": "https://example.com/cb",
    }
    r = httpx.post(f"{KIE_API_BASE}/generate", headers=_headers(),
                   json=body, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Suno submit: {r.text[:200]}")
    tid = (r.json().get("data") or {}).get("taskId")
    if not tid:
        raise RuntimeError(f"Suno: {r.text[:200]}")
    return tid


def fetch_voice(tid):
    d = poll_task(tid, "ElevenLabs")
    return download_first(d, OUTPUT_DIR / f"v10_voice_{int(time.time())}.mp3")


def fetch_broll(tid, name):
    d = poll_task(tid, name)
    return download_first(d, OUTPUT_DIR / f"v10_{name}_{int(time.time())}.mp4")


def fetch_suno(tid):
    try:
        d = poll_suno(tid)
    except Exception as e:
        log.warning(f"Suno fetch failed: {e}")
        return None
    resp = d.get("response", {})
    sd = resp.get("sunoData", []) if isinstance(resp, dict) else []
    audio_url = ""
    if sd and isinstance(sd, list):
        audio_url = sd[0].get("audioUrl") or sd[0].get("streamAudioUrl", "")
    if not audio_url:
        return None
    out = OUTPUT_DIR / f"v10_music_{int(time.time())}.mp3"
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(audio_url)
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  ↓ {out.name}")
    return out


def generate_avatar(audio_path):
    log.info("Avatar Pro...")
    face_url = upload_file(CANONICAL_FACE)
    audio_url = upload_file(audio_path)
    tid = submit_kie("kling/ai-avatar-pro", {
        "image_url": face_url, "audio_url": audio_url,
        "prompt": (
            "A confident young entrepreneur speaking directly to camera. "
            "Eye contact, calm authority, slight half-smile on key claims. "
            "Hands resting on the desk, occasional small gestures. Modern "
            "minimalist office, warm natural light. Holds steady — does NOT "
            "look away from camera. Toward the very end, settles into a "
            "natural relaxed expression as if the thought is complete."
        ),
    })
    d = poll_task(tid, "Avatar")
    return download_first(d, OUTPUT_DIR / f"v10_avatar_{int(time.time())}.mp4")


# ── Phase 2: post-production ────────────────────────────────────────────────

def parse_ass_keyword_time(ass_path: Path, pattern_re: str) -> float | None:
    """Find timestamp of the first caption matching a regex."""
    rx = re.compile(pattern_re, re.IGNORECASE)
    for line in ass_path.read_text().splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        if rx.search(parts[9]):
            try:
                h, m, s = parts[1].strip().split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except Exception:
                continue
    return None


def apply_keyword_punches(video: Path, captions: Path) -> Path:
    """Split video at keyword timestamps and apply micro-zoom on each."""
    log.info("Applying keyword punch zooms...")
    work = video.parent
    dur = video_dur(video)

    # Find each keyword's timestamp
    punches: list[tuple[float, float, float]] = []  # (start, end, zoom)
    for pattern, zoom, hold in PUNCH_TARGETS:
        t = parse_ass_keyword_time(captions, pattern)
        if t is not None:
            punches.append((t, min(t + hold, dur - 0.1), zoom))
            log.info(f"  Punch '{pattern}' at t={t:.2f}s zoom={zoom}")

    if not punches:
        log.info("  No keywords matched — skipping punches.")
        return video

    # Build segments: pre, [punch1, between, punch2, ...], post
    punches.sort()
    segments = []  # list of (start, end, zoom_or_none)
    cursor = 0.0
    for s, e, z in punches:
        if s > cursor:
            segments.append((cursor, s, None))
        segments.append((s, e, z))
        cursor = e
    if cursor < dur:
        segments.append((cursor, dur, None))

    # Render each segment
    work_dir = work / f"_punches_{int(time.time())}"
    work_dir.mkdir(exist_ok=True)
    seg_files = []
    for i, (s, e, z) in enumerate(segments):
        out = work_dir / f"seg_{i:03d}.mp4"
        if z is None:
            vf = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280"
        else:
            # Static crop-zoom by scaling smaller center and rescaling out
            crop_w = int(720 / z) // 2 * 2  # ensure even
            crop_h = int(1280 / z) // 2 * 2
            crop_x = (720 - crop_w) // 2
            crop_y = (1280 - crop_h) // 2
            vf = (
                f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
                f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=720:1280"
            )
        subprocess.run([
            FFMPEG, "-y", "-i", str(video),
            "-ss", f"{s}", "-to", f"{e}",
            "-vf", vf,
            "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(out),
        ], check=True, capture_output=True)
        seg_files.append(out)

    concat_list = work_dir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{s.resolve()}'" for s in seg_files))
    out = work / "punched.mp4"
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(out),
    ], check=True, capture_output=True)
    log.info(f"  Punched: {out.name}")
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


def apply_color_and_grain(video: Path) -> Path:
    """M31-inspired orange-teal grade + subtle film grain."""
    log.info("Applying orange-teal LUT + film grain...")
    work = video.parent
    out = work / "graded.mp4"
    vf = (
        # S-curve contrast + saturation bump
        "eq=contrast=1.10:saturation=1.18:gamma=0.97,"
        # Warm shadows (orange) + cool highlights (teal)
        "colorbalance=rs=0.06:gs=0.01:bs=-0.06:rm=0.03:gm=0:bm=-0.03:rh=-0.03:gh=0:bh=-0.05,"
        # Custom S-curve (gentle)
        "curves=master='0/0 0.25/0.20 0.75/0.80 1/1',"
        # Subtle film grain (temporal noise)
        "noise=alls=6:allf=t+u"
    )
    subprocess.run([
        FFMPEG, "-y", "-i", str(video),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy",
        str(out),
    ], check=True, capture_output=True)
    log.info(f"  Graded: {out.name}")
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
    log.info("Mix music + loudnorm + burn captions + small handle watermark...")
    work = video.parent

    # First: loudnorm + music mix in one pass
    if music and music.exists():
        mixed = work / "mixed.mp4"
        dur = video_dur(video)
        subprocess.run([
            FFMPEG, "-y", "-i", str(video), "-i", str(music),
            "-filter_complex",
            # Dialogue: loudnorm to -14 LUFS, bg: low + fade
            f"[0:a]loudnorm=I=-14:LRA=11:tp=-1[dlg];"
            f"[1:a]volume=0.08,afade=t=in:st=0:d=1.5,afade=t=out:st={dur-1.5}:d=1.5[bg];"
            f"[dlg][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(mixed),
        ], check=True, capture_output=True)
        video = mixed
    else:
        # Loudnorm only
        normed = work / "normed.mp4"
        subprocess.run([
            FFMPEG, "-y", "-i", str(video),
            "-af", "loudnorm=I=-14:LRA=11:tp=-1",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(normed),
        ], check=True, capture_output=True)
        video = normed

    # Burn captions + small corner handle (no big "PART 2" card)
    hook_escaped = HOOK.replace("'", r"'\''")
    burn_filter = (
        f"ass='{captions.resolve()}',"
        # Subtle hook overlay for first 2.4s only (no exit zoom)
        f"drawtext=text='{hook_escaped}'"
        f":font='Montserrat Black':fontsize=40:fontcolor=white"
        f":borderw=2:bordercolor=black"
        f":x=(w-text_w)/2:y=h*0.13"
        f":alpha='if(lt(t,0.25),t/0.25,if(lt(t,2.0),1,if(lt(t,2.4),(2.4-t)/0.4,0)))'"
        f":enable='between(t,0,2.4)',"
        # Static handle watermark, bottom-left, low opacity, always on
        f"drawtext=text='@daniel.calder'"
        f":font='Inter':fontsize=20:fontcolor=white@0.55"
        f":borderw=1:bordercolor=black@0.5"
        f":x=20:y=h-40"
    )

    ts = int(time.time())
    final = OUTPUT_DIR / f"daniel_reel_v10_{ts}.mp4"
    subprocess.run([
        FFMPEG, "-y", "-i", str(video),
        "-vf", burn_filter,
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
    log.info("VirtuAI — Industry-Grade Reel v10 (Four-hour rule)")
    log.info("=" * 60)

    if not CANONICAL_FACE.exists():
        raise FileNotFoundError(CANONICAL_FACE)

    # Phase 1: submit everything in parallel
    log.info("Phase 1: parallel submit (voice + 4 brolls + music)...")
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

    # Phase 2: fetch voice first (avatar depends on it)
    log.info("Phase 2: fetch voice...")
    audio_path = fetch_voice(voice_task)

    # Phase 3: avatar + parallel fetch of brolls & music
    log.info("Phase 3: Avatar + parallel fetch...")
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        avatar_fut = ex.submit(generate_avatar, audio_path)
        broll_futs = [(n, ex.submit(fetch_broll, t, n)) for n, t in broll_tasks]
        music_fut = ex.submit(fetch_suno, suno_task) if suno_task else None

        avatar = avatar_fut.result()
        brolls = [f.result() for _, f in broll_futs]
        music = music_fut.result() if music_fut else None

    # Phase 4: post-production stack
    log.info("Phase 4: post-production...")
    work = OUTPUT_DIR / f"_v10_work_{int(time.time())}"
    work.mkdir(exist_ok=True)

    # Copy avatar into work dir so all derived files live here
    base = work / "base.mp4"
    subprocess.run([
        FFMPEG, "-y", "-i", str(avatar),
        "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1",
        "-r", "30",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        str(base),
    ], check=True, capture_output=True)
    log.info(f"  Base: {video_dur(base):.1f}s")

    # B-roll overlay schedule — aligned to spoken lines
    dur = video_dur(base)
    schedule = [
        (0, dur * 0.20, dur * 0.30),  # dawn_desk during "block four hours before 10 AM"
        (1, dur * 0.65, dur * 0.75),  # clock_spinning during "grind twelve hours"
        (2, dur * 0.45, dur * 0.55),  # proof_montage during "shipped, written, tripled"
        (3, dur * 0.80, dur * 0.90),  # focused_typing during "four hours of focus beats them"
    ]
    schedule = [s for s in schedule if s[0] < len(brolls)]
    overlaid = overlay_brolls(base, brolls, schedule)

    # Color grade + film grain
    graded = apply_color_and_grain(overlaid)

    # Captions (uses our updated 72px + vertical-center module)
    captions = make_captions(graded)

    # Keyword punch zooms (uses caption timing to find keywords)
    punched = apply_keyword_punches(graded, captions)

    # Final: music + loudnorm + burn captions + handle watermark
    final = finalize(punched, captions, music)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
    log.info("=" * 60)

    log.info("Reviewer gate...")
    from virtuai.tools.video_reviewer import review_video, format_review_report
    review = review_video(final)
    print(format_review_report(review))


if __name__ == "__main__":
    main()
