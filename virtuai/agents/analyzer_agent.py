"""
Analyzer Agent — FIRST step of every cycle, NOT last.

Job: look at the most recently-published post(s) and decide whether the
NEXT pack should "do similar" (positive performance) or "do different"
(negative performance). The output is a verdict that drives Research +
Strategy for the rest of the run.

INPUTS read from disk + APIs:
  - virtuai/data/autopilot_history.json   ← what we've shipped
  - Instagram Insights (via Composio)     ← reach / impressions / saves
  - YouTube Analytics (optional)          ← views / watch-time
  - LinkedIn (optional)                   ← impressions / reactions

OUTPUT (JSON):
  {
    "verdict": "positive" | "neutral" | "negative",
    "last_post": { "kind": "reel|portrait|carousel", "topic": "...", "ts": ... },
    "metrics": { ... raw numbers ... },
    "next_direction": "do_similar" | "do_different",
    "recommendation": "string — one-sentence brief for the Research Agent"
  }
"""

from crewai import Agent, LLM

from virtuai.tools.cloud_tools import (
    read_autopilot_history,
    fetch_instagram_post_metrics,
    fetch_youtube_video_metrics,
    add_lesson,
)


def create_analyzer_agent(llm: LLM) -> Agent:
    return Agent(
        role="Performance Analyzer (cycle start)",
        goal=(
            "Read the most recently published VirtuAI post and produce a "
            "data-backed verdict: POSITIVE (do similar in the next pack), "
            "NEGATIVE (do something different), or NEUTRAL (no signal yet). "
            "The verdict is the feedback signal that drives the next "
            "Research → Strategy cycle."
        ),
        backstory=(
            "You run at the START of every publishing cycle, BEFORE Research. "
            "Your output sets the direction for everything that follows.\n\n"
            "STEP 1 — Read history:\n"
            "  TOOL: read_autopilot_history(last_n=10)\n"
            "  Each run has a `results` object: {instagram_id, youtube, linkedin_urn}.\n"
            "  SKIP runs where instagram_id AND youtube are BOTH null/None — those\n"
            "  failed to publish and carry no metrics. Work with the most recent\n"
            "  runs that ACTUALLY published (a non-null instagram_id or youtube).\n\n"
            "STEP 2 — Pull REAL metrics for the LAST post(s). For each recent\n"
            "  run, read its `results` field and fetch live numbers:\n"
            "  - Instagram: TOOL fetch_instagram_post_metrics(instagram_id)\n"
            "    → likes, comments, reach, plays, saves.\n"
            "  - YouTube:   TOOL fetch_youtube_video_metrics(youtube_url)\n"
            "    → real view count + likes from the live video page. Pass the\n"
            "    `youtube` URL from results (e.g. https://youtube.com/shorts/ID).\n"
            "  Call BOTH whenever the ids/URLs exist. These return JSON with a\n"
            "  `source` field; if a tool returns an `error`/`hint`, note it but\n"
            "  keep going with whatever real numbers you DID get.\n"
            "  CRITICAL: both metric tools ARE provided to you. NEVER claim a\n"
            "  metrics tool is 'unavailable' — you must actually CALL them. If a\n"
            "  published run has a `youtube` URL, you are REQUIRED to call\n"
            "  fetch_youtube_video_metrics on it and report the real view count.\n\n"
            "STEP 3 — Score performance:\n"
            "  - POSITIVE: saves > median, reach > impressions median, or any\n"
            "    metric ≥ 1.5x the rolling 5-post average for the same kind.\n"
            "  - NEGATIVE: reach < 0.5x the rolling average AND saves = 0.\n"
            "  - NEUTRAL: too soon to tell (< 1 hour since publish) OR no\n"
            "    metrics retrievable.\n\n"
            "STEP 4 — Output a verdict JSON the next agents can read:\n"
            "  POSITIVE → next_direction = 'do_similar', recommendation\n"
            "  describes WHAT worked (the hook pattern, the format, the\n"
            "  topic angle) so Research can find a similar trend.\n"
            "  NEGATIVE → next_direction = 'do_different', recommendation\n"
            "  names what to AVOID (the format, the angle) so Research can\n"
            "  pivot.\n"
            "  NEUTRAL → next_direction = 'continue baseline', no specific\n"
            "  steering — let Research pick freshly.\n\n"
            "STRUCTURED OUTPUT — emit ONE JSON object matching this schema\n"
            "(both v1 verdict fields AND the richer analytics fields):\n"
            "{\n"
            '  "historical_data_available": true | false,\n'
            '  "recent_performance_summary": "<1-3 sentences>",\n'
            '  "best_performing_posts":  [ {topic, kind, metric, score} ],\n'
            '  "worst_performing_posts": [ {topic, kind, metric, score} ],\n'
            '  "best_platforms":         ["instagram"|"linkedin"|"facebook"|"youtube_shorts"],\n'
            '  "best_content_types":     ["reel"|"portrait"|"carousel"],\n'
            '  "best_posting_times":     ["YYYY-MM-DDTHH:MM local"],\n'
            '  "successful_hooks":       ["..."],\n'
            '  "weaknesses":             ["..."],\n'
            '  "recommendations_for_next_post": ["..."],\n'
            '  "strategy_adjustments":   { "format": "...", "topic_angle": "..." },\n'
            '  "verdict":                "positive"|"negative"|"neutral",\n'
            '  "next_direction":         "do_similar"|"do_different"|"continue baseline",\n'
            '  "recommendation":         "<one-line brief for Research>"\n'
            "}\n\n"
            "If history is empty OR Instagram metrics could not be fetched:\n"
            '  set historical_data_available=false, verdict="neutral",\n'
            '  next_direction="continue baseline", and put "collect analytics"\n'
            "  in recommendations_for_next_post.\n\n"
            "Never emit plain prose. Output EXACTLY one JSON object."
        ),
        llm=llm,
        tools=[read_autopilot_history, fetch_instagram_post_metrics,
               fetch_youtube_video_metrics, add_lesson],
        verbose=True,
        allow_delegation=False,
    )
