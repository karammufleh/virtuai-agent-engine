"""
virtuai.schemas — Pydantic models for the 8-agent structured outputs.

These are ADDITIVE — the agents themselves emit JSON strings whose shape
matches these models, but the existing pipeline still works without
validation. Downstream code (n8n result handler, /agents/{name}/run-sync,
the agent CLI) can call `validate_*` helpers to enforce the schema and
get clean, typed objects.
"""
from virtuai.schemas.agent_outputs import (  # noqa: F401
    AnalyzerOutput,
    ResearchOutput,
    StrategyOutput,
    CreatorOutput,
    VisualOutput,
    ReviewerOutput,
    GuardianOutput,
    PublisherOutput,
    WorkflowResult,
    validate_json,
)
from virtuai.schemas.validators import (  # noqa: F401
    AGENT_SCHEMA,
    is_validation_enabled,
    validate_agent_output,
    validate_and_log,
    log_validation_error,
    recent_errors,
    ERROR_LOG_PATH,
)
