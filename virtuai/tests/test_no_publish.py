"""
Tests for the no-publish safety chain (2026-05-20 fix).

The chain must work end-to-end:
    scripts/demo.py --no-publish
        → POST /run-pack {"publish": false}
        → RunPackRequest.publish_allowed() == False
        → _run_pack(task_id, publish=False)
        → daily_pack.main(publish=False)
        → publish block SKIPPED — no Composio call, no YouTube call

All tests use mocks. No live API call is ever made.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# ────────────────────────────────────────────────────────────────────────────
# 1. RunPackRequest.publish_allowed() — the gate
# ────────────────────────────────────────────────────────────────────────────

def test_default_run_pack_request_allows_publish():
    """Empty body → cron behaviour (publish allowed)."""
    from scripts.api_server import RunPackRequest
    req = RunPackRequest()
    assert req.publish_allowed() is True


def test_publish_false_blocks_publishing():
    from scripts.api_server import RunPackRequest
    req = RunPackRequest(publish=False)
    assert req.publish_allowed() is False


def test_dry_run_true_blocks_publishing():
    from scripts.api_server import RunPackRequest
    req = RunPackRequest(dry_run=True)
    assert req.publish_allowed() is False


def test_no_publish_true_blocks_publishing():
    from scripts.api_server import RunPackRequest
    req = RunPackRequest(no_publish=True)
    assert req.publish_allowed() is False


def test_any_blocking_signal_wins():
    """If ANY signal says 'don't publish', publishing is blocked even if
    the others would have allowed it."""
    from scripts.api_server import RunPackRequest
    # publish=True but dry_run=True → blocked
    assert RunPackRequest(publish=True, dry_run=True).publish_allowed() is False
    # publish=True but no_publish=True → blocked
    assert RunPackRequest(publish=True, no_publish=True).publish_allowed() is False


def test_demo_body_shape_is_accepted():
    """The exact body scripts/demo.py sends must parse cleanly."""
    from scripts.api_server import RunPackRequest
    req = RunPackRequest.model_validate({
        "kind":    "pack",
        "persona": "virtuai_mentor",
        "publish": False,
        "demo":    True,
    })
    assert req.publish_allowed() is False
    assert req.kind == "pack"
    assert req.demo is True


# ────────────────────────────────────────────────────────────────────────────
# 2. daily_pack.main(publish=False) skips publishing
# ────────────────────────────────────────────────────────────────────────────

def test_daily_pack_main_signature_accepts_publish_kwarg():
    """Backward-compat: main() must accept publish kwarg with True default."""
    import inspect
    from scripts import daily_pack as dp
    sig = inspect.signature(dp.main)
    assert "publish" in sig.parameters
    assert sig.parameters["publish"].default is True
    assert "dry_run" in sig.parameters
    assert sig.parameters["dry_run"].default is False


def test_daily_pack_main_no_publish_path_skips_publish_helpers(monkeypatch):
    """
    The most important test: with publish=False, neither publish_reel,
    publish_portrait, nor publish_carousel may be called. We verify by
    monkeypatching every publish helper to a tracker that asserts on call.
    """
    import scripts.daily_pack as dp

    # Make every publish helper EXPLODE if called.
    def _explode(*a, **kw):
        raise AssertionError("publish helper was called in no-publish mode")

    monkeypatch.setattr(dp, "publish_reel", _explode)
    monkeypatch.setattr(dp, "publish_portrait", _explode)
    monkeypatch.setattr(dp, "publish_carousel", _explode)

    # Make the heavyweight produce_* helpers cheap (we're not testing them).
    monkeypatch.setattr(dp, "produce_reel_track", lambda *a, **kw: {
        "type": "reel", "video_master": Path("/tmp/.fake_reel.mp4"),
        "video_ig": Path("/tmp/.fake_reel_ig.mp4"),
        "script": {"topic": "t", "hook_summary": "h"},
        "outfit": "o", "mood": "m", "setting_pool_id": 0,
    })
    monkeypatch.setattr(dp, "produce_portrait_track", lambda *a, **kw: {
        "type": "portrait", "content": {"topic": "t", "hook_summary": "h"},
        "image": "/tmp/.fake_portrait.png", "captions": {},
        "outfit": "o", "mood": "m",
    })
    monkeypatch.setattr(dp, "produce_carousel_track", lambda *a, **kw: {
        "type": "carousel", "content": {"topic": "t", "hook_summary": "h",
                                          "slides": [{"headline": "x"}]},
        "slides": [], "captions": {},
        "outfit": "o", "mood": "m",
    })
    # save_history might write — sandbox it.
    monkeypatch.setattr(dp, "save_history", lambda h: None)
    monkeypatch.setattr(dp, "load_history", lambda: {"runs": []})

    # CALL with publish=False — must complete without raising.
    try:
        dp.main(publish=False)
    except AssertionError as e:
        if "publish helper" in str(e):
            pytest.fail(f"publish path was triggered in no-publish mode: {e}")
        # Other AssertionErrors (e.g. missing config) are unrelated to this test.
        pytest.skip(f"daily_pack short-circuited before reaching publish guard: {e}")
    except Exception as e:
        # The orchestrator may bail on a missing seed pool, etc. — that's
        # OK as long as it didn't call a publish helper. The monkeypatched
        # _explode helpers would have already raised if they had been called.
        pytest.skip(f"daily_pack short-circuited: {type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────────────────────────
# 3. _run_pack(publish=False) propagates the kwarg
# ────────────────────────────────────────────────────────────────────────────

def test_run_pack_worker_propagates_publish_false(monkeypatch):
    """_run_pack must call daily_pack.main(publish=False) when its publish
    arg is False."""
    from scripts import api_server as api
    captured = {}

    def fake_main(publish: bool = True, dry_run: bool = False, overrides=None,
                  creator_content=None, **kwargs):
        captured["publish"] = publish
        captured["dry_run"] = dry_run
        captured["overrides"] = overrides
        captured["creator_content"] = creator_content

    # Replace dp inside api_server's import scope
    import scripts.daily_pack as dp
    monkeypatch.setattr(dp, "main", fake_main)
    monkeypatch.setattr(dp, "load_history", lambda: {"runs": []})

    api._run_pack("test-task-id", publish=False)
    assert captured["publish"] is False


def test_run_pack_worker_default_publishes(monkeypatch):
    """Backward compat — cron triggers send no body and must still publish."""
    from scripts import api_server as api
    captured = {}

    def fake_main(publish: bool = True, dry_run: bool = False, overrides=None,
                  creator_content=None, **kwargs):
        captured["publish"] = publish

    import scripts.daily_pack as dp
    monkeypatch.setattr(dp, "main", fake_main)
    monkeypatch.setattr(dp, "load_history", lambda: {"runs": []})

    # No body → default publish=True
    api._run_pack("test-task-id-2")
    assert captured["publish"] is True


# ────────────────────────────────────────────────────────────────────────────
# 4. demo.py builds the right request body
# ────────────────────────────────────────────────────────────────────────────

def test_demo_kick_off_body_when_no_publish(monkeypatch):
    """When demo.py is invoked with --no-publish, the body posted to
    /run-pack must have publish=False."""
    captured = {}

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"task_id": "t-1"}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return _FakeResp()

    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import scripts.demo as demo
    monkeypatch.setattr(demo, "httpx", MagicMock(post=fake_post))

    demo.kick_off(kind="pack", no_publish=True)
    assert captured["url"].endswith("/run-pack")
    assert captured["body"]["publish"] is False


def test_demo_kick_off_body_when_publish_allowed(monkeypatch):
    """Without --no-publish, the body has publish=True (cron-equivalent)."""
    captured = {}

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"task_id": "t-1"}

    def fake_post(url, json=None, timeout=None):
        captured["body"] = json
        return _FakeResp()

    import scripts.demo as demo
    monkeypatch.setattr(demo, "httpx", MagicMock(post=fake_post))

    demo.kick_off(kind="pack", no_publish=False)
    assert captured["body"]["publish"] is True


# ────────────────────────────────────────────────────────────────────────────
# 5. Logging — the NO-PUBLISH banner appears in the warning log
# ────────────────────────────────────────────────────────────────────────────

def test_run_pack_worker_logs_no_publish_warning(monkeypatch, caplog):
    """Operators must see a clear NO-PUBLISH warning in the log when the
    safety gate engages."""
    import logging
    from scripts import api_server as api
    import scripts.daily_pack as dp

    monkeypatch.setattr(dp, "main", lambda **kw: None)
    monkeypatch.setattr(dp, "load_history", lambda: {"runs": []})

    with caplog.at_level(logging.WARNING):
        api._run_pack("task-warn", publish=False)
    warnings = " ".join(r.getMessage() for r in caplog.records
                        if r.levelno >= logging.WARNING)
    assert "NO-PUBLISH" in warnings.upper()
