# Active Audit Log and Circuit Breaker for Publisher Authentication

_Report-ready section drafted 2026-05-21. Drop into the VirtuAI capstone
academic report verbatim, or extract sub-sections as needed. Every claim
is reproducible from the code paths and commands cited._

---

## 1. Problem Statement

VirtuAI publishes content to four social platforms (Instagram, LinkedIn,
Facebook, YouTube Shorts) on behalf of a synthetic persona, **Daniel
Calder**. During development the project hit two distinct platform-side
defensive responses that motivated this work:

| Platform | Failure mode | Root cause (platform-side) |
|---|---|---|
| Facebook | Anti-abuse code **368 / subcode 4854002** after the first media post | Page Identity Verification required for newly created brand Pages |
| LinkedIn | Government-ID verification screen during OAuth re-authentication | LinkedIn anti-fake-account heuristic flagged the persona profile after a stale "Initiated" OAuth connection sat for 15 days |

These outcomes are not VirtuAI bugs — they are platform fraud
heuristics responding to signals that arise naturally when an automated
system holds long-lived credentials for a persona account. The most
dangerous of those signals is **repeated authentication failure**: a
401/403 retried multiple times in a short window is indistinguishable,
from the platform's perspective, from a credential-stuffing bot.

The project therefore needs an in-process defence that:

