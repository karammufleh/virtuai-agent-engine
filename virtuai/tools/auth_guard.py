"""
auth_guard.py — active audit log + circuit breaker for publisher auth.

Why this exists
───────────────
Every social platform runs anti-bot fraud heuristics. The single biggest
trigger we've seen with this persona (Daniel Calder) is *repeated auth
failure*: a 401/403 retried five times in a minute looks identical to a
credential-stuffing bot. LinkedIn already escalated to government-ID
verification on this account; Facebook tripped its identity-confirmation
gate (code 368). The pattern is now well-established.

This module is the "active log" that prevents that loop:

  • record(platform, action, ok, error=...) — append one JSONL line per
    call. We can audit history offline.
  • gate(platform) — raise CircuitOpenError if the platform has tripped.
    Callers MUST call this before doing the work.
  • CircuitBreaker — opens after 2 consecutive auth failures within 24h,
    closes on the first success after that. Non-auth errors (network
    timeout, 5xx) do NOT count, so a flaky upload host can't lock us out.

It is intentionally tiny: a single JSONL file + an in-memory counter
hydrated from that file at startup. No external deps beyond stdlib.

The log path is virtuai/data/logs/auth_audit.jsonl. Open it any time:

    tail -f virtuai/data/logs/auth_audit.jsonl | jq .

Schema per line:
    {"ts": "2026-05-21T17:42:03Z", "platform": "linkedin",
     "action": "LINKEDIN_CREATE_LINKED_IN_POST",
     "ok": false, "error_class": "ComposioAuthError",
     "status_code": 401, "message": "..."}
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("virtuai.tools.auth_guard")

ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / "virtuai" / "data" / "logs" / "auth_audit.jsonl"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

# Trip after this many consecutive auth failures.
AUTH_FAIL_LIMIT = int(os.environ.get("VIRTUAI_AUTH_FAIL_LIMIT", "2"))
# Look-back window for "consecutive" — older failures don't count.
AUTH_FAIL_WINDOW_SECONDS = int(
    os.environ.get("VIRTUAI_AUTH_FAIL_WINDOW", str(24 * 3600))
)
# Substrings that classify a generic exception as an auth failure.
# Used when callers don't pass a status_code explicitly.
_AUTH_HINTS = (
    "401", "403", "unauthorized", "forbidden", "invalid_grant",
    "invalid_token", "token expired", "auth", "permission", "scope",
)


class CircuitOpenError(RuntimeError):
    """Raised by gate() when a platform's circuit is open. Halts the publish
    without performing it, preventing further auth failures from accumulating."""


@dataclass
class _PlatformState:
    consecutive_auth_fails: int = 0
    last_fail_ts: float = 0.0
    last_success_ts: float = 0.0
    # Latched: once tripped, stays tripped until a manual reset or a
    # successful operation. Healthcheck script can reset by calling
    # reset(platform).
    open: bool = False
    last_reason: str = ""


class CircuitBreaker:
    """One instance, shared across the process. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, _PlatformState] = {}
        # Eager hydration: read existing log NOW so subsequent record()
        # calls (which write the log line BEFORE updating the breaker)
        # don't get double-counted by lazy hydration.
        self._hydrate_from_log()

    # ── public API ──────────────────────────────────────────────────────

    def gate(self, platform: str) -> None:
        """Raise CircuitOpenError if `platform` is tripped. No-op otherwise."""
        with self._lock:
            st = self._states.get(platform)
            if st and st.open:
                raise CircuitOpenError(
                    f"auth_guard: {platform} circuit OPEN "
                    f"(reason: {st.last_reason}). "
                    f"Last failure at {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(st.last_fail_ts))} UTC. "
                    f"Run scripts/publisher_healthcheck.py to validate, then "
                    f"call auth_guard.reset({platform!r}) to re-enable."
                )

    def record_success(self, platform: str, action: str = "") -> None:
        with self._lock:
            st = self._states.setdefault(platform, _PlatformState())
            st.consecutive_auth_fails = 0
            st.last_success_ts = time.time()
            st.open = False  # close on success
            st.last_reason = ""

    def record_auth_failure(
        self, platform: str, action: str, message: str, status_code: int | None
    ) -> None:
        with self._lock:
            st = self._states.setdefault(platform, _PlatformState())
            now = time.time()
            # If the previous fail was outside the window, treat as fresh.
            if now - st.last_fail_ts > AUTH_FAIL_WINDOW_SECONDS:
                st.consecutive_auth_fails = 0
            st.consecutive_auth_fails += 1
            st.last_fail_ts = now
            st.last_reason = f"{action} → {status_code or ''} {message[:160]}"
            if st.consecutive_auth_fails >= AUTH_FAIL_LIMIT:
                st.open = True
                logger.warning(
                    f"auth_guard: TRIPPED {platform} "
                    f"after {st.consecutive_auth_fails} consecutive auth fails."
                )

    def reset(self, platform: str) -> None:
        """Manual override — closes the circuit for `platform`. Used by
        the healthcheck script after it verifies tokens are healthy."""
        with self._lock:
            st = self._states.setdefault(platform, _PlatformState())
            st.consecutive_auth_fails = 0
            st.open = False
            st.last_reason = ""
        logger.info(f"auth_guard: RESET {platform}")

    def status(self) -> dict[str, dict[str, Any]]:
        """Read-only snapshot for healthcheck / debug."""
        with self._lock:
            return {
                p: {
                    "open": s.open,
                    "consecutive_auth_fails": s.consecutive_auth_fails,
                    "last_fail_ts": s.last_fail_ts,
                    "last_success_ts": s.last_success_ts,
                    "last_reason": s.last_reason,
                }
                for p, s in self._states.items()
            }

    # ── internals ───────────────────────────────────────────────────────

    def _hydrate_from_log(self) -> None:
        """Replay the last 24h of log entries to reconstruct state.
        Runs ONCE at construction so subsequent record() writes don't
        get re-read and double-counted.

        Avoids the "restart loses the breaker" footgun: a process crash
        + restart still respects the recorded auth failures from before."""
        if not LOG_PATH.exists():
            return
        cutoff = time.time() - AUTH_FAIL_WINDOW_SECONDS
        try:
            # Read last ~500 lines; trip logic only needs recent history.
            lines = LOG_PATH.read_text(encoding="utf-8").splitlines()[-500:]
        except OSError:
            return
        for raw in lines:
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts = entry.get("ts_epoch") or 0
            if ts < cutoff:
                continue
            platform = entry.get("platform") or ""
            if not platform:
                continue
            st = self._states.setdefault(platform, _PlatformState())
            if entry.get("ok"):
                st.consecutive_auth_fails = 0
                st.last_success_ts = ts
                st.open = False
                st.last_reason = ""
            elif entry.get("auth_fail"):
                if ts - st.last_fail_ts > AUTH_FAIL_WINDOW_SECONDS:
                    st.consecutive_auth_fails = 0
                st.consecutive_auth_fails += 1
                st.last_fail_ts = ts
                st.last_reason = entry.get("message", "")[:160]
                if st.consecutive_auth_fails >= AUTH_FAIL_LIMIT:
                    st.open = True


