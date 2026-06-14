#!/usr/bin/env python3
"""Resume v14 from Phase C — Kling i2v + lipsync + post.

Uses existing assets from the previous failed run + a robust HTTP layer
(retries on httpx.ReadTimeout) since that was the failure mode last time.
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
log = logging.getLogger("v14_resume")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

SCRIPT_PATH = ROOT / "virtuai/data/scripts/v14_1778755605.json"
VOICE = ROOT / "virtuai/data/generated_videos/v14_voice_1778755629.mp3"
MUSIC = ROOT / "virtuai/data/generated_videos/v14_music_1778755772.mp3"
SCENE_IMAGES = [
    OUTPUT_DIR / "v14_scene_0_1778755629.png",
    OUTPUT_DIR / "v14_scene_1_1778755632.png",
    OUTPUT_DIR / "v14_scene_2_1778755639.png",
    OUTPUT_DIR / "v14_scene_3_1778755629.png",
]

POLL_INTERVAL = 12
POLL_TIMEOUT = 1800
HTTP_TIMEOUT = 60
MAX_RETRIES = 5

LIVE_MOTION_CLAUSE = (
    " The same person in the image speaks naturally to camera with subtle "
    "head movement, natural blinking, small hand gestures. Slow handheld "
    "camera with gentle drift and very gentle push-in. The background is "
    "ALIVE: leaves rustling, pedestrians or ambient figures moving in soft "
    "focus, atmospheric haze, natural light shifting subtly. Cinematic "
    "real-iPhone documentary aesthetic, photo-real, NOT a static portrait."
)


def _headers():
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def http_get_retry(url, params=None):
    for attempt in range(MAX_RETRIES):
        try:
            return httpx.get(url, params=params, headers=_headers(),
                             timeout=HTTP_TIMEOUT)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError,
                httpx.RemoteProtocolError) as e:
            wait = (2 ** attempt) * 2
            log.warning(f"  GET retry {attempt+1}/{MAX_RETRIES} after {wait}s: {e}")
            time.sleep(wait)
    return httpx.get(url, params=params, headers=_headers(), timeout=HTTP_TIMEOUT)


def http_post_retry(url, json_body):
    for attempt in range(MAX_RETRIES):
        try:
            return httpx.post(url, headers=_headers(), json=json_body,
                              timeout=HTTP_TIMEOUT)
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
                               files={"file": (filepath.name, f)},
                               timeout=300)
            r.raise_for_status()
            return r.json()["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)
        except Exception as e:
            wait = (2 ** attempt) * 3
            log.warning(f"  upload retry {attempt+1}/{MAX_RETRIES} after {wait}s: {e}")
            time.sleep(wait)
    raise RuntimeError(f"Upload failed after {MAX_RETRIES} attempts")


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
        raise RuntimeError(f"Submit failed for {model}: {r.text[:300]}")
    return tid


def poll_task(tid, label):
    deadline = time.time() + POLL_TIMEOUT
    last = ""
    while time.time() < deadline:
        try:
            r = http_get_retry(f"{KIE_API_BASE}/jobs/recordInfo",
                               params={"taskId": tid})
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
            log.warning(f"  {label} poll transient: {e} (will retry)")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(label)


def download_first(data, out):
    rj = json.loads(data.get("resultJson", "{}"))
    urls = rj.get("resultUrls", [])
    if not urls:
        raise RuntimeError(f"No URLs: {rj}")
    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=600, follow_redirects=True) as c:
                dl = c.get(urls[0])
                dl.raise_for_status()
                out.write_bytes(dl.content)
            log.info(f"  ↓ {out.name} ({out.stat().st_size/1024/1024:.1f}MB)")
            return out
        except Exception as e:
            wait = (2 ** attempt) * 3
            log.warning(f"  download retry {attempt+1}/{MAX_RETRIES} after {wait}s: {e}")
            time.sleep(wait)
    raise RuntimeError(f"Download failed after {MAX_RETRIES} attempts")


def kling_i2v_one(scene_url, visual_prompt, idx):
    motion_prompt = (visual_prompt + LIVE_MOTION_CLAUSE)[:980]
    log.info(f"  Submitting kling-i2v-{idx} (pro mode)...")
    tid = submit_kie("kling-3.0/video", {
        "prompt": motion_prompt,
        "image_urls": [scene_url],
        "duration": "5",
        "mode": "pro",
        "aspect_ratio": "9:16",
        "sound": False,
        "multi_shots": False,
    })
    d = poll_task(tid, f"kling-i2v-{idx}")
    out = OUTPUT_DIR / f"v14_clip_{idx}_{int(time.time())}.mp4"
    return download_first(d, out)


def concat_clips(clips):
    log.info(f"Concat {len(clips)} clips...")
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
    log.info("Kling V1-6 lipsync...")
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
    log.info("Post: natural look + music + handle...")
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
    log.info("VirtuAI — v14 RESUME (Phase C+D)")
    log.info("=" * 60)

    script = json.loads(SCRIPT_PATH.read_text())
    log.info(f"Topic: {script['topic']}")

    log.info("Uploading scene images...")
    scene_urls = [upload_to_tmpfiles(img) for img in SCENE_IMAGES]
    log.info(f"  Uploaded {len(scene_urls)}")

    log.info("Phase C: 4 × Kling 3.0 i2v PRO in parallel...")
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = []
        for i, (url, sc) in enumerate(zip(scene_urls, script["scenes"])):
            futs.append((i, ex.submit(kling_i2v_one, url, sc["visual_prompt"], i)))
        clips = [f.result() for _, f in futs]

    log.info("Phase D: concat → lipsync → post...")
    stitched = concat_clips(clips)
    total_dur = video_dur(stitched)
    with_silent = attach_silent_audio(stitched, total_dur)
    synced = lip_sync(with_silent, VOICE)
    final = post_produce(synced, MUSIC if MUSIC.exists() else None)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info(f"Duration: {video_dur(final):.1f}s")
    log.info(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
