"""
Tests for the 8-agent planning pass wired into scripts/daily_pack.py.

Proves (per spec):
  1. daily_pack calls the 8-agent planner (run_daily_pack_agents).
  2. The returned agent plan is actually used (outfit/mood/seed flow into produce_*).
  3. No-publish mode still skips publishing.
  4. Fallback to deterministic rotation works if the agent planner fails.
  5. The manifest includes agent_trace (and agent_mode).

The heavy parts (real agents, Kling/Nano render, live publish, health probes,
history I/O) are mocked so the tests are fast, deterministic, and side-effect
free. The deterministic fallback (build_deterministic_plan) is left REAL so the
fallback test exercises the genuine rotation logic.
"""
import glob
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import scripts.daily_pack as dp  # noqa: E402


# ── fixtures / helpers ───────────────────────────────────────────────────────

FAKE_HISTORY = {"runs": [
    {"kind": "reel", "topic": "old reel topic", "outfit": "navy zip-up hoodie",
     "mood": "case study", "topic_seed": "old seed", "setting_pool_id": 0},
]}

KNOWN_PLAN = {
    "reel": {"topic_seed": "AGENT REEL SEED", "outfit": "rust-orange sweatshirt",
             "mood": "case study", "setting_pool_id": 2},
    "portrait": {"topic_seed": "AGENT PORTRAIT SEED", "outfit": "charcoal merino quarter-zip pullover",
                 "mood": "hot take"},
    "carousel": {"topic_seed": "AGENT CAROUSEL SEED", "outfit": "forest-green flannel over a plain white tee",
                 "mood": "step-by-step"},
    "agent_trace": [{"agent": n, "output": f"{n} planned"} for n in
                    ["analyzer", "research", "strategy", "creator",
                     "visual", "reviewer", "guardian", "publisher"]],
    "reviewer_notes": "PASS — varied and concrete",
    "guardian_status": "VERDICT=APPROVE — clean",
    "publisher_plan": "reel -> instagram+youtube_shorts; images -> instagram",
}


def _fake_reel(*a, **k):
    return {"type": "reel", "script": {"topic": "reel topic", "hook_summary": "h",
                                       "scenes": [{"audio_text": "x"}]},
            "video_master": "m.mp4", "video_ig": "i.mp4",
            "outfit": a[0] if a else "o", "mood": a[1] if len(a) > 1 else "m",
            "setting_pool_id": a[2] if len(a) > 2 else 0}


def _fake_portrait(*a, **k):
    return {"type": "portrait", "content": {"topic": "portrait topic", "headline": "H", "subhead": "S"},
            "image": "p.png", "captions": {"instagram": "ig", "linkedin": "li"},
            "run_dir": "rd", "outfit": a[0] if a else "o", "mood": a[1] if len(a) > 1 else "m"}


def _fake_carousel(*a, **k):
    return {"type": "carousel", "content": {"topic": "carousel topic",
                                            "slides": [{"headline": "h", "subhead": "s"}]},
            "captions": {"instagram": "ig", "linkedin": "li"},
            "run_dir": "rd", "outfit": a[0] if a else "o", "mood": a[1] if len(a) > 1 else "m"}


def _common_patches(tmp_root, planner):
    """Patch everything heavy. `planner` is the run_daily_pack_agents stand-in.
    Returns the list of mock.patch context managers (already started)."""
    patches = {
        "ROOT": mock.patch.object(dp, "ROOT", tmp_root),
        "load_history": mock.patch.object(dp, "load_history", return_value={"runs": list(FAKE_HISTORY["runs"])}),
        "save_history": mock.patch.object(dp, "save_history"),
        "produce_reel_track": mock.patch.object(dp, "produce_reel_track", side_effect=_fake_reel),
        "produce_portrait_track": mock.patch.object(dp, "produce_portrait_track", side_effect=_fake_portrait),
        "produce_carousel_track": mock.patch.object(dp, "produce_carousel_track", side_effect=_fake_carousel),
        "guardian": mock.patch.object(dp, "_guardian_gate", return_value=(True, {"decision": "APPROVE"})),
        "publish_reel": mock.patch.object(dp, "publish_reel", return_value={}),
        "publish_portrait": mock.patch.object(dp, "publish_portrait", return_value={}),
        "publish_carousel": mock.patch.object(dp, "publish_carousel", return_value={}),
        "planner": mock.patch("virtuai.agents.daily_pack_crew.run_daily_pack_agents", planner),
        "preflight": mock.patch("scripts.publisher_healthcheck.preflight",
                                return_value={"youtube_shorts": True, "instagram": True,
                                              "linkedin": False, "facebook": True}),
    }
    started = {name: p.start() for name, p in patches.items()}
    return patches, started


def _latest_manifest(tmp_root):
    files = glob.glob(str(tmp_root / "virtuai/data/content_packages" / "daily_pack_*.json"))
    assert files, "no manifest written"
    return json.loads(Path(max(files, key=os.path.getmtime)).read_text())


# ── tests ────────────────────────────────────────────────────────────────────

def test_daily_pack_calls_the_8_agent_planner(tmp_path):
    planner = mock.MagicMock(return_value=dict(KNOWN_PLAN))
    patches, _ = _common_patches(tmp_path, planner)
    try:
        dp.main(publish=False)
        assert planner.call_count == 1, "run_daily_pack_agents was not called"
        ctx = planner.call_args.args[0]
        # the planner receives the full planning context
        for key in ("recent_topics", "available_seeds", "outfit_pool",
                    "mood_pool", "setting_pools", "publish"):
            assert key in ctx, f"context missing {key}"
        assert _latest_manifest(tmp_path)["agent_mode"] == "agents"
    finally:
        for p in patches.values():
            p.stop()


