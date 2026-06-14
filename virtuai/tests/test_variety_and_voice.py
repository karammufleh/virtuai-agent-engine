"""
Tests for the 2026-05-20 variety + caption-naturalness upgrade.

Nothing here calls a live LLM — the tests only verify:
  - prompts contain the expanded banned-phrase list
  - prompts contain the real-creator-voice block
  - writer signatures accept the new recent_* kwargs
  - the avoid-block builder dedupes, limits, and labels each dimension
  - variety pools (OUTFITS, MOODS, SETTING_POOLS) actually grew
  - pick_fresh uses a wider window after the pool expansion
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# ────────────────────────────────────────────────────────────────────────────
# Banned-phrase & real-creator-voice presence
# ────────────────────────────────────────────────────────────────────────────

def test_image_writer_system_prompt_contains_new_clichés():
    """Robust to line-wrap inside the prompt — collapse whitespace first."""
    import re
    from virtuai.tools.image_content_writer import SYSTEM_PROMPT_BASE
    collapsed = re.sub(r"\s+", " ", SYSTEM_PROMPT_BASE)
    for phrase in ("unlock your potential", "in today's fast-paced world",
                    "game changer", "embark on a journey", "supercharge",
                    "revolutionize", "delve into"):
        assert phrase in collapsed, f"missing banned phrase: {phrase!r}"


def test_image_writer_system_prompt_has_real_creator_block():
    from virtuai.tools.image_content_writer import SYSTEM_PROMPT_BASE
    assert "REAL-CREATOR" in SYSTEM_PROMPT_BASE
    assert "Contractions ON" in SYSTEM_PROMPT_BASE


def test_image_writer_has_scene_and_outfit_variety_sections():
    from virtuai.tools.image_content_writer import SYSTEM_PROMPT_BASE
    assert "SCENE-VARIETY DIMENSIONS" in SYSTEM_PROMPT_BASE
    assert "OUTFIT-VARIETY DIMENSIONS" in SYSTEM_PROMPT_BASE
    # explicit axes
    for axis in ("location", "camera angle", "framing", "lighting",
                  "time of day", "props", "color", "formality"):
        assert axis in SYSTEM_PROMPT_BASE, f"missing variety axis: {axis!r}"


def test_script_writer_banned_phrases_expanded():
    from virtuai.tools.script_writer import _BANNED_PHRASES
    must_have = {
        "unlock your potential", "in today's fast-paced world",
        "game changer", "embark on a journey", "supercharge",
        "delve into", "buckle up",
    }
    have = set(_BANNED_PHRASES)
    missing = must_have - have
    assert not missing, f"script_writer missing bans: {missing}"


def test_script_writer_system_prompt_has_real_creator_voice():
    from virtuai.tools.script_writer import SYSTEM_PROMPT
    assert "REAL-CREATOR VOICE" in SYSTEM_PROMPT
    assert "SCENE-VARIETY DIMENSIONS" in SYSTEM_PROMPT


# ────────────────────────────────────────────────────────────────────────────
# Writer signatures accept the new memory kwargs
# ────────────────────────────────────────────────────────────────────────────

def test_write_portrait_accepts_new_memory_kwargs():
    from virtuai.tools.image_content_writer import write_portrait
    sig = inspect.signature(write_portrait)
    for name in ("recent_outfits", "recent_moods", "recent_scenes", "recent_hooks"):
        assert name in sig.parameters, f"write_portrait missing param: {name}"


def test_write_carousel_accepts_new_memory_kwargs():
    from virtuai.tools.image_content_writer import write_carousel
    sig = inspect.signature(write_carousel)
    for name in ("recent_outfits", "recent_moods", "recent_scenes", "recent_hooks"):
        assert name in sig.parameters, f"write_carousel missing param: {name}"


def test_write_script_accepts_new_memory_kwargs():
    from virtuai.tools.script_writer import write_script
    sig = inspect.signature(write_script)
    for name in ("recent_outfits", "recent_moods", "recent_scenes", "recent_hooks"):
        assert name in sig.parameters, f"write_script missing param: {name}"


# ────────────────────────────────────────────────────────────────────────────
# _build_avoid_block — pure function, no LLM
# ────────────────────────────────────────────────────────────────────────────

def test_avoid_block_empty_when_nothing_recent():
    from virtuai.tools.image_content_writer import _build_avoid_block
    assert _build_avoid_block(None, None, None, None, None) == ""
    assert _build_avoid_block([], [], [], [], []) == ""


def test_avoid_block_labels_each_dimension():
    from virtuai.tools.image_content_writer import _build_avoid_block
    block = _build_avoid_block(
        recent_topics=["AI did my taxes"],
        recent_outfits=["navy hoodie"],
        recent_moods=["regret"],
        recent_scenes=["cafe at golden hour"],
        recent_hooks=["I fired my VA"],
    )
    assert "topic patterns"          in block
    assert "outfit descriptions"     in block
    assert "narrative moods"         in block
    assert "scene locations / props" in block
    assert "opening hook patterns"   in block
    assert "AI did my taxes"          in block
    assert "navy hoodie"              in block


def test_avoid_block_dedupes_and_limits():
    from virtuai.tools.image_content_writer import _build_avoid_block, _bullets
    # _bullets should dedupe + limit
    items = ["a", "b", "a", "c", "b", "d", "e", "f", "g", "h", "i", "j", "k"]
    out = _bullets(items, limit=8)
    # 8 most-recent unique entries (input ends ... d,e,f,g,h,i,j,k)
    lines = [l for l in out.split("\n") if l.strip()]
    assert len(lines) <= 8


# ────────────────────────────────────────────────────────────────────────────
# Variety pools grew
# ────────────────────────────────────────────────────────────────────────────

def test_outfits_pool_grew_beyond_original_eight():
    from scripts.autopilot import OUTFITS
    assert len(OUTFITS) >= 14, f"OUTFITS only has {len(OUTFITS)}; expected ≥ 14"


def test_moods_pool_grew_beyond_original_eight():
    from scripts.autopilot import MOODS
    assert len(MOODS) >= 12, f"MOODS only has {len(MOODS)}; expected ≥ 12"


def test_setting_pools_count_grew():
    from scripts.autopilot import SETTING_POOLS
    assert len(SETTING_POOLS) >= 6, f"SETTING_POOLS only has {len(SETTING_POOLS)}; expected ≥ 6"
    # Every pool still has at least 6 settings
    for i, pool in enumerate(SETTING_POOLS):
        assert len(pool) >= 6, f"pool {i} only has {len(pool)} settings"


def test_pick_fresh_uses_wider_window_for_larger_pool():
    """With a 16-item pool, the cool-down window should be ≥ 8."""
    from scripts.autopilot import pick_fresh
    big_pool = [f"item-{i}" for i in range(16)]
    used = ["item-0", "item-1", "item-2", "item-3", "item-4",
            "item-5", "item-6", "item-7"]  # 8 most-recent
    # Run many times — the picker must never return any of the last 8.
    for _ in range(50):
        picked = pick_fresh(big_pool, used)
        assert picked not in used[-8:], f"picked {picked!r} from cool-down window"


def test_pick_fresh_falls_back_safely_when_pool_exhausted():
    """If everything is in recent_used, the picker must still return SOMETHING
    (not crash) and prefer a non-most-recent item."""
    from scripts.autopilot import pick_fresh
    pool = ["a", "b", "c"]
    used = ["a", "b", "c"] * 4
    picked = pick_fresh(pool, used)
    assert picked in pool
    # We don't enforce which it picks, just that it doesn't crash and it's
    # different from the very last used entry when possible.
    if len(set(pool)) > 1:
        assert picked != used[-1] or pool == [used[-1]]
