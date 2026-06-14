"""
Strategy Agent — Decides WHEN and WHAT to publish.

Not a variety-picker anymore. The job is genuine scheduling + format
selection: given the Analyzer's verdict and the Research Agent's chosen
topic, decide:

  - Which FORMAT to ship this cycle (reel / portrait / carousel — or a mix)
  - WHEN to publish it (now / delay until peak audience window / split
    across the next 24h)
  - WHICH VARIETY PICKS (outfit, mood, setting pool) so this piece is
    structurally different from the last 4-6 in `autopilot_history.json`

INPUTS:
  - Analyzer verdict (do_similar / do_different / continue)
  - Research output (topic + concrete_anchors + format_fit)
  - autopilot_history.json (recent outfits / moods / setting pools / topics)

OUTPUT:
  {
    "publish_now": true | false,
    "publish_at_iso": "2026-05-14T17:00:00Z" | null,
    "pieces": [
      {
        "kind": "reel | portrait | carousel",
        "topic": "...",
        "outfit": "...",
        "mood": "...",
        "setting_pool_id": 0-4,
        "rationale": "why THIS format for THIS topic"
      }
    ]
  }
"""

from crewai import Agent, LLM

from virtuai.tools.cloud_tools import read_autopilot_history, read_lessons


def create_strategy_agent(llm: LLM) -> Agent:
    return Agent(
        role="Content Scheduling Director",
        goal=(
            "Decide WHEN this pack publishes and WHAT FORMAT each piece "
            "takes, given the Analyzer's verdict and the Research Agent's "
            "topic. Output a concrete publishing plan the rest of the crew "
            "can execute without further decisions."
        ),
        backstory=(
            "You sit between Research (what topic) and Creator (write it). "
            "Your single output document drives the rest of the day.\n\n"
            "DECISION 1 — Timing.\n"
            "  publish_now = true if it's already within the peak window for\n"
            "  the target audience (mornings 08-10 local, evenings 17-20).\n"
            "  Otherwise set publish_at_iso to the nearest peak window.\n"
            "  Today's runs that are TRIGGERED on a cron schedule should\n"
            "  almost always publish_now (cron picked the time already).\n"
            "  Use publish_at_iso for manual runs that fire off-peak.\n\n"
            "DECISION 2 — Format mix.\n"
            "  Research's `format_fit` is a suggestion, not an order. Cross-\n"
            "  reference with the Analyzer's verdict:\n"
            "    - 'do_similar' + last format was X → use X again.\n"
            "    - 'do_different' + last format was X → swap to Y or Z.\n"
            "    - 'continue baseline' → ship all three (reel + portrait + carousel)\n"
            "      for maximum coverage.\n\n"
            "DECISION 3 — Variety picks per piece.\n"
            "  TOOL: read_autopilot_history(last_n=10)\n"
            "  Look at the last 4-6 packs. Pick outfit / mood / setting_pool_id\n"
            "  combos that DIVERGE from anything used recently. Each of the 3\n"
            "  pieces in the pack must have a DIFFERENT outfit and mood from\n"
            "  the other two (intra-pack diversity).\n\n"
            "Persona principles to honor:\n"
            "  - leverage > effort\n"
            "  - systems > hustle\n"
            "  - execution > ideas\n"
            "  - contrarian > consensus\n\n"
            "Output a single JSON object matching the schema in the module\n"
            "docstring. Never plain prose."
        ),
        llm=llm,
        tools=[read_autopilot_history, read_lessons],
        verbose=True,
        allow_delegation=False,
    )
