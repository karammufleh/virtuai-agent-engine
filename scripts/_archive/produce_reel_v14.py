#!/usr/bin/env python3
"""
produce_reel_v14.py — LIVE-BACKGROUND cinematic reel.

User locked-in spec:
  - Niche: business + AI + automation (script_writer enforces)
  - Persona: locked to canonical_daniel.png
  - Live backgrounds: environmental + camera motion (not Avatar Pro's frozen scene)
  - No captions
  - ElevenLabs Liam voice
  - Premium quality (cost no object → mode="pro")

Architecture:
  1. DeepSeek picks AI/business topic + writes scene-by-scene script
  2. ElevenLabs Liam → full audio
  3. Suno → background music
  4. Nano Banana 2 → N scene-edited images (Daniel in N real locations)
  5. Kling 3.0 IMAGE-TO-VIDEO pro mode → cinematic animated clip per scene
       (camera dolly + environmental motion + person speaks naturally)
  6. Concat into one cinematic master
  7. Kling V1-6 video-to-video LIPSYNC re-aligns mouth to ElevenLabs voice
  8. Natural look + music + corner handle. NO captions.
  9. Reviewer gate.

No Avatar Pro. The whole scene is alive — that's the point.
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

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("produce_reel_v14")

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
POLL_TIMEOUT = 1500

LIVE_MOTION_CLAUSE = (
    " The same person in the image speaks naturally to camera with subtle "
    "head movement, natural blinking, and small hand gestures. Slow handheld "
    "camera feel with gentle drift and subtle push-in. The background is "
    "ALIVE: leaves rustling in the wind, pedestrians or ambient figures "
    "moving in soft focus, atmospheric haze, natural light shifting subtly. "
    "Cinematic real-iPhone documentary aesthetic, NOT a static portrait. "
    "Photo-real."
)


def _headers():
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def submit_kie(model, input_data):
    r = httpx.post(f"{KIE_API_BASE}/jobs/createTask", headers=_headers(),
                   json={"model": model, "input": input_data}, timeout=30)
    r.raise_for_status()
    tid = (r.json().get("data") or {}).get("taskId")
    if not tid:
        raise RuntimeError(f"Submit failed for {model}: {r.text[:400]}")
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


def submit_voice(text):
    return submit_kie("elevenlabs/text-to-speech-turbo-2-5", {
        "text": text, "voice": ELEVENLABS_VOICE,
        "stability": 0.55, "similarity_boost": 0.78, "style": 0.42,
        "speed": 1.05,
    })


def fetch_voice(tid):
    d = poll_task(tid, "ElevenLabs")
    return download_first(d, OUTPUT_DIR / f"v14_voice_{int(time.time())}.mp3")


def submit_suno():
    body = {
        "prompt": ("Calm confident underscore for a founder reel — soft acoustic "
                   "piano with warm ambient pad, very subtle rhythmic pulse, "
                   "contemplative but driving. No vocals. Instrumental only."),
        "customMode": False, "instrumental": True, "model": "V3_5",
        "callBackUrl": "https://example.com/cb",
    }
    r = httpx.post(f"{KIE_API_BASE}/generate", headers=_headers(),
                   json=body, timeout=30)
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
    out = OUTPUT_DIR / f"v14_music_{int(time.time())}.mp3"
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(audio_url)
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  ↓ {out.name}")
    return out


def submit_scene_edit(canonical_url, scene_visual_prompt):
    edit_prompt = (
        "Keep the same person and face exactly. " + scene_visual_prompt +
        " Photo-real candid documentary photography, slight natural grain, "
        "authentic phone-camera aesthetic. Same dark polo. No text in the image."
    )
    return submit_kie("google/nano-banana-edit", {
        "prompt": edit_prompt[:1500],
        "image_urls": [canonical_url],
        "output_format": "png",
        "image_size": "9:16",
    })


def fetch_scene_image(tid, idx):
    d = poll_task(tid, f"scene-edit-{idx}")
    return download_first(d, OUTPUT_DIR / f"v14_scene_{idx}_{int(time.time())}.png")


def submit_kling_i2v(scene_url, scene_visual_prompt, duration=5):
    # Strip element references (@daniel, etc.) — the image already contains
    # the person, and Kling rejects role-ref prompts without kling_elements.
    import re as _re
    cleaned = _re.sub(r"@\w+", "the person", scene_visual_prompt)
    motion_prompt = (cleaned + LIVE_MOTION_CLAUSE)[:980]
    return submit_kie("kling-3.0/video", {
        "prompt": motion_prompt,
        "image_urls": [scene_url],
        "duration": str(duration),
        "mode": "pro",
        "aspect_ratio": "9:16",
        "sound": False,
        "multi_shots": False,
    })


def fetch_kling_i2v(tid, idx):
    d = poll_task(tid, f"kling-i2v-{idx}")
    return download_first(d, OUTPUT_DIR / f"v14_clip_{idx}_{int(time.time())}.mp4")


def concat_clips(clips):
    log.info(f"Concat {len(clips)} cinematic clips...")
    work = OUTPUT_DIR / f"_v14_work_{int(time.time())}"
    work.mkdir(exist_ok=True)
    normed = []
    for i, c in enumerate(clips):
        out = work / f"clip_{i:02d}.mp4"
        subprocess.run([
            FFMPEG, "-y", "-i", str(c),
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1",
            "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-an",
            str(out),
        ], check=True, capture_output=True)
        normed.append(out)

    list_file = work / "concat.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in normed))
    out = work / "stitched.mp4"
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(out),
    ], check=True, capture_output=True)
    log.info(f"  Stitched: {video_dur(out):.1f}s")
    return out


def attach_silent_audio(video, target_dur):
    out = video.parent / "with_silent.mp4"
    subprocess.run([
        FFMPEG, "-y", "-i", str(video),
        "-f", "lavfi", "-t", f"{target_dur}",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out),
    ], check=True, capture_output=True)
    return out


def lip_sync(video, audio):
    log.info("Kling V1-6 lipsync (video + audio → synced)...")
    from virtuai.tools.kling_omni import lip_sync as kling_lipsync
    video_url = upload_to_tmpfiles(video)
    audio_url = upload_to_tmpfiles(audio)
    log.info(f"  video → {video_url}")
    log.info(f"  audio → {audio_url}")
    result = kling_lipsync(
        video_url=video_url, audio_url=audio_url,
        output_filename=f"v14_lipsync_{int(time.time())}.mp4",
    )
    return Path(result["local_path"])


def post_produce(video, music):
    log.info("Natural look + music + handle...")
    work = OUTPUT_DIR / f"_v14_post_{int(time.time())}"
    work.mkdir(exist_ok=True)

    natural = work / "natural.mp4"
    vf = (
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,"
        "eq=contrast=1.03:saturation=1.00:gamma=1.0,"
        "curves=master='0/0 0.3/0.29 0.7/0.71 1/1',"
        "noise=alls=6:allf=t+u,"
        "vignette=PI/6.5"
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
            f"[1:a]volume=0.06,afade=t=in:st=0:d=1.5,afade=t=out:st={dur-1.5}:d=1.5[bg];"
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
    final = OUTPUT_DIR / f"daniel_reel_v14_{ts}.mp4"
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


def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — Live-Background Cinematic Reel v14")
    log.info("=" * 60)

    if not CANONICAL_FACE.exists():
        raise FileNotFoundError(CANONICAL_FACE)

    log.info("Step 1: DeepSeek script (locked niche)...")
    from virtuai.tools.script_writer import write_script, full_audio_text
    script = write_script(topic=None, n_scenes=4)
    script_path = SCRIPT_DIR / f"v14_{int(time.time())}.json"
    script_path.write_text(json.dumps(script, indent=2))
    log.info(f"  Topic: {script['topic']}")
    log.info(f"  Hook:  {script['hook_summary']}")
    log.info(f"  {len(script['scenes'])} scenes")

    full_text = full_audio_text(script)
    canonical_url = upload_to_tmpfiles(CANONICAL_FACE)
    n_scenes = len(script["scenes"])

    log.info("Phase A: parallel submit (voice + music + scene edits)...")
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        voice_fut = ex.submit(submit_voice, full_text)
        music_fut = ex.submit(submit_suno)
        scene_edit_futs = [
            (i, ex.submit(submit_scene_edit, canonical_url, sc["visual_prompt"]))
            for i, sc in enumerate(script["scenes"])
        ]
        voice_task = voice_fut.result()
        music_task = music_fut.result()
        scene_edit_tasks = [(i, f.result()) for i, f in scene_edit_futs]

    log.info("Phase B: parallel fetch (voice + scene images + music)...")
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        voice_dl_fut = ex.submit(fetch_voice, voice_task)
        music_dl_fut = ex.submit(fetch_suno, music_task)
        scene_img_futs = [(i, ex.submit(fetch_scene_image, t, i))
                          for i, t in scene_edit_tasks]
        audio_path = voice_dl_fut.result()
        music_path = music_dl_fut.result()
        scene_images = [f.result() for _, f in scene_img_futs]

    log.info("Phase C: Kling 3.0 i2v (live motion) for each scene in parallel...")
    scene_urls = [upload_to_tmpfiles(img) for img in scene_images]

    with cf.ThreadPoolExecutor(max_workers=max(n_scenes, 4)) as ex:
        i2v_futs = []
        for i, (url, sc) in enumerate(zip(scene_urls, script["scenes"])):
            i2v_futs.append((i, ex.submit(
                submit_kling_i2v, url, sc["visual_prompt"], 5
            )))
        i2v_tasks = [(i, f.result()) for i, f in i2v_futs]

    with cf.ThreadPoolExecutor(max_workers=max(n_scenes, 4)) as ex:
        clip_futs = [(i, ex.submit(fetch_kling_i2v, t, i)) for i, t in i2v_tasks]
        clips = [f.result() for _, f in clip_futs]

    log.info(f"  {len(clips)} cinematic clips generated")

    log.info("Phase D: concat → lipsync → post...")
    stitched = concat_clips(clips)
    total_dur = video_dur(stitched)
    with_silent = attach_silent_audio(stitched, total_dur)
    synced = lip_sync(with_silent, audio_path)
    final = post_produce(synced, music_path)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info(f"Duration: {video_dur(final):.1f}s")
    log.info(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
    log.info("=" * 60)

    log.info("Reviewer gate...")
    from virtuai.tools.video_reviewer import review_video, format_review_report
    review = review_video(final)
    print(format_review_report(review))


if __name__ == "__main__":
    main()
