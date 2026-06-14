"""
Pydantic models for the 8-agent structured outputs.

Each agent emits JSON whose shape matches one of these models. Use
`validate_json(model, raw_text)` to:
  1. Strip CrewAI's wrapper formatting (```json ... ``` fences, prose).
  2. Parse the JSON.
  3. Validate against the Pydantic model.

If validation fails the helper returns a tuple (None, error_message) —
no exception is raised, so downstream code can decide whether to retry
the agent or fall back to a default.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ────────────────────────────────────────────────────────────────────────────
# 1. AnalyzerOutput — performance verdict
# ────────────────────────────────────────────────────────────────────────────

class AnalyzerOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    historical_data_available:        bool
    recent_performance_summary:       str = ""
    best_performing_posts:            list[dict[str, Any]] = Field(default_factory=list)
    worst_performing_posts:           list[dict[str, Any]] = Field(default_factory=list)
    best_platforms:                   list[str] = Field(default_factory=list)
    best_content_types:               list[str] = Field(default_factory=list)
    best_posting_times:               list[str] = Field(default_factory=list)
    successful_hooks:                 list[str] = Field(default_factory=list)
    weaknesses:                       list[str] = Field(default_factory=list)
    recommendations_for_next_post:    list[str] = Field(default_factory=list)
    strategy_adjustments:             dict[str, Any] = Field(default_factory=dict)

    # Legacy v1 fields (Analyzer-first cycle-driver design). Both the v1
    # `verdict / next_direction` and the new richer fields can coexist.
    verdict:                          Literal["positive", "negative", "neutral", ""] = ""
    next_direction:                   str = ""
    recommendation:                   str = ""


# ────────────────────────────────────────────────────────────────────────────
# 2. ResearchOutput — trend with seven scoring dimensions
# ────────────────────────────────────────────────────────────────────────────

class ResearchOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    trend_found:                  bool = True
    trend_name:                   str
    trend_summary:                str = ""
    niche:                        str = ""
    why_it_is_trending:           str = ""
    sources:                      list[str] = Field(default_factory=list)
    keywords:                     list[str] = Field(default_factory=list)
    hashtags:                     list[str] = Field(default_factory=list)
    platforms_where_seen:         list[str] = Field(default_factory=list)

    # Seven-dimensional scoring, each 0-10
    freshness_score:              float = Field(0, ge=0, le=10)
    niche_relevance_score:        float = Field(0, ge=0, le=10)
    virality_score:               float = Field(0, ge=0, le=10)
    platform_relevance_score:     float = Field(0, ge=0, le=10)
    persona_fit_score:            float = Field(0, ge=0, le=10)
    risk_score:                   float = Field(0, ge=0, le=10)
    content_opportunity_score:    float = Field(0, ge=0, le=10)

    recommendation:               Literal["continue", "skip"] = "continue"
    reason:                       str = ""

    @property
    def total_score(self) -> float:
        """Sum of positive-direction scores minus risk (risk is bad)."""
        positives = (
            self.freshness_score + self.niche_relevance_score
            + self.virality_score + self.platform_relevance_score
            + self.persona_fit_score + self.content_opportunity_score
        )
        return positives - self.risk_score

    @property
    def should_skip(self) -> bool:
        """Centralized skip rule: agent explicitly says skip OR scores are weak."""
        if self.recommendation == "skip":
            return True
        # Hard gate: any of these on their own should kill the cycle.
        if self.freshness_score < 4:    return True
        if self.niche_relevance_score < 4: return True
        if self.risk_score > 7:         return True
        return False


# ────────────────────────────────────────────────────────────────────────────
# 3. StrategyOutput — platform, format, timing, angle
# ────────────────────────────────────────────────────────────────────────────

# Only platforms we actually have a publisher for, per the trimmed stack.
ActivePlatform = Literal["instagram", "linkedin", "facebook", "youtube_shorts"]


class StrategyOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    selected_platforms:           list[ActivePlatform] = Field(default_factory=list)
    primary_platform:             ActivePlatform | str = ""
    content_type:                 Literal["reel", "post", "carousel", "story", "short", "article"] | str = "reel"
    recommended_posting_time:     str = ""
    reason_for_timing:            str = ""
    content_angle:                str = ""
    target_audience:              str = ""
    hook_strategy:                str = ""
    caption_strategy:             str = ""
    visual_style:                 str = ""
    video_style:                  str = ""
    cta:                          str = ""
    platform_adaptations:         dict[str, Any] = Field(default_factory=dict)
    success_prediction:           str = ""
    risks:                        list[str] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────────────
# 4. CreatorOutput — script, prompts, scene plan
# ────────────────────────────────────────────────────────────────────────────

class ScenePlanItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    scene_number:        int
    visual_description:  str
    voiceover:           str = ""
    on_screen_text:      str = ""
    duration_seconds:    float = 0.0


class CreatorOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    main_hook:           str
    script:              str
    voiceover_script:    str = ""
    caption:             str
    hashtags:            list[str] = Field(default_factory=list)
    cta:                 str = ""
    image_prompt:        str = ""
    video_prompt:        str = ""
    negative_prompt:     str = ""
    scene_plan:          list[ScenePlanItem] = Field(default_factory=list)
    platform_versions:   dict[str, dict[str, Any]] = Field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────────────
# 5. VisualOutput — what was actually rendered
# ────────────────────────────────────────────────────────────────────────────

class VisualOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    reference_images_used:        list[str] = Field(default_factory=list)
    generated_images:             list[str] = Field(default_factory=list)
    generated_videos:             list[str] = Field(default_factory=list)
    image_model_used:             str = ""
    video_model_used:             str = ""
    visual_consistency_notes:     str = ""
    problems:                     list[str] = Field(default_factory=list)
    recommendation:               Literal["approve", "retry", "revise_prompt"] = "approve"


# ────────────────────────────────────────────────────────────────────────────
# 6. ReviewerOutput — post-render quality verdict
# ────────────────────────────────────────────────────────────────────────────

class ReviewerScores(BaseModel):
    model_config = ConfigDict(extra="allow")
    trend_alignment:       float = Field(0, ge=0, le=10)
    script_quality:        float = Field(0, ge=0, le=10)
    caption_quality:       float = Field(0, ge=0, le=10)
    visual_quality:        float = Field(0, ge=0, le=10)
    video_quality:         float = Field(0, ge=0, le=10)
    persona_consistency:   float = Field(0, ge=0, le=10)
    platform_fit:          float = Field(0, ge=0, le=10)
    virality_potential:    float = Field(0, ge=0, le=10)
    clarity:               float = Field(0, ge=0, le=10)
    professional_quality:  float = Field(0, ge=0, le=10)
    script_visual_match:   float = Field(0, ge=0, le=10)


class ReviewerOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    approval_status:       Literal["approve", "revise", "reject"]
    scores:                ReviewerScores = Field(default_factory=ReviewerScores)
    issues:                list[str] = Field(default_factory=list)
    required_revisions:    list[str] = Field(default_factory=list)
    revise_agent:          Literal["creator", "visual", "strategy", "research", "none"] = "none"
    final_comment:         str = ""


# ────────────────────────────────────────────────────────────────────────────
# 7. GuardianOutput — safety & policy verdict
# ────────────────────────────────────────────────────────────────────────────

class PlatformRisk(BaseModel):
    """Per-platform risk note for the platforms we actually publish to."""
    model_config = ConfigDict(extra="allow")
    instagram:        str = ""
    linkedin:         str = ""
    facebook:         str = ""
    youtube_shorts:   str = ""


class GuardianOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    safety_status:                   Literal["safe", "needs_revision", "reject", "manual_review"]
    platform_risk:                   PlatformRisk = Field(default_factory=PlatformRisk)
    ethical_risks:                   list[str] = Field(default_factory=list)
    copyright_risks:                 list[str] = Field(default_factory=list)
    misinformation_risks:            list[str] = Field(default_factory=list)
    policy_issues:                   list[str] = Field(default_factory=list)
    required_changes:                list[str] = Field(default_factory=list)
    revise_agent:                    Literal["creator", "visual", "strategy", "none"] = "none"
    ai_disclosure_recommendation:    str = ""
    final_decision:                  str = ""


# ────────────────────────────────────────────────────────────────────────────
# 8. PublisherOutput — what was published, where, and how
# ────────────────────────────────────────────────────────────────────────────

class PublisherOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    publisher_status:        Literal[
        "published", "scheduled", "failed",
        "manual_approval_required", "skipped", "dry_run"
    ]
    platform:                str = ""
    content_type:            str = ""
    published_url:           str = ""
    scheduled_time:          str = ""
    composio_action_used:    str = ""
    youtube_upload_used:     bool = False
    files_published:         list[str] = Field(default_factory=list)
    caption_published:       str = ""
    errors:                  list[str] = Field(default_factory=list)
    logs_saved_to:           list[str] = Field(default_factory=list)
    next_step:               str = ""


# ────────────────────────────────────────────────────────────────────────────
# 9. WorkflowResult — the wrapper the pipeline returns to n8n
# ────────────────────────────────────────────────────────────────────────────

class WorkflowResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    workflow_status:           Literal[
        "completed", "stopped", "failed", "manual_review_required"
    ]
    final_decision:            Literal[
        "approved", "rejected", "needs_revision",
        "manual_review_required", "skipped"
    ]
    reason:                    str = ""
    selected_trend:            dict[str, Any] = Field(default_factory=dict)
    selected_platform:         str = ""
    selected_content_type:     str = ""
    publish_ready:             bool = False
    manual_review_required:    bool = False
    retries_used:              int = 0
    errors:                    list[str] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _strip_fences(text: str) -> str:
    """Pull the JSON block out of a fenced Markdown code block, if present."""
    if not isinstance(text, str):
        return text
    m = _FENCE_RE.search(text)
    return m.group(1).strip() if m else text.strip()


def _first_json_object(text: str) -> str | None:
    """Find the first balanced {...} block in the text."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i + 1]
    return None


def validate_json(model: type[BaseModel], raw_text: str) -> tuple[BaseModel | None, str]:
    """
    Parse `raw_text` (an agent's JSON output, possibly wrapped in prose or
    fenced) and validate against `model`. Returns (parsed, '') on success
    or (None, error_message) on failure. Never raises.
    """
    if not isinstance(raw_text, str):
        return None, f"expected str, got {type(raw_text).__name__}"
    candidate = _strip_fences(raw_text)
    if not candidate.startswith("{"):
        # Try to pluck the first {...} block from the prose
        obj = _first_json_object(candidate)
        if obj is None:
            return None, "no JSON object found in agent output"
        candidate = obj
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"
    try:
        return model.model_validate(data), ""
    except Exception as e:
        return None, f"schema validation error: {e}"
