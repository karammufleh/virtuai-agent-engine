"""
Tests for `virtuai.utils.asset_download`.

These tests use a mocked `httpx.Client` so no real network calls are
made. They verify every safety property of the SSL workaround:

  1. SSL verification is ON by default.
  2. `VIRTUAI_TRUST_KIE_CDN=false` does NOT disable verification.
  3. `VIRTUAI_TRUST_KIE_CDN=true` disables verification ONLY for
     hosts in the KIE CDN allowlist (today: `tempfile.aiquickdraw.com`).
  4. `api.kie.ai` NEVER gets relaxed verification, even with env true.
  5. Unknown / arbitrary hosts NEVER get relaxed verification.
  6. A WARNING is emitted with the hostname when relaxed mode runs.
  7. The helper raises on bad URL inputs (never silently swallows).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from virtuai.utils.asset_download import (
    _should_relax_verify,
    _trust_cdn_enabled,
    _host_from,
    download_generated_asset,
    _KIE_CDN_ALLOWLIST,
    _NEVER_RELAX_HOSTS,
)


# ────────────────────────────────────────────────────────────────────────────
# Decision-gate tests — pure logic, no network
# ────────────────────────────────────────────────────────────────────────────

def test_default_does_not_relax(monkeypatch):
    """No env var set → verification stays ON for every URL."""
    monkeypatch.delenv("VIRTUAI_TRUST_KIE_CDN", raising=False)
    assert _trust_cdn_enabled() is False
    for url in ("https://tempfile.aiquickdraw.com/v/foo.mp4",
                "https://api.kie.ai/api/v1/jobs/createTask",
                "https://example.com/file.mp4"):
        assert _should_relax_verify(url) is False, url


def test_explicit_false_does_not_relax(monkeypatch):
    monkeypatch.setenv("VIRTUAI_TRUST_KIE_CDN", "false")
    assert _trust_cdn_enabled() is False
    assert _should_relax_verify("https://tempfile.aiquickdraw.com/x") is False


def test_true_relaxes_only_for_allowlisted_host(monkeypatch):
    monkeypatch.setenv("VIRTUAI_TRUST_KIE_CDN", "true")
    assert _trust_cdn_enabled() is True
    # Allowlisted host → True
    assert _should_relax_verify("https://tempfile.aiquickdraw.com/v/abc.mp4") is True
    # Unknown host → still False
    assert _should_relax_verify("https://example.com/x") is False
    # Subdomain of allowlisted root → still False (must match exactly)
    assert _should_relax_verify("https://evil.tempfile.aiquickdraw.com/x") is False


def test_api_endpoints_never_get_relaxed_even_with_env_true(monkeypatch):
    """Critical: the KIE API itself, file upload, and other API endpoints
    must NEVER get verify=False, even when the env var is on."""
    monkeypatch.setenv("VIRTUAI_TRUST_KIE_CDN", "true")
    for host in _NEVER_RELAX_HOSTS:
        url = f"https://{host}/some/path"
        assert _should_relax_verify(url) is False, host


def test_invalid_url_does_not_relax(monkeypatch):
    monkeypatch.setenv("VIRTUAI_TRUST_KIE_CDN", "true")
    for bad in ("", "not-a-url", "://broken", "ftp://"):
        assert _should_relax_verify(bad) is False, bad


def test_allowlist_contains_only_tempfile_host():
    """Drift guard — make sure no one accidentally added an API host."""
    assert _KIE_CDN_ALLOWLIST == frozenset({"tempfile.aiquickdraw.com"})


def test_never_relax_hosts_contains_api_kie():
    """Drift guard — api.kie.ai must always be in the never-relax list."""
    assert "api.kie.ai" in _NEVER_RELAX_HOSTS


def test_host_from_parses_correctly():
    assert _host_from("https://Example.COM/foo") == "example.com"
    assert _host_from("https://tempfile.aiquickdraw.com:443/x") == "tempfile.aiquickdraw.com"
    assert _host_from("") == ""


# ────────────────────────────────────────────────────────────────────────────
# download_generated_asset — uses a mocked httpx.Client so no real HTTP
# ────────────────────────────────────────────────────────────────────────────

def _make_mock_client(content: bytes = b"x" * 16) -> MagicMock:
    """Construct a MagicMock that mimics httpx.Client well enough."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.content = content

    cm = MagicMock()                 # the Client instance returned by httpx.Client(...)
    cm.__enter__.return_value = cm
    cm.__exit__.return_value = None
    cm.get.return_value = resp
    return cm


