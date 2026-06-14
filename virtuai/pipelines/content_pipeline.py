"""
VirtuAI Content Pipeline — End-to-end content generation workflow.
Powered by local fine-tuned Phi-3.5-mini + LLaVA via the VirtuAI backend.

Pipeline order:
  1. Research Agent  → discovers trending topics
  2. Strategy Agent  → selects topics, maps to platforms/formats
  3. Creator Agent   → generates text content per platform
  4. Visual Agent    → generates images/visual descriptions
  5. Reviewer Agent  → checks quality, persona consistency, formatting
  6. Guardian Agent  → safety/ethics/policy gate (APPROVE / REVISE / BLOCK)
  7. Publisher Agent → publishes APPROVE'd items via Composio (LinkedIn, X, IG, ...)
  8. Analyzer Agent  → builds analytics plan + tracks future engagement

LLM architecture:
  - Agent reasoning: local Phi-3.5-mini (fine-tuned) via OpenAI-compatible endpoint
  - Content tools:   local VLM backend (generate, analyze-image, safety-check)
  - Vision tools:    LLaVA 1.5 7B via mlx-vlm
  - Publishing:      Composio hosted SDK (live if COMPOSIO_API_KEY set, dry-run else)

Requires the backend to be running first:
    python run_backend.py
"""

import os
import httpx
from crewai import Crew, Task, Process, LLM
from dotenv import load_dotenv

load_dotenv()

from virtuai.agents import (
    create_research_agent,
    create_strategy_agent,
    create_creator_agent,
    create_visual_agent,
    create_reviewer_agent,
    create_guardian_agent,
    create_analyzer_agent,
    make_publisher,
)
from virtuai.tools.composio_tools import is_configured as _composio_configured
from virtuai.utils.config_loader import load_persona, load_all_platforms

BACKEND_URL = "http://localhost:8765"


def _check_backend():
    """Verify the local VLM backend is running before starting the pipeline."""
    try:
        r = httpx.get(f"{BACKEND_URL}/health", timeout=5)
        info = r.json()
        print(f"✓ Backend connected — text: {info.get('text_model', 'unknown')} | "
              f"vision: {'enabled' if info.get('vision_capable') else 'disabled'}")
        return True
    except Exception:
        print("\n" + "="*60)
        print("  ERROR: VirtuAI backend is not running!")
        print("  Start it first in another terminal:")
        print("    python run_backend.py")
        print("="*60 + "\n")
        return False


def _create_local_llm() -> LLM:
    """
    Create LLM using local fine-tuned Phi-3.5-mini (fused LoRA).
    Points CrewAI at the OpenAI-compatible endpoint in our FastAPI backend.
    No external API key needed — everything runs locally.
    """
    return LLM(
        model="openai/phi-3.5-mini",
        base_url=f"{BACKEND_URL}/v1",
        api_key="local",          # Required field but ignored by our backend
        max_retries=3,
        timeout=180,              # Local inference can be slow — give it time
    )


def _create_kie_llm() -> LLM:
    """
    Create LLM using KIE.ai's DeepSeek endpoint (OpenAI-compatible).
    No daily quota limit. Uses the same KIE_API_KEY as video generation.
    """
    api_key = os.environ.get("KIE_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "KIE_API_KEY not set in .env. Get one from https://kie.ai"
        )
    return LLM(
        model="openai/deepseek-chat",
        base_url="https://kieai.erweima.ai/api/v1",
        api_key=api_key,
        max_retries=3,
        timeout=120,
    )


