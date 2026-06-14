"""
Schema validation tests for the 8-agent structured outputs.

Each test feeds the schema a representative example (the kind of JSON the
agent's backstory tells it to emit) and asserts:
  - validate_json succeeds for well-formed input
  - validate_json fails cleanly (returns (None, error)) for bad input
  - the helper survives prose-wrapped or code-fenced JSON

These tests never call an external API.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from virtuai.schemas.agent_outputs import (
    AnalyzerOutput, ResearchOutput, StrategyOutput, CreatorOutput,
    VisualOutput, ReviewerOutput, GuardianOutput, PublisherOutput,
    WorkflowResult, validate_json,
)


# ────────────────────────────────────────────────────────────────────────────
# Happy-path examples — one per agent
# ────────────────────────────────────────────────────────────────────────────

def test_analyzer_valid():
    raw = """
    {
      "historical_data_available": true,
      "recent_performance_summary": "Last reel saved 4x median.",
      "best_platforms": ["instagram"],
      "best_content_types": ["reel"],
      "successful_hooks": ["I built a 4-agent team for $48/month."],
      "weaknesses": [],
      "recommendations_for_next_post": ["Same money-stunt lever, different tool"],
      "strategy_adjustments": {"format": "reel"},
      "verdict": "positive",
      "next_direction": "do_similar",
      "recommendation": "Ship another money-stunt reel with a different tool."
    }
    """
    parsed, err = validate_json(AnalyzerOutput, raw)
    assert err == ""
    assert parsed.verdict == "positive"
    assert parsed.historical_data_available is True


def test_research_valid_skip():
    raw = """```json
    {
      "trend_found": false,
      "trend_name": "weak",
      "freshness_score": 2,
      "niche_relevance_score": 8,
      "virality_score": 3,
      "platform_relevance_score": 5,
      "persona_fit_score": 6,
      "risk_score": 4,
      "content_opportunity_score": 3,
      "recommendation": "skip",
      "reason": "no fresh signal in the last 7 days"
    }
    ```"""
    parsed, err = validate_json(ResearchOutput, raw)
    assert err == ""
    assert parsed.should_skip is True
    assert parsed.recommendation == "skip"


def test_research_skip_via_freshness():
    """recommendation='continue' but freshness too low → still skip."""
    raw = """{
      "trend_name": "weak topic", "freshness_score": 2, "niche_relevance_score": 8,
      "virality_score": 9, "platform_relevance_score": 9, "persona_fit_score": 9,
      "risk_score": 3, "content_opportunity_score": 9,
      "recommendation": "continue", "reason": "" }"""
    parsed, err = validate_json(ResearchOutput, raw)
    assert err == ""
    assert parsed.should_skip is True   # freshness gate trips


def test_strategy_valid():
    raw = """{
      "selected_platforms": ["instagram", "linkedin"],
      "primary_platform":   "instagram",
      "content_type":       "reel",
      "recommended_posting_time": "2026-05-19T17:00",
      "reason_for_timing":  "evening peak",
      "content_angle":      "post-mortem of a failed $48/month stack",
      "target_audience":    "early-stage founders",
      "hook_strategy":      "contrarian: 'Most people are using AI the wrong way.'",
      "caption_strategy":   "story → numbers → lesson → CTA",
      "visual_style":       "smart-casual studio portrait",
      "video_style":        "slow dolly + clean cuts",
      "cta":                "Save this for the next AI playbook.",
      "platform_adaptations": {"instagram": {"hashtags": ["#ai"]}},
      "success_prediction": "above median for reels",
      "risks": ["overlap with last week's hook"]
    }"""
    parsed, err = validate_json(StrategyOutput, raw)
    assert err == ""
    assert parsed.primary_platform == "instagram"


def test_creator_valid():
    raw = """{
      "main_hook": "Most people are using AI the wrong way.",
      "script":    "Beat 1...Beat 2...Beat 6.",
      "voiceover_script": "Most people are using AI the wrong way.",
      "caption":   "Most people are using AI the wrong way. ...",
      "hashtags":  ["#ai", "#automation"],
      "cta":       "Save this for later.",
      "image_prompt": "Daniel in studio, navy crewneck, soft daylight.",
      "video_prompt": "Daniel mid-shot, slow push-in, calm delivery.",
      "negative_prompt": "blurry, double face",
      "scene_plan": [
        {"scene_number": 1, "visual_description": "Studio MS",
         "voiceover": "Most people...", "on_screen_text": "Most people are using AI wrong.",
         "duration_seconds": 5}
      ],
      "platform_versions": {"instagram": {"caption": "..."}}
    }"""
    parsed, err = validate_json(CreatorOutput, raw)
    assert err == ""
    assert len(parsed.scene_plan) == 1


def test_visual_valid():
    raw = """{
      "reference_images_used": ["virtuai/persona/canonical_daniel.png"],
      "generated_images":      ["/abs/path/portrait.png"],
      "generated_videos":      ["/abs/path/reel.mp4"],
      "image_model_used":      "nano-banana-2",
      "video_model_used":      "kling-3.0/video",
      "visual_consistency_notes": "Face match >= 0.86 vs canonical",
      "problems":              [],
      "recommendation":        "approve"
    }"""
    parsed, err = validate_json(VisualOutput, raw)
    assert err == ""
    assert parsed.recommendation == "approve"


def test_reviewer_valid_approve():
    raw = """{
      "approval_status": "approve",
      "scores": {
        "trend_alignment": 8, "script_quality": 9, "caption_quality": 8,
        "visual_quality": 9, "video_quality": 9, "persona_consistency": 9,
        "platform_fit": 9, "virality_potential": 8, "clarity": 9,
        "professional_quality": 9, "script_visual_match": 9
      },
      "issues": [], "required_revisions": [], "revise_agent": "none",
      "final_comment": "Ships."
    }"""
    parsed, err = validate_json(ReviewerOutput, raw)
    assert err == ""
    assert parsed.approval_status == "approve"


def test_guardian_valid_safe():
    raw = """{
      "safety_status": "safe",
      "platform_risk": {
        "instagram": "low", "linkedin": "low", "facebook": "low", "youtube_shorts": "low"
      },
      "ethical_risks": [], "copyright_risks": [], "misinformation_risks": [],
      "policy_issues": [], "required_changes": [],
      "revise_agent": "none",
      "ai_disclosure_recommendation": "none needed",
      "final_decision": "ship"
    }"""
    parsed, err = validate_json(GuardianOutput, raw)
    assert err == ""
    assert parsed.safety_status == "safe"


def test_publisher_valid_dry_run():
    raw = """{
      "publisher_status": "dry_run",
      "platform": "instagram",
      "content_type": "reel",
      "published_url": "",
      "scheduled_time": "",
      "composio_action_used": "",
      "youtube_upload_used": false,
      "files_published": ["/abs/path/reel.mp4"],
      "caption_published": "...",
      "errors": [],
      "logs_saved_to": ["virtuai/data/logs/composio_dry_run.jsonl"],
      "next_step": "n/a"
    }"""
    parsed, err = validate_json(PublisherOutput, raw)
    assert err == ""
    assert parsed.publisher_status == "dry_run"


def test_workflow_result_valid():
    raw = """{
      "workflow_status": "completed",
      "final_decision":  "approved",
      "reason":          "all gates passed",
      "selected_trend":  {"topic": "..."},
      "selected_platform": "instagram",
      "selected_content_type": "reel",
      "publish_ready":   true,
      "manual_review_required": false,
      "retries_used":    0,
      "errors":          []
    }"""
    parsed, err = validate_json(WorkflowResult, raw)
    assert err == ""
    assert parsed.workflow_status == "completed"


# ────────────────────────────────────────────────────────────────────────────
# Failure modes
# ────────────────────────────────────────────────────────────────────────────

def test_validate_json_handles_prose_wrap():
    raw = "Here's my answer.\n```json\n{\"approval_status\": \"approve\"}\n```\nThanks!"
    parsed, err = validate_json(ReviewerOutput, raw)
    assert err == ""
    assert parsed.approval_status == "approve"


def test_validate_json_rejects_garbage():
    parsed, err = validate_json(ReviewerOutput, "lol no JSON here")
    assert parsed is None
    assert err


def test_validate_json_rejects_wrong_enum():
    raw = '{"approval_status": "maybe"}'
    parsed, err = validate_json(ReviewerOutput, raw)
    assert parsed is None
    assert "validation" in err.lower() or "literal" in err.lower()