def test_download_default_uses_verify_true(monkeypatch, tmp_path):
    monkeypatch.delenv("VIRTUAI_TRUST_KIE_CDN", raising=False)
    mock_client_cls = MagicMock(return_value=_make_mock_client())
    with patch("virtuai.utils.asset_download.httpx.Client", mock_client_cls):
        out = download_generated_asset(
            "https://tempfile.aiquickdraw.com/v/abc.mp4",
            tmp_path / "abc.mp4",
        )
    assert out.exists()
    # httpx.Client was called with verify=True
    _, kwargs = mock_client_cls.call_args
    assert kwargs["verify"] is True


def test_download_relaxed_only_when_both_conditions_hold(monkeypatch, tmp_path):
    monkeypatch.setenv("VIRTUAI_TRUST_KIE_CDN", "true")
    mock_client_cls = MagicMock(return_value=_make_mock_client())
    with patch("virtuai.utils.asset_download.httpx.Client", mock_client_cls):
        download_generated_asset(
            "https://tempfile.aiquickdraw.com/v/abc.mp4",
            tmp_path / "abc.mp4",
        )
    _, kwargs = mock_client_cls.call_args
    assert kwargs["verify"] is False, "verify should be False for allowlisted CDN"


def test_download_api_endpoint_stays_verified_even_with_env_true(monkeypatch, tmp_path):
    """Critical: api.kie.ai keeps verify=True even when env says relax."""
    monkeypatch.setenv("VIRTUAI_TRUST_KIE_CDN", "true")
    mock_client_cls = MagicMock(return_value=_make_mock_client(b"{}"))
    with patch("virtuai.utils.asset_download.httpx.Client", mock_client_cls):
        download_generated_asset(
            "https://api.kie.ai/api/v1/jobs/recordInfo",
            tmp_path / "resp.json",
        )
    _, kwargs = mock_client_cls.call_args
    assert kwargs["verify"] is True, "API endpoints must always verify"


def test_download_unknown_host_stays_verified_even_with_env_true(monkeypatch, tmp_path):
    monkeypatch.setenv("VIRTUAI_TRUST_KIE_CDN", "true")
    mock_client_cls = MagicMock(return_value=_make_mock_client())
    with patch("virtuai.utils.asset_download.httpx.Client", mock_client_cls):
        download_generated_asset(
            "https://random-cdn.example.com/x.mp4",
            tmp_path / "x.mp4",
        )
    _, kwargs = mock_client_cls.call_args
    assert kwargs["verify"] is True, "unknown hosts must always verify"


def test_warning_emitted_with_hostname_when_relaxed(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("VIRTUAI_TRUST_KIE_CDN", "true")
    mock_client_cls = MagicMock(return_value=_make_mock_client())
    import logging
    with caplog.at_level(logging.WARNING, logger="virtuai.utils.asset_download"):
        with patch("virtuai.utils.asset_download.httpx.Client", mock_client_cls):
            download_generated_asset(
                "https://tempfile.aiquickdraw.com/v/abc.mp4",
                tmp_path / "abc.mp4",
            )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING log when relaxed mode is used"
    msg = warnings[0].getMessage()
    assert "tempfile.aiquickdraw.com" in msg
    assert "TEMPORARILY DISABLED" in msg
    assert "VIRTUAI_TRUST_KIE_CDN" in msg


def test_no_warning_when_default(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv("VIRTUAI_TRUST_KIE_CDN", raising=False)
    mock_client_cls = MagicMock(return_value=_make_mock_client())
    import logging
    with caplog.at_level(logging.WARNING, logger="virtuai.utils.asset_download"):
        with patch("virtuai.utils.asset_download.httpx.Client", mock_client_cls):
            download_generated_asset(
                "https://tempfile.aiquickdraw.com/v/abc.mp4",
                tmp_path / "abc.mp4",
            )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings, "no warning expected when verification is on"


def test_empty_url_raises(tmp_path):
    with pytest.raises(ValueError):
        download_generated_asset("", tmp_path / "x.mp4")


def test_http_error_propagates(monkeypatch, tmp_path):
    """raise_for_status() failures must propagate — never silent."""
    monkeypatch.delenv("VIRTUAI_TRUST_KIE_CDN", raising=False)
    bad_resp = MagicMock()
    bad_resp.raise_for_status.side_effect = RuntimeError("HTTP 500")
    cm = MagicMock()
    cm.__enter__.return_value = cm
    cm.__exit__.return_value = None
    cm.get.return_value = bad_resp
    mock_client_cls = MagicMock(return_value=cm)
    with patch("virtuai.utils.asset_download.httpx.Client", mock_client_cls):
        with pytest.raises(RuntimeError, match="HTTP 500"):
            download_generated_asset(
                "https://tempfile.aiquickdraw.com/v/x.mp4",
                tmp_path / "x.mp4",
            )
