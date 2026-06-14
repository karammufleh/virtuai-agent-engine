"""
Tests for scripts/demo.py's polling loop.

The 2026-05-20 bug: the poll() loop only recognised
`done / succeeded / completed` as terminal-success states, but the API
returns `state="success"`. That caused the demo client to keep polling
until its 15-min timeout even though the pack was done in ~7 min.

Fix added `success` (and `fail` symmetrically). These tests pin the
contract so the regression can't sneak back in.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _fake_httpx_get(state_value: str):
    """Returns a MagicMock for httpx.get() that yields {state: <value>}."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"state": state_value, "stage": "?"}
    return MagicMock(return_value=resp)


def test_poll_recognises_state_success(monkeypatch):
    """`state=success` from /run-pack must terminate the poll loop."""
    import scripts.demo as demo
    # Make sleep instant so we don't actually wait between iterations.
    monkeypatch.setattr(demo.time, "sleep", lambda *_: None)
    monkeypatch.setattr(demo.httpx, "get", _fake_httpx_get("success"))
    result = demo.poll("test-task-1")
    assert result is not None, "poll should return when state=success"
    assert result["state"] == "success"


def test_poll_recognises_state_done(monkeypatch):
    """Backward compat — historical terminal states still work."""
    import scripts.demo as demo
    monkeypatch.setattr(demo.time, "sleep", lambda *_: None)
    monkeypatch.setattr(demo.httpx, "get", _fake_httpx_get("done"))
    assert demo.poll("test-task-2") is not None


def test_poll_recognises_state_succeeded(monkeypatch):
    import scripts.demo as demo
    monkeypatch.setattr(demo.time, "sleep", lambda *_: None)
    monkeypatch.setattr(demo.httpx, "get", _fake_httpx_get("succeeded"))
    assert demo.poll("test-task-3") is not None


def test_poll_recognises_state_completed(monkeypatch):
    import scripts.demo as demo
    monkeypatch.setattr(demo.time, "sleep", lambda *_: None)
    monkeypatch.setattr(demo.httpx, "get", _fake_httpx_get("completed"))
    assert demo.poll("test-task-4") is not None


def test_poll_recognises_state_failed_and_fail(monkeypatch):
    """Both `failed` and `fail` must short-circuit to None (run failed)."""
    import scripts.demo as demo
    monkeypatch.setattr(demo.time, "sleep", lambda *_: None)
    for term in ("failed", "fail", "error"):
        monkeypatch.setattr(demo.httpx, "get", _fake_httpx_get(term))
        assert demo.poll(f"task-{term}") is None, term


def test_poll_keeps_polling_when_running(monkeypatch):
    """`state=running` should NOT short-circuit. Verify by enforcing the
    timeout — we shorten it so the test runs fast."""
    import scripts.demo as demo
    # Force the poll deadline to 0.05 s so the loop exits via timeout.
    monkeypatch.setattr(demo, "PACK_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(demo, "POLL_SEC", 0.01)
    monkeypatch.setattr(demo.time, "sleep", lambda *_: None)
    monkeypatch.setattr(demo.httpx, "get", _fake_httpx_get("running"))
    assert demo.poll("task-running") is None
