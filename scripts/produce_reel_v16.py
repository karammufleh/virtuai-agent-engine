#!/usr/bin/env python3
"""
produce_reel_v16.py — Long-form story reel (30s, 6 beats, 2 Kling renders).

User complaint on v15: "story isn't complete". Root cause: Kling 3.0 caps
each render at 15s — too tight to fit setup/incident/struggle/turn/proof/
meaning. v16 splits the 6-scene script into two Kling multi-shot renders
and concatenates them, giving 30s of breathing room with full story arc.

Architecture:
  1. Claude Sonnet 4.6 writes a 6-scene script with full story arc
  2. Split scenes into Render A (1-3) and Render B (4-6)
  3. Generate both renders in PARALLEL — each with face-locked Daniel,
     native speech (sound=True), live backgrounds (kling_elements)
  4. Concat A + B into one 30s video
  5. Mix Suno music underbed
  6. (Optional) ElevenLabs Voice Changer → Liam
  7. Natural look + handle, no captions

Same Kling 3.0 native lipsync quality you liked in v15 — just twice the
runtime for a complete story.
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

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("produce_reel_v16")

OUTPUT_DIR = ROOT / "virtuai" / "data" / "generated_videos"
SCRIPT_DIR = ROOT / "virtuai" / "data" / "scripts"
SCRIPT_DIR.mkdir(parents=True, exist_ok=True)

KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
import os as _os, shutil as _shutil
FFMPEG = _os.environ.get("FFMPEG_BIN") or _shutil.which("ffmpeg") or "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = _os.environ.get("FFPROBE_BIN") or _shutil.which("ffprobe") or "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

CANONICAL_FACE = ROOT / "virtuai" / "persona" / "canonical_daniel.png"
ELEVENLABS_VOICE_ID = "TX3LPaxmHKxFdv7VOQHJ"
N_SCENES = 6                  # full story arc
SHOT_DURATION = 5             # 5s per shot

# Reel video model + fallback. Kling 3.0 (multi_shots + kling_elements face-lock
# + native sound) is the primary. When it's in an outage we fall back to Seedance
# 2.0 (bytedance/seedance-2) — a true KIE jobs/createTask drop-in with first-frame
# + reference-image identity lock and native audio (generate_audio). Seedance has
# no single-call multi-shot, so we render one clip per scene and concat; the two
# things that matter for the persona format are preserved — the face as the start
# frame / reference (identity) and spoken audio. Set REEL_FALLBACK_MODEL="" to
# disable the fallback.
KLING_PRIMARY_MODEL = os.environ.get("KLING_PRIMARY_MODEL", "kling-3.0/video").strip()
REEL_FALLBACK_MODEL = os.environ.get("REEL_FALLBACK_MODEL", "bytedance/seedance-2").strip()

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
    """Now backed by KIE's own file-stream-upload (more reliable)."""
    from virtuai.tools.kie_upload import upload as _kie_upload
    return _kie_upload(filepath)


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
    # Delegate to the shared helper so the optional KIE-CDN SSL workaround
    # (VIRTUAI_TRUST_KIE_CDN=true) is available when KIE's tempfile CDN is
    # serving an incomplete cert chain. Default behavior: verify=True.
    from virtuai.utils.asset_download import download_generated_asset
    download_generated_asset(urls[0], out)
    log.info(f"  ↓ {out.name} ({out.stat().st_size/1024/1024:.1f}MB)")
    return out


def _compress_visual_for_kling(rich: str) -> str:
    p = re.sub(r"\[\d+\]\s*[A-Z][A-Z\s+&]+:\s*", "", rich)
    p = re.sub(r"\s+", " ", p).strip()
    p = p.replace("@daniel ", "").replace("@daniel,", "").replace("@daniel.", "")
    return p


def build_kling_shot(scene, dur=SHOT_DURATION):
    dialogue = scene["audio_text"].strip().replace('"', '\\"')
    visual = _compress_visual_for_kling(scene["visual_prompt"])[:300]
    dlg = dialogue[:200]
    prompt = f"{visual} Says: \"{dlg}\". Photo-real, alive background."
    return {"prompt": prompt[:495], "duration": dur}


