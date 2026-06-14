"""
post_now.py — Generate ONE fresh persona post for a platform.

Pipeline:
  1. Pick a topic from a platform-specific topic pool, novelty-checked against
     the FAISS topic memory (rejects anything > 0.85 cosine sim with any
     previous post).
  2. Generate platform-formatted text via /generate (Phi-3.5 LoRA).
  3. Generate the appropriate asset:
       - video platforms (tiktok/instagram_reels/youtube_shorts):
           /generate-voice → audio.wav, then Wav2Lip → video.mp4
       - image platforms (linkedin/x/instagram/medium):
           mflux-generate-z-image-turbo + dnlcldr LoRA → image.png
  4. Write everything to virtuai/persona/demo/<platform>/feed/<post_id>/
  5. Update the platform's manifest.json to include the new post_id at the
     front of its feed list.
  6. Add the new text to topic_memory so future posts won't repeat the angle.

Usage:
    python virtuai/persona/scripts/post_now.py --platform tiktok
    python virtuai/persona/scripts/post_now.py --platform linkedin --topic "AI disruption"
    python virtuai/persona/scripts/post_now.py --auto    # auto-pick the platform with the smallest feed
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# Disable parallel tokenizers to keep topic_memory's MiniLM stable on macOS.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

ROOT = Path(__file__).resolve().parents[3]
PERSONA = ROOT / "virtuai" / "persona"
DEMO_DIR = PERSONA / "demo"
HERO_FACE = PERSONA / "face_dataset" / "daniel_hero.png"
WAV2LIP_RENDER = PERSONA / "scripts" / "wav2lip_render.py"
SADTALKER_VENV_PY = "/Users/karammufleh/virtuai-sadtalker-venv/bin/python"
MFLUX_BIN = "/Users/karammufleh/virtuai-venv/bin/mflux-generate-z-image-turbo"
BACKEND = "http://localhost:8765"
ANCHOR_FILE = PERSONA / "persona_anchor.json"

# Diverse topic pool per platform — used as candidates for novelty-checked
# selection. We don't want repeats, so each call picks a fresh angle.
TOPIC_POOL: dict[str, list[str]] = {
    "linkedin": [
        "the founder mistake of confusing motion with progress",
        "what venture-backed founders get wrong about AI tooling",
        "why 'pivoting' is overused and rarely the right call",
        "the specific moment that changed how I think about delegation",
        "the hidden tax of meetings nobody talks about",
        "why your first 100 customers should hate one specific thing",
        "the one quarterly review question that actually predicts churn",
    ],
    "x": [
        "your morning routine doesn't matter — your reaction time to inbound does",
        "shipping fast beats shipping perfect, but shipping at all beats both",
        "AI didn't take your job — your refusal to learn one new tool every quarter did",
        "the most valuable skill in 2026: writing prompts that compound",
        "stop reading productivity threads. start tracking which 3 hours produced 80% of last month's wins",
        "every operator I respect has automated the same five things — here are mine",
    ],
    "instagram": [
        "the desk setup that finally cured my afternoon energy crash",
        "what I cut from my weekly schedule and what 90 minutes per day got me back",
        "five 'productivity hacks' that actually drained my output",
        "why I track my calendar in two colors only",
        "the daily walk that doubles as my best decision-making tool",
        "how I run a 6-figure side project on 45 minutes a day",
    ],
    "medium": [
        "the operator's playbook for using AI agents without losing strategic ownership",
        "why most AI productivity claims collapse under audit",
        "a 90-day framework I run when I'm hired into a chaotic team",
        "the four-question filter I use before adopting any new SaaS",
        "decision velocity vs decision quality: the tradeoff most leaders get backwards",
        "what compounding really looks like in week-by-week founder data",
    ],
    "tiktok": [
        "stop optimizing your morning routine. start auditing your last hour.",
        "the 60-second test that tells you if your business is built on leverage or labor",
        "three things I removed from my week that gave me back 10 hours",
        "if you can't explain it in one sentence, you don't understand it yet",
        "your competitive advantage isn't more hours. it's better questions.",
    ],
    "instagram_reels": [
        "how I plan a week in 30 minutes and don't touch the schedule again",
        "what 50 founder calls taught me about who actually scales",
        "the single document every solo operator should write before hiring",
        "why your tools are too cheap and your time is way too cheap",
        "three signals you're about to burn out — caught a week before",
    ],
    "youtube_shorts": [
        "the day I deleted half my SaaS subscriptions and grew faster",
        "what I'd tell a 22-year-old who wants to build something that compounds",
        "the difference between hustlers and operators in 90 seconds",
        "why your team doesn't ship faster: you've made every decision a meeting",
        "I rebuilt my workflow around AI agents in 30 days — here's what worked",
    ],
}

PLATFORM_FORMAT: dict[str, dict] = {
    "linkedin": {
        "format": "long-form professional post (no spoken script)",
        "is_video": False,
        "max_tokens": 500,
    },
    "x": {
        "format": "punchy 280-char tweet with one strong line + one CTA",
        "is_video": False,
        "max_tokens": 200,
    },
    "instagram": {
        "format": "Instagram caption with hook + body + CTA + 5 hashtags",
        "is_video": False,
        "max_tokens": 300,
    },
    "medium": {
        "format": "Medium article opening section: hook, thesis, first 3 paragraphs",
        "is_video": False,
        "max_tokens": 600,
    },
    "tiktok": {
        "format": "15-second spoken script, ~38 words, hook → body → CTA, NO hashtags or markdown",
        "is_video": True,
        "spoken_word_target": 38,
        "max_tokens": 180,
    },
    "instagram_reels": {
        "format": "30-second spoken script, ~75 words, hook → body → CTA, NO hashtags or markdown",
        "is_video": True,
        "spoken_word_target": 75,
        "max_tokens": 220,
    },
    "youtube_shorts": {
        "format": "45-second spoken script, ~110 words, hook → body → CTA, NO hashtags or markdown",
        "is_video": True,
        "spoken_word_target": 110,
        "max_tokens": 320,
    },
}

# For image platforms, scenes are randomized to give visual variety while the
# face stays locked via the dnlcldr LoRA.
SCENE_POOL = [
    "moody studio portrait, dark navy backdrop, dramatic side lighting, looking thoughtfully off camera",
    "candid lifestyle photo at a clean modern desk with laptop, soft window light, slight smile",
    "editorial author photo in a minimal home office, large window with natural light, books blurred behind",
    "professional headshot, sharp dark blazer, neutral grey background, soft studio lighting, looking confidently at camera",
    "rooftop golden hour portrait, looking off camera, cinematic shallow depth of field",
    "coffee shop candid, warm window light, slight smile, blurred coffee equipment behind",
    "low-key warehouse studio, hard rim light, dark tones, looking directly at camera",
    "outdoor neutral background morning light, casual sweater, slight head turn, natural expression",
]


def slugify(text: str, max_len: int = 50) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:max_len].strip("-") or "post"


def post_request(endpoint: str, payload: dict, timeout: float = 600.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BACKEND}{endpoint}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def pick_topic(platform: str, override: str | None = None) -> str:
    """Pick a platform-specific topic, novelty-checked against topic memory."""
    sys.path.insert(0, str(ROOT))
    from virtuai.persona.topic_memory import get_topic_memory
    mem = get_topic_memory()

    pool = TOPIC_POOL.get(platform, [])
    if override:
        return override
    if not pool:
        return f"a fresh take on {platform}"

    rng = random.Random()
    rng.shuffle(pool)
    for candidate in pool:
        is_novel, sim, nearest = mem.is_novel(candidate)
        if is_novel:
            return candidate
        print(f"[topic-memory] '{candidate[:60]}...' too similar to '{nearest}' (sim={sim:.3f}) — skipping")
    # If everything is too similar, fall back to a random one anyway
    return random.choice(pool)


def generate_text(platform: str, topic: str) -> str:
    plan = PLATFORM_FORMAT[platform]
    prompt = (
        f"Write {plan['format']} about: {topic}.\n\n"
        "Voice: direct, motivational, no fluff, exactly one CTA.\n"
        "Banned phrases: 'in today's fast-paced world', 'game-changer', 'revolutionary', "
        "'at the end of the day', 'dive deep', 'let's unpack'.\n"
        "Strong scroll-stopping hook as the first line."
    )
    if plan.get("spoken_word_target"):
        prompt += (
            f"\n\nThis will be SPOKEN ALOUD by an AI avatar — write conversational sentences, "
            f"no bullet points, no parenthetical asides, no hashtags. Target ~{plan['spoken_word_target']} words."
        )
    print(f"[gen text] dispatching ({platform}, {plan['max_tokens']} tokens)...")
    t0 = time.time()
    res = post_request(
        "/generate",
        # temperature 0.7 (was 0.85) — tighter to reduce persona drift while
        # still leaving enough variance for varied scripts. Identity stays
        # locked at the LoRA + system-prompt + banned-phrase level regardless.
        {"prompt": prompt, "platform": platform, "max_tokens": plan["max_tokens"], "temperature": 0.7},
        timeout=300,
    )
    print(f"  ✓ {len(res['content'].split())} words in {time.time()-t0:.1f}s")
    return res["content"].strip()


def generate_voice(text: str, post_dir: Path) -> dict:
    print(f"[gen voice] dispatching ({len(text.split())} words)...")
    t0 = time.time()
    res = post_request(
        "/generate-voice",
        {"text": text, "speed": 1.0, "seed": int(time.time()) % 100000, "nfe_step": 32},
        timeout=900,
    )
    src = Path(res["audio_path"])
    dst = post_dir / "audio.wav"
    dst.write_bytes(src.read_bytes())
    print(f"  ✓ {res['duration_s']:.1f}s of audio in {time.time()-t0:.1f}s")
    return {"path": dst, "duration_s": res["duration_s"], "sample_rate": res["sample_rate"]}


def render_video(audio_path: Path, post_dir: Path) -> Path:
    print(f"[render video] Wav2Lip on {HERO_FACE.name} + {audio_path.name}...")
    t0 = time.time()
    cmd = [
        SADTALKER_VENV_PY,
        str(WAV2LIP_RENDER),
        "--face", str(HERO_FACE),
        "--audio", str(audio_path),
        "--quality", "Improved",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        sys.exit(f"Wav2Lip render failed (exit {proc.returncode})")
    src_line = next((l for l in proc.stdout.splitlines() if "→" in l and ".mp4" in l), None)
    if not src_line:
        sys.exit("Wav2Lip wrapper produced no output path")
    src = Path(src_line.split("→", 1)[1].strip())
    dst = post_dir / "video.mp4"
    shutil.copy2(src, dst)
    print(f"  ✓ {time.time()-t0:.1f}s")
    return dst


def _render_image_once(post_dir: Path, prompt_prefix: str, lora: Path,
                       *, scene: str, seed: int) -> Path:
    """Single Z-Image-Turbo + LoRA render at the given scene + seed."""
    prompt = f"{prompt_prefix} {scene}, cinematic, sharp focus, high detail"
    out_path = post_dir / "image.png"
    cmd = [
        MFLUX_BIN, "-q", "8",
        "--lora-paths", str(lora),
        "--prompt", prompt,
        "--width", "1024", "--height", "1024",
        "--steps", "4",
        "--seed", str(seed),
        "--output", str(out_path),
    ]
    print(f"[render image] {scene[:60]}... seed={seed}")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not out_path.exists():
        sys.stderr.write(proc.stderr[-2000:])
        sys.exit(f"Image render failed (exit {proc.returncode})")
    print(f"  ✓ {time.time()-t0:.1f}s")
    return out_path


def render_image(post_dir: Path, prompt_prefix: str, lora: Path,
                 *, topic: str | None = None, post_text: str | None = None,
                 platform: str | None = None,
                 max_retries: int = 2) -> tuple[Path, dict]:
    """
    Pick a scene, run the coherence pre-flight check, then render. If the
    scene doesn't fit the topic's tone, pick a different one BEFORE we burn
    a 7-minute Z-Image render. Falls back to rendering anyway after `max_retries`
    rejections.
    """
    sys.path.insert(0, str(ROOT))
    from virtuai.persona.coherence import check_scene_coherence

    coherence_log: list[dict] = []
    rejected_scenes: set[str] = set()

    def pick_scene() -> str:
        pool = [s for s in SCENE_POOL if s not in rejected_scenes]
        return random.choice(pool) if pool else random.choice(SCENE_POOL)

    chosen_scene = pick_scene()
    if topic and post_text:
        # Pre-flight: keep picking scenes (text-only check, ~2s each) until one
        # passes coherence or we hit max_retries. This is cheap because we
        # haven't rendered anything yet.
        attempts = 0
        while True:
            attempts += 1
            result = check_scene_coherence(chosen_scene, topic, post_text, platform=platform)
            print(f"[coherence] attempt {attempts}: {result.decision} — {result.reason}")
            coherence_log.append({"attempt": attempts, "scene": chosen_scene, **result.to_dict()})
            if result.passes(allow_borderline=True):
                break
            if attempts > max_retries:
                print(f"[coherence] ⚠ accepting after {max_retries} rejections — best scene wins")
                break
            rejected_scenes.add(chosen_scene)
            chosen_scene = pick_scene()
            print(f"[coherence] retry — picking new scene")
    else:
        coherence_log.append({"attempt": 1, "scene": chosen_scene,
                              "decision": "SKIPPED", "reason": "no topic/text supplied"})

    chosen_seed = random.randint(1000, 9999)
    img = _render_image_once(post_dir, prompt_prefix, lora, scene=chosen_scene, seed=chosen_seed)
    return img, {"final_scene": chosen_scene, "final_seed": chosen_seed, "history": coherence_log}


def find_lora() -> Path:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/opt/ffmpeg@7/lib")
    from virtuai.models.backend import _find_persona_lora
    p = _find_persona_lora()
    if p is None:
        sys.exit("No persona LoRA found")
    return p


def auto_pick_platform() -> str:
    """Pick the platform with the smallest feed (round-robin growth)."""
    sizes = {}
    for plat in TOPIC_POOL.keys():
        feed_dir = DEMO_DIR / plat / "feed"
        sizes[plat] = sum(1 for _ in feed_dir.iterdir()) if feed_dir.exists() else 0
    smallest = min(sizes.items(), key=lambda kv: (kv[1], kv[0]))
    print(f"[auto] feed sizes: {sizes} → posting to {smallest[0]}")
    return smallest[0]


def post(platform: str, topic_override: str | None = None) -> Path:
    if platform not in PLATFORM_FORMAT:
        sys.exit(f"Unknown platform: {platform}")
    plan = PLATFORM_FORMAT[platform]

    topic = pick_topic(platform, topic_override)
    print(f"\n────── {platform.upper()} — '{topic}' ──────")

    text = generate_text(platform, topic)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    post_id = f"{timestamp}__{slugify(topic)}"
    post_dir = DEMO_DIR / platform / "feed" / post_id
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "text.md").write_text(text + "\n", encoding="utf-8")

    record: dict = {
        "platform": platform,
        "post_id": post_id,
        "topic": topic,
        "format": plan["format"],
        "is_video": plan["is_video"],
        "created_at": timestamp,
        "text": text,
        "text_path": str((post_dir / "text.md").relative_to(ROOT)),
    }

    if plan["is_video"]:
        voice = generate_voice(text, post_dir)
        record["audio_path"] = str(voice["path"].relative_to(ROOT))
        record["audio_duration_s"] = voice["duration_s"]
        video = render_video(voice["path"], post_dir)
        record["video_path"] = str(video.relative_to(ROOT))
        record["render_model"] = "Wav2Lip (Improved)"
    else:
        anchor = json.loads(ANCHOR_FILE.read_text(encoding="utf-8"))
        prompt_prefix = anchor.get("prompts", {}).get("image_prefix", "a photo of dnlcldr man,")
        lora = find_lora()
        img, coherence_record = render_image(
            post_dir, prompt_prefix, lora,
            topic=topic, post_text=text, platform=platform, max_retries=1,
        )
        record["image_path"] = str(img.relative_to(ROOT))
        record["render_model"] = "z-image-turbo + dnlcldr LoRA"
        record["coherence"] = coherence_record

    (post_dir / "manifest.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    # Update the platform's feed index (newest-first)
    platform_manifest_path = DEMO_DIR / platform / "manifest.json"
    if platform_manifest_path.exists():
        platform_manifest = json.loads(platform_manifest_path.read_text(encoding="utf-8"))
    else:
        platform_manifest = {"platform": platform, "is_video": plan["is_video"], "format": plan["format"], "feed": []}
    platform_manifest.setdefault("feed", [])
    platform_manifest["feed"] = [post_id] + [pid for pid in platform_manifest["feed"] if pid != post_id]
    platform_manifest_path.write_text(json.dumps(platform_manifest, indent=2), encoding="utf-8")

    # Add to topic memory so future posts won't dup the angle
    sys.path.insert(0, str(ROOT))
    from virtuai.persona.topic_memory import get_topic_memory
    mem = get_topic_memory()
    try:
        mem.add(post_id, text, metadata={"platform": platform, "topic": topic, "source": "post_now"})
    except ValueError:
        pass  # already exists — fine

    print(f"\n✓ post stored at {post_dir.relative_to(ROOT)}")
    return post_dir


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--platform", help=f"One of: {','.join(TOPIC_POOL.keys())}")
    p.add_argument("--topic", help="Override the topic instead of picking from the pool")
    p.add_argument("--auto", action="store_true", help="Auto-pick the platform with the smallest feed")
    args = p.parse_args()

    if args.auto:
        platform = auto_pick_platform()
    elif args.platform:
        platform = args.platform
    else:
        sys.exit("Provide --platform or --auto")

    post(platform, topic_override=args.topic)


if __name__ == "__main__":
    main()
