#!/usr/bin/env python3
"""
produce_reel_v11.py — Multi-scene reel with natural color.

User complaint: previous reels look AI-rendered, same scene throughout,
color grade too heavy. This version addresses ALL three:

  1. Nano Banana 2 edits canonical_daniel.png into 3 DIFFERENT real
     environments while preserving face identity (cafe, airport lounge,
     home study). Avatar Pro then animates each scene-swapped image with
     a segment of the audio → the reel cuts between 3 actual locations.

  2. NATURAL color (no orange-teal LUT). Mild S-curve only, plus
     authentic film grain and subtle vignette to mask plastic AI skin.

  3. Parallel KIE submission (~10 min wall-clock for the whole reel).
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
log = logging.getLogger("produce_reel_v11")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

CANONICAL_FACE = ROOT / "virtuai" / "persona" / "canonical_daniel.png"
ELEVENLABS_VOICE = "TX3LPaxmHKxFdv7VOQHJ"  # Liam

HOOK = "The four-hour rule."

# Split script into 3 scenes — each scene gets its own Avatar Pro generation
# in a different real-world location.
SCENES = [
    {
        "name": "scene_cafe",
        "edit_prompt": (
            "Keep the same person and face. Place them seated at a small "
            "outdoor European cafe table, soft morning sunlight, warm brick "
            "wall behind them, a single espresso cup on the table. Natural "
            "candid photography style, slightly grainy, real iPhone-shot look. "
            "Shallow depth of field, background slightly blurred. Same dark "
            "polo shirt. Eye-level medium shot. No text in the image."
        ),
        "audio_text": (
            "There's a four-hour rule that quietly built my career. "
            "Every day, I block four hours before 10 AM."
        ),
    },
    {
        "name": "scene_lounge",
        "edit_prompt": (
            "Keep the same person and face. Place them seated in a quiet "
            "airport lounge by a huge floor-to-ceiling window, soft diffused "
            "natural light, blurred airplane visible outside in shallow depth "
            "of field. Wearing the same dark polo. Eye-level medium shot, "
            "professional candid photography. Realistic phone-camera "
            "aesthetic, slight noise and natural color. No text."
        ),
        "audio_text": (
            "No meetings, no email, no Slack. Two years in: "
            "three products shipped, one book written, income tripled."
        ),
    },
    {
        "name": "scene_study",
        "edit_prompt": (
            "Keep the same person and face. Place them at a warm wood "
            "writing desk in a quiet home study, bookshelf behind them in "
            "soft focus, single warm desk lamp providing the key light. "
            "Same dark polo. Eye-level medium shot, intimate candid "
            "photography. Realistic natural color, mild grain, authentic "
            "DSLR or iPhone aesthetic. No text."
        ),
        "audio_text": (
            "Most people grind twelve hours. Four hours of focus beats "
            "them every time. Four hours. That's the rule."
        ),
    },
]

# B-roll: keep semantic ties but with NATURAL look (no over-stylized cinematic)
BROLL_PROMPTS = [
    {
        "name": "broll_clock",
        "prompt": (
            "Candid time-lapse of a wall clock spinning fast, real photography "
            "aesthetic, natural light through a window, soft shadows sweeping "
            "across a wooden floor. iPhone-shot look, slight grain, real "
            "color. No text. 9:16 vertical. Static camera."
        ),
        "duration": 5,
    },
    {
        "name": "broll_journaling",
        "prompt": (
            "Candid overhead shot of a hand writing in a leather notebook "
            "with a fountain pen, morning coffee mug beside it, soft natural "
            "window light. Real iPhone-shot photography aesthetic, slightly "
            "grainy, natural color (no heavy filter). Shallow depth of field. "
            "9:16 vertical. No text legible."
        ),
        "duration": 5,
    },
]

POLL_INTERVAL = 10
POLL_TIMEOUT = 900


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


def upload_file(filepath):
    with open(filepath, "rb") as f:
        r = httpx.post("https://tmpfiles.org/api/v1/upload",
                       files={"file": (filepath.name, f)}, timeout=180)
    r.raise_for_status()
    return r.json()["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


def video_dur(p):
    r = subprocess.run([FFPROBE, "-v", "quiet", "-print_format", "json",
                       "-show_format", str(p)], capture_output=True, text=True)
    return float(json.loads(r.stdout)["format"]["duration"])


# ── Phase 1: parallel jobs ──────────────────────────────────────────────────

def submit_scene_edit(scene, source_url):
    return submit_kie("google/nano-banana-edit", {
        "prompt": scene["edit_prompt"],
        "image_urls": [source_url],
        "output_format": "png",
        "image_size": "9:16",
    })


def submit_voice(scene):
    return submit_kie("elevenlabs/text-to-speech-turbo-2-5", {
        "text": scene["audio_text"], "voice": ELEVENLABS_VOICE,
        "stability": 0.5, "similarity_boost": 0.78, "style": 0.4,
        "speed": 1.10,
    })


def submit_broll(cfg):
    return submit_kie("kling-3.0/video", {
        "prompt": cfg["prompt"], "duration": str(cfg["duration"]),
        "aspect_ratio": "9:16", "mode": "std", "sound": False,
        "multi_shots": False,
    })


def submit_suno():
    body = {
        "prompt": ("Calm intimate focus music, soft acoustic piano with warm "
                   "ambient pad, gentle tempo, contemplative, real recorded "
                   "feel with subtle room noise. No vocals. Instrumental."),
        "customMode": False, "instrumental": True, "model": "V3_5",
        "callBackUrl": "https://example.com/cb",
    }
    r = httpx.post(f"{KIE_API_BASE}/generate", headers=_headers(),
                   json=body, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Suno: {r.text[:200]}")
    tid = (r.json().get("data") or {}).get("taskId")
    if not tid:
        raise RuntimeError(f"Suno: {r.text[:200]}")
    return tid


def fetch_image(tid, name):
    d = poll_task(tid, name)
    out = OUTPUT_DIR / f"v11_{name}_{int(time.time())}.png"
    return download_first(d, out)


def fetch_voice(tid, name):
    d = poll_task(tid, f"voice_{name}")
    return download_first(d, OUTPUT_DIR / f"v11_{name}_voice_{int(time.time())}.mp3")


def fetch_broll(tid, name):
    d = poll_task(tid, name)
    return download_first(d, OUTPUT_DIR / f"v11_{name}_{int(time.time())}.mp4")


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
    out = OUTPUT_DIR / f"v11_music_{int(time.time())}.mp3"
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(audio_url)
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  ↓ {out.name}")
    return out


def generate_avatar(scene_image, audio_path, name):
    log.info(f"Avatar Pro for {name}...")
    img_url = upload_file(scene_image)
    aud_url = upload_file(audio_path)
    tid = submit_kie("kling/ai-avatar-pro", {
        "image_url": img_url, "audio_url": aud_url,
        "prompt": (
            "Confident person speaking naturally to camera, calm authority, "
            "occasional small hand gestures, slight head movement, natural "
            "blinking, candid documentary style — not staged. Eye contact "
            "with camera."
        ),
    })
    d = poll_task(tid, f"avatar_{name}")
    return download_first(d, OUTPUT_DIR / f"v11_avatar_{name}_{int(time.time())}.mp4")


# ── Stitching + post ───────────────────────────────────────────────────────

def concat_scenes(avatars: list[Path]) -> Path:
    log.info("Concat scenes (with seamless audio)...")
    work = OUTPUT_DIR / f"_v11_work_{int(time.time())}"
    work.mkdir(exist_ok=True)

    # Normalize each to 720x1280, 30fps so concat is uniform
    normed = []
    for i, a in enumerate(avatars):
        out = work / f"scene_{i:02d}.mp4"
        subprocess.run([
            FFMPEG, "-y", "-i", str(a),
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1",
            "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(out),
        ], check=True, capture_output=True)
        normed.append(out)

    list_file = work / "concat.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in normed))
    out = work / "combined.mp4"
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(out),
    ], check=True, capture_output=True)
    log.info(f"  Combined: {video_dur(out):.1f}s")
    return out


def overlay_brolls(base: Path, brolls: list[Path], schedule: list[tuple]) -> Path:
    if not schedule:
        return base
    log.info(f"Overlaying {len(schedule)} b-roll cuts...")
    inputs = ["-i", str(base)]
    for b in brolls:
        inputs += ["-i", str(b)]
    filter_parts = [
        "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1[base]"
    ]
    last = "base"
    for i, (b_idx, start, end) in enumerate(schedule):
        if b_idx >= len(brolls):
            continue
        in_idx = b_idx + 1
        sc = f"b{i}"
        out_lbl = f"v{i+1}"
        filter_parts.append(
            f"[{in_idx}:v]scale=720:1280:force_original_aspect_ratio=increase,"
            f"crop=720:1280,setsar=1,setpts=PTS-STARTPTS+{start}/TB[{sc}]"
        )
        filter_parts.append(
            f"[{last}][{sc}]overlay=x=0:y=0:enable='between(t,{start},{end})':"
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


def apply_natural_look(video: Path) -> Path:
    """Real-camera feel: mild S-curve, real grain, subtle vignette. NO color cast."""
    log.info("Applying natural look (mild contrast + grain + vignette)...")
    work = video.parent
    out = work / "natural.mp4"
    vf = (
        # Very mild contrast bump (no saturation/hue change)
        "eq=contrast=1.04:saturation=1.02:gamma=1.0,"
        # Subtle S-curve (gentler than v10)
        "curves=master='0/0 0.3/0.28 0.7/0.72 1/1',"
        # Real-camera grain — slightly heavier than v10 to feel filmed
        "noise=alls=10:allf=t+u,"
        # Subtle vignette (real lens darkening at edges)
        "vignette=PI/5.5"
    )
    subprocess.run([
        FFMPEG, "-y", "-i", str(video),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy",
        str(out),
    ], check=True, capture_output=True)
    log.info(f"  Natural: {out.name}")
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
    log.info("Mix music + loudnorm + burn captions + handle watermark...")
    work = video.parent

    if music and music.exists():
        mixed = work / "mixed.mp4"
        dur = video_dur(video)
        subprocess.run([
            FFMPEG, "-y", "-i", str(video), "-i", str(music),
            "-filter_complex",
            f"[0:a]loudnorm=I=-14:LRA=11:tp=-1[dlg];"
            f"[1:a]volume=0.07,afade=t=in:st=0:d=1.5,afade=t=out:st={dur-1.5}:d=1.5[bg];"
            f"[dlg][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(mixed),
        ], check=True, capture_output=True)
        video = mixed

    hook_escaped = HOOK.replace("'", r"'\''")
    burn_filter = (
        f"ass='{captions.resolve()}',"
        f"drawtext=text='{hook_escaped}'"
        f":font='Montserrat Black':fontsize=40:fontcolor=white"
        f":borderw=2:bordercolor=black"
        f":x=(w-text_w)/2:y=h*0.13"
        f":alpha='if(lt(t,0.25),t/0.25,if(lt(t,2.0),1,if(lt(t,2.4),(2.4-t)/0.4,0)))'"
        f":enable='between(t,0,2.4)',"
        f"drawtext=text='@daniel.calder'"
        f":font='Inter':fontsize=20:fontcolor=white@0.55"
        f":borderw=1:bordercolor=black@0.5"
        f":x=20:y=h-40"
    )

    ts = int(time.time())
    final = OUTPUT_DIR / f"daniel_reel_v11_{ts}.mp4"
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
    log.info("VirtuAI — Multi-scene Real-Look Reel v11")
    log.info("=" * 60)

    if not CANONICAL_FACE.exists():
        raise FileNotFoundError(CANONICAL_FACE)

    # Upload canonical face once for all scene edits
    log.info("Uploading canonical Daniel...")
    canonical_url = upload_file(CANONICAL_FACE)

    # Phase 1: submit EVERYTHING in parallel
    # 3 scene edits + 3 voices + 2 brolls + 1 suno = 9 parallel jobs
    log.info("Phase 1: parallel submit (3 scenes + 3 voices + 2 brolls + music)...")
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        scene_futs = [(s["name"], ex.submit(submit_scene_edit, s, canonical_url)) for s in SCENES]
        voice_futs = [(s["name"], ex.submit(submit_voice, s)) for s in SCENES]
        broll_futs = [(cfg["name"], ex.submit(submit_broll, cfg)) for cfg in BROLL_PROMPTS]
        suno_fut = ex.submit(submit_suno)

        scene_tasks = [(n, f.result()) for n, f in scene_futs]
        voice_tasks = [(n, f.result()) for n, f in voice_futs]
        broll_tasks = [(n, f.result()) for n, f in broll_futs]
        try:
            suno_task = suno_fut.result()
        except Exception as e:
            log.warning(f"Suno submit failed: {e}")
            suno_task = None

    # Phase 2: fetch scene images + voice segments + b-rolls in parallel
    log.info("Phase 2: parallel fetch images + voices + brolls...")
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        scene_fetch_futs = [(n, ex.submit(fetch_image, t, n)) for n, t in scene_tasks]
        voice_fetch_futs = [(n, ex.submit(fetch_voice, t, n)) for n, t in voice_tasks]
        broll_fetch_futs = [(n, ex.submit(fetch_broll, t, n)) for n, t in broll_tasks]
        suno_fut = ex.submit(fetch_suno, suno_task) if suno_task else None

        scene_images = [f.result() for _, f in scene_fetch_futs]
        voice_audios = [f.result() for _, f in voice_fetch_futs]
        brolls = [f.result() for _, f in broll_fetch_futs]
        music = suno_fut.result() if suno_fut else None

    # Phase 3: 3 Avatar Pro generations in PARALLEL (one per scene)
    log.info("Phase 3: 3 Avatar Pro generations in parallel...")
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        avatar_futs = []
        for i, (img, aud, scene) in enumerate(zip(scene_images, voice_audios, SCENES)):
            avatar_futs.append(ex.submit(generate_avatar, img, aud, scene["name"]))
        avatars = [f.result() for f in avatar_futs]

    # Phase 4: post-production
    log.info("Phase 4: stitch + post-production...")
    combined = concat_scenes(avatars)
    total_dur = video_dur(combined)

    # B-roll overlays at natural transition points
    schedule = [
        (0, total_dur * 0.42, total_dur * 0.50),  # clock during scene 2 start
        (1, total_dur * 0.18, total_dur * 0.26),  # journaling during scene 1
    ]
    overlaid = overlay_brolls(combined, brolls, schedule)

    natural = apply_natural_look(overlaid)
    captions = make_captions(natural)
    final = finalize(natural, captions, music)

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
