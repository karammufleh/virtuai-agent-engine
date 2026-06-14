"""
cloud_tools.py — CrewAI @tool wrappers for KIE-cloud-backed capabilities.

These are the tools that drive the Phase 3 production pipeline:
  - Claude Sonnet 4.6 for viral script + carousel + portrait writing
  - Kling 3.0 multi-shot for cinematic native-lipsync video
  - Nano Banana 2 for face-locked scene-edit imagery
  - Suno for instrumental underbeds
  - Slide renderer (PIL) for typography on top of generated backgrounds
  - YouTube Direct + Composio for cross-platform publishing

Counterpart to local_tools.py (which wraps the local Phi/Z-Image/F5-TTS
backend). Agents pull from whichever toolset fits their job.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from crewai.tools import tool

logger = logging.getLogger("virtuai.tools.cloud")


# ══════════════════════════════════════════════════════════════════════════════
# Script / Content Writing Tools (Creator Agent)
# ══════════════════════════════════════════════════════════════════════════════

@tool("write_viral_script")
def write_viral_script(
    topic: str = "",
    n_scenes: int = 6,
    outfit: str = "navy zip-up hoodie",
    mood: str = "",
) -> str:
    """
    Use Claude Sonnet 4.6 to write a 6-beat viral reel script (setup →
    incident → struggle → turn → proof → meaning) in the locked
    AI/automation niche. Returns the full script as JSON.

    Args:
        topic: Optional topic seed. Pass "" to let Claude pick one.
        n_scenes: Number of story beats (3-6, default 6 for full arc).
        outfit: What the on-screen persona wears.
        mood: Optional creative direction (e.g. "contrarian rant",
              "personal regret", "step-by-step").
    """
    from virtuai.tools.script_writer import write_script
    try:
        script = write_script(
            topic=topic or None,
            n_scenes=n_scenes,
            outfit=outfit,
            mood=mood or None,
        )
        return json.dumps(script)
    except Exception as e:
        logger.error(f"write_viral_script failed: {e}")
        return json.dumps({"error": str(e)})


@tool("write_carousel_content")
def write_carousel_content(
    topic: str = "",
    outfit: str = "navy zip-up hoodie",
    mood: str = "",
) -> str:
    """
    Use Claude Sonnet 4.6 to write a 5-slide carousel post (cover →
    problem → insight → proof → payoff) with structured image prompts
    + full IG/LinkedIn captions. Returns JSON.
    """
    from virtuai.tools.image_content_writer import write_carousel
    try:
        content = write_carousel(
            topic=topic or None, outfit=outfit, mood=mood or None,
        )
        return json.dumps(content)
    except Exception as e:
        logger.error(f"write_carousel_content failed: {e}")
        return json.dumps({"error": str(e)})


@tool("write_portrait_content")
def write_portrait_content(
    topic: str = "",
    outfit: str = "navy zip-up hoodie",
    mood: str = "",
) -> str:
    """
    Use Claude Sonnet 4.6 to write a single portrait quote post:
    headline + subhead + image prompt + caption. Returns JSON.
    """
    from virtuai.tools.image_content_writer import write_portrait
    try:
        content = write_portrait(
            topic=topic or None, outfit=outfit, mood=mood or None,
        )
        return json.dumps(content)
    except Exception as e:
        logger.error(f"write_portrait_content failed: {e}")
        return json.dumps({"error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# Video / Image Generation Tools (Visual Agent)
# ══════════════════════════════════════════════════════════════════════════════

@tool("generate_cinematic_reel")
def generate_cinematic_reel(script_json: str) -> str:
    """
    Generate a 30s cinematic reel via Kling 3.0 multi-shot (native audio
    + lipsync) using a script produced by write_viral_script.
    Returns JSON with the final video path.
    """
    from scripts.produce_reel_v16 import (
        upload_to_tmpfiles, kling_render, submit_suno, fetch_suno,
        concat_renders, voice_change_to_liam, post_produce, video_dur,
        CANONICAL_FACE, N_SCENES,
    )
    import concurrent.futures as cf

    try:
        script = json.loads(script_json)
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
        return json.dumps({
            "video_path": str(final),
            "duration_sec": video_dur(final),
            "topic": script.get("topic", ""),
        })
    except Exception as e:
        logger.error(f"generate_cinematic_reel failed: {e}")
        return json.dumps({"error": str(e)})


@tool("render_image_post")
def render_image_post(content_json: str, kind: str = "auto") -> str:
    """
    Generate the actual image(s) for a portrait or carousel post.
    Calls Nano Banana 2 for backgrounds, then PIL slide_renderer for
    typography. Returns JSON with the final image paths.

    Args:
        content_json: JSON produced by write_portrait_content or
                      write_carousel_content.
        kind: "portrait" / "carousel" / "auto" (auto-detect from content).
    """
    from scripts.produce_images import produce_portrait, produce_carousel
    try:
        content = json.loads(content_json)
        t = content.get("type", "")
        if kind == "auto":
            kind = "carousel" if t == "carousel_5" else "portrait"

        outfit = "navy zip-up hoodie"  # caller can override via write_*_content
        if kind == "carousel":
            # The image_content_writer doesn't store outfit on the doc; pass through
            from scripts.produce_images import produce_carousel as _pc
            # Need to call the underlying engine with a pre-written content dict:
            # re-route via a small helper. The simplest path: re-render from
            # content by invoking the inner Nano Banana + render_slide loop.
            return _render_carousel_from_content(content)
        else:
            return _render_portrait_from_content(content)
    except Exception as e:
        logger.error(f"render_image_post failed: {e}")
        return json.dumps({"error": str(e)})


def _render_portrait_from_content(content: dict) -> str:
    import time
    from virtuai.tools.kie_upload import upload as _kie_upload
    from virtuai.tools.slide_renderer import render_portrait_quote
    from scripts.produce_images import gen_persona_bg, CANONICAL_FACE, POSTS_DIR

    run_dir = POSTS_DIR / f"portrait_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)
    canonical_url = _kie_upload(CANONICAL_FACE)
    bg = gen_persona_bg(content["image_prompt"], canonical_url, run_dir / "bg.png")
    final = run_dir / "portrait.png"
    render_portrait_quote(
        bg, headline=content["headline"],
        subhead=content["subhead"], out_path=final,
    )
    return json.dumps({"image": str(final), "run_dir": str(run_dir)})


def _render_carousel_from_content(content: dict) -> str:
    import time
    import concurrent.futures as cf
    from virtuai.tools.kie_upload import upload as _kie_upload
    from virtuai.tools.slide_renderer import render_slide
    from scripts.produce_images import (
        gen_persona_bg, gen_concept_bg, CANONICAL_FACE, POSTS_DIR,
    )

    run_dir = POSTS_DIR / f"carousel_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)
    canonical_url = _kie_upload(CANONICAL_FACE)
    bg_dir = run_dir / "_bg"
    bg_dir.mkdir(exist_ok=True)

    def _gen(slide):
        bg_path = bg_dir / f"slide_{slide['id']}_bg.png"
        if slide.get("uses_persona"):
            return gen_persona_bg(slide["image_prompt"], canonical_url, bg_path)
        return gen_concept_bg(slide["image_prompt"], canonical_url, bg_path)

    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        bg_paths = list(ex.map(_gen, content["slides"]))

    slide_paths = []
    for slide, bg in zip(content["slides"], bg_paths):
        out = run_dir / f"slide_{slide['id']:02d}.png"
        render_slide(bg, headline=slide["headline"], subhead=slide["subhead"],
                     out_path=out, slide_index=slide["id"], total=5)
        slide_paths.append(str(out))

    return json.dumps({"slides": slide_paths, "run_dir": str(run_dir)})


# ══════════════════════════════════════════════════════════════════════════════
# Publishing Tools (Publisher Agent)
# ══════════════════════════════════════════════════════════════════════════════

@tool("publish_reel_to_youtube")
def publish_reel_to_youtube(video_path: str, title: str, description: str,
                             public: bool = True) -> str:
    """Upload an MP4 to YouTube Shorts via OAuth2."""
    from virtuai.tools.youtube_direct import upload_video
    try:
        result = upload_video(
            video_path=video_path,
            title=(title + " #Shorts")[:95] if "#Shorts" not in title else title[:95],
            description="#Shorts\n\n" + description[:4990],
            tags=["AI", "automation", "founder", "shorts"],
            privacy_status="public" if public else "unlisted",
        )
        body = (result.get("data") or {}).get("response_data") or {}
        vid = body.get("id")
        return json.dumps({
            "platform": "youtube",
            "id": vid,
            "url": f"https://youtube.com/shorts/{vid}" if vid else None,
        })
    except Exception as e:
        logger.error(f"publish_reel_to_youtube failed: {e}")
        return json.dumps({"error": str(e)})


@tool("publish_reel_to_instagram")
def publish_reel_to_instagram(video_path: str, caption: str) -> str:
    """Publish a video as an Instagram Reel via Composio."""
    from scripts.publish_v16 import publish_instagram
    try:
        result = publish_instagram(Path(video_path), caption)
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool("publish_image_to_instagram")
def publish_image_to_instagram(image_path: str, caption: str) -> str:
    """Publish a single image post (e.g. carousel cover) to Instagram."""
    from scripts.publish_images import publish_ig_single
    try:
        return json.dumps(publish_ig_single(Path(image_path), caption), default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool("publish_post_to_linkedin")
def publish_post_to_linkedin(text: str, link: str = "",
                              image_path: str = "") -> str:
    """Publish a LinkedIn text post, optionally with a link or cover image."""
    try:
        if image_path:
            from scripts.publish_images import publish_linkedin_with_image
            return json.dumps(
                publish_linkedin_with_image(Path(image_path), text),
                default=str,
            )
        from scripts.publish_v16 import publish_linkedin
        return json.dumps(publish_linkedin(text, link or None), default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# Research Tools (Research Agent)
# ══════════════════════════════════════════════════════════════════════════════

@tool("discover_trending_topic")
def discover_trending_topic(niche: str = "AI + automation in business",
                             avoid_topics: str = "") -> str:
    """
    Use Claude Sonnet 4.6 to brainstorm 10 candidate viral topics in the
    locked niche and recommend the strongest one. Returns JSON with
    candidates + winner.
    """
    import os
    import httpx
    api_key = os.environ.get("KIE_API_KEY", "").strip()
    if not api_key:
        return json.dumps({"error": "KIE_API_KEY not set"})
    avoid_list = [t.strip() for t in avoid_topics.split("|") if t.strip()]
    avoid_block = ""
    if avoid_list:
        avoid_block = "\n\nDO NOT repeat any of these:\n" + "\n".join(
            f"  ✗ {t}" for t in avoid_list
        )

    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 2500,
        "temperature": 0.9,
        "system": (
            f"You scout viral short-form video topics for the niche: {niche}. "
            "Each candidate must be a specific anecdote with a real dollar "
            "amount, real tool, real role, and a contrarian angle. Mix "
            "emotional levers across the 10: at least 2 post-mortems (public "
            "failure), 2 money stunts ($X → $Y transformations), 2 contrarian "
            "predictions, 2 behind-the-scenes peeks, 2 consensus-busters. "
            "Output 10 numbered candidates, then 'WINNER: <number> — "
            "<one-line why>'."
        ),
        "messages": [{"role": "user", "content": "Brainstorm 10 candidates now."
                       + avoid_block}],
    }
    try:
        r = httpx.post("https://api.kie.ai/claude/v1/messages",
                       headers={"Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json"},
                       json=body, timeout=60)
        r.raise_for_status()
        text = "\n".join(c.get("text", "") for c in r.json().get("content", [])
                         if c.get("type") == "text")
        return json.dumps({"raw": text})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _claude_call(system: str, user: str, max_tokens: int = 1500,
                 temperature: float = 0.85, timeout: int = 60) -> str:
    """Shared Claude Sonnet 4.6 call via KIE for the viral-idea tools."""
    import os, httpx
    api_key = os.environ.get("KIE_API_KEY", "").strip()
    if not api_key:
        return json.dumps({"error": "KIE_API_KEY not set"})
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    try:
        r = httpx.post("https://api.kie.ai/claude/v1/messages",
                       headers={"Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json"},
                       json=body, timeout=timeout)
        r.raise_for_status()
        text = "\n".join(c.get("text", "") for c in r.json().get("content", [])
                         if c.get("type") == "text")
        return json.dumps({"raw": text})
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool("fetch_industry_signals")
def fetch_industry_signals(category: str = "AI and automation in business",
                            days: int = 7) -> str:
    """
    Surface SPECIFIC industry signals from the last N days that the Research
    Agent can mine for topic ideas: AI product launches, founder threads,
    public failures, pricing changes, regulatory news, viral demos,
    hiring/layoff patterns. Returns JSON of >= 8 signals, each tagged with
    a viral_potential score and a suggested_angle.
    """
    return _claude_call(
        system=(
            f"You surface SPECIFIC industry signals from the last {days} days in: "
            f"{category}. Signal categories you watch: product launches, founder "
            "threads, public failures, pricing shifts, regulatory news, viral demos, "
            "hiring/layoff patterns. For each signal output: "
            "{category, headline, what_happened (1-2 sentences), "
            "why_it_could_go_viral, suggested_angle, viral_potential (1-10)}. "
            "Return at least 8 signals as a JSON array."
        ),
        user="Surface the freshest signals now.",
        max_tokens=2200,
        temperature=0.75,
        timeout=90,
    )


@tool("brainstorm_viral_angles")
def brainstorm_viral_angles(topic: str, count: int = 6) -> str:
    """
    Given ONE base topic, generate `count` sharply different viral angles to
    choose from. Each angle picks a different emotional lever — contrarian,
    bold prediction, public post-mortem, behind-the-scenes, money stunt,
    consensus-buster. Returns JSON with hook_line + why_viral per angle.
    """
    return _claude_call(
        system=(
            f"You generate {count} sharply different viral angles for ONE base "
            "topic. Each angle picks a different emotional lever: contrarian, "
            "bold prediction, public post-mortem, behind-the-scenes, money "
            "stunt, consensus-buster. For each angle output: "
            "{angle_type, hook_line (the literal first sentence of the reel — "
            "must be specific, name a dollar amount or tool), why_viral "
            "(1 line on the emotional trigger)}. Output JSON: "
            "{topic, angles: [...]}."
        ),
        user=f"Topic: {topic}\nGenerate {count} angles.",
        max_tokens=1800,
        temperature=0.9,
    )


@tool("fetch_viral_hook_patterns")
def fetch_viral_hook_patterns(platform: str = "instagram_reels") -> str:
    """
    Return 8-10 PROVEN viral hook archetypes for the platform
    (instagram_reels, youtube_shorts, tiktok, linkedin, twitter). Each
    archetype has a name + 2 example opener sentences + when_to_use guidance.
    Use this as a phrasing library — pick an archetype that fits the
    Analyzer's verdict, then pattern-match the candidate hook to it.
    """
    return _claude_call(
        system=(
            f"You return proven viral hook archetypes for {platform} in the "
            "AI / automation operator niche. Output 8-10 archetypes used by "
            "top performers. For each: "
            "{archetype, examples: [2 literal opener sentences], "
            "when_to_use (1 line on the topic shape that fits)}. "
            "Output a JSON array."
        ),
        user="List hook archetypes now.",
        max_tokens=1500,
        temperature=0.55,
    )


@tool("score_topic_virality")
def score_topic_virality(topic: str) -> str:
    """
    Rate a candidate topic on 4 viral dimensions (each 0-10):
      - emotional_charge (surprise, outrage, envy, FOMO)
      - specificity (real $, real tools, real timeframes)
      - contrarian_ness (challenges a consensus belief)
      - saveability (would someone bookmark it?)
    Sum is 0-40. Returns JSON with scores, total, verdict, and 1-line
    fixes if total < 29. Use this to choose between candidates produced
    by discover_trending_topic / brainstorm_viral_angles.
    """
    return _claude_call(
        system=(
            "You score short-form video topics for virality across 4 dimensions, "
            "each 0-10:\n"
            "  emotional_charge — surprise / outrage / envy / FOMO\n"
            "  specificity — concrete $, named tools, real timeframes (vague=low)\n"
            "  contrarian_ness — challenges a consensus belief\n"
            "  saveability — would someone bookmark it for later?\n"
            "Sum the scores (0-40). Verdict thresholds: "
            "<20=weak, 20-28=ok, 29-34=good, 35+=viral. "
            "If total < 29, return 2-3 one-line fixes to push it higher. "
            "Output JSON: {topic, scores: {...}, total, verdict, fixes_if_low: [...]}"
        ),
        user=f"Score this topic: {topic}",
        max_tokens=900,
        temperature=0.3,
        timeout=45,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Analytics Tools (Analyzer Agent)
# ══════════════════════════════════════════════════════════════════════════════

@tool("read_autopilot_history")
def read_autopilot_history(last_n: int = 10) -> str:
    """
    Read the last N runs from the autopilot history log (topic, outfit,
    mood, platform IDs). Used by the Analyzer to summarize what's been
    posted and identify patterns or content gaps.
    """
    from pathlib import Path as _Path
    hist_path = _Path(__file__).resolve().parents[2] / "virtuai/data/autopilot_history.json"
    if not hist_path.exists():
        return json.dumps({"runs": [], "note": "no history yet"})
    try:
        data = json.loads(hist_path.read_text())
        runs = data.get("runs", [])
        return json.dumps({"total": len(runs), "recent": runs[-last_n:]}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# Inter-Agent Messaging (shared inbox + persistent lessons)
# ══════════════════════════════════════════════════════════════════════════════

_AGENT_INBOX = Path(__file__).resolve().parents[2] / "virtuai/data/agent_messages.jsonl"
_BANNED_PATTERNS = Path(__file__).resolve().parents[2] / "virtuai/data/banned_patterns.json"
_LESSONS = Path(__file__).resolve().parents[2] / "virtuai/data/lessons.json"


@tool("send_agent_message")
def send_agent_message(from_agent: str, to_agent: str,
                        subject: str, body: str) -> str:
    """
    Append a message to the shared inter-agent inbox. Used by Reviewer
    and Guardian to send specific REVISE feedback to Creator so it can
    fix the issue on retry.

    Args:
        from_agent: name of the sending agent
        to_agent:   recipient agent name
        subject:    one of {REVISE, BLOCK, INFO}
        body:       the specific feedback / instructions
    """
    import time as _time
    msg = {
        "ts": int(_time.time()),
        "from": from_agent,
        "to": to_agent,
        "subject": subject,
        "body": body,
        "read": False,
    }
    _AGENT_INBOX.parent.mkdir(parents=True, exist_ok=True)
    with open(_AGENT_INBOX, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg) + "\n")
    return json.dumps({"sent": True, "to": to_agent, "subject": subject})


@tool("read_my_messages")
def read_my_messages(agent_name: str, mark_read: bool = True) -> str:
    """
    Read all unread messages addressed to me. The Creator agent calls
    this BEFORE writing to see if any other agent has REVISE feedback
    from a previous attempt.

    Args:
        agent_name: my own agent name
        mark_read:  if True, mark messages as read after returning them
    """
    if not _AGENT_INBOX.exists():
        return json.dumps({"messages": []})
    lines = _AGENT_INBOX.read_text().splitlines()
    msgs = []
    rewrite: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        m = json.loads(line)
        if m.get("to") == agent_name and not m.get("read", False):
            msgs.append(m)
            if mark_read:
                m["read"] = True
        rewrite.append(json.dumps(m))
    if mark_read and msgs:
        _AGENT_INBOX.write_text("\n".join(rewrite) + "\n")
    return json.dumps({"messages": msgs})


@tool("add_banned_pattern")
def add_banned_pattern(pattern: str, reason: str) -> str:
    """
    Add a permanently banned topic / phrase / format pattern. The
    Guardian writes here on BLOCK; Research + Creator read this file
    before producing content so the system never re-attempts something
    that was rejected.
    """
    data = {"patterns": []}
    if _BANNED_PATTERNS.exists():
        try:
            data = json.loads(_BANNED_PATTERNS.read_text())
        except Exception:
            pass
    import time as _time
    data["patterns"].append({
        "ts": int(_time.time()),
        "pattern": pattern,
        "reason": reason,
    })
    _BANNED_PATTERNS.parent.mkdir(parents=True, exist_ok=True)
    _BANNED_PATTERNS.write_text(json.dumps(data, indent=2))
    return json.dumps({"added": True, "total": len(data["patterns"])})


@tool("read_banned_patterns")
def read_banned_patterns() -> str:
    """Read the cumulative banned-patterns list. Used by Research and Creator."""
    if not _BANNED_PATTERNS.exists():
        return json.dumps({"patterns": []})
    try:
        return _BANNED_PATTERNS.read_text()
    except Exception:
        return json.dumps({"patterns": []})


@tool("add_lesson")
def add_lesson(category: str, lesson: str) -> str:
    """
    Record a persistent lesson the system has learned. The Analyzer
    writes here when a post performs notably well or badly so future
    runs can lean toward / away from what worked.
    """
    data = {"lessons": []}
    if _LESSONS.exists():
        try:
            data = json.loads(_LESSONS.read_text())
        except Exception:
            pass
    import time as _time
    data["lessons"].append({
        "ts": int(_time.time()),
        "category": category,
        "lesson": lesson,
    })
    _LESSONS.parent.mkdir(parents=True, exist_ok=True)
    _LESSONS.write_text(json.dumps(data, indent=2))
    return json.dumps({"added": True, "total": len(data["lessons"])})


@tool("read_lessons")
def read_lessons() -> str:
    """Read accumulated lessons. Used by Research + Strategy."""
    if not _LESSONS.exists():
        return json.dumps({"lessons": []})
    try:
        return _LESSONS.read_text()
    except Exception:
        return json.dumps({"lessons": []})


@tool("fetch_instagram_post_metrics")
def fetch_instagram_post_metrics(ig_media_id: str) -> str:
    """
    Fetch engagement metrics (likes, comments, reach, impressions, saves)
    for a previously published Instagram post via Composio's IG insights
    action. Pass the IG media id returned at publish time.
    """
    try:
        from composio import Composio
        from composio_crewai import CrewAIProvider
        import os as _os
        cp = Composio(provider=CrewAIProvider())
        tools = cp.tools.get(
            user_id=_os.environ.get("COMPOSIO_USER_ID", "default"),
            tools=["INSTAGRAM_GET_MEDIA_INSIGHTS", "INSTAGRAM_GET_MEDIA"],
        )
        tool_list = list(tools)
        if not tool_list:
            return json.dumps({"error": "no IG insights tool available"})
        # Try insights first; fall back to basic media
        for t in tool_list:
            try:
                res = t.run(media_id=ig_media_id)
                if res:
                    return json.dumps(res, default=str)
            except Exception:
                continue
        return json.dumps({"error": "no tool accepted the request"})
    except Exception as e:
        return json.dumps({"error": str(e)})