def kling_render(scenes, face_url, label, max_attempts: int = 3):
    """Generate ONE multi-shot render of up to 3 scenes.

    Primary: Kling 3.0 (multi_shots + kling_elements face-lock + native sound).
    Retries on TRANSIENT render failures — Kling intermittently returns
    'Internal Error, Please try again later'. Each attempt re-submits a fresh
    job (a failed Kling job can't be re-polled, only re-submitted), with
    increasing backoff between tries.

    Fallback: if Kling 3.0 is exhausted (e.g. a full model outage), render the
    same scenes on Seedance 2.0 instead so reels still ship. Returns ONE mp4 per
    label either way, so the downstream A+B concat is unchanged.
    """
    log.info(f"Kling render {label} ({len(scenes)} shots)...")

    # Outage switch: while Kling 3.0 is known-down, REEL_SKIP_PRIMARY=1 goes
    # straight to the fallback so we don't burn ~15 min per Kling attempt
    # (which also blows n8n's HTTP timeout). Unset it once Kling recovers.
    if os.environ.get("REEL_SKIP_PRIMARY", "").strip().lower() in ("1", "true", "yes"):
        if not REEL_FALLBACK_MODEL:
            raise RuntimeError("REEL_SKIP_PRIMARY set but no REEL_FALLBACK_MODEL configured")
        log.warning(
            f"  kling-{label}: REEL_SKIP_PRIMARY set — skipping {KLING_PRIMARY_MODEL}, "
            f"rendering directly on {REEL_FALLBACK_MODEL}")
        return _seedance_fallback_render(scenes, face_url, label)

    multi_prompt = [build_kling_shot(sc) for sc in scenes]
    total_dur = sum(p["duration"] for p in multi_prompt)
    payload = {
        "multi_shots": True,
        "multi_prompt": multi_prompt,
        "image_urls": [face_url],
        "duration": str(total_dur),
        "aspect_ratio": "9:16",
        "mode": "pro",
        "sound": True,
        "kling_elements": [{
            "name": "daniel",
            "description": (
                "A 28-year-old man with short dark wavy hair, light stubble, "
                "wearing a dark polo shirt"
            ),
            "element_input_urls": [face_url, face_url],
        }],
    }
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            tid = submit_kie(KLING_PRIMARY_MODEL, payload)
            d = poll_task(tid, f"kling-{label}")
            out = OUTPUT_DIR / f"v16_kling_{label}_{int(time.time())}.mp4"
            return download_first(d, out)
        except Exception as e:
            last_err = e
            log.warning(f"  kling-{label} attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                wait = 15 * attempt
                log.info(f"  retrying kling-{label} in {wait}s…")
                time.sleep(wait)

    # Primary (Kling 3.0) exhausted. Try the Seedance fallback, if enabled.
    if not REEL_FALLBACK_MODEL:
        raise RuntimeError(
            f"kling-{label} failed after {max_attempts} attempts "
            f"(no fallback configured): {last_err}")
    log.warning(
        f"  kling-{label}: primary {KLING_PRIMARY_MODEL} failed after "
        f"{max_attempts} attempts ({last_err}) — falling back to "
        f"{REEL_FALLBACK_MODEL}")
    try:
        return _seedance_fallback_render(scenes, face_url, label)
    except Exception as fb_err:
        raise RuntimeError(
            f"kling-{label} failed: primary {KLING_PRIMARY_MODEL} after "
            f"{max_attempts} attempts ({last_err}); fallback "
            f"{REEL_FALLBACK_MODEL} also failed ({fb_err})")


def _seedance_shot(scene, face_url, label, idx, max_attempts: int = 2):
    """Render ONE scene on Seedance 2.0 (bytedance/seedance-2).

    Seedance has no multi-shot on KIE, so this is a single clip. Identity is
    locked with Daniel's face as first_frame_url (start-frame animation — the
    direct analog of Kling's start frame). NOTE: Seedance makes first_frame_url
    and reference_image_urls mutually exclusive, so we use the start frame only.
    The scripted line goes in the prompt with generate_audio=True so it's spoken
    natively — the analog of Kling's sound=True + 'Says: "..."'.
    """
    visual = _compress_visual_for_kling(scene["visual_prompt"])[:300]
    dlg = scene["audio_text"].strip().replace('"', '\\"')[:200]
    prompt = f"{visual} Says: \"{dlg}\". Photo-real, alive background."
    payload = {
        "prompt": prompt,
        "first_frame_url": face_url,
        "generate_audio": True,
        "resolution": "720p",
        "aspect_ratio": "9:16",
        "duration": SHOT_DURATION if 4 <= SHOT_DURATION <= 15 else 5,
    }
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            tid = submit_kie(REEL_FALLBACK_MODEL, payload)
            d = poll_task(tid, f"seedance-{label}{idx}")
            out = OUTPUT_DIR / f"v16_seedance_{label}{idx}_{int(time.time())}.mp4"
            return download_first(d, out)
        except Exception as e:
            last_err = e
            log.warning(
                f"  seedance-{label}{idx} attempt {attempt}/{max_attempts} "
                f"failed: {e}")
            if attempt < max_attempts:
                time.sleep(15 * attempt)
    raise RuntimeError(f"seedance-{label}{idx} failed after {max_attempts} "
                       f"attempts: {last_err}")


def _seedance_fallback_render(scenes, face_url, label):
    """Render `scenes` on the fallback model (one clip per scene, in PARALLEL)
    and concat into a single mp4 for this label.

    Parallelizing the per-scene renders keeps a full fallback reel inside a few
    minutes (each clip ~3-4 min) instead of summing them, so the pack finishes
    within n8n's HTTP timeout. If any scene render fails, the exception
    propagates and this label is dropped (the rest of the pack still ships).
    """
    log.warning(
        f"  [FALLBACK] rendering {label} on {REEL_FALLBACK_MODEL} "
        f"({len(scenes)} single-shot clips, parallel)")
    results: dict[int, Path] = {}
    with cf.ThreadPoolExecutor(max_workers=len(scenes)) as ex:
        futs = {ex.submit(_seedance_shot, sc, face_url, label, i): i
                for i, sc in enumerate(scenes, 1)}
        for fut in cf.as_completed(futs):
            results[futs[fut]] = fut.result()
    clips = [results[i] for i in sorted(results)]  # restore scene order
    if len(clips) == 1:
        return clips[0]
    return _concat_clips(clips, label)


def _concat_clips(clips: list[Path], label: str) -> Path:
    """Normalize each clip to 9:16 @30fps with synced audio, then concat.

    Same normalization as concat_renders, generalized to N clips so the
    fallback can stitch per-scene clips back into one render per label.
    """
    work = OUTPUT_DIR / f"_v16_fb_{label}_{int(time.time())}"
    work.mkdir(exist_ok=True)
    normed = []
    for i, c in enumerate(clips):
        out = work / f"part_{i}.mp4"
        clip_dur = video_dur(c)
        subprocess.run([
            FFMPEG, "-y", "-i", str(c),
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1",
            "-af", f"aresample=async=1:first_pts=0,atrim=0:{clip_dur},asetpts=PTS-STARTPTS",
            "-r", "30", "-vsync", "cfr", "-t", f"{clip_dur:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            str(out),
        ], check=True, capture_output=True)
        normed.append(out)
    list_file = work / "concat.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in normed))
    out = work / "combined.mp4"
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-fflags", "+genpts", "-vsync", "cfr", "-r", "30",
        "-af", "aresample=async=1000",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        str(out),
    ], check=True, capture_output=True)
    log.info(f"  [FALLBACK] {label}: concatenated {len(clips)} clips "
             f"({video_dur(out):.1f}s)")
    return out


