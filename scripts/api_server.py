#!/usr/bin/env python3
"""
api_server.py — FastAPI orchestration layer for n8n (or any HTTP caller).

Exposes the daily content pipeline as HTTP endpoints so workflow tools
like n8n can schedule it, trigger it on demand, and read state.

Endpoints:
  POST /run-pack         — kick off a full daily pack (reel + portrait
                            + carousel + auto-publish). Returns task_id
                            immediately; pack runs in background.
  GET  /status/{task_id} — poll for state + final URLs.
  GET  /history          — last N runs from autopilot_history.json.
  GET  /healthz          — basic liveness probe.

  POST /run-reel         — just produce a reel (no publishing).
  POST /run-portrait     — just produce a portrait.
  POST /run-carousel     — just produce a carousel.

  POST /publish-reel     — publish a previously-produced reel by path.
  POST /publish-portrait — publish a portrait.
  POST /publish-carousel — publish a carousel.

Run:
  uvicorn scripts.api_server:app --host 0.0.0.0 --port 9090
n8n nodes call http://localhost:9090/run-pack and poll /status/{id}.
(Port 9090 to avoid colliding with run_website.py on 8080.)
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# `ROOT` is exposed at module scope so endpoints (e.g. run_agent_sync)
# can reach virtuai/data/agent_messages.jsonl without re-deriving paths.

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("api_server")

app = FastAPI(
    title="VirtuAI Daily Pack API",
    description="HTTP orchestration for the autopilot content pipeline.",
    version="1.0.0",
)


# In-memory task registry. For production, swap for Redis or SQLite.
TASKS: dict[str, dict[str, Any]] = {}


def _new_task(kind: str) -> str:
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {
        "task_id": task_id,
        "kind": kind,
        "state": "queued",
        "created_at": int(time.time()),
        "started_at": None,
        "finished_at": None,
        "error": None,
        "result": None,
    }
    return task_id


def _update_task(task_id: str, **fields):
    if task_id in TASKS:
        TASKS[task_id].update(fields)


# ── Request schemas ─────────────────────────────────────────────────────────

class RunPackRequest(BaseModel):
    """Optional overrides for one-off pack runs.

    NOTE: `publish` and `dry_run` and `no_publish` are honoured by /run-pack.
    Default is `publish=True` so the cron-triggered behavior is unchanged.
    """
    topic_seed: Optional[str] = None
    outfit: Optional[str] = None
    mood: Optional[str] = None
    # 2026-05-20 — explicit publish-safety flags. Any of these set to false /
    # true respectively will skip the publish step entirely.
    publish: bool = True
    dry_run: bool = False
    no_publish: bool = False
    # Unused informational flags — preserved for compatibility with callers
    # (demo.py sends `demo: true` and `kind: "pack"`):
    kind: Optional[str] = None
    persona: Optional[str] = None
    demo: Optional[bool] = None

    def publish_allowed(self) -> bool:
        """Centralised safety gate — returns True only when ALL signals say
        'go live'. Any single 'no' wins."""
        return bool(self.publish) and (not self.dry_run) and (not self.no_publish)


class PublishReelRequest(BaseModel):
    video_master_path: str
    video_ig_path: Optional[str] = None
    script_json: dict
    publish_youtube: bool = True
    publish_instagram: bool = True
    publish_linkedin: bool = True


class PublishImageRequest(BaseModel):
    run_dir: str  # virtuai/data/generated_images/posts/...
    publish_instagram: bool = True
    publish_linkedin: bool = True


class RunCrewRequest(BaseModel):
    """Run the full CrewAI 8-agent crew (the autonomous-agent showcase path).

    Distinct from /run-pack (the deterministic daily_pack orchestrator).
    `build_only=True` builds + validates the crew (backend check, 8 agents,
    tasks) WITHOUT kicking off any LLM/render calls — a zero-cost health probe.
    """
    platforms: Optional[list[str]] = None
    persona: str = "virtuai_mentor"
    llm: str = "kie"            # "kie" (cloud reasoning) or "local" (MLX)
    build_only: bool = False    # True => validate wiring only, no kickoff, no spend


# ── Background runners ──────────────────────────────────────────────────────

def _run_pack(task_id: str, publish: bool = True, overrides: Optional[dict] = None,
              creator_content: Optional[dict] = None):
    """Wraps scripts.daily_pack.main() and records state in TASKS.

    `publish=False` propagates all the way to daily_pack.main() and
    blocks every Composio + YouTube Direct call for this run.
    `overrides` (optional) carries agent-chosen topic/angle so the pack is
    seeded by the agents instead of daily_pack's random rotation.
    `creator_content` (optional) carries the Creator agent's authored
    reel/portrait/carousel content so those pieces are rendered VERBATIM.
    """
    _update_task(task_id, state="running", started_at=int(time.time()))
    if not publish:
        log.warning("NO-PUBLISH MODE: publishing skipped for task %s", task_id)
    if overrides and overrides.get("topic"):
        log.info("AGENT-SEEDED pack for task %s — topic=%r", task_id, overrides["topic"][:80])
    if creator_content and any(creator_content.values()):
        log.info("CREATOR-AUTHORED content for task %s: %s", task_id,
                 {k: bool(v) for k, v in creator_content.items()})
    try:
        # daily_pack writes to autopilot_history.json automatically; we
        # mirror the last record into the task result for easy polling.
        from scripts import daily_pack as dp
        dp.main(publish=publish, overrides=overrides, creator_content=creator_content)
        # Pull the last 3 history entries (one pack = 3 runs)
        hist = dp.load_history()
        last3 = hist["runs"][-3:]
        _update_task(
            task_id, state="success",
            finished_at=int(time.time()),
            result={"pack": last3, "published": publish},
        )
    except Exception as e:
        log.exception("pack failed")
        _update_task(task_id, state="failed",
                     finished_at=int(time.time()), error=str(e))


def _crew_core(req: RunCrewRequest) -> dict:
    """Build the CrewAI crew and (unless build_only) kick it off.

    Returns a JSON-serialisable summary. Shared by the async and sync
    crew endpoints so both behave identically.
    """
    from virtuai.pipelines.content_pipeline import build_content_crew

    crew = build_content_crew(
        target_platforms=req.platforms,
        persona_name=req.persona,
        llm_provider=req.llm,
    )
    agent_count = len(getattr(crew, "agents", []) or [])
    task_count = len(getattr(crew, "tasks", []) or [])

    if req.build_only:
        return {
            "mode": "build_only",
            "ok": True,
            "agents": agent_count,
            "tasks": task_count,
            "persona": req.persona,
            "platforms": req.platforms or "all enabled",
            "note": "Crew built + validated (backend reachable, agents wired). "
                    "No kickoff — zero cost.",
        }

    result = crew.kickoff()

    # Persist like main.py does, so crew runs leave the same artifact trail.
    out_dir = ROOT / "virtuai/data/content_packages"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"crew_run_{ts}.json"
    payload = {
        "timestamp": ts,
        "persona": req.persona,
        "platforms": req.platforms or "all enabled",
        "result": str(result),
    }
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "mode": "kickoff",
        "ok": True,
        "agents": agent_count,
        "tasks": task_count,
        "output_file": str(out_file),
        "result": str(result)[:4000],
    }


def _run_crew(task_id: str, req: RunCrewRequest):
    """Background wrapper around _crew_core with TASKS bookkeeping."""
    _update_task(task_id, state="running", started_at=int(time.time()))
    try:
        summary = _crew_core(req)
        _update_task(task_id, state="success",
                     finished_at=int(time.time()), result=summary)
    except Exception as e:
        log.exception("crew run failed")
        _update_task(task_id, state="failed",
                     finished_at=int(time.time()), error=str(e))


def _run_reel_only(task_id: str, req: RunPackRequest):
    _update_task(task_id, state="running", started_at=int(time.time()))
    try:
        from virtuai.tools.script_writer import write_script
        from scripts.produce_reel_v16 import (
            upload_to_tmpfiles, kling_render, submit_suno, fetch_suno,
            concat_renders, voice_change_to_liam, post_produce, video_dur,
            CANONICAL_FACE, N_SCENES,
        )
        import concurrent.futures as cf

        script = write_script(
            topic=req.topic_seed, n_scenes=6,
            outfit=req.outfit or "navy zip-up hoodie",
            mood=req.mood,
        )
        scenes = script["scenes"][:N_SCENES]
        half = (len(scenes) + 1) // 2
        face_url = upload_to_tmpfiles(CANONICAL_FACE)
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            a = ex.submit(kling_render, scenes[:half], face_url, "A")
            b = ex.submit(kling_render, scenes[half:], face_url, "B")
            s = ex.submit(submit_suno)
            suno_task = s.result()
            render_a = a.result()
            render_b = b.result()
        music = fetch_suno(suno_task) if suno_task else None
        combined = concat_renders(render_a, render_b)
        voice_changed = voice_change_to_liam(combined)
        final = post_produce(voice_changed, music)

        _update_task(task_id, state="success",
                     finished_at=int(time.time()),
                     result={"video_path": str(final),
                              "duration_sec": video_dur(final),
                              "topic": script["topic"],
                              "script": script})
    except Exception as e:
        log.exception("reel-only failed")
        _update_task(task_id, state="failed",
                     finished_at=int(time.time()), error=str(e))


def _run_portrait_only(task_id: str, req: RunPackRequest):
    _update_task(task_id, state="running", started_at=int(time.time()))
    try:
        from scripts.produce_images import produce_portrait
        result = produce_portrait(
            outfit=req.outfit or "navy zip-up hoodie",
            mood=req.mood,
        )
        _update_task(task_id, state="success",
                     finished_at=int(time.time()), result=result)
    except Exception as e:
        log.exception("portrait failed")
        _update_task(task_id, state="failed",
                     finished_at=int(time.time()), error=str(e))


def _run_carousel_only(task_id: str, req: RunPackRequest):
    _update_task(task_id, state="running", started_at=int(time.time()))
    try:
        from scripts.produce_images import produce_carousel
        result = produce_carousel(
            outfit=req.outfit or "navy zip-up hoodie",
            mood=req.mood,
        )
        _update_task(task_id, state="success",
                     finished_at=int(time.time()), result=result)
    except Exception as e:
        log.exception("carousel failed")
        _update_task(task_id, state="failed",
                     finished_at=int(time.time()), error=str(e))


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": int(time.time())}


@app.get("/history")
def history(last_n: int = 10):
    hist_path = ROOT / "virtuai/data/autopilot_history.json"
    if not hist_path.exists():
        return {"runs": [], "total": 0}
    data = json.loads(hist_path.read_text())
    runs = data.get("runs", [])
    return {"total": len(runs), "recent": runs[-last_n:]}


@app.get("/status/{task_id}")
def status(task_id: str):
    if task_id not in TASKS:
        raise HTTPException(404, f"unknown task {task_id}")
    return TASKS[task_id]


@app.get("/tasks")
def tasks(last_n: int = 20):
    """Return all in-memory tasks (newest first)."""
    sorted_tasks = sorted(TASKS.values(), key=lambda t: t["created_at"], reverse=True)
    return {"tasks": sorted_tasks[:last_n]}


@app.post("/run-pack")
def run_pack(bg: BackgroundTasks, req: Optional[RunPackRequest] = None):
    """Full daily pack (reel + portrait + carousel).

    By default publishes to Composio + YouTube Direct (cron-trigger behavior).
    Set `publish=false` / `dry_run=true` / `no_publish=true` in the request
    body to SKIP all publishing — generation still happens, mp4 + PNGs +
    manifest are still saved to disk, but no Composio or YouTube call is made.
    """
    task_id = _new_task("daily_pack")
    # Default — backward compat with old cron callers that send empty body.
    publish_allowed = True
    if req is not None:
        publish_allowed = req.publish_allowed()
        if not publish_allowed:
            log.warning("NO-PUBLISH MODE: /run-pack received publish=%s dry_run=%s "
                        "no_publish=%s — Publisher will NOT be called.",
                        req.publish, req.dry_run, req.no_publish)
    bg.add_task(_run_pack, task_id, publish_allowed)
    return {"task_id": task_id, "state": "queued",
            "publish": publish_allowed,
            "poll": f"/status/{task_id}"}


@app.post("/run-crew")
def run_crew(bg: BackgroundTasks, req: Optional[RunCrewRequest] = None):
    """Run the full CrewAI 8-agent crew in the background. Poll /status/{id}.

    The crew's Publisher agent publishes LIVE when COMPOSIO_API_KEY is set.
    Use `{"build_only": true}` to validate the crew wiring with zero spend.
    """
    req = req or RunCrewRequest()
    task_id = _new_task("crew")
    bg.add_task(_run_crew, task_id, req)
    return {"task_id": task_id, "state": "queued",
            "build_only": req.build_only,
            "poll": f"/status/{task_id}"}


@app.post("/run-crew-sync")
def run_crew_sync(req: Optional[RunCrewRequest] = None):
    """Synchronous CrewAI crew run — blocks until the crew finishes and
    returns the result inline. Long-running on full kickoff; prefer /run-crew
    + polling for production. Kept for single-node n8n callers.
    """
    req = req or RunCrewRequest()
    try:
        return _crew_core(req)
    except Exception as e:
        log.exception("crew sync run failed")
        raise HTTPException(500, f"crew run failed: {e}")


@app.post("/run-reel")
def run_reel(req: RunPackRequest, bg: BackgroundTasks):
    """Produce a reel only (no publish)."""
    task_id = _new_task("reel")
    bg.add_task(_run_reel_only, task_id, req)
    return {"task_id": task_id, "state": "queued",
            "poll": f"/status/{task_id}"}


@app.post("/run-portrait")
def run_portrait(req: RunPackRequest, bg: BackgroundTasks):
    """Produce a portrait only (no publish)."""
    task_id = _new_task("portrait")
    bg.add_task(_run_portrait_only, task_id, req)
    return {"task_id": task_id, "state": "queued",
            "poll": f"/status/{task_id}"}


@app.post("/run-carousel")
def run_carousel(req: RunPackRequest, bg: BackgroundTasks):
    """Produce a carousel only (no publish)."""
    task_id = _new_task("carousel")
    bg.add_task(_run_carousel_only, task_id, req)
    return {"task_id": task_id, "state": "queued",
            "poll": f"/status/{task_id}"}


@app.post("/publish-reel")
def publish_reel_route(req: PublishReelRequest):
    """Publish a previously-produced reel."""
    from scripts.publish_v16 import (
        build_caption, publish_youtube, publish_instagram, publish_linkedin,
    )
    captions = build_caption(req.script_json)
    out: dict[str, Any] = {}
    yt_url = None
    if req.publish_youtube:
        try:
            yt = publish_youtube(Path(req.video_master_path), captions, public=True)
            out["youtube"] = yt
            yt_url = yt.get("url")
        except Exception as e:
            out["youtube"] = {"error": str(e)}
    if req.publish_instagram:
        try:
            ig_path = Path(req.video_ig_path or req.video_master_path)
            out["instagram"] = publish_instagram(ig_path, captions["instagram_caption"])
        except Exception as e:
            out["instagram"] = {"error": str(e)}
    if req.publish_linkedin:
        try:
            out["linkedin"] = publish_linkedin(captions["linkedin_post"], yt_url)
        except Exception as e:
            out["linkedin"] = {"error": str(e)}
    return out


@app.post("/publish-image-post")
def publish_image_post(req: PublishImageRequest):
    """Publish a portrait or carousel from its run directory."""
    run_dir = Path(req.run_dir)
    if not run_dir.exists():
        raise HTTPException(404, f"run_dir not found: {run_dir}")
    content = json.loads((run_dir / "content.json").read_text())
    captions = json.loads((run_dir / "captions.json").read_text())
    out: dict[str, Any] = {}

    from scripts.publish_images import publish_ig_single, publish_linkedin_with_image
    if content["type"] == "carousel_5":
        slides = sorted(run_dir.glob("slide_*.png"))
        slides = [s for s in slides if "_bg" not in s.name]
        cover = slides[0] if slides else None
        if cover is None:
            raise HTTPException(500, "no rendered slides in run_dir")
    else:
        cover = run_dir / "portrait.png"

    if req.publish_instagram:
        try:
            out["instagram"] = publish_ig_single(cover, captions["instagram"])
        except Exception as e:
            out["instagram"] = {"error": str(e)}
    if req.publish_linkedin:
        try:
            out["linkedin"] = publish_linkedin_with_image(cover, captions["linkedin"])
        except Exception as e:
            out["linkedin"] = {"error": str(e)}
    return out


# ── n8n convenience ────────────────────────────────────────────────────────

@app.post("/n8n/trigger-pack")
def n8n_trigger_pack(bg: BackgroundTasks, req: Optional[RunPackRequest] = None):
    """
    Same as /run-pack but with a stable URL for n8n schedule triggers.
    Returns the task_id immediately. Use a wait+poll node on /status.

    Honours the same publish-safety gate as /run-pack: send
    {"publish": false} (or dry_run / no_publish) to render without posting.
    Default stays publish=True for backward-compatible cron callers.
    """
    publish_allowed = req.publish_allowed() if req is not None else True
    if not publish_allowed:
        log.warning("NO-PUBLISH MODE: /n8n/trigger-pack publish gate OFF — "
                    "Publisher will NOT be called.")
    task_id = _new_task("daily_pack_n8n")
    bg.add_task(_run_pack, task_id, publish_allowed)
    return {
        "task_id": task_id,
        "state": "queued",
        "publish": publish_allowed,
        "poll_url": f"/status/{task_id}",
        "expected_runtime_sec": 720,
    }


class RenderPublishRequest(BaseModel):
    """Agent-driven render+publish. The n8n Render node forwards the raw
    agent outputs; we extract the chosen topic/angle and seed daily_pack
    with them so the published pack IS what the agents decided.
    """
    creator_output: Optional[str] = None
    strategy_output: Optional[str] = None
    research_output: Optional[str] = None
    publish: bool = True
    dry_run: bool = False
    no_publish: bool = False

    def publish_allowed(self) -> bool:
        return bool(self.publish) and (not self.dry_run) and (not self.no_publish)


def _coerce_json(blob: Optional[str]) -> dict:
    """Best-effort parse of an agent's text output into a dict.

    Agents emit JSON but may wrap it in ```json fences or prose. Strip
    fences and grab the outermost {...}. Returns {} on any failure.
    """
    if not blob:
        return {}
    import re as _re
    s = blob.strip()
    s = _re.sub(r"^```(?:json)?|```$", "", s, flags=_re.MULTILINE).strip()
    try:
        return json.loads(s)
    except Exception:
        m = _re.search(r"\{.*\}", s, _re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
    return {}


def _extract_overrides(req: "RenderPublishRequest") -> dict:
    """Derive a topic/angle seed from the agents' outputs.

    Preference order for the topic seed: Strategy.content_angle (the
    sharpened topic+angle) -> Creator.main_hook -> Research topic. This is
    what daily_pack expands into reel/portrait/carousel via write_script.
    """
    strat = _coerce_json(req.strategy_output)
    creator = _coerce_json(req.creator_output)
    research = _coerce_json(req.research_output)

    topic = (
        (strat.get("content_angle") or "").strip()
        or (creator.get("main_hook") or creator.get("topic") or "").strip()
        or (research.get("topic") or research.get("chosen_topic") or "").strip()
    )
    angle = (strat.get("hook_strategy") or strat.get("content_angle") or "").strip()
    overrides: dict[str, Any] = {}
    if topic:
        overrides["topic"] = topic[:300]
    if angle and angle != topic:
        overrides["angle"] = angle[:300]
    return overrides


def _creator_content_from(req: "RenderPublishRequest") -> Optional[dict]:
    """Adapt the n8n Creator agent's output into renderer-ready content so the
    pack renders the Creator's authored reel/portrait/carousel VERBATIM.
    Returns None on any failure (the pack then generates content as before)."""
    if not req.creator_output:
        return None
    try:
        from virtuai.agents.creator_adapter import adapt_creator_content
        cc = adapt_creator_content(req.creator_output)
        return cc if any(cc.values()) else None
    except Exception as e:
        log.warning("creator content adapt failed (%s) — will generate instead", e)
        return None


@app.post("/n8n/render-publish")
def n8n_render_publish(bg: BackgroundTasks, req: Optional[RenderPublishRequest] = None):
    """Agent-driven daily pack: render the Creator agent's authored content
    verbatim (any piece it didn't author is generated on the agents' chosen
    topic/angle), then publish. Returns task_id immediately. Honours
    publish/dry_run like /run-pack.
    """
    req = req or RenderPublishRequest()
    overrides = _extract_overrides(req)
    creator_content = _creator_content_from(req)
    publish_allowed = req.publish_allowed()
    task_id = _new_task("daily_pack_agentseeded")
    log.info("AGENT-DRIVEN render+publish queued (publish=%s) overrides=%s creator=%s",
             publish_allowed, {k: (v[:60] if isinstance(v, str) else v) for k, v in overrides.items()},
             {k: bool(v) for k, v in (creator_content or {}).items()})
    bg.add_task(_run_pack, task_id, publish_allowed, overrides, creator_content)
    return {
        "task_id": task_id,
        "state": "queued",
        "publish": publish_allowed,
        "agent_seeded": bool(overrides.get("topic")),
        "topic_seed": overrides.get("topic"),
        "creator_authored": {k: bool(v) for k, v in (creator_content or {}).items()},
        "poll_url": f"/status/{task_id}",
        "expected_runtime_sec": 720,
    }


@app.post("/n8n/render-publish-sync")
def n8n_render_publish_sync(req: Optional[RenderPublishRequest] = None):
    """SYNCHRONOUS agent-driven render+publish. Blocks until the pack is fully
    rendered + published, then returns the live post URLs inline.

    Use this from the n8n Render node with a long HTTP timeout (~15 min) so
    the node itself waits and surfaces the result — avoids n8n's pause/resume
    Wait node (broken in some n8n builds). FastAPI runs this sync handler in a
    worker thread, so /healthz and other routes stay responsive meanwhile.
    """
    req = req or RenderPublishRequest()
    overrides = _extract_overrides(req)
    creator_content = _creator_content_from(req)
    publish_allowed = req.publish_allowed()
    log.info("AGENT-DRIVEN render+publish (SYNC, publish=%s) topic=%r creator=%s",
             publish_allowed, (overrides.get("topic") or "")[:80],
             {k: bool(v) for k, v in (creator_content or {}).items()})
    from scripts import daily_pack as dp
    try:
        dp.main(publish=publish_allowed, overrides=overrides, creator_content=creator_content)
        last3 = dp.load_history()["runs"][-3:]
        return {
            "state": "success",
            "publish": publish_allowed,
            "agent_seeded": bool(overrides.get("topic")),
            "topic_seed": overrides.get("topic"),
            "creator_authored": {k: bool(v) for k, v in (creator_content or {}).items()},
            "pack": last3,
        }
    except Exception as e:
        log.exception("sync render+publish failed")
        raise HTTPException(500, f"render+publish failed: {e}")


# ── Per-agent endpoints (for n8n granular control) ──────────────────────────

class AgentRunRequest(BaseModel):
    """Generic agent invocation. `input` is whatever the agent expects."""
    input: dict = {}


def _instantiate_agents():
    """Build all 8 agents. Agent REASONING runs on DeepSeek via the KIE
    gateway (openai/deepseek-chat); individual content tools call Claude
    Sonnet 4.6 separately. Cached after first call."""
    if hasattr(_instantiate_agents, "_cache"):
        return _instantiate_agents._cache
    from crewai import LLM
    import os as _os
    api_key = _os.environ.get("KIE_API_KEY", "")
    llm = LLM(
        model="openai/deepseek-chat",
        base_url="https://kieai.erweima.ai/api/v1",
        api_key=api_key,
    )
    from virtuai.utils.config_loader import load_persona
    persona = load_persona("virtuai_mentor")
    from virtuai.agents import (
        create_research_agent, create_strategy_agent, create_creator_agent,
        create_visual_agent, create_reviewer_agent, create_guardian_agent,
        create_analyzer_agent, make_publisher,
    )
    agents = {
        "research":  create_research_agent(llm),
        "strategy":  create_strategy_agent(llm),
        "creator":   create_creator_agent(llm, persona),
        "visual":    create_visual_agent(llm, persona),
        "reviewer":  create_reviewer_agent(llm, persona),
        "guardian":  create_guardian_agent(llm, persona),
        "analyzer":  create_analyzer_agent(llm),
        "publisher": make_publisher(llm),
    }
    _instantiate_agents._cache = agents
    return agents


@app.get("/agents")
def list_agents():
    """List all 8 CrewAI agents + the tools each owns."""
    agents = _instantiate_agents()
    return {
        name: {
            "role": getattr(a, "role", name),
            "goal": getattr(a, "goal", "")[:200],
            "tools": [getattr(t, "name", getattr(t, "__name__", "?"))
                      for t in (a.tools or [])],
        }
        for name, a in agents.items()
    }


def _run_agent_task(task_id: str, agent_name: str, prompt: str, context: dict):
    _update_task(task_id, state="running", started_at=int(time.time()))
    try:
        from crewai import Task
        agents = _instantiate_agents()
        if agent_name not in agents:
            raise ValueError(f"unknown agent: {agent_name}")
        agent = agents[agent_name]
        task = Task(
            description=prompt,
            expected_output="Concise, actionable output for the next step in the pipeline.",
            agent=agent,
        )
        result = agent.execute_task(task, context=json.dumps(context, default=str))
        _update_task(task_id, state="success",
                     finished_at=int(time.time()),
                     result={"agent": agent_name, "output": str(result)})
    except Exception as e:
        log.exception(f"agent {agent_name} failed")
        _update_task(task_id, state="failed",
                     finished_at=int(time.time()), error=str(e))


@app.post("/agents/{agent_name}/run-sync")
def run_agent_sync(agent_name: str, req: AgentRunRequest):
    """
    Synchronous agent invocation — blocks until the agent's task is done
    and returns the final output. Use this from n8n so each agent is a
    single HTTP node (no wait + poll loop needed).
    """
    if agent_name not in {"research", "strategy", "creator", "visual",
                          "reviewer", "guardian", "publisher", "analyzer"}:
        raise HTTPException(404, f"unknown agent: {agent_name}")
    from crewai import Task
    inp = req.input or {}
    prompt = inp.get("prompt") or f"Execute your role as {agent_name} agent."
    context = inp.get("context", {})
    try:
        # Auto-inject any unread inter-agent messages addressed to this agent.
        # Reviewer / Guardian write REVISE feedback into the inbox; Creator
        # (and any other recipient) reads it here so the agent's prompt
        # always includes the latest feedback without depending on the
        # agent to remember to call read_my_messages().
        try:
            inbox_path = ROOT / "virtuai/data/agent_messages.jsonl"
            if inbox_path.exists():
                pending = []
                for line in inbox_path.read_text().splitlines():
                    if not line.strip():
                        continue
                    m = json.loads(line)
                    if m.get("to") == agent_name and not m.get("read", False):
                        pending.append(m)
                if pending:
                    context = dict(context)
                    context["unread_messages"] = pending
                    log.info(f"  injected {len(pending)} unread messages for {agent_name}")
        except Exception as e:
            log.warning(f"  inbox inject failed: {e}")

        agents = _instantiate_agents()
        agent = agents[agent_name]
        task = Task(
            description=prompt,
            expected_output="Concise, actionable output for the next step in the pipeline.",
            agent=agent,
        )
        result = agent.execute_task(task, context=json.dumps(context, default=str))
        return {
            "ok": True,
            "agent": agent_name,
            "output": str(result),
            "tools": [getattr(t, "name", getattr(t, "__name__", "?"))
                      for t in (agent.tools or [])],
        }
    except Exception as e:
        log.exception(f"agent {agent_name} (sync) failed")
        return {"ok": False, "agent": agent_name, "error": str(e)}


@app.post("/agents/{agent_name}/run")
def run_agent(agent_name: str, req: AgentRunRequest, bg: BackgroundTasks):
    """
    Invoke a single agent with a free-form prompt + context dict.

    Body:
      {
        "input": {
          "prompt": "Discover a viral topic in AI/automation.",
          "context": { ... any structured input ... }
        }
      }
    """
    if agent_name not in {"research", "strategy", "creator", "visual",
                          "reviewer", "guardian", "publisher", "analyzer"}:
        raise HTTPException(404, f"unknown agent: {agent_name}")
    inp = req.input or {}
    prompt = inp.get("prompt") or f"Execute your role as {agent_name} agent."
    context = inp.get("context", {})
    task_id = _new_task(f"agent:{agent_name}")
    bg.add_task(_run_agent_task, task_id, agent_name, prompt, context)
    return {"task_id": task_id, "state": "queued",
            "poll": f"/status/{task_id}"}


# ── Per-platform endpoints (direct one-shot publishing) ─────────────────────

class PlatformYouTubeRequest(BaseModel):
    video_path: str
    title: str
    description: str
    public: bool = True


class PlatformIGReelRequest(BaseModel):
    video_path: str
    caption: str


class PlatformIGImageRequest(BaseModel):
    image_path: str
    caption: str


class PlatformIGCarouselRequest(BaseModel):
    slide_paths: list[str]
    caption: str


class PlatformLinkedInRequest(BaseModel):
    text: str
    image_path: Optional[str] = None
    link: Optional[str] = None


@app.post("/platforms/youtube/upload")
def platform_youtube(req: PlatformYouTubeRequest):
    """One-shot YouTube upload. Returns the video URL on success."""
    from virtuai.tools.youtube_direct import upload_video
    try:
        result = upload_video(
            video_path=req.video_path,
            title=req.title[:95],
            description=req.description[:4990],
            tags=["AI", "automation", "founder", "shorts"],
            privacy_status="public" if req.public else "unlisted",
        )
        body = (result.get("data") or {}).get("response_data") or {}
        vid = body.get("id")
        return {
            "ok": bool(vid),
            "video_id": vid,
            "url": f"https://youtube.com/shorts/{vid}" if vid else None,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/platforms/instagram/post-reel")
def platform_ig_reel(req: PlatformIGReelRequest):
    from scripts.publish_v16 import publish_instagram
    try:
        return publish_instagram(Path(req.video_path), req.caption)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/platforms/instagram/post-image")
def platform_ig_image(req: PlatformIGImageRequest):
    from scripts.publish_images import publish_ig_single
    try:
        return publish_ig_single(Path(req.image_path), req.caption)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/platforms/instagram/post-carousel")
def platform_ig_carousel(req: PlatformIGCarouselRequest):
    """5-slide swipe carousel via direct Meta Graph API (falls back to cover)."""
    from scripts.publish_images import publish_ig_carousel
    try:
        return publish_ig_carousel(
            [Path(p) for p in req.slide_paths], req.caption,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/platforms/linkedin/post")
def platform_linkedin(req: PlatformLinkedInRequest):
    """LinkedIn post (text + optional image + optional link)."""
    from scripts.publish_v16 import publish_linkedin
    from scripts.publish_images import publish_linkedin_with_image
    try:
        if req.image_path:
            return publish_linkedin_with_image(Path(req.image_path), req.text)
        return publish_linkedin(req.text, req.link)
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── n8n granular schedule endpoints ─────────────────────────────────────────

@app.post("/n8n/run-reel-and-publish")
def n8n_run_reel_and_publish(bg: BackgroundTasks):
    """One-shot: produce a reel, then publish to YouTube + IG + LinkedIn."""
    task_id = _new_task("reel_publish_n8n")

    def _flow(tid):
        _update_task(tid, state="running", started_at=int(time.time()))
        try:
            from virtuai.tools.script_writer import write_script
            from scripts.produce_reel_v16 import (
                upload_to_tmpfiles, kling_render, submit_suno, fetch_suno,
                concat_renders, voice_change_to_liam, post_produce, video_dur,
                CANONICAL_FACE, N_SCENES,
            )
            from scripts.publish_v16 import (
                build_caption, publish_youtube, publish_instagram, publish_linkedin,
            )
            from scripts.daily_pack import ig_optimize
            import concurrent.futures as cf

            script = write_script(topic=None, n_scenes=6)
            scenes = script["scenes"][:N_SCENES]
            half = (len(scenes) + 1) // 2
            face_url = upload_to_tmpfiles(CANONICAL_FACE)
            with cf.ThreadPoolExecutor(max_workers=3) as ex:
                a = ex.submit(kling_render, scenes[:half], face_url, "A")
                b = ex.submit(kling_render, scenes[half:], face_url, "B")
                s = ex.submit(submit_suno)
                suno_task = s.result()
                render_a = a.result()
                render_b = b.result()
            music = fetch_suno(suno_task) if suno_task else None
            combined = concat_renders(render_a, render_b)
            voice_changed = voice_change_to_liam(combined)
            final = post_produce(voice_changed, music)
            ig_video = ig_optimize(final)
            captions = build_caption(script)

            results: dict[str, Any] = {}
            yt_url = None
            try:
                yt = publish_youtube(final, captions, public=True)
                results["youtube"] = yt
                yt_url = yt.get("url")
            except Exception as e:
                results["youtube"] = {"error": str(e)}
            try:
                results["instagram"] = publish_instagram(
                    ig_video, captions["instagram_caption"])
            except Exception as e:
                results["instagram"] = {"error": str(e)}
            try:
                results["linkedin"] = publish_linkedin(
                    captions["linkedin_post"], yt_url)
            except Exception as e:
                results["linkedin"] = {"error": str(e)}

            _update_task(tid, state="success",
                         finished_at=int(time.time()),
                         result={"topic": script["topic"],
                                  "video_master": str(final),
                                  "results": results})
        except Exception as e:
            log.exception("reel+publish failed")
            _update_task(tid, state="failed",
                         finished_at=int(time.time()), error=str(e))

    bg.add_task(_flow, task_id)
    return {"task_id": task_id, "state": "queued",
            "poll_url": f"/status/{task_id}",
            "expected_runtime_sec": 700}


@app.post("/n8n/run-images-and-publish")
def n8n_run_images_and_publish(bg: BackgroundTasks):
    """One-shot: produce portrait + carousel, then publish each to IG + LinkedIn."""
    task_id = _new_task("images_publish_n8n")

    def _flow(tid):
        _update_task(tid, state="running", started_at=int(time.time()))
        try:
            from scripts.produce_images import produce_portrait, produce_carousel
            from scripts.publish_images import (
                publish_ig_single, publish_ig_carousel, publish_linkedin_with_image,
            )
            from scripts.autopilot import OUTFITS, MOODS, pick_fresh, load_history

            hist = load_history()
            recent_out = [r.get("outfit") for r in hist["runs"][-6:]]
            outfit_p = pick_fresh(OUTFITS, recent_out)
            outfit_c = pick_fresh([o for o in OUTFITS if o != outfit_p],
                                  recent_out + [outfit_p])

            portrait = produce_portrait(outfit=outfit_p)
            carousel = produce_carousel(outfit=outfit_c)

            results: dict[str, Any] = {"portrait": {}, "carousel": {}}

            # Portrait → IG + LinkedIn
            try:
                results["portrait"]["instagram"] = publish_ig_single(
                    Path(portrait["image"]), portrait["captions"]["instagram"])
            except Exception as e:
                results["portrait"]["instagram"] = {"error": str(e)}
            try:
                results["portrait"]["linkedin"] = publish_linkedin_with_image(
                    Path(portrait["image"]), portrait["captions"]["linkedin"])
            except Exception as e:
                results["portrait"]["linkedin"] = {"error": str(e)}

            # Carousel → IG (swipe if IG_ACCESS_TOKEN) + LinkedIn
            slide_paths = sorted(Path(carousel["run_dir"]).glob("slide_*.png"))
            slide_paths = [s for s in slide_paths if "_bg" not in s.name]
            try:
                results["carousel"]["instagram"] = publish_ig_carousel(
                    slide_paths, carousel["captions"]["instagram"])
            except Exception as e:
                results["carousel"]["instagram"] = {"error": str(e)}
            try:
                cover = slide_paths[0] if slide_paths else None
                if cover:
                    results["carousel"]["linkedin"] = publish_linkedin_with_image(
                        cover, carousel["captions"]["linkedin"])
            except Exception as e:
                results["carousel"]["linkedin"] = {"error": str(e)}

            _update_task(tid, state="success",
                         finished_at=int(time.time()),
                         result={
                             "portrait_topic": portrait["content"]["topic"],
                             "carousel_topic": carousel["content"]["topic"],
                             "results": results,
                         })
        except Exception as e:
            log.exception("images+publish failed")
            _update_task(tid, state="failed",
                         finished_at=int(time.time()), error=str(e))

    bg.add_task(_flow, task_id)
    return {"task_id": task_id, "state": "queued",
            "poll_url": f"/status/{task_id}",
            "expected_runtime_sec": 300}


# ── LinkedIn amplifier webhook (called by both reel + image flows) ──────────

class LinkedInAmplifyRequest(BaseModel):
    """
    Universal LinkedIn amplifier. Send any caption + optional media:
    every reel and image flow ends with a call here so LinkedIn always
    fires for every published piece.
    """
    text: str
    image_path: Optional[str] = None
    yt_url: Optional[str] = None
    source: Optional[str] = None  # "reel" | "portrait" | "carousel"


# ── Per-model endpoints (direct one-shot calls) ────────────────────────────

class ModelClaudeRequest(BaseModel):
    system: str = ""
    prompt: str
    max_tokens: int = 1500
    temperature: float = 0.7
    model: str = "claude-sonnet-4-6"


class ModelKlingReelRequest(BaseModel):
    """Run Kling 3.0 multi-shot on a Claude-written script JSON."""
    script: dict
    face_url: Optional[str] = None  # if None, uses canonical_daniel.png


class ModelKlingI2VRequest(BaseModel):
    image_url: str
    prompt: str
    duration: int = 5
    mode: str = "pro"


class ModelNanoBananaRequest(BaseModel):
    image_url: str
    prompt: str
    aspect: str = "9:16"


class ModelSunoRequest(BaseModel):
    prompt: str = ("Calm minimalist underscore for a founder reel, "
                   "no vocals, instrumental.")


class ModelTTSRequest(BaseModel):
    text: str
    voice: str = "TX3LPaxmHKxFdv7VOQHJ"  # Liam


class ModelVoiceChangerRequest(BaseModel):
    audio_path: str
    voice_id: str = "TX3LPaxmHKxFdv7VOQHJ"


class ModelWhisperRequest(BaseModel):
    audio_path: str
    model: str = "base"


@app.get("/models")
def list_models():
    """Enumerate every model endpoint + the underlying provider."""
    return {
        "claude":               {"path": "/models/claude/chat",          "provider": "KIE.ai → Anthropic Claude Sonnet 4.6"},
        "kling_reel":           {"path": "/models/kling/reel",            "provider": "KIE.ai → Kling 3.0 multi-shot"},
        "kling_i2v":            {"path": "/models/kling/i2v",             "provider": "KIE.ai → Kling 3.0 image-to-video"},
        "nano_banana":          {"path": "/models/nano-banana/edit",      "provider": "KIE.ai → Google Nano Banana 2"},
        "suno":                 {"path": "/models/suno/music",            "provider": "KIE.ai → Suno V3.5"},
        "elevenlabs_tts":       {"path": "/models/elevenlabs/tts",        "provider": "KIE.ai → ElevenLabs Turbo 2.5"},
        "elevenlabs_changer":   {"path": "/models/elevenlabs/voice-changer", "provider": "ElevenLabs direct → speech-to-speech"},
        "whisper":              {"path": "/models/whisper/captions",      "provider": "OpenAI Whisper (local)"},
        "youtube_upload":       {"path": "/platforms/youtube/upload",     "provider": "YouTube Data API v3"},
        "instagram_post_image": {"path": "/platforms/instagram/post-image", "provider": "Composio + Meta Graph"},
        "instagram_carousel":   {"path": "/platforms/instagram/post-carousel", "provider": "Meta Graph (direct, if IG_ACCESS_TOKEN)"},
        "instagram_reel":       {"path": "/platforms/instagram/post-reel", "provider": "Composio + Meta Graph"},
        "linkedin_post":        {"path": "/platforms/linkedin/post",      "provider": "Composio"},
    }


@app.post("/models/claude/chat")
def model_claude_chat(req: ModelClaudeRequest):
    """Call Claude (Anthropic-compat via KIE) once. Returns text response."""
    import os as _os
    api_key = _os.environ.get("KIE_API_KEY", "")
    try:
        r = httpx.post(
            "https://api.kie.ai/claude/v1/messages",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": req.model, "max_tokens": req.max_tokens,
                "temperature": req.temperature,
                "system": req.system,
                "messages": [{"role": "user", "content": req.prompt}],
            },
            timeout=120,
        )
        r.raise_for_status()
        body = r.json()
        text = "\n".join(c.get("text", "") for c in body.get("content", [])
                         if c.get("type") == "text")
        return {"ok": True, "model": req.model, "text": text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/models/kling/reel")
def model_kling_reel(req: ModelKlingReelRequest, bg: BackgroundTasks):
    """Kick off a full Kling 3.0 multi-shot reel render from a script dict."""
    task_id = _new_task("model:kling-reel")

    def _go(tid):
        _update_task(tid, state="running", started_at=int(time.time()))
        try:
            from scripts.produce_reel_v16 import (
                upload_to_tmpfiles, kling_render, submit_suno, fetch_suno,
                concat_renders, voice_change_to_liam, post_produce, video_dur,
                CANONICAL_FACE, N_SCENES,
            )
            import concurrent.futures as cf

            scenes = req.script["scenes"][:N_SCENES]
            half = (len(scenes) + 1) // 2
            face_url = req.face_url or upload_to_tmpfiles(CANONICAL_FACE)
            with cf.ThreadPoolExecutor(max_workers=3) as ex:
                a = ex.submit(kling_render, scenes[:half], face_url, "A")
                b = ex.submit(kling_render, scenes[half:], face_url, "B")
                s = ex.submit(submit_suno)
                suno_task = s.result()
                render_a = a.result()
                render_b = b.result()
            music = fetch_suno(suno_task) if suno_task else None
            combined = concat_renders(render_a, render_b)
            voice_changed = voice_change_to_liam(combined)
            final = post_produce(voice_changed, music)
            _update_task(tid, state="success",
                         finished_at=int(time.time()),
                         result={"video_path": str(final),
                                  "duration_sec": video_dur(final)})
        except Exception as e:
            _update_task(tid, state="failed",
                         finished_at=int(time.time()), error=str(e))
    bg.add_task(_go, task_id)
    return {"task_id": task_id, "state": "queued", "poll_url": f"/status/{task_id}"}


@app.post("/models/kling/i2v")
def model_kling_i2v(req: ModelKlingI2VRequest, bg: BackgroundTasks):
    """Kling 3.0 image-to-video (one clip from one source image)."""
    task_id = _new_task("model:kling-i2v")

    def _go(tid):
        _update_task(tid, state="running", started_at=int(time.time()))
        try:
            from virtuai.tools.kie_kling import generate_video
            result = generate_video(
                prompt=req.prompt,
                image_urls=[req.image_url],
                duration=req.duration,
                mode=req.mode,
                aspect_ratio="9:16",
                sound=False,
            )
            _update_task(tid, state="success",
                         finished_at=int(time.time()),
                         result=result)
        except Exception as e:
            _update_task(tid, state="failed",
                         finished_at=int(time.time()), error=str(e))
    bg.add_task(_go, task_id)
    return {"task_id": task_id, "state": "queued", "poll_url": f"/status/{task_id}"}


@app.post("/models/nano-banana/edit")
def model_nano_banana(req: ModelNanoBananaRequest):
    """One-shot Nano Banana 2 image edit. Returns local PNG path."""
    import os as _os
    api_key = _os.environ.get("KIE_API_KEY", "")
    try:
        # Submit
        r = httpx.post(
            "https://api.kie.ai/api/v1/jobs/createTask",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": "google/nano-banana-edit",
                "input": {
                    "prompt": req.prompt[:1500],
                    "image_urls": [req.image_url],
                    "output_format": "png",
                    "image_size": req.aspect,
                },
            },
            timeout=30,
        )
        r.raise_for_status()
        tid = (r.json().get("data") or {}).get("taskId")
        if not tid:
            return {"ok": False, "error": f"submit failed: {r.text[:200]}"}
        # Poll
        deadline = time.time() + 300
        while time.time() < deadline:
            p = httpx.get(
                "https://api.kie.ai/api/v1/jobs/recordInfo",
                params={"taskId": tid},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            d = p.json().get("data", {})
            state = d.get("state", "")
            if state in ("success", "completed", "succeed"):
                rj = json.loads(d.get("resultJson", "{}"))
                urls = rj.get("resultUrls", [])
                return {"ok": True, "image_url": urls[0] if urls else None}
            if state in ("failed", "error", "fail"):
                return {"ok": False, "error": d.get("failMsg", state)}
            time.sleep(5)
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/models/suno/music")
def model_suno_music(req: ModelSunoRequest):
    """Generate a Suno music track. Returns audio URL when ready."""
    import os as _os
    api_key = _os.environ.get("KIE_API_KEY", "")
    try:
        r = httpx.post(
            "https://api.kie.ai/api/v1/generate",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "prompt": req.prompt,
                "customMode": False, "instrumental": True,
                "model": "V3_5",
                "callBackUrl": "https://example.com/cb",
            },
            timeout=30,
        )
        if r.status_code != 200:
            return {"ok": False, "error": r.text[:200]}
        tid = (r.json().get("data") or {}).get("taskId")
        deadline = time.time() + 600
        while time.time() < deadline:
            p = httpx.get(
                "https://api.kie.ai/api/v1/generate/record-info",
                params={"taskId": tid},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            d = p.json().get("data", {})
            if d.get("status") == "SUCCESS":
                resp_inner = d.get("response", {})
                sd = resp_inner.get("sunoData", []) if isinstance(resp_inner, dict) else []
                if sd:
                    url = sd[0].get("audioUrl") or sd[0].get("streamAudioUrl", "")
                    return {"ok": True, "audio_url": url}
                return {"ok": False, "error": "no audio in response"}
            if d.get("status") in ("FAILED", "ERROR"):
                return {"ok": False, "error": d.get("status")}
            time.sleep(10)
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/models/elevenlabs/tts")
def model_elevenlabs_tts(req: ModelTTSRequest):
    """ElevenLabs TTS via KIE."""
    import os as _os
    api_key = _os.environ.get("KIE_API_KEY", "")
    try:
        r = httpx.post(
            "https://api.kie.ai/api/v1/jobs/createTask",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": "elevenlabs/text-to-speech-turbo-2-5",
                "input": {"text": req.text, "voice": req.voice,
                          "stability": 0.55, "similarity_boost": 0.78,
                          "style": 0.4, "speed": 1.0},
            },
            timeout=30,
        )
        r.raise_for_status()
        tid = (r.json().get("data") or {}).get("taskId")
        deadline = time.time() + 180
        while time.time() < deadline:
            p = httpx.get(
                "https://api.kie.ai/api/v1/jobs/recordInfo",
                params={"taskId": tid},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            d = p.json().get("data", {})
            state = d.get("state", "")
            if state in ("success", "completed", "succeed"):
                rj = json.loads(d.get("resultJson", "{}"))
                urls = rj.get("resultUrls", [])
                return {"ok": True, "audio_url": urls[0] if urls else None}
            if state in ("failed", "error", "fail"):
                return {"ok": False, "error": d.get("failMsg", state)}
            time.sleep(5)
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/models/elevenlabs/voice-changer")
def model_elevenlabs_voice_changer(req: ModelVoiceChangerRequest):
    """ElevenLabs Speech-to-Speech (voice swap). Requires ELEVENLABS_API_KEY."""
    import os as _os
    key = _os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        return {"ok": False, "error": "ELEVENLABS_API_KEY not set in .env"}
    try:
        url = f"https://api.elevenlabs.io/v1/speech-to-speech/{req.voice_id}"
        out = Path(req.audio_path).with_suffix(".swapped.mp3")
        with open(req.audio_path, "rb") as f:
            r = httpx.post(
                url,
                headers={"xi-api-key": key, "accept": "audio/mpeg"},
                files={"audio": (Path(req.audio_path).name, f, "audio/mpeg")},
                data={"model_id": "eleven_english_sts_v2",
                      "voice_settings": json.dumps({"stability": 0.45,
                                                     "similarity_boost": 0.85,
                                                     "style": 0.4})},
                params={"output_format": "mp3_44100_192"},
                timeout=300,
            )
        if r.status_code != 200:
            return {"ok": False, "error": r.text[:300]}
        out.write_bytes(r.content)
        return {"ok": True, "audio_path": str(out)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/models/whisper/captions")
def model_whisper_captions(req: ModelWhisperRequest):
    """Run local Whisper on an audio file → ASS captions path."""
    try:
        from virtuai.tools.caption_generator import create_captions
        ass = create_captions(audio_path=req.audio_path,
                              whisper_model=req.model,
                              words_per_group=2)
        return {"ok": True, "ass_path": str(ass)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/webhook/linkedin-amplify")
def webhook_linkedin_amplify(req: LinkedInAmplifyRequest):
    """
    Called from both n8n schedules (reel + images) as the final step.
    Guarantees LinkedIn coverage on every piece even if the upstream
    workflow's LinkedIn step gets disabled.
    """
    from scripts.publish_v16 import publish_linkedin
    from scripts.publish_images import publish_linkedin_with_image
    try:
        if req.image_path:
            result = publish_linkedin_with_image(Path(req.image_path), req.text)
        else:
            result = publish_linkedin(req.text, req.yt_url)
        return {"ok": True, "source": req.source, "result": result}
    except Exception as e:
        return {"ok": False, "source": req.source, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9090)
