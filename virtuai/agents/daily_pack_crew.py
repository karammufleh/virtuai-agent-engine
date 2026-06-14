"""
daily_pack_crew.py — the official 8-agent planning pass for the daily pack.

`run_daily_pack_agents(context)` runs the eight VirtuAI agents in the canonical
order — Analyzer → Research → Strategy → Creator → Visual → Reviewer →
Guardian → Publisher — as a PLANNING crew, and returns a concrete plan that
`scripts/daily_pack.py` uses INSTEAD of its random seed/outfit/mood rotation.

This is a PLANNING pass only: the agents reason over the supplied `context`
and do NOT render, publish, or write any files (their side-effecting tools are
stripped for this pass). Actual rendering + publishing remain daily_pack's job
via `produce_*_track` + `publish_*`. If anything here fails, daily_pack falls
back to `build_deterministic_plan` (the original rotation) and marks the
manifest `agent_mode="fallback"`.

Returned dict shape:
    {
      "reel":     {"topic_seed", "outfit", "mood", "setting_pool_id"},
      "portrait": {"topic_seed", "outfit", "mood"},
      "carousel": {"topic_seed", "outfit", "mood"},
      "agent_trace":     [ {"agent": str, "output": str}, ... ],
      "reviewer_notes":  str | None,
      "guardian_status": str | None,
      "publisher_plan":  str | None,
    }
"""
from __future__ import annotations

import json
import logging
import os
import random
import re

log = logging.getLogger("daily_pack.crew")

# Canonical run order — the official 8-agent pipeline.
AGENT_ORDER = ["analyzer", "research", "strategy", "creator",
               "visual", "reviewer", "guardian", "publisher"]

PLAN_KEYS = ("reel", "portrait", "carousel")


# ── Deterministic plan (fallback + validated base) ───────────────────────────

def _pick(pool: list, recently_used: list):
    """The same fresh-picker daily_pack/autopilot already use."""
    from scripts.autopilot import pick_fresh
    return pick_fresh(list(pool), list(recently_used))


def build_deterministic_plan(context: dict) -> dict:
    """The original daily_pack rotation logic, as a pure function of `context`.

    Guarantees a COMPLETE, VALID plan: 3 distinct outfits + moods + topic
    seeds and a valid `setting_pool_id`. Used both as the FALLBACK (when the
    agent pipeline fails) and as the base the agent plan is normalized against,
    so the returned plan is always well-formed regardless of agent output.
    """
    outfits = list(context.get("outfit_pool") or [])
    moods_by = context.get("mood_pools_by_kind") or {}
    mood_all = list(context.get("mood_pool") or [])
    recent_outfits = list(context.get("recent_outfits") or [])
    recent_moods = list(context.get("recent_moods") or [])
    recent_pools = list(context.get("recent_pools") or [])
    setting_pools = list(context.get("setting_pools") or [0])
    seeds = list(context.get("available_seeds") or [])

    def moods(kind: str) -> list:
        return list(moods_by.get(kind) or mood_all or ["operator voice"])

    outfit_reel = _pick(outfits or ["dark polo shirt"], recent_outfits)
    outfit_portrait = _pick([o for o in outfits if o != outfit_reel] or [outfit_reel],
                            recent_outfits + [outfit_reel])
    outfit_carousel = _pick([o for o in outfits if o not in (outfit_reel, outfit_portrait)]
                            or [outfit_reel], recent_outfits + [outfit_reel, outfit_portrait])

    mood_reel = _pick(moods("reel"), recent_moods)
    mood_portrait = _pick([m for m in moods("portrait") if m != mood_reel] or [mood_reel],
                          recent_moods + [mood_reel])
    mood_carousel = _pick([m for m in moods("carousel") if m not in (mood_reel, mood_portrait)]
                          or [mood_reel], recent_moods + [mood_reel, mood_portrait])

    pool_idx = next((i for i in setting_pools if i not in recent_pools),
                    setting_pools[0] if setting_pools else 0)

    if len(seeds) < 3:
        seeds = list(context.get("available_seeds") or seeds) or ["AI + automation in business"]
    ordered = seeds[:]
    random.shuffle(ordered)
    while len(ordered) < 3:
        ordered += seeds or ["AI + automation in business"]
    seed_reel, seed_portrait, seed_carousel = ordered[:3]

    return {
        "reel": {"topic_seed": seed_reel, "outfit": outfit_reel,
                 "mood": mood_reel, "setting_pool_id": pool_idx},
        "portrait": {"topic_seed": seed_portrait, "outfit": outfit_portrait, "mood": mood_portrait},
        "carousel": {"topic_seed": seed_carousel, "outfit": outfit_carousel, "mood": mood_carousel},
        "agent_trace": None,
        "reviewer_notes": None,
        "guardian_status": None,
        "publisher_plan": None,
    }


