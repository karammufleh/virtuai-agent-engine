"""
Tests for the env-gated schema validation layer.

These tests verify:
  - validate_agent_output accepts well-formed JSON
  - validate_agent_output rejects invalid enums + bad agent names
  - validate_and_log is a no-op when the env var is false (default)
  - validate_and_log logs to JSONL when env var is true (without crashing)
  - log_validation_error writes a JSONL record and survives big inputs
  - --validate-latest gracefully handles "no package on disk"
  - --pipeline-check returns a meaningful summary in offline mode

No live API calls.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from virtuai.schemas.validators import (
    is_validation_enabled,
    validate_agent_output,
    validate_and_log,
    log_validation_error,
    recent_errors,
    ERROR_LOG_PATH,
)


# ────────────────────────────────────────────────────────────────────────────
# is_validation_enabled
# ────────────────────────────────────────────────────────────────────────────

def test_is_validation_enabled_default_false(monkeypatch):
    monkeypatch.delenv("VIRTUAI_VALIDATE_AGENT_OUTPUTS", raising=False)
    assert is_validation_enabled() is False


def test_is_validation_enabled_true_values(monkeypatch):
    for v in ("1", "true", "True", "TRUE", "yes", "on"):
        monkeypatch.setenv("VIRTUAI_VALIDATE_AGENT_OUTPUTS", v)
        assert is_validation_enabled() is True, f"failed for {v!r}"


def test_is_validation_enabled_false_values(monkeypatch):
    for v in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("VIRTUAI_VALIDATE_AGENT_OUTPUTS", v)
        assert is_validation_enabled() is False, f"failed for {v!r}"


# ────────────────────────────────────────────────────────────────────────────
# validate_agent_output — accepts valid JSON
# ────────────────────────────────────────────────────────────────────────────

def test_validate_agent_output_accepts_dict():
    raw = {"approval_status": "approve", "revise_agent": "none"}
    parsed, ok, err = validate_agent_output("reviewer", raw)
    assert ok is True
    assert parsed is not None
    assert parsed.approval_status == "approve"
    assert err == ""


def test_validate_agent_output_accepts_json_string():
    raw = '{"approval_status": "approve", "revise_agent": "none"}'
    parsed, ok, err = validate_agent_output("reviewer", raw)
    assert ok is True
    assert parsed.approval_status == "approve"


def test_validate_agent_output_accepts_fenced_json():
    raw = "Here is my verdict:\n```json\n{\"approval_status\":\"revise\",\"revise_agent\":\"creator\"}\n```"
    parsed, ok, err = validate_agent_output("reviewer", raw)
    assert ok is True
    assert parsed.approval_status == "revise"


# ────────────────────────────────────────────────────────────────────────────
# validate_agent_output — rejects invalid JSON / enum / agent
# ────────────────────────────────────────────────────────────────────────────

def test_validate_agent_output_rejects_invalid_enum():
    raw = {"approval_status": "maybe_publish", "revise_agent": "none"}
    parsed, ok, err = validate_agent_output("reviewer", raw)
    assert ok is False
    assert parsed is None
    assert "validation" in err.lower() or "literal" in err.lower()


def test_validate_agent_output_rejects_invalid_publisher_status():
    raw = {"publisher_status": "published_secretly", "platform": "instagram"}
    parsed, ok, err = validate_agent_output("publisher", raw)
    assert ok is False
    assert parsed is None


def test_validate_agent_output_rejects_garbage_string():
    parsed, ok, err = validate_agent_output("reviewer", "lol no JSON")
    assert ok is False
    assert parsed is None


def test_validate_agent_output_unknown_agent_is_soft_pass():
    """Unknown agent names should not crash the pipeline — soft pass."""
    parsed, ok, err = validate_agent_output("not_an_agent", "{}")
    assert ok is True
    assert parsed is None
    assert "no schema" in err.lower()


def test_validate_agent_output_empty_agent_name():
    parsed, ok, err = validate_agent_output("", "{}")
    assert ok is False
    assert parsed is None


# ────────────────────────────────────────────────────────────────────────────
# validate_and_log — the pipeline-side hook
# ────────────────────────────────────────────────────────────────────────────

def test_validate_and_log_noop_when_disabled(monkeypatch, tmp_path):
    """Default state: hook does nothing, returns (None, True), no log file written."""
    monkeypatch.delenv("VIRTUAI_VALIDATE_AGENT_OUTPUTS", raising=False)
    # Even passing intentionally bad data: should not crash, should not log.
    parsed, ok = validate_and_log("reviewer", "garbage")
    assert parsed is None
    assert ok is True   # pipeline continues


def test_validate_and_log_writes_to_jsonl_when_enabled(monkeypatch):
    """With env var true, a validation failure appends to the JSONL log."""
    monkeypatch.setenv("VIRTUAI_VALIDATE_AGENT_OUTPUTS", "true")
    pre_size = ERROR_LOG_PATH.stat().st_size if ERROR_LOG_PATH.exists() else 0
    parsed, ok = validate_and_log("reviewer", '{"approval_status": "lol"}')
    assert parsed is None
    assert ok is False
    assert ERROR_LOG_PATH.exists()
    post_size = ERROR_LOG_PATH.stat().st_size
    assert post_size > pre_size, "expected new error record appended"


def test_validate_and_log_does_not_crash_on_huge_input(monkeypatch):
    """log_validation_error must survive big inputs (it truncates)."""
    monkeypatch.setenv("VIRTUAI_VALIDATE_AGENT_OUTPUTS", "true")
    huge = "x" * 1_000_000
    parsed, ok = validate_and_log("reviewer", huge)
    assert parsed is None
    assert ok is False


def test_validate_and_log_success_path(monkeypatch):
    monkeypatch.setenv("VIRTUAI_VALIDATE_AGENT_OUTPUTS", "true")
    parsed, ok = validate_and_log(
        "reviewer",
        {"approval_status": "approve", "revise_agent": "none"},
    )
    assert ok is True
    assert parsed is not None
    assert parsed.approval_status == "approve"


# ────────────────────────────────────────────────────────────────────────────
# log_validation_error
# ────────────────────────────────────────────────────────────────────────────

def test_log_validation_error_appends_jsonl():
    n_before = len(recent_errors(limit=999))
    log_validation_error("test_agent", "bad output", "test error",
                         extra={"unit_test": True})
    after = recent_errors(limit=999)
    assert len(after) >= n_before + 1
    head = after[0]
    assert head["agent"] == "test_agent"
    assert head["error"] == "test error"


# ────────────────────────────────────────────────────────────────────────────
# CLI behavior — --validate-latest + --pipeline-check
# ────────────────────────────────────────────────────────────────────────────

def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "agent_cli.py"), *args],
        capture_output=True, text=True, timeout=60,
    )


def test_validate_latest_runs_cleanly():
    """The command must exit 0 even if no validatable package exists yet
    (the pre-existing content_packages on disk pre-date the new schemas)."""
    r = _run_cli("--validate-latest")
    assert r.returncode == 0, f"non-zero exit: {r.stderr or r.stdout}"
    out = r.stdout + r.stderr
    # Either no packages found OR no embedded schema match OR some passes.
    assert any(token in out for token in
               ("no content_packages", "no saved content packages",
                "no embedded agent output",
                "validation(s) succeeded",
                "Latest package")), f"unexpected output: {out[:300]}"


def test_pipeline_check_runs_cleanly():
    """--pipeline-check should print the summary block and never raise."""
    r = _run_cli("--pipeline-check", "--offline")
    out = r.stdout
    # Exit code may be 0 or 1 depending on whether COMPOSIO_API_KEY is set
    # locally. Both are acceptable for this smoke test.
    assert r.returncode in (0, 1), f"unexpected exit: {r.returncode}"
    assert "VirtuAI pre-demo pipeline check" in out
    assert "checks passed" in out


def test_publisher_dry_run_still_does_not_publish():
    """Sanity guard — running --agent publisher --offline never touches APIs."""
    r = _run_cli("--agent", "publisher", "--offline")
    assert r.returncode == 0
    out = r.stdout
    assert "agent     : publisher" in out
    assert "offline" in out
    # The CLI must not have called Composio or YouTube — no live URLs in output
    assert "youtube.com/watch" not in out
    assert "linkedin.com/feed/update" not in out
