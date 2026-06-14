"""
Tests for the Seedance 2.0 fallback in scripts/produce_reel_v16.py.

When the primary video model (kling-3.0/video) is in an outage, kling_render
must fall back to bytedance/seedance-2 — rendering each scene as its own clip
(face = first_frame_url + reference image, generate_audio=true) and
concatenating — so reels still ship.

Network/render/ffmpeg are mocked; these tests verify ROUTING + the fallback
payload shape, not real renders.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import scripts.produce_reel_v16 as rv  # noqa: E402

SCENES = [{"visual_prompt": f"scene {i} visual", "audio_text": f"line {i}"}
          for i in range(1, 4)]


def _stub_render(monkeypatch, submit_fn):
    """Mock the network + concat so only routing is exercised."""
    monkeypatch.setattr(rv, "submit_kie", submit_fn)
    monkeypatch.setattr(rv, "poll_task", lambda tid, label: {"ok": True})
    monkeypatch.setattr(rv, "download_first", lambda d, out: out)
    monkeypatch.setattr(rv, "_concat_clips",
                        lambda clips, label: Path(f"/tmp/combined_{label}.mp4"))
    monkeypatch.setattr(rv.time, "sleep", lambda *a, **k: None)


def test_falls_back_to_seedance_when_kling_is_down(monkeypatch):
    calls = []

    def fake_submit(model, payload):
        calls.append(model)
        if model == rv.KLING_PRIMARY_MODEL:
            raise RuntimeError("Internal Error, Please try again later")
        # fallback model: assert it carries the persona-critical Seedance fields
        assert payload["first_frame_url"] == "http://face.png"
        # first_frame and reference images are mutually exclusive on Seedance
        assert "reference_image_urls" not in payload
        assert payload["generate_audio"] is True
        assert payload["aspect_ratio"] == "9:16"
        assert 4 <= payload["duration"] <= 15
        return "tid-seedance"

    _stub_render(monkeypatch, fake_submit)
    result = rv.kling_render(SCENES, "http://face.png", "A", max_attempts=1)

    assert calls[0] == rv.KLING_PRIMARY_MODEL, "primary must be tried first"
    assert calls.count(rv.REEL_FALLBACK_MODEL) == len(SCENES), \
        "fallback renders one clip per scene"
    assert result == Path("/tmp/combined_A.mp4")


def test_primary_success_never_touches_fallback(monkeypatch):
    calls = []

    def fake_submit(model, payload):
        calls.append(model)
        return "tid-30"

    _stub_render(monkeypatch, fake_submit)
    result = rv.kling_render(SCENES, "http://face.png", "A", max_attempts=2)

    assert calls == [rv.KLING_PRIMARY_MODEL], "one submit, no fallback"
    assert rv.REEL_FALLBACK_MODEL not in calls
    assert result == (rv.OUTPUT_DIR / result.name)  # primary returns its own mp4


def test_no_fallback_configured_raises(monkeypatch):
    monkeypatch.setattr(rv, "REEL_FALLBACK_MODEL", "")

    def fake_submit(model, payload):
        raise RuntimeError("Internal Error, Please try again later")

    _stub_render(monkeypatch, fake_submit)
    with pytest.raises(RuntimeError, match="no fallback configured"):
        rv.kling_render(SCENES, "http://face.png", "A", max_attempts=1)


def test_skip_primary_goes_straight_to_seedance(monkeypatch):
    # REEL_SKIP_PRIMARY=1 must bypass Kling entirely (outage switch)
    monkeypatch.setenv("REEL_SKIP_PRIMARY", "1")
    calls = []

    def fake_submit(model, payload):
        calls.append(model)
        return "tid-seedance"

    _stub_render(monkeypatch, fake_submit)
    result = rv.kling_render(SCENES, "http://face.png", "A", max_attempts=3)

    assert rv.KLING_PRIMARY_MODEL not in calls, "Kling must be skipped entirely"
    assert calls.count(rv.REEL_FALLBACK_MODEL) == len(SCENES)
    assert result == Path("/tmp/combined_A.mp4")


def test_single_scene_fallback_skips_concat(monkeypatch):
    # one scene -> one fallback clip -> returned directly (no concat needed)
    calls = []

    def fake_submit(model, payload):
        calls.append(model)
        if model == rv.KLING_PRIMARY_MODEL:
            raise RuntimeError("upstream API service timed out")
        return "tid-seedance"

    _stub_render(monkeypatch, fake_submit)
    result = rv.kling_render(SCENES[:1], "http://face.png", "B", max_attempts=1)

    assert calls.count(rv.REEL_FALLBACK_MODEL) == 1
    # single clip returned straight from download_first (under OUTPUT_DIR)
    assert result == (rv.OUTPUT_DIR / result.name)
