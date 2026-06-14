"""
Lightweight schema-validation helpers that NEVER crash the pipeline.

Two use modes:

1. **Optional, env-gated pipeline hook** — call `validate_and_log(...)` after
   any agent emits a result. When `VIRTUAI_VALIDATE_AGENT_OUTPUTS=false`
   (default), the call is a near-no-op. When `true`, validation runs and
   failures land in `virtuai/data/logs/agent_validation_errors.jsonl`. The
   running pipeline is NEVER interrupted by a validation failure.

2. **Always-on CLI hook** — `scripts/agent_cli.py --validate-latest` and
   `--pipeline-check` call `validate_agent_output` directly, regardless of
   the env var.

Both modes return a tuple `(parsed_or_none, ok_bool, error_message)` so
callers can decide what to do — log it, continue, retry, anything.

The helpers know how to map agent names → Pydantic models, and they
tolerate prose-wrapped or code-fenced JSON via `validate_json`.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from virtuai.schemas.agent_outputs import (
    AnalyzerOutput, ResearchOutput, StrategyOutput, CreatorOutput,
    VisualOutput, ReviewerOutput, GuardianOutput, PublisherOutput,
    WorkflowResult, validate_json,
)

logger = logging.getLogger("virtuai.schemas.validators")

# ─── Project paths ─────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[2]
ERROR_LOG_PATH = _ROOT / "virtuai" / "data" / "logs" / "agent_validation_errors.jsonl"

# ─── Agent → schema mapping ────────────────────────────────────────────────

AGENT_SCHEMA: dict[str, type[BaseModel]] = {
    "analyzer":  AnalyzerOutput,
    "research":  ResearchOutput,
    "strategy":  StrategyOutput,
    "creator":   CreatorOutput,
    "visual":    VisualOutput,
    "reviewer":  ReviewerOutput,
    "guardian":  GuardianOutput,
    "publisher": PublisherOutput,
    "workflow":  WorkflowResult,
}


# ─── Env-var gate ──────────────────────────────────────────────────────────

def is_validation_enabled() -> bool:
    """True iff the user explicitly opted-in via env. Default: false."""
    return os.environ.get("VIRTUAI_VALIDATE_AGENT_OUTPUTS", "false").lower() in (
        "1", "true", "yes", "on"
    )


# ─── Validation core — never raises ────────────────────────────────────────

def validate_agent_output(
    agent_name: str,
    raw_output: Any,
) -> tuple[BaseModel | None, bool, str]:
    """
    Validate `raw_output` against the schema for `agent_name`.

    Always returns `(parsed_or_none, ok, error_message)`. Never raises —
    safe to call inside an except block or a hot loop.

    Treats unknown `agent_name` as a pass-through (ok=True, parsed=None,
    error='no schema for <agent_name>'). The pipeline won't crash on an
    agent that doesn't yet have a Pydantic model.
    """
    if not isinstance(agent_name, str) or not agent_name:
        return None, False, "agent_name must be a non-empty string"

    key = agent_name.strip().lower()
    model = AGENT_SCHEMA.get(key)
    if model is None:
        # Soft pass — no schema registered yet. Don't fail the pipeline.
        return None, True, f"no schema registered for agent '{agent_name}'"

    # Accept either a raw JSON string OR an already-parsed dict.
    if isinstance(raw_output, dict):
        try:
            parsed = model.model_validate(raw_output)
            return parsed, True, ""
        except Exception as e:  # pydantic ValidationError or otherwise
            return None, False, f"schema validation error: {e}"

    if isinstance(raw_output, str):
        parsed, err = validate_json(model, raw_output)
        return parsed, parsed is not None, err

    return None, False, f"raw_output type not supported: {type(raw_output).__name__}"


# ─── Persistent error log — append-only JSONL, atomic per record ───────────

def log_validation_error(
    agent_name: str,
    raw_output: Any,
    error_message: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    """
    Append a record to `agent_validation_errors.jsonl`. Truncates the raw
    output to 2KB so the log doesn't balloon. Returns the log path.

    Idempotent — if the log directory doesn't exist, it's created.
    """
    ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    truncated = ""
    if isinstance(raw_output, str):
        truncated = raw_output[:2048]
    elif isinstance(raw_output, dict):
        try:
            truncated = json.dumps(raw_output)[:2048]
        except Exception:
            truncated = repr(raw_output)[:2048]
    else:
        truncated = repr(raw_output)[:2048]

    record = {
        "ts":        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "agent":     agent_name,
        "error":     error_message,
        "raw_head":  truncated,
    }
    if extra:
        record["extra"] = extra

    with ERROR_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return ERROR_LOG_PATH


# ─── The pipeline-side hook ────────────────────────────────────────────────

def validate_and_log(
    agent_name: str,
    raw_output: Any,
    *,
    critical: bool = False,
    extra: dict[str, Any] | None = None,
) -> tuple[BaseModel | None, bool]:
    """
    The pipeline hook. Call this AFTER an agent emits a result.

    - When `VIRTUAI_VALIDATE_AGENT_OUTPUTS=false` (default): no-op.
      Returns `(None, True)` — pipeline keeps moving.
    - When env is `true`: runs validation. On success, returns
      `(parsed, True)`. On failure, appends a record to the JSONL log
      and returns `(None, False)`.
    - `critical=True` does NOT raise (callers stay in control); it only
      escalates the log level so monitors can pick it up.

    Returns `(parsed_or_none, ok_bool)`. The caller decides whether to
    branch on the ok flag — the helper never branches the pipeline.
    """
    if not is_validation_enabled():
        return None, True  # opted out — preserve current behavior

    parsed, ok, err = validate_agent_output(agent_name, raw_output)
    if ok and parsed is not None:
        logger.debug(f"[validate] {agent_name}: ok")
        return parsed, True

    if ok and parsed is None:
        # No schema registered — log INFO, don't treat as failure.
        logger.info(f"[validate] {agent_name}: skipped ({err})")
        return None, True

    # Failure
    log_validation_error(agent_name, raw_output, err, extra=extra)
    level = logging.ERROR if critical else logging.WARNING
    logger.log(level, f"[validate] {agent_name}: FAIL — {err}")
    return None, False


# ─── Read-back for diagnostics ─────────────────────────────────────────────

def recent_errors(limit: int = 10) -> list[dict]:
    """Return the most recent validation error records, newest first."""
    if not ERROR_LOG_PATH.exists():
        return []
    lines = ERROR_LOG_PATH.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out
