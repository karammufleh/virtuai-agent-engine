"""
Tests for virtuai/tools/auth_guard.py — the active audit log + circuit
breaker that protects accounts from getting flagged by repeated auth
failures.

The breaker logic:
  • Auth failure = 401/403 status OR error message matches _AUTH_HINTS
  • Trip after AUTH_FAIL_LIMIT (default 2) consecutive auth failures
  • Non-auth failures (network, 500s) are logged but DO NOT trip
  • A success resets the counter and closes the circuit
  • gate(platform) raises CircuitOpenError when tripped

We rebuild the singleton between tests by reaching into the module —
tests need to be deterministic and not depend on prior runs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from virtuai.tools import auth_guard
from virtuai.tools.auth_guard import (
    CircuitBreaker, CircuitOpenError, classify_error,
)


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    """Redirect the audit log to a tmp path AND reset the singleton between
    tests so no state leaks. The module already created the real log dir at
    import time; we just point future writes at tmp_path."""
    fake_log = tmp_path / "auth_audit.jsonl"
    monkeypatch.setattr(auth_guard, "LOG_PATH", fake_log)
    # Replace the breaker singleton with a fresh one so state doesn't leak
    monkeypatch.setattr(auth_guard, "_breaker", CircuitBreaker())
    yield fake_log


# ── classify_error ──────────────────────────────────────────────────────────


def test_classify_401_is_auth():
    assert classify_error(None, 401) is True


def test_classify_403_is_auth():
    assert classify_error(None, 403) is True


def test_classify_500_is_not_auth():
    assert classify_error(RuntimeError("internal server error"), 500) is False


def test_classify_message_unauthorized():
    assert classify_error(RuntimeError("Unauthorized: token invalid")) is True


def test_classify_message_invalid_grant():
    assert classify_error(RuntimeError("invalid_grant")) is True


def test_classify_message_network_is_not_auth():
    assert classify_error(ConnectionError("Connection reset by peer")) is False


# ── record / gate ───────────────────────────────────────────────────────────


def test_record_appends_jsonl(isolated_log):
    auth_guard.record("linkedin", "TEST", ok=True)
    lines = isolated_log.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["platform"] == "linkedin"
    assert entry["action"] == "TEST"
    assert entry["ok"] is True
    assert entry["auth_fail"] is False


def test_record_success_does_not_trip():
    for _ in range(10):
        auth_guard.record("linkedin", "TEST", ok=True)
    auth_guard.gate("linkedin")  # should not raise


def test_record_non_auth_failure_does_not_trip():
    """Network timeouts and 5xx shouldn't lock us out — those are platform
    flakiness, not credential rot."""
    for _ in range(5):
        auth_guard.record(
            "instagram", "TEST",
            ok=False, status_code=503,
            error=RuntimeError("Service Unavailable"),
        )
    auth_guard.gate("instagram")  # still closed


def test_record_one_auth_failure_does_not_trip():
    auth_guard.record(
        "linkedin", "TEST",
        ok=False, status_code=401,
        error=RuntimeError("Unauthorized"),
    )
    auth_guard.gate("linkedin")  # still closed after 1


def test_record_two_auth_failures_trips():
    """The protective behavior we built this for."""
    for _ in range(2):
        auth_guard.record(
            "linkedin", "LINKEDIN_CREATE_LINKED_IN_POST",
            ok=False, status_code=401,
            error=RuntimeError("Unauthorized"),
        )
    with pytest.raises(CircuitOpenError) as exc:
        auth_guard.gate("linkedin")
    assert "linkedin" in str(exc.value).lower()
    assert "OPEN" in str(exc.value)


def test_success_after_failure_closes_circuit():
    # 2 fails → trip
    for _ in range(2):
        auth_guard.record(
            "facebook", "X",
            ok=False, status_code=401, error=RuntimeError("Unauthorized"),
        )
    with pytest.raises(CircuitOpenError):
        auth_guard.gate("facebook")
    # One success → closed
    auth_guard.record("facebook", "X", ok=True)
    auth_guard.gate("facebook")  # should not raise


def test_manual_reset_closes_circuit():
    for _ in range(2):
        auth_guard.record(
            "instagram", "X",
            ok=False, status_code=403, error=RuntimeError("Forbidden"),
        )
    with pytest.raises(CircuitOpenError):
        auth_guard.gate("instagram")
    auth_guard.reset("instagram")
    auth_guard.gate("instagram")  # should not raise


def test_status_reports_open_state():
    for _ in range(2):
        auth_guard.record(
            "linkedin", "X",
            ok=False, status_code=401, error=RuntimeError("Unauthorized"),
        )
    state = auth_guard.status()
    assert state["linkedin"]["open"] is True
    assert state["linkedin"]["consecutive_auth_fails"] == 2


def test_platforms_are_independent():
    """Tripping LinkedIn must NOT trip Instagram."""
    for _ in range(2):
        auth_guard.record(
            "linkedin", "X",
            ok=False, status_code=401, error=RuntimeError("Unauthorized"),
        )
    with pytest.raises(CircuitOpenError):
        auth_guard.gate("linkedin")
    auth_guard.gate("instagram")  # untouched
    auth_guard.gate("facebook")
    auth_guard.gate("youtube_shorts")


def test_log_includes_status_code_and_message(isolated_log):
    auth_guard.record(
        "linkedin", "LINKEDIN_CREATE_LINKED_IN_POST",
        ok=False, status_code=403,
        error=RuntimeError("Restricted account"),
    )
    entry = json.loads(isolated_log.read_text().splitlines()[-1])
    assert entry["status_code"] == 403
    assert "Restricted" in entry["message"]
    assert entry["auth_fail"] is True


def test_extra_field_passed_through(isolated_log):
    auth_guard.record(
        "youtube_shorts", "YOUTUBE_UPLOAD_PUT",
        ok=True, extra={"video_id": "abc123"},
    )
    entry = json.loads(isolated_log.read_text().splitlines()[-1])
    assert entry["extra"]["video_id"] == "abc123"