1. Stops publishing as soon as authentication starts to fail (preventing
   the publisher from compounding the platform's suspicion score).
2. Records every authentication outcome to a durable log, so failures
   are visible *before* the next scheduled publish rather than during it.
3. Distinguishes credential failures (which compound fraud signals) from
   benign infrastructure failures (5xx, network timeouts), so a flaky
   CDN cannot trip the safety mechanism by accident.

## 2. Design

The implementation introduces two collaborating components:

- **`auth_guard`** — an in-process circuit breaker plus a JSONL audit
  log. Source: [`virtuai/tools/auth_guard.py`](../virtuai/tools/auth_guard.py).
- **`publisher_healthcheck`** — a read-only daily probe that calls each
  platform's identity endpoint without publishing anything. Source:
  [`scripts/publisher_healthcheck.py`](../scripts/publisher_healthcheck.py).

### 2.1 Circuit-breaker semantics

The circuit-breaker pattern is implemented per platform. Each platform
holds the following observable state:

| Field | Meaning |
|---|---|
| `consecutive_auth_fails` | Number of recent auth failures since the last success |
| `last_fail_ts` | Wall-clock time of the most recent auth failure (used for window decay) |
| `last_success_ts` | Wall-clock time of the most recent success |
| `open` | Boolean; `True` means the platform is gated off |
| `last_reason` | Short human-readable string describing the trip reason |

Transitions:

- `record_success(p)` → resets `consecutive_auth_fails` to 0 and closes
  the circuit. Any prior trip is forgiven on the first success.
- `record_auth_failure(p)` → increments the counter. If the counter
  reaches the trip threshold (default 2, configurable via
  `VIRTUAI_AUTH_FAIL_LIMIT`) the circuit opens.
- `gate(p)` → raises `CircuitOpenError` if the circuit is open. Called
  at the entry point of every publishing function.

The trip threshold is intentionally low (2). The cost of refusing one
extra publish is negligible; the cost of compounding a platform's fraud
score is potentially permanent loss of API access.

### 2.2 Failure classification

A failure is treated as an *authentication* failure if either:

- the HTTP status code is 401 or 403, OR
- the exception's class name or message contains any of:
  `unauthorized`, `forbidden`, `invalid_grant`, `invalid_token`,
  `token expired`, `auth`, `permission`, `scope`.

All other failures (5xx, network timeouts, malformed JSON, missing
config) are logged but do *not* count toward the trip threshold. This
prevents the most common false-positive in production systems: a flaky
CDN or transient network blip silently locking the publisher out.

### 2.3 Persistence and recovery

The audit log uses JSONL (newline-delimited JSON) at
`virtuai/data/logs/auth_audit.jsonl`. One line is written per
authentication attempt with the following schema:

```json
{"ts": "2026-05-21T17:53:04Z",
 "ts_epoch": 1779385984.80,
 "platform": "facebook",
 "action":   "FACEBOOK_CREATE_PHOTO_POST",
 "ok": true,
 "status_code": null,
 "auth_fail": false,
 "error_class": "",
 "message": "",
 "extra": {"tool_count": 20, "via": "composio_toolkit_list"}}
```

On construction, the `CircuitBreaker` eagerly replays log entries from
the last 24 hours to reconstruct in-memory state. This survives process
restarts — if the publisher crashed five minutes after tripping a
circuit, the next process honours the trip rather than re-attempting and
deepening the failure pattern.

### 2.4 Health check

`publisher_healthcheck.py` probes each platform's identity endpoint with
a read-only call:

| Platform | Probe | Detects |
|---|---|---|
| YouTube | `oauth2/v3/tokeninfo` (token introspection) | refresh-token revocation, scope drift |
| LinkedIn | Composio `LINKEDIN_GET_MY_INFO` | dropped/expired connection |
| Instagram | Direct `graph.facebook.com/{ig_user_id}` if `IG_ACCESS_TOKEN` set, else Composio toolkit lookup | dead Page-linked token |
| Facebook | Direct `graph.facebook.com/{page_id}` if `FB_PAGE_ACCESS_TOKEN` set, else Composio toolkit lookup | dropped Page connection |

Each probe writes one log line via the same `auth_guard.record(...)`
interface used by publishers, so health checks contribute to the same
24-hour state window the circuit breaker reasons over. Probes never
publish; they only read identity. The script exits with status `0` when
all platforms are healthy and `1` when any is failing — suitable for
cron and CI gating.

## 3. Integration Points

The audit-log hook is wired into every code path that contacts a
platform API, including:

| File | Function(s) wrapped |
|---|---|
| [`virtuai/tools/youtube_direct.py`](../virtuai/tools/youtube_direct.py) | `upload_video` (token refresh, init, PUT) |
| [`scripts/publish_v16.py`](../scripts/publish_v16.py) | `publish_facebook_reel`, `publish_instagram`, `publish_linkedin` |
| [`scripts/publish_images.py`](../scripts/publish_images.py) | `publish_ig_single`, `publish_linkedin_with_image`, `publish_facebook_image` |
| [`virtuai/tools/ig_carousel.py`](../virtuai/tools/ig_carousel.py) | `publish_carousel` (direct Meta Graph) |

The pattern in each wrapped function is the same:

```python
def publish_X(...):
    auth_guard.gate("platform")          # halt early if tripped
    try:
        result = make_api_call(...)
    except Exception as e:
        auth_guard.record("platform", "ACTION", ok=False, error=e)
        raise
    auth_guard.record("platform", "ACTION", ok=True)
    return result
```

The same module is used by `publisher_healthcheck.py` so live publishing
and dry-run probing share one source of truth.

## 4. Evaluation

### 4.1 Unit-test coverage

A 17-case test suite at [`virtuai/tests/test_auth_guard.py`](../virtuai/tests/test_auth_guard.py)
covers:

- Failure classification (401/403 vs 5xx vs network error vs message-hint)
- Logging contract (JSONL append, status_code, message, extra fields)
- Single auth failure does *not* trip the circuit
- Two consecutive auth failures *do* trip the circuit
- A success after a trip closes the circuit
- A manual `reset()` closes the circuit
- Platforms are independent (tripping LinkedIn does not affect Instagram)
- Non-auth failures (5xx) never trip the circuit, even when repeated

Run: `pytest virtuai/tests/test_auth_guard.py -v` — **17 / 17 pass**.

### 4.2 Regression suite

The full project test suite was re-run after the integration: **127 /
127 pass** (110 prior tests + 17 new). Pre-demo readiness check passes
**23 / 23**.

| Command | Result |
|---|---|
| `pytest` | 127 passed |
| `python scripts/agent_cli.py --pipeline-check` | 23 / 23 OK |

### 4.3 Live health-check verification

The health check was executed against the production credentials at
2026-05-21 17:56 UTC:

```
[OK ]  youtube_shorts   scope=youtube.upload, expires_in=3598
[FAIL] linkedin         No connected account for user ID danielcalder-
[OK ]  instagram        Composio IG toolkit returned tools
[OK ]  facebook         Composio FB toolkit returned tools
```

The single failure is genuine (the LinkedIn connection had been
disconnected by the platform earlier in the session) rather than a
probe bug. This validates the design: the system surfaces real platform
state without ever publishing, and it does so in a single command that
can be cron-scheduled.

## 5. Position in the VirtuAI Architecture

VirtuAI's eight-agent pipeline already includes a **Guardian Agent**
responsible for ethics, copyright, and policy-compliance refusals. The
`auth_guard` system complements Guardian by providing a *technical*
refusal layer: where Guardian rejects content that *should not* ship
on policy grounds, `auth_guard` rejects publishes that *cannot* ship
without harming the persona's standing with the platform.

This distinction matters academically because it demonstrates two
distinct kinds of agentic safety:

1. **Content-policy safety** (Guardian) — semantic reasoning over the
   asset itself.
2. **Operational-policy safety** (`auth_guard`) — runtime defence
   against compounding failure patterns at the platform boundary.

Both refusals are first-class outcomes in the Publisher Agent's
structured output schema (`publisher_status: "skipped"`).

## 6. Limitations and Future Work

- **Platform-policy gates are platform-side.** This module prevents the
  publisher from making things worse; it cannot reverse Facebook's
  identity-verification requirement or LinkedIn's government-ID gate.
  Those require user-side action.
- **Refresh windows are coarse-grained.** The trip window is set to 24h
  to favour safety over recovery speed; a future revision could
  implement exponential back-off rather than a binary open/closed state.
- **Cross-process coordination is durable but not real-time.** Multiple
  publishers running concurrently would each hold their own in-memory
  state; the JSONL log is the eventual consistency layer. For VirtuAI's
  single-publisher topology this is sufficient.

## 7. Reproducing the Numbers in this Section

```bash
# 17 / 17 auth_guard unit tests
pytest virtuai/tests/test_auth_guard.py -v

# 127 / 127 full suite
pytest

# 23 / 23 readiness check
python scripts/agent_cli.py --pipeline-check

# Live health probe against production credentials (no publishing)
python scripts/publisher_healthcheck.py

# Reset a tripped circuit after fixing the underlying credential
python scripts/publisher_healthcheck.py --reset all

# Inspect the audit log directly
tail -f virtuai/data/logs/auth_audit.jsonl | jq .
```