# Module-level singleton.
_breaker = CircuitBreaker()


# ── classification ──────────────────────────────────────────────────────


def classify_error(
    exc: BaseException | None, status_code: int | None = None
) -> bool:
    """Return True if `exc`/`status_code` looks like a credential/auth
    failure (the kind that would compound platform fraud scoring)."""
    if status_code in (401, 403):
        return True
    if exc is None:
        return False
    msg = (str(exc) + " " + type(exc).__name__).lower()
    return any(hint in msg for hint in _AUTH_HINTS)


# ── public functions (the API callers use) ──────────────────────────────


def gate(platform: str) -> None:
    """Halt publish if the breaker is open. Call this FIRST in every
    publish_* function. Cheap (no I/O after first call per process)."""
    _breaker.gate(platform)


def record(
    platform: str,
    action: str,
    *,
    ok: bool,
    status_code: int | None = None,
    error: BaseException | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one audit-log entry and update the breaker state.

    Call at the END of every publish action (success OR failure path).
    The combination of `ok=False` + an auth-classified error is what
    causes a circuit to open after AUTH_FAIL_LIMIT consecutive hits.
    """
    auth_fail = (not ok) and classify_error(error, status_code)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ts_epoch": time.time(),
        "platform": platform,
        "action": action,
        "ok": ok,
        "status_code": status_code,
        "auth_fail": auth_fail,
        "error_class": type(error).__name__ if error else "",
        "message": (str(error)[:300] if error else ""),
    }
    if extra:
        entry["extra"] = extra
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:  # log is best-effort; never block a publish on log I/O
        logger.warning(f"auth_guard: failed to write audit log: {e}")

    if ok:
        _breaker.record_success(platform, action)
    elif auth_fail:
        _breaker.record_auth_failure(
            platform, action, str(error or "")[:160], status_code
        )
    # Non-auth failures (5xx, timeouts) are logged but don't count toward trip.


def reset(platform: str) -> None:
    """Manually close the circuit for `platform`. Called by healthcheck
    after it confirms the token is healthy again."""
    _breaker.reset(platform)


def status() -> dict[str, dict[str, Any]]:
    """Read-only snapshot of all known platforms' breaker state."""
    return _breaker.status()


def log_path() -> Path:
    """Where the audit log lives. Useful for healthcheck reports."""
    return LOG_PATH
