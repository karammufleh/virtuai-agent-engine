#!/usr/bin/env python3
"""
autopilot.py — End-to-end auto-publishing with variety.

Each run picks a DIFFERENT outfit, setting pool, and creative mood so
no two reels look or sound alike. Topic patterns from the last 8 runs
are passed to Claude as an avoid-list.

  1. Load history (topics / outfits / moods we've already used)
  2. Pick fresh outfit + mood + setting pool
  3. Claude Sonnet 4.6 writes a 6-beat story avoiding recent patterns
  4. Two Kling 3.0 renders → 30s cinematic with native audio + lipsync
  5. Suno music underbed
  6. Re-encode audio for Instagram (48 kHz, -14 LUFS, 256 kbps)
  7. Auto-publish to YouTube + Instagram + LinkedIn
  8. Save run to history so next run avoids these choices

Run: python scripts/autopilot.py
"""
from __future__ import annotations

import json
import logging
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("autopilot")

OUTPUT_DIR = ROOT / "virtuai/data/generated_videos"
HISTORY_PATH = ROOT / "virtuai/data/autopilot_history.json"
import os as _os, shutil as _shutil
FFMPEG = _os.environ.get("FFMPEG_BIN") or _shutil.which("ffmpeg") or "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"


# ── Variety pools ───────────────────────────────────────────────────────────

OUTFITS = [
    # base 8 (kept for continuity with locked baseline)
    "navy zip-up hoodie over a white tee",
    "white linen button-down with sleeves rolled to the elbow",
    "grey crewneck sweater",
    "black henley with subtle stubble",
    "olive-green field jacket over a charcoal tee",
    "vintage denim jacket over a grey hoodie",
    "rust-orange sweatshirt",
    "navy short-sleeve polo (the classic)",
    # 2026-05-20 expansion — more variety across type / color / season
    "cream cable-knit cardigan over a heather grey tee",
    "charcoal merino quarter-zip pullover",
    "sand-coloured chore jacket over a navy tee",
    "black bomber jacket with a thin chain necklace",
    "deep-burgundy henley, sleeves at the elbow",
    "forest-green flannel over a plain white tee",
    "stone wool overshirt over a black turtleneck",
    "light denim shirt under a tan suede jacket",
    "heather-grey hoodie with subtle texture",
    "off-white knit polo, no collar stand",
]

# Each mood gives Claude a distinct narrative posture.
MOODS = [
    # base 8 (kept)
    "personal regret — admit a costly mistake you made building AI",
    "contrarian rant — push back on a popular AI tip that's wrong",
    "case study — a single specific automation, with the numbers",
    "hot take — a prediction about where AI work is going",
    "live observation — something you noticed this week that surprised you",
    "step-by-step — exactly how you built a small system, no fluff",
    "comparison — a tool you abandoned vs the one you replaced it with",
    "hidden cost — what nobody tells you about scaling AI in your business",
    # 2026-05-20 expansion — more narrative postures
    "post-mortem — what publicly failed and what I learned in the aftermath",
    "behind-the-scenes — a small ugly detail of how the workflow actually runs",
    "money stunt — a specific dollar transformation ($X → $Y) with the math",
    "consensus-buster — challenge a belief everyone in the niche shares",
    "client confession — something a client did that I now copy",
    "tool defection — why I quit a popular tool I used to love",
]