# ── 8-agent planning pass ────────────────────────────────────────────────────

def _build_llm():
    from crewai import LLM
    return LLM(
        model="openai/deepseek-chat",
        base_url="https://kieai.erweima.ai/api/v1",
        api_key=os.environ.get("KIE_API_KEY", ""),
    )


def _strip_tools(agent):
    """Remove every tool — this is a planning pass, so the agents must not
    render, publish, or write files. They reason purely over the prompt."""
    for setter in (lambda: setattr(agent, "tools", []),
                   lambda: object.__setattr__(agent, "tools", [])):
        try:
            setter()
            break
        except Exception:
            continue
    return agent


def _build_agents(llm, persona) -> dict:
    from virtuai.agents import (
        create_analyzer_agent, create_research_agent, create_strategy_agent,
        create_creator_agent, create_visual_agent, create_reviewer_agent,
        create_guardian_agent, make_publisher,
    )
    agents = {
        "analyzer": create_analyzer_agent(llm),
        "research": create_research_agent(llm),
        "strategy": create_strategy_agent(llm),
        "creator": create_creator_agent(llm, persona),
        "visual": create_visual_agent(llm, persona),
        "reviewer": create_reviewer_agent(llm, persona),
        "guardian": create_guardian_agent(llm, persona),
    }
    try:
        agents["publisher"] = make_publisher(llm)
    except Exception as e:  # make_publisher can hit Composio at construction
        from crewai import Agent
        log.warning(f"make_publisher unavailable in planning pass ({e}); using a lightweight stand-in")
        agents["publisher"] = Agent(
            role="Content Publisher (planning)",
            goal="Propose a publishing plan (platforms + order) for the pack. Do not publish.",
            backstory="You plan distribution only. Output a short publishing plan.",
            llm=llm, verbose=False, allow_delegation=False,
        )
    return {k: _strip_tools(a) for k, a in agents.items()}


def _ctx_block(context: dict) -> str:
    """Compact context the planning tasks reason over."""
    def short(xs, n=8):
        return [str(x)[:80] for x in (xs or [])[:n]]
    return json.dumps({
        "publish_mode": "live" if context.get("publish") else "no-publish",
        "suggested_topic": context.get("suggested_topic") or "",
        "suggested_angle": context.get("suggested_angle") or "",
        "available_topic_seeds": short(context.get("available_seeds"), 14),
        "outfit_pool": short(context.get("outfit_pool"), 18),
        "mood_pool": short(context.get("mood_pool"), 14),
        "setting_pool_ids": context.get("setting_pools") or [],
        "recent_topics": short(context.get("recent_topics")),
        "recent_seeds": short(context.get("recent_seeds")),
        "recent_outfits": short(context.get("recent_outfits")),
        "recent_moods": short(context.get("recent_moods")),
        "recent_hooks": short(context.get("recent_hooks")),
    }, ensure_ascii=False)[:3500]