def submit_suno():
    body = {
        "prompt": ("Calm contemplative founder underscore — soft acoustic piano "
                   "with warm ambient pad, very gentle pulse, intimate, no vocals."),
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
    out = OUTPUT_DIR / f"v16_music_{int(time.time())}.mp3"
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(audio_url)
        dl.raise_for_status()
        out.write_bytes(dl.content)
    log.info(f"  ↓ {out.name}")
    return out


def concat_renders(render_a: Path, render_b: Path) -> Path:
    """Concat with audio resync to prevent lip-sync drift at the join.

    The drift bug: Kling renders A and B each have slightly different audio
    sample-rate / start-offset characteristics. Naive concat keeps the video
    timestamps clean but lets audio accumulate sub-frame errors that show
    as lip-sync drift by the end. Fix: trim each render's audio to the
    EXACT video duration and concat A/V streams separately with
    aresample=async=1 to lock audio to video timestamps.
    """
    log.info("Concat Render A + Render B (audio-synced)...")
    work = OUTPUT_DIR / f"_v16_work_{int(time.time())}"
    work.mkdir(exist_ok=True)

    normed = []
    for i, c in enumerate([render_a, render_b]):
        out = work / f"part_{i}.mp4"
        clip_dur = video_dur(c)
        subprocess.run([
            FFMPEG, "-y", "-i", str(c),
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1",
            "-af", f"aresample=async=1:first_pts=0,atrim=0:{clip_dur},asetpts=PTS-STARTPTS",
            "-r", "30",
            "-vsync", "cfr",
            "-t", f"{clip_dur:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            str(out),
        ], check=True, capture_output=True)
        normed.append(out)

    list_file = work / "concat.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in normed))
    out = work / "combined.mp4"
    # Use concat demuxer with explicit re-encode to maintain A/V sync across
    # the boundary instead of stream-copy (which keeps mismatched timestamps).
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-fflags", "+genpts",
        "-vsync", "cfr",
        "-r", "30",
        "-af", "aresample=async=1000",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        str(out),
    ], check=True, capture_output=True)
    log.info(f"  Combined (synced): {video_dur(out):.1f}s")
    return out