# Sets of locations that feel cohesive together (so the 6 scenes have a vibe).
SETTING_POOLS = [
    [  # URBAN day
        "sunlit corner cafe with espresso machine and people in soft focus",
        "rooftop terrace, city skyline in distance",
        "city sidewalk during golden hour with cars passing",
        "co-working space lounge, large window, ambient figures",
        "underground subway platform, train pulling in",
        "modern loft kitchen with morning light",
    ],
    [  # COZY indoor
        "warm wood writing desk in a quiet home study, bookshelf behind",
        "leather armchair near a fireplace with low light",
        "kitchen island at dusk with one pendant lamp on",
        "reading nook with rain visible through the window",
        "bedroom edge of bed with morning light through curtains",
        "garage workshop with tools and a single work lamp",
    ],
    [  # OUTDOOR active
        "park bench under an oak tree, dappled sunlight",
        "lakeside dock at sunrise, mist rising off the water",
        "hiking trail clearing with mountains in the distance",
        "beach at golden hour, sand and small waves",
        "boxing gym with a heavy bag and gym lights",
        "running path at dusk with city lights starting to pop",
    ],
    [  # TRAVEL / hospitality
        "boutique hotel lobby with modern art and concierge desk",
        "airport business lounge by floor-to-ceiling window with planes",
        "rented apartment with luggage open on a bench",
        "co-working space in another city, different skyline visible",
        "high-end hotel rooftop bar at dusk",
        "train window seat with countryside blurring by",
    ],
    [  # PRODUCTION / making
        "small podcast studio with mic and acoustic panels",
        "video editing bay with multiple monitors glowing",
        "design studio whiteboard covered in flow diagrams",
        "kitchen counter with notebook open and one open book",
        "indoor plant-filled office with afternoon light",
        "art gallery interior with one painting in shallow focus",
    ],
    # 2026-05-20 expansion — two more pools for visual variety
    [  # URBAN evening
        "neon-lit ramen counter at 9pm, condensation on the window",
        "rooftop bar at blue hour, city traffic in distant focus",
        "underground vinyl shop with warm tungsten light",
        "rain-soaked sidewalk reflecting storefront signage",
        "speakeasy interior with low amber lighting",
        "late-night convenience store aisle, fluorescent buzz",
    ],
    [  # CREATIVE / hands-on
        "ceramics studio with throwing wheel and clay-dusted apron",
        "woodworking bench with sawdust catching light",
        "darkroom with red safelight and prints hanging",
        "small print shop with risograph machine in foreground",
        "florist's workbench mid-arrangement",
        "barber shop chair with classic mirrored back wall",
    ],
]


def load_history() -> dict:
    if HISTORY_PATH.exists():
        return json.loads(HISTORY_PATH.read_text())
    return {"runs": []}


def save_history(hist: dict):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(hist, indent=2))