def test_returned_agent_plan_is_used(tmp_path):
    planner = mock.MagicMock(return_value=dict(KNOWN_PLAN))
    patches, started = _common_patches(tmp_path, planner)
    try:
        dp.main(publish=False)
        # produce_reel_track(outfit, mood, pool_idx, avoid, seed, **kw)
        reel_args = started["produce_reel_track"].call_args.args
        assert reel_args[0] == KNOWN_PLAN["reel"]["outfit"]
        assert reel_args[1] == KNOWN_PLAN["reel"]["mood"]
        assert reel_args[2] == KNOWN_PLAN["reel"]["setting_pool_id"]
        assert reel_args[4] == KNOWN_PLAN["reel"]["topic_seed"]
        # portrait: produce_portrait_track(outfit, mood, avoid, seed, **kw)
        port_args = started["produce_portrait_track"].call_args.args
        assert port_args[0] == KNOWN_PLAN["portrait"]["outfit"]
        assert port_args[3] == KNOWN_PLAN["portrait"]["topic_seed"]
        car_args = started["produce_carousel_track"].call_args.args
        assert car_args[0] == KNOWN_PLAN["carousel"]["outfit"]
        assert car_args[3] == KNOWN_PLAN["carousel"]["topic_seed"]
    finally:
        for p in patches.values():
            p.stop()


def test_no_publish_mode_skips_publishing(tmp_path):
    planner = mock.MagicMock(return_value=dict(KNOWN_PLAN))
    patches, started = _common_patches(tmp_path, planner)
    try:
        dp.main(publish=False)
        started["publish_reel"].assert_not_called()
        started["publish_portrait"].assert_not_called()
        started["publish_carousel"].assert_not_called()
    finally:
        for p in patches.values():
            p.stop()


def test_publish_mode_does_publish(tmp_path):
    planner = mock.MagicMock(return_value=dict(KNOWN_PLAN))
    patches, started = _common_patches(tmp_path, planner)
    try:
        dp.main(publish=True)
        started["publish_reel"].assert_called_once()
        started["publish_portrait"].assert_called_once()
        started["publish_carousel"].assert_called_once()
    finally:
        for p in patches.values():
            p.stop()


def test_fallback_when_planner_fails(tmp_path):
    # planner raises -> daily_pack must fall back to deterministic rotation
    planner = mock.MagicMock(side_effect=RuntimeError("planner exploded"))
    patches, started = _common_patches(tmp_path, planner)
    try:
        dp.main(publish=False)  # must NOT raise
        manifest = _latest_manifest(tmp_path)
        assert manifest["agent_mode"] == "fallback"
        # production still happened via the deterministic plan
        started["produce_reel_track"].assert_called_once()
        # deterministic seed comes from TOPIC_SEEDS, not the (failed) agent plan
        reel_seed = started["produce_reel_track"].call_args.args[4]
        assert reel_seed in dp.TOPIC_SEEDS
    finally:
        for p in patches.values():
            p.stop()


def test_manifest_includes_agent_trace(tmp_path):
    planner = mock.MagicMock(return_value=dict(KNOWN_PLAN))
    patches, _ = _common_patches(tmp_path, planner)
    try:
        dp.main(publish=False)
        manifest = _latest_manifest(tmp_path)
        assert "agent_trace" in manifest
        assert manifest["agent_trace"] == KNOWN_PLAN["agent_trace"]
        assert [t["agent"] for t in manifest["agent_trace"]] == [
            "analyzer", "research", "strategy", "creator",
            "visual", "reviewer", "guardian", "publisher"]
    finally:
        for p in patches.values():
            p.stop()


def test_creator_content_is_rendered_verbatim(tmp_path):
    # When creator_content is supplied, the EXACT Creator content must flow
    # into the producers (script= for the reel, content= for the images),
    # i.e. write_script/write_portrait/write_carousel are bypassed.
    creator_content = {
        "reel_script": {"topic": "T", "hook_summary": "h",
                        "scenes": [{"audio_text": "the creator's exact spoken words here",
                                    "visual_prompt": "v"}]},
        "portrait_content": {"type": "portrait", "topic": "T",
                             "headline": "CREATOR HEADLINE", "subhead": "s",
                             "image_prompt": "ip", "_source": "creator"},
        "carousel_content": {"type": "carousel_5", "topic": "T",
                             "slides": [{"id": i, "headline": f"h{i}", "subhead": "s",
                                         "image_prompt": "ip", "uses_persona": i in (1, 5)}
                                        for i in range(1, 6)], "_source": "creator"},
    }
    planner = mock.MagicMock(return_value=dict(KNOWN_PLAN))
    patches, started = _common_patches(tmp_path, planner)
    try:
        dp.main(publish=False, creator_content=creator_content)
        assert started["produce_reel_track"].call_args.kwargs.get("script") == creator_content["reel_script"]
        assert started["produce_portrait_track"].call_args.kwargs.get("content") == creator_content["portrait_content"]
        assert started["produce_carousel_track"].call_args.kwargs.get("content") == creator_content["carousel_content"]
    finally:
        for p in patches.values():
            p.stop()


def test_no_creator_content_falls_back_to_generation(tmp_path):
    # Without creator_content, producers get script=None / content=None and
    # generate as before (no regression to the generated path).
    planner = mock.MagicMock(return_value=dict(KNOWN_PLAN))
    patches, started = _common_patches(tmp_path, planner)
    try:
        dp.main(publish=False)
        assert started["produce_reel_track"].call_args.kwargs.get("script") is None
        assert started["produce_portrait_track"].call_args.kwargs.get("content") is None
        assert started["produce_carousel_track"].call_args.kwargs.get("content") is None
    finally:
        for p in patches.values():
            p.stop()
