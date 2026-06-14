"""
Research Agent — Finds ACTUAL trending topics in the locked niche.

Not a random topic picker. The job is to surface what's currently being
discussed in the AI + automation business space — recent product
launches, founder threads, public failures, regulatory shifts, pricing
moves — and pick the ONE most fertile for a 30-second reel + carousel.

INPUTS:
  - Analyzer Agent's verdict (do_similar / do_different / continue)
  - Past topic patterns from autopilot_history.json (to avoid repeats)

TOOLS (cloud-only viral-idea pipeline):
  - fetch_industry_signals(category, days)
      Surfaces >= 8 fresh signals (launches, founder threads, public
      failures, pricing shifts, regulatory news) from the last N days.
  - discover_trending_topic(niche, avoid_topics)
      Brainstorms 10 candidate topics across 5 emotional levers
      (post-mortem, money stunt, contrarian prediction, behind-the-
      scenes, consensus-buster) and picks the strongest.
  - brainstorm_viral_angles(topic, count)
      Takes ONE topic and spins `count` sharply different viral angles
      so the agent can pick the best spin instead of the first idea.
  - fetch_viral_hook_patterns(platform)
      Returns 8-10 PROVEN hook archetypes for the target platform with
      literal opener examples and when_to_use guidance.
  - score_topic_virality(topic)
      Rates a candidate on 4 dimensions (emotional_charge, specificity,
      contrarian_ness, saveability — 0-10 each). Use to pick the
      highest-scoring candidate before locking the final topic.

The local heuristic tools (search_trending_topics_local,
analyze_platform_signals_local) still live in virtuai.tools.local_tools
for manual scripts but are NOT exposed here.

OUTPUT:
  {
    "topic": "one specific anecdote, contrarian, operator-level",
    "why_now": "what makes this topic timely (1-2 sentences)",
    "concrete_anchors": ["named tools / dollar amounts / timeframes the script MUST include"],
    "format_fit": "reel | portrait | carousel | all-three",
    "avoids": ["patterns we've already used that this topic deliberately swerves around"]
  }
"""

from crewai import Agent, LLM

from virtuai.tools.cloud_tools import (
    discover_trending_topic,
    fetch_industry_signals,
    brainstorm_viral_angles,
    fetch_viral_hook_patterns,
    score_topic_virality,
    read_banned_patterns,
    read_lessons,
)


def create_research_agent(llm: LLM) -> Agent:
    return Agent(
        role="Trend Research Specialist",
        goal=(
            "Surface ONE specific, currently-trending topic in the locked "
            "niche (AI + automation in real businesses) that pairs with the "
            "Analyzer's verdict — similar to what just worked, or "
            "structurally different from what just flopped."
        ),
        backstory=(
            "You scout REAL signals, not generic 'AI is changing the world' "
            "topics. Run the 5-step viral-idea funnel every cycle:\n\n"
            "STEP 1 — Read the brief.\n"
            "  - Analyzer's verdict (do_similar / do_different / continue).\n"
            "  - read_banned_patterns() — never touch anything Guardian killed.\n"
            "  - read_lessons() — learnings the Analyzer has logged.\n\n"
            "STEP 2 — Gather raw signal.\n"
            "  TOOL: fetch_industry_signals(category='AI and automation in\n"
            "         business', days=7)\n"
            "  Returns >= 8 fresh signals (product launches, founder threads,\n"
            "  public failures, pricing changes, regulatory news, viral demos,\n"
            "  hiring/layoff patterns). This is your raw material.\n\n"
            "STEP 3 — Brainstorm candidates.\n"
            "  TOOL: discover_trending_topic(niche, avoid_topics)\n"
            "  Generates 10 candidates spread across 5 emotional levers\n"
            "  (post-mortem, money stunt, contrarian prediction, behind-the-\n"
            "  scenes, consensus-buster). PASS the Analyzer's avoid-list +\n"
            "  banned patterns as `avoid_topics` (pipe-separated).\n"
            "  Verdict logic:\n"
            "    'do_similar'  → keep the same emotional lever as the last\n"
            "                    winner (e.g. another money stunt).\n"
            "    'do_different' → switch lever entirely (e.g. post-mortem →\n"
            "                    contrarian prediction).\n"
            "    'continue'    → pick the highest-scoring candidate, free.\n\n"
            "STEP 4 — Spin angles + pick the strongest spin.\n"
            "  TOOL: brainstorm_viral_angles(topic=<top candidate>, count=6)\n"
            "  Returns 6 different angles on that ONE topic. Pick the angle\n"
            "  whose hook_line is the most specific (real $, real tool, real\n"
            "  role named in the first sentence).\n\n"
            "STEP 5 — Validate before locking.\n"
            "  TOOL: score_topic_virality(topic=<final candidate>)\n"
            "  Must score >= 29/40. If lower, apply the suggested fixes and\n"
            "  re-score before passing to Strategy.\n"
            "  TOOL: fetch_viral_hook_patterns(platform='instagram_reels')\n"
            "  Pull the hook archetype library. The hook_line you ship MUST\n"
            "  pattern-match at least one archetype.\n\n"
            "MANDATORY ANCHORS in your final output JSON:\n"
            "  - Specific dollar amount (e.g. $4,200, $48/month, $1.1M)\n"
            "  - Named real tool (Claude, Zapier, Notion, Apollo, Cursor…)\n"
            "  - Specific role or person (my CFO, three contractors, junior dev)\n"
            "  - Specific timeframe (last Tuesday, six weeks, one weekend)\n"
            "  - Virality score >= 29 with breakdown\n\n"
            "BANNED topic patterns: generic 'productivity hacks', 'morning\n"
            "routines', 'future of AI', 'leverage your X', anything that\n"
            "doesn't sound like an operator who shipped something this month."
        ),
        llm=llm,
        tools=[
            fetch_industry_signals,
            discover_trending_topic,
            brainstorm_viral_angles,
            fetch_viral_hook_patterns,
            score_topic_virality,
            read_banned_patterns,
            read_lessons,
        ],
        verbose=True,
        allow_delegation=False,
    )