def pick_fresh(pool: list, recently_used: list, fallback_random=True):
    """
    Pick an item in `pool` not in the recent-use window.

    Window scales with pool size so larger pools enforce a longer
    cool-down (e.g. 16-item OUTFITS pool → don't repeat for the last 8).
    """
    window = min(len(pool) - 1, max(5, len(pool) // 2))
    used = list(recently_used[-window:])
    candidates = [item for item in pool if item not in used]
    if candidates:
        return random.choice(candidates) if fallback_random else candidates[0]
    # Pool fully exhausted within the window — pick any non-most-recent.
    last = recently_used[-1] if recently_used else None
    safe = [item for item in pool if item != last]
    return random.choice(safe) if safe else pool[0]


def ig_optimize(src: Path) -> Path:
    log.info("IG audio optimization (48kHz, -14 LUFS, 256k AAC)...")
    out = src.with_name(src.stem + "_IG.mp4")
    subprocess.run([
        FFMPEG, "-y", "-i", str(src),
        "-af", "loudnorm=I=-14:LRA=11:tp=-1",
        "-ar", "48000",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "256k",
        "-movflags", "+faststart",
        str(out),
    ], check=True, capture_output=True)
    log.info(f"  ✓ IG-optimized: {out.name}")
    return out


def main():
    t0 = time.time()
    log.info("=" * 60)
    log.info("VirtuAI — AUTOPILOT (with variety rotation)")
    log.info("=" * 60)

    # Load history & pick fresh choices
    hist = load_history()
    runs = hist["runs"]
    log.info(f"History: {len(runs)} prior runs loaded")

    recent_outfits = [r.get("outfit") for r in runs[-4:]]
    recent_moods = [r.get("mood") for r in runs[-4:]]
    recent_pools = [r.get("setting_pool_id") for r in runs[-3:]]
    recent_topics = [r.get("topic") for r in runs[-8:]]

    outfit = pick_fresh(OUTFITS, recent_outfits)
    mood = pick_fresh(MOODS, recent_moods)
    pool_idx = next((i for i in range(len(SETTING_POOLS))
                     if i not in recent_pools), 0)
    setting_pool = SETTING_POOLS[pool_idx]

    log.info(f"  Outfit:      {outfit}")
    log.info(f"  Mood:        {mood[:80]}...")
    log.info(f"  Setting pool #{pool_idx}: {setting_pool[0][:50]}...")
    log.info(f"  Avoiding topics: {len(recent_topics)} prior")

    # Phase 1: Claude script with variety params
    log.info("Phase 1: Claude Sonnet 4.6 script (variety-aware)...")
    from virtuai.tools.script_writer import write_script
    script = write_script(
        topic=None, n_scenes=6,
        recent_topics=recent_topics,
        outfit=outfit, mood=mood,
        setting_pool=setting_pool,
    )
    log.info(f"  Topic: {script['topic']}")
    log.info(f"  Hook:  {script['hook_summary']}")

    # Save script immediately so we can debug if production fails
    script_path = ROOT / "virtuai/data/scripts" / f"autopilot_{int(time.time())}.json"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(json.dumps(script, indent=2))

    # Phase 2: feed the locked script into produce_reel_v16's renderer
    # We need to render this exact script (not let v16 regenerate one).
    log.info("Phase 2: Kling rendering using locked script...")
    from scripts.produce_reel_v16 import (
        upload_to_tmpfiles, kling_render, submit_suno, fetch_suno,
        concat_renders, voice_change_to_liam, post_produce, video_dur,
        CANONICAL_FACE, N_SCENES,
    )
    import concurrent.futures as cf

    scenes = script["scenes"][:N_SCENES]
    half = (len(scenes) + 1) // 2
    face_url = upload_to_tmpfiles(CANONICAL_FACE)

    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        kling_a_fut = ex.submit(kling_render, scenes[:half], face_url, "A")
        kling_b_fut = ex.submit(kling_render, scenes[half:], face_url, "B")
        suno_fut = ex.submit(submit_suno)
        suno_task = suno_fut.result()
        render_a = kling_a_fut.result()
        render_b = kling_b_fut.result()

    music = fetch_suno(suno_task) if suno_task else None
    combined = concat_renders(render_a, render_b)
    voice_changed = voice_change_to_liam(combined)
    final = post_produce(voice_changed, music)
    log.info(f"  Master: {final.name} ({video_dur(final):.1f}s)")

    # Phase 3: IG audio fix
    ig_video = ig_optimize(final)

    # Phase 4: publish to all platforms
    # Preflight healthcheck — open circuits for any unhealthy platform so
    # publish attempts skip them cleanly (same protection as daily_pack.py).
    log.info("Phase 4 preflight: probing platform tokens...")
    from scripts.publisher_healthcheck import preflight
    _health = preflight(["youtube_shorts", "instagram", "linkedin"])
    for _p, _ok in _health.items():
        log.info(f"  {'✓' if _ok else '✗'} {_p}")

    from scripts.publish_v16 import (
        build_caption, publish_youtube, publish_instagram, publish_linkedin,
    )
    captions = build_caption(script)
    log.info(f"  Caption title: {captions['title']}")

    results = {}
    log.info("Phase 4a: YouTube Shorts (public)...")
    try:
        yt = publish_youtube(final, captions, public=True)
        results["youtube"] = yt
        yt_url = yt.get("url")
    except Exception as e:
        log.error(f"YouTube failed: {e}")
        results["youtube"] = {"error": str(e)}
        yt_url = None

    log.info("Phase 4b: Instagram Reel...")
    try:
        results["instagram"] = publish_instagram(ig_video, captions["instagram_caption"])
    except Exception as e:
        log.error(f"Instagram failed: {e}")
        results["instagram"] = {"error": str(e)}

    log.info("Phase 4c: LinkedIn...")
    try:
        results["linkedin"] = publish_linkedin(captions["linkedin_post"], yt_url)
    except Exception as e:
        log.error(f"LinkedIn failed: {e}")
        results["linkedin"] = {"error": str(e)}

    # Phase 5: save run to history
    run_record = {
        "ts": int(time.time()),
        "topic": script["topic"],
        "hook": script["hook_summary"],
        "outfit": outfit,
        "mood": mood,
        "setting_pool_id": pool_idx,
        "video_master": str(final),
        "video_ig": str(ig_video),
        "results": {
            "youtube": (results.get("youtube") or {}).get("url"),
            "instagram_id": (((results.get("instagram") or {}).get("result") or {})
                             .get("data", {}) or {}).get("id"),
            "linkedin_urn": (((results.get("linkedin") or {}).get("result") or {})
                             .get("data", {}) or {}).get("x_restli_id"),
        },
    }
    hist["runs"].append(run_record)
    save_history(hist)

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"AUTOPILOT DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"  Topic:     {script['topic']}")
    log.info(f"  Outfit:    {outfit}")
    log.info(f"  Mood:      {mood[:60]}")
    log.info(f"  YouTube:   {run_record['results']['youtube']}")
    log.info(f"  Instagram: Reel {run_record['results']['instagram_id']}")
    log.info(f"  LinkedIn:  {run_record['results']['linkedin_urn']}")
    log.info(f"  Run #{len(hist['runs'])} saved to {HISTORY_PATH.name}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