def voice_change_to_liam(video_path: Path) -> Path:
    if not ELEVENLABS_API_KEY:
        log.info("  (Voice Changer skipped — ELEVENLABS_API_KEY not set)")
        return video_path
    log.info("ElevenLabs Voice Changer → Liam...")
    work = OUTPUT_DIR / f"_v16_vc_{int(time.time())}"
    work.mkdir(exist_ok=True)
    in_audio = work / "in.mp3"
    subprocess.run([
        FFMPEG, "-y", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-b:a", "192k", str(in_audio),
    ], check=True, capture_output=True)
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
        headers = {"xi-api-key": ELEVENLABS_API_KEY, "accept": "audio/mpeg"}
        r = httpx.post(url, headers=headers, files=files, data=data,
                       timeout=300, params={"output_format": "mp3_44100_192"})
    if r.status_code != 200:
        log.warning(f"  Voice Changer failed ({r.status_code}): {r.text[:300]}")
        return video_path
    out_audio.write_bytes(r.content)
    out = work / "voice_swapped.mp4"
    subprocess.run([
        FFMPEG, "-y", "-i", str(video_path), "-i", str(out_audio),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out),
    ], check=True, capture_output=True)
    return out


def post_produce(video: Path, music: Path | None) -> Path:
    log.info("Post: natural look + music + handle...")
    work = OUTPUT_DIR / f"_v16_post_{int(time.time())}"
    work.mkdir(exist_ok=True)

    natural = work / "natural.mp4"
    vf = (
        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,"
        "eq=contrast=1.02:saturation=1.00:gamma=1.0,"
        "noise=alls=6:allf=t+u"
    )
    subprocess.run([
        FFMPEG, "-y", "-i", str(video), "-vf", vf, "-r", "30",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy", str(natural),
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
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(normed),
        ], check=True, capture_output=True)
        source = normed

    burn = (
        "drawtext=text='@daniel.calder'"
        ":font='Inter':fontsize=20:fontcolor=white@0.55"
        ":borderw=1:bordercolor=black@0.5:x=20:y=h-40"
    )
    ts = int(time.time())
    final = OUTPUT_DIR / f"daniel_reel_v16_{ts}.mp4"
    subprocess.run([
        FFMPEG, "-y", "-i", str(source), "-vf", burn,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-r", "30", str(final),
    ], check=True, capture_output=True)
    return final


def produce(return_script: bool = False):
    """Produce a v16 reel. Returns the final Path (or tuple with script)."""
    out = main()
    return out


def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — v16 Long-form Story Reel (30s, 6 beats, 2 Kling renders)")
    log.info(f"  Voice changer: {'ENABLED' if ELEVENLABS_API_KEY else 'disabled'}")
    log.info("=" * 60)

    if not CANONICAL_FACE.exists():
        raise FileNotFoundError(CANONICAL_FACE)

    log.info("Step 1: Claude Sonnet 4.6 — 6-beat story script...")
    from virtuai.tools.script_writer import write_script
    script = write_script(topic=None, n_scenes=N_SCENES)
    script_path = SCRIPT_DIR / f"v16_{int(time.time())}.json"
    script_path.write_text(json.dumps(script, indent=2))
    log.info(f"  Topic: {script['topic']}")
    log.info(f"  Hook:  {script['hook_summary']}")
    log.info(f"  Beats: {[s.get('story_beat', '?') for s in script['scenes']]}")
    log.info(f"  Words: {script.get('total_words')}")

    scenes = script["scenes"][:N_SCENES]
    half = (len(scenes) + 1) // 2
    scenes_a = scenes[:half]
    scenes_b = scenes[half:]
    log.info(f"  Render A: scenes 1-{half} | Render B: scenes {half+1}-{len(scenes)}")

    log.info("Step 2: Upload canonical face...")
    face_url = upload_to_tmpfiles(CANONICAL_FACE)

    log.info("Step 3: Two Kling renders + Suno in parallel...")
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        kling_a_fut = ex.submit(kling_render, scenes_a, face_url, "A")
        kling_b_fut = ex.submit(kling_render, scenes_b, face_url, "B")
        suno_fut = ex.submit(submit_suno)
        suno_task = suno_fut.result()
        render_a = kling_a_fut.result()
        render_b = kling_b_fut.result()

    music = fetch_suno(suno_task) if suno_task else None

    log.info("Step 4: Concat the two renders...")
    combined = concat_renders(render_a, render_b)

    log.info(f"Step 5: Voice changer — {'ENABLED' if ELEVENLABS_API_KEY else 'skipped'}")
    voice_changed = voice_change_to_liam(combined)

    log.info("Step 6: Post production...")
    final = post_produce(voice_changed, music)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"Final: {final}")
    log.info(f"Duration: {video_dur(final):.1f}s")
    log.info(f"Size: {final.stat().st_size/1024/1024:.1f} MB")
    log.info("=" * 60)
    return final, script


if __name__ == "__main__":
    main()
