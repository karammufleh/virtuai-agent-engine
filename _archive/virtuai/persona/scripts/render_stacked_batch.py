"""
render_stacked_batch.py — Replace the 3 video-platform feeds with NEW posts
rendered through the full stacked pipeline (SadTalker motion + Wav2Lip
refinement) so the platform videos finally have real head movement.

Each video platform gets a fresh ~12-second script (hardcoded, no Phi
hallucination), F5-TTS audio, then stacked-render. Outputs land as a NEW
post in the platform's feed (existing posts are preserved). Time budget:
~25 min per clip × 3 = ~75 min total.

Usage:
    python virtuai/persona/scripts/render_stacked_batch.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

ROOT = Path(__file__).resolve().parents[3]
PERSONA = ROOT / "virtuai" / "persona"
DEMO_DIR = PERSONA / "demo"
HERO_FACE = PERSONA / "face_dataset" / "daniel_hero.png"
STACKED_RENDER = PERSONA / "scripts" / "stacked_render.py"
SADTALKER_VENV_PY = "/Users/karammufleh/virtuai-sadtalker-venv/bin/python"
BACKEND = "http://localhost:8765"

# Hardcoded scripts — proofread, ~30 words each, no instruction-format leakage.
# Each is calibrated to ~12 sec spoken at F5-TTS pace.
PLATFORM_SCRIPTS: dict[str, dict] = {
    "tiktok": {
        "topic": "stop chasing motivation, build systems instead",
        "text": (
            "Stop chasing motivation. Motivation is unreliable. "
            "Systems run even when you do not feel like it. "
            "Build the system once. Refine it forever. Save this if you needed to hear it."
        ),
    },
    "instagram_reels": {
        "topic": "the question to ask before saying yes to anything",
        "text": (
            "Here is the question I ask before saying yes to anything new. "
            "Will this still be worth my time in twelve months? "
            "If the answer is no, the answer is no. Steal this filter. It will save you years."
        ),
    },
    "youtube_shorts": {
        "topic": "advice to my 22-year-old self about leverage",
        "text": (
            "What I would tell my 22 year old self. "
            "Stop trading hours for money. Start trading hours for systems that earn while you sleep. "
            "Pick one tool. Master it. Compound. The earlier you start, the bigger the gap. "
            "Subscribe if this is the reminder you needed."
        ),
    },
}


def slugify(text: str, max_len: int = 50) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:max_len].strip("-") or "post"


def post_request(endpoint: str, payload: dict, timeout: float = 900.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BACKEND}{endpoint}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def generate_voice(text: str) -> tuple[Path, float]:
    print(f"[voice] dispatching ({len(text.split())} words)...")
    t0 = time.time()
    res = post_request(
        "/generate-voice",
        {"text": text, "speed": 1.0, "seed": 42, "nfe_step": 32},
        timeout=900,
    )
    print(f"  ✓ {res['duration_s']:.2f}s of audio in {time.time()-t0:.1f}s")
    return Path(res["audio_path"]), float(res["duration_s"])


def stacked_render(image: Path, audio: Path, out_path: Path) -> None:
    cmd = [
        SADTALKER_VENV_PY,
        str(STACKED_RENDER),
        "--image", str(image),
        "--audio", str(audio),
        "--out", str(out_path),
        "--size", "256",
    ]
    print(f"[stacked render] dispatching...")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10800)
    elapsed = time.time() - t0
    if proc.returncode != 0 or not out_path.exists():
        sys.stderr.write((proc.stdout or "")[-2000:])
        sys.stderr.write((proc.stderr or "")[-2000:])
        sys.exit(f"stacked render failed exit {proc.returncode}")
    print(f"  ✓ stacked render done in {elapsed/60:.1f} min")


def render_platform(platform: str, plan: dict) -> None:
    print(f"\n══════════════════════ {platform.upper()} ══════════════════════")
    print(f"topic: {plan['topic']}")
    print(f"text: {plan['text']}")

    # 1. F5-TTS audio
    audio_src, duration = generate_voice(plan["text"])

    # 2. Set up the new post directory in the feed
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    post_id = f"{timestamp}__{slugify(plan['topic'])}"
    post_dir = DEMO_DIR / platform / "feed" / post_id
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "text.md").write_text(plan["text"] + "\n", encoding="utf-8")
    audio_dst = post_dir / "audio.wav"
    audio_dst.write_bytes(audio_src.read_bytes())
    print(f"  → {post_dir.relative_to(ROOT)}")

    # 3. Stacked render directly into the post dir
    video_dst = post_dir / "video.mp4"
    stacked_render(HERO_FACE, audio_dst, video_dst)

    # 4. Per-post manifest
    manifest = {
        "platform": platform,
        "post_id": post_id,
        "topic": plan["topic"],
        "format": f"{int(duration)}s spoken script (stacked render)",
        "is_video": True,
        "created_at": timestamp,
        "text": plan["text"],
        "text_path": str((post_dir / "text.md").relative_to(ROOT)),
        "audio_path": str(audio_dst.relative_to(ROOT)),
        "audio_duration_s": duration,
        "video_path": str(video_dst.relative_to(ROOT)),
        "render_model": "Stacked: SadTalker (motion) + Wav2Lip (lip-sync)",
    }
    (post_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # 5. Update platform-level feed index — newest first
    index_path = DEMO_DIR / platform / "manifest.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index = {"platform": platform, "is_video": True, "feed": []}
    index.setdefault("feed", [])
    index["feed"] = [post_id] + [pid for pid in index["feed"] if pid != post_id]
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    # 6. Topic memory record
    sys.path.insert(0, str(ROOT))
    from virtuai.persona.topic_memory import get_topic_memory
    mem = get_topic_memory()
    try:
        mem.add(post_id, plan["text"], metadata={"platform": platform, "topic": plan["topic"], "source": "stacked_batch"})
    except ValueError:
        pass


def main() -> None:
    if not HERO_FACE.exists():
        sys.exit(f"hero face missing: {HERO_FACE}")

    overall_t0 = time.time()
    for platform, plan in PLATFORM_SCRIPTS.items():
        render_platform(platform, plan)

    elapsed = time.time() - overall_t0
    print(f"\n✓ all 3 stacked renders done in {elapsed/60:.1f} min")


if __name__ == "__main__":
    main()