def _build_tasks(agents: dict, context: dict):
    from crewai import Task
    ctx = _ctx_block(context)
    base = (f"PLANNING PASS — reason only, do NOT render, publish, or call tools.\n"
            f"PACK CONTEXT (json):\n{ctx}\n\n")

    specs = {
        "analyzer": "Analyze recent performance/history in the context and output a one-paragraph verdict (positive/negative/neutral → do_similar/do_different) to steer this pack.",
        "research": "Using the analyzer verdict + context, pick the core trending AI/automation-in-business topic theme for today's pack. Honour `suggested_topic` if present. One paragraph.",
        "strategy": ("Produce the concrete 3-PIECE PLAN. For reel, portrait, and carousel choose a DISTINCT "
                     "topic_seed, outfit (from outfit_pool), and mood (from mood_pool), plus a setting_pool_id "
                     "(from setting_pool_ids) for the reel — all DIVERGING from the recent_* lists. "
                     "Output ONLY this JSON and nothing else:\n"
                     '{\n  "reel": {"topic_seed":"...","outfit":"...","mood":"...","setting_pool_id":0},\n'
                     '  "portrait": {"topic_seed":"...","outfit":"...","mood":"..."},\n'
                     '  "carousel": {"topic_seed":"...","outfit":"...","mood":"..."}\n}'),
        "creator": "Briefly confirm the 3-piece plan is writable (a strong hook exists for each format). One short paragraph; do not write the full scripts.",
        "visual": "Briefly note the visual direction (setting/framing) that fits the plan. One short paragraph; do NOT render anything.",
        "reviewer": "Review the plan for quality/variety/concreteness. Output PASS or REVISE plus 1-2 line notes.",
        "guardian": "Ethics/policy/persona check on the planned topics. Output the first line exactly as VERDICT=APPROVE or VERDICT=BLOCK, then a one-line reason.",
        "publisher": "Output a short publishing plan: which platforms each piece targets (reel → instagram+youtube_shorts, images → instagram) and the order. Do NOT publish.",
    }
    tasks = []
    prev = None
    for name in AGENT_ORDER:
        t = Task(
            description=base + specs[name],
            expected_output="Concise output as instructed (JSON for strategy).",
            agent=agents[name],
            context=[prev] if prev is not None else None,
        )
        tasks.append(t)
        prev = t
    return tasks


def _collect_trace(crew_output) -> list:
    outs = getattr(crew_output, "tasks_output", None) or []
    trace = []
    for name, to in zip(AGENT_ORDER, outs):
        raw = getattr(to, "raw", None)
        if raw is None:
            raw = str(to)
        trace.append({"agent": name, "output": str(raw)[:2000]})
    return trace


def _agent_output(trace: list, name: str):
    for t in trace:
        if t.get("agent") == name:
            return t.get("output")
    return None


def _coerce_json(blob) -> dict:
    if not blob:
        return {}
    s = re.sub(r"^```(?:json)?|```$", "", str(blob).strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
    return {}


def _merge_agent_plan(base: dict, raw: dict, context: dict) -> dict:
    """Override the deterministic base with the agents' picks where valid.
    outfit/mood must be pool members; topic_seed may be any non-empty agent
    choice (the agents steer the content); setting_pool_id must be valid."""
    outfits = set(context.get("outfit_pool") or [])
    moods = set(context.get("mood_pool") or [])
    pools = set(context.get("setting_pools") or [])
    for piece in PLAN_KEYS:
        ap = (raw or {}).get(piece) or {}
        if isinstance(ap.get("outfit"), str) and ap["outfit"] in outfits:
            base[piece]["outfit"] = ap["outfit"]
        if isinstance(ap.get("mood"), str) and ap["mood"] in moods:
            base[piece]["mood"] = ap["mood"]
        ts = ap.get("topic_seed")
        if isinstance(ts, str) and ts.strip():
            base[piece]["topic_seed"] = ts.strip()
        if piece == "reel":
            sp = ap.get("setting_pool_id")
            if isinstance(sp, bool):
                sp = None
            if isinstance(sp, int) and sp in pools:
                base["reel"]["setting_pool_id"] = sp
    return base


def run_daily_pack_agents(context: dict) -> dict:
    """Run the official 8-agent pipeline (planning pass) and return a concrete
    pack plan + traces. See module docstring for the returned shape. Raises on
    a hard failure (no LLM key, crew error) so daily_pack can fall back."""
    from crewai import Crew, Process
    from virtuai.utils.config_loader import load_persona

    if not os.environ.get("KIE_API_KEY", "").strip():
        raise RuntimeError("KIE_API_KEY missing — cannot run the 8-agent planner")

    llm = _build_llm()
    persona = load_persona("virtuai_mentor")
    agents = _build_agents(llm, persona)
    tasks = _build_tasks(agents, context)

    log.info("Running official 8-agent planning pipeline: %s", " → ".join(AGENT_ORDER))
    crew = Crew(agents=[agents[n] for n in AGENT_ORDER], tasks=tasks,
                process=Process.sequential, verbose=False)
    result = crew.kickoff()

    trace = _collect_trace(result)
    plan = build_deterministic_plan(context)               # complete + valid base
    plan = _merge_agent_plan(plan, _coerce_json(_agent_output(trace, "strategy")), context)
    plan["agent_trace"] = trace
    plan["reviewer_notes"] = _agent_output(trace, "reviewer")
    plan["guardian_status"] = _agent_output(trace, "guardian")
    plan["publisher_plan"] = _agent_output(trace, "publisher")
    return plan
