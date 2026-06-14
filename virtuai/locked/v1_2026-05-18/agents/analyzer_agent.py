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
            "  Get the most recent published runs (reel / portrait / carousel)\n"
            "  with their instagram_id, youtube URL, and LinkedIn URN.\n\n"
            "STEP 2 — Pull metrics for the LAST post:\n"
            "  TOOL: fetch_instagram_post_metrics(ig_media_id)\n"
            "  Get likes, comments, saves, reach, impressions for the most\n"
            "  recent Instagram piece. (If YouTube/LinkedIn metric tools are\n"
            "  available later, use those too.)\n\n"
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
            "Always output a single JSON object — never plain prose."
        ),
        llm=llm,
        tools=[read_autopilot_history, fetch_instagram_post_metrics, add_lesson],
        verbose=True,
        allow_delegation=False,
    )