def build_content_crew(
    target_platforms: list[str] | None = None,
    persona_name: str = "virtuai_mentor",
    llm_provider: str = "local",
) -> Crew:
    """
    Build the full VirtuAI content generation crew.

    Args:
        target_platforms: List of platform IDs to generate for.
                         Defaults to all enabled platforms.
        persona_name: Persona config file name (without .yaml).
        llm_provider: "kie" (default — KIE.ai DeepSeek for agent reasoning)
                      or "local" (Phi-3.5-mini via local backend).
                      Both modes use local backend tools for generation.
    """
    # Load configs
    persona = load_persona(persona_name)
    all_platforms = load_all_platforms()

    if target_platforms:
        platforms = {k: v for k, v in all_platforms.items() if k in target_platforms}
    else:
        platforms = all_platforms

    platform_names = ", ".join(platforms.keys())

    # The Phase-1 local VLM backend is OPTIONAL. The final cloud workflow runs the
    # daily pack through scripts/daily_pack.py (FastAPI server + n8n) and uses the
    # KIE.ai gateway for all generation — it does not need this backend. This legacy
    # CLI pipeline still probes it, but degrades to cloud-only instead of aborting.
    if not _check_backend():
        print("\n⚠ Local VLM backend (:8765) not reachable — continuing in CLOUD-ONLY mode. "
              "For the production pack use the FastAPI server + n8n (see README Quickstart), "
              "which never needs the local backend.\n")

    # Create shared LLM
    if llm_provider == "kie":
        llm = _create_kie_llm()
        print(f"✓ Agent LLM: KIE.ai DeepSeek — generation tools use the KIE.ai cloud gateway")
    else:
        llm = _create_local_llm()
        print(f"✓ Agent LLM: local Phi-3.5-mini (fine-tuned LoRA)")

    # Create agents (all share the same local fine-tuned Phi-3.5-mini LLM)
    research_agent = create_research_agent(llm)
    strategy_agent = create_strategy_agent(llm)
    creator_agent = create_creator_agent(llm, persona)
    visual_agent = create_visual_agent(llm, persona)
    reviewer_agent = create_reviewer_agent(llm, persona)
    guardian_agent = create_guardian_agent(llm, persona)
    analyzer_agent = create_analyzer_agent(llm)
    # Publisher auto-detects mode: live if COMPOSIO_API_KEY is set, else dry-run.
    # Pre-fetches LinkedIn URN and bakes it into the agent's backstory.
    publisher_agent = make_publisher(llm)
    publisher_mode = "LIVE" if _composio_configured() else "DRY-RUN"
    print(f"✓ Publisher mode: {publisher_mode}")

    # ── Define Tasks ─────────────────────────────────────────

    task_research = Task(
        description=(
            f"Research trending topics in these niches: {', '.join(persona.get('niche', []))}.\n"
            f"Target platforms: {platform_names}.\n"
            f"Content pillars to focus on: "
            f"{', '.join(p['name'] for p in persona.get('content_pillars', []))}.\n\n"
            "Deliver a research brief with:\n"
            "1. Top 5 trending topics with relevance scores\n"
            "2. Platform signals — what content types are performing well\n"
            "3. Recommended angles for each topic"
        ),
        expected_output=(
            "A structured research brief in JSON format containing trending topics, "
            "platform signals, and recommended content angles."
        ),
        agent=research_agent,
    )

    task_strategy = Task(
        description=(
            "Based on the research brief, create a content strategy:\n"
            "1. Select the TOP 1-2 topics with highest engagement potential\n"
            "2. For each selected topic, define:\n"
            "   - Content objective (educate, inspire, provoke, etc.)\n"
            "   - Target platforms and the specific format for EACH platform\n"
            f"   - Available platforms: {platform_names}\n"
            "3. Map content formats:\n"
            "   - LinkedIn: long-form post or article\n"
            "   - X: tweet or thread (3-7 tweets)\n"
            "   - Instagram: carousel caption or reel script\n"
            "   - TikTok: short video script (15-60s)\n"
            "   - YouTube Shorts: short video script (30-60s)\n"
            "   - Medium: long-form article (800-2000 words)\n"
            "4. Specify the key message and angle for each platform"
        ),
        expected_output=(
            "A content strategy document in JSON format with selected topics, "
            "platform-format mappings, objectives, and key messages for each platform."
        ),
        agent=strategy_agent,
    )

    task_create = Task(
        description=(
            "Generate the actual content for EACH platform based on the strategy.\n"
            "You MUST follow the persona rules exactly:\n"
            f"- Tone: {', '.join(persona.get('voice', {}).get('tone', []))}\n"
            f"- Style: {', '.join(persona.get('voice', {}).get('style', []))}\n"
            "- Every post needs a strong hook as the first line\n"
            "- Every post must end with a CTA\n"
            "- Use power words: "
            f"{', '.join(persona.get('vocabulary', {}).get('power_words', []))}\n"
            "- NEVER use banned phrases\n\n"
            "Generate separate content for EACH platform with proper formatting:\n"
            "- Respect character limits per platform\n"
            "- Include hashtags per platform rules\n"
            "- Adapt tone per platform (sharper on X, professional on LinkedIn, etc.)"
        ),
        expected_output=(
            "A JSON object with platform IDs as keys, each containing the generated "
            "content text, format type, hashtags, and CTA used."
        ),
        agent=creator_agent,
    )

    task_visual = Task(
        description=(
            "Generate ACTUAL visual assets for each platform using local AI models:\n\n"
            "FOR IMAGE PLATFORMS (LinkedIn, X, Instagram, Medium):\n"
            "1. Use the generate_image_local tool with a detailed prompt\n"
            "   - Describe: subject, setting, lighting, colors, mood\n"
            "   - The tool auto-applies the VirtuAI dark/futuristic style\n"
            "   - The tool auto-sizes images for each platform\n"
            "2. After generating, use analyze_image_for_content to review it\n\n"
            "FOR VIDEO PLATFORMS (TikTok, Instagram Reels, YouTube Shorts):\n"
            "1. Use generate_talking_head_local with a spoken script\n"
            "   - Script must start with a viral hook (first 3 seconds)\n"
            "   - End with exactly one CTA\n"
            "2. After the video is generated, run generate_captions on it\n"
            "   to produce word-by-word ASS subtitle captions\n"
            "3. Then run build_reel with the clip, captions file, output path,\n"
            "   and the hook text (first sentence of the script) to produce\n"
            "   a finished reel with burned-in captions and hook overlay\n\n"
            "Visual style: real-world environments (café, office, terrace), "
            "eye-level medium shot, golden hour or soft window light, shallow DOF. "
            "Camera in front of subject — never selfie POV, never phone in hand."
        ),
        expected_output=(
            "A JSON object mapping each platform to its generated visual assets — "
            "image file paths for image platforms, reel file path for video "
            "platforms (with captions burned in), plus any captions from the "
            "vision model review."
        ),
        agent=visual_agent,
    )

    task_review = Task(
        description=(
            "Run the full PROJECT_STANDARDS.md QA checklist on ALL content.\n\n"
            "FOR EACH IMAGE: use verify_face_identity to check ArcFace score >= 0.70.\n"
            "FOR EACH TEXT: use review_content_quality and analyze_sentiment_local.\n\n"
            "FULL 17-ITEM CHECKLIST:\n"
            "1. Visual: camera in front of subject (no selfie POV)\n"
            "2. Visual: no phone visible in subject's hand\n"
            "3. Visual: real-world environment with depth and props\n"
            "4. Visual: eye-level medium shot, shallow DOF\n"
            "5. Visual: subject matches Daniel (ArcFace >= 0.70)\n"
            "6. Visual: 9:16 aspect ratio for reels\n"
            "7. Audio: Daniel's cloned voice (F5-TTS)\n"
            "8. Audio: lip sync present\n"
            "9. Captions: word-by-word CapCut style\n"
            "10. Captions: synced via Whisper timestamps\n"
            "11. Hook: text overlay in first 3 seconds\n"
            "12. Hook: viral opener pattern (not 'hey everyone')\n"
            "13. Content: 15-30 second duration\n"
            "14. Content: 5-beat structure (hook/problem/insight/proof/CTA)\n"
            "15. Content: specific numbers, named tools, personal experience\n"
            "16. Content: topic passes novelty check\n"
            "17. Emoji: max 2 per post, none in LinkedIn/Medium\n\n"
            "For each platform, provide:\n"
            "- PASS: all checks pass, ready for Guardian\n"
            "- REVISE: specify which items failed and how to fix\n\n"
            "The bar: a stranger scrolling their feed couldn't tell within 5 seconds "
            "that it's AI-generated."
        ),
        expected_output=(
            "A review report in JSON format with PASS/REVISE verdict per platform, "
            "checklist results (17 items), ArcFace scores for images, tone analysis, "
            "and specific revision notes for any failures."
        ),
        agent=reviewer_agent,
    )

    task_guardian = Task(
        description=(
            "FINAL SAFETY GATE — Run comprehensive checks on all content:\n"
            "1. Use content_safety_check tool on EACH platform's content\n"
            "2. Check for ALL forbidden topics (pornography, hate speech, violence, "
            "illegal activities, discrimination, terrorism, self-harm, doxxing)\n"
            "3. Check restricted topics have proper disclaimers\n"
            "4. Verify no false claims or fabricated statistics\n"
            "5. Verify platform-specific policy compliance\n"
            "6. Check for manipulation tactics\n\n"
            "For each platform's content, make a final decision:\n"
            "- APPROVE: safe to publish\n"
            "- REVISE: needs specific safety-related changes\n"
            "- BLOCK: cannot be published (explain why)\n\n"
            "Your output determines what gets published. When in doubt, BLOCK."
        ),
        expected_output=(
            "A safety report in JSON format with APPROVE/REVISE/BLOCK decision "
            "for each platform, safety scores, issues found, and blocking reasons."
        ),
        agent=guardian_agent,
    )

    task_publish = Task(
        description=(
            "Publish the content that Guardian approved. For each platform in the "
            "Guardian report:\n"
            "  - If verdict is APPROVE → call the matching tool to publish.\n"
            "  - If verdict is REVISE  → skip (do not publish, mark as 'held for revision').\n"
            "  - If verdict is BLOCK   → skip (do not publish, mark as 'blocked').\n\n"
            "Tool selection by platform:\n"
            "  - linkedin       → LINKEDIN_CREATE_LINKED_IN_POST (Composio)\n"
            "    body field is 'commentary' (not 'text'); 'author' must be the\n"
            "    LinkedIn URN given in your backstory.\n"
            "  - x / twitter    → TWITTER_CREATION_OF_A_POST (Composio, body field 'text')\n"
            "                     Note: posting requires paid X API tier; calls may fail.\n"
            "  - instagram      → INSTAGRAM_CREATE_MEDIA_CONTAINER then\n"
            "                     INSTAGRAM_CREATE_POST (Composio, two-step).\n"
            "                     Required: ig_user_id (env IG_USER_ID), image_url (public URL),\n"
            "                     caption. Then publish with creation_id.\n"
            "  - facebook       → FACEBOOK_CREATE_POST (Composio).\n"
            "                     Required: message (text), page_id (env FB_PAGE_ID).\n"
            "  - youtube_shorts → YOUTUBE_DIRECT_UPLOAD (DIRECT, NOT Composio)\n"
            "                     args: video_path (local mp4 path), title, description,\n"
            "                     tags (list), privacy_status ('public'/'unlisted'/'private').\n"
            "                     DO NOT use YOUTUBE_UPLOAD_VIDEO — it's intentionally\n"
            "                     absent because it drops a required COPPA field.\n"
            "  - medium         → not supported (platform deprecated public API in 2023)\n\n"
            "If the corresponding tool isn't in your tool list, record that platform "
            "as 'tool_unavailable' and move on — DO NOT fabricate a tool call.\n\n"
            "Return ONE publish report covering all platforms attempted."
        ),
        expected_output=(
            "A publish report in JSON format with one entry per platform: "
            "{platform, verdict_from_guardian, action_taken (published/skipped/"
            "tool_unavailable/error), tool_used, response_id_or_url, error_message}."
        ),
        agent=publisher_agent,
    )

    task_analyze = Task(
        description=(
            "Create an analysis template for the content that was just generated.\n"
            "Define the KPIs to track for each platform:\n"
            "1. Engagement metrics to collect (likes, comments, shares, impressions)\n"
            "2. Benchmarks for success per platform\n"
            "3. A/B testing suggestions (what to vary next time)\n"
            "4. Recommendations for future content based on:\n"
            "   - The topic chosen\n"
            "   - The format used\n"
            "   - The platform targeted\n\n"
            "Cross-reference the Publisher's report — only set up tracking for "
            "platforms where the post actually went live."
        ),
        expected_output=(
            "An analytics plan in JSON format with KPIs per platform, success "
            "benchmarks, A/B testing suggestions, and strategic recommendations."
        ),
        agent=analyzer_agent,
    )

    # ── Assemble Crew ────────────────────────────────────────

    crew = Crew(
        agents=[
            research_agent,
            strategy_agent,
            creator_agent,
            visual_agent,
            reviewer_agent,
            guardian_agent,
            publisher_agent,
            analyzer_agent,
        ],
        tasks=[
            task_research,
            task_strategy,
            task_create,
            task_visual,
            task_review,
            task_guardian,
            task_publish,
            task_analyze,
        ],
        process=Process.sequential,
        verbose=True,
    )

    return crew
