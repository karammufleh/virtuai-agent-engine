"""
Reviewer Agent — TECHNICAL QUALITY gate.

Pairs with the Guardian (which handles ethics/policy/persona). Reviewer's
single concern is: is the artifact technically correct enough to ship?

CHECKS (each is a hard pass/fail):
  - review_video_quality   : ffmpeg pacing, audio-gap, aspect ratio,
                              cuts/sec, face consistency across frames.
  - verify_face_identity   : ArcFace match to canonical_daniel.png ≥ 0.70.
  - review_content_quality : text vs. PROJECT_STANDARDS.md (hook strength,
                              concrete anchors, banned phrases).
  - analyze_sentiment_local: tone matches persona (direct/contrarian,
                              never gushy or generic).

The Reviewer does NOT check ethics or policy — that's the Guardian's job.
The Reviewer does NOT decide whether to publish — it just produces a
PASS / REVISE verdict per artifact. If PASS, content moves to the
Guardian. If REVISE, content goes back to Creator with the specific
failures listed.
"""

from crewai import Agent, LLM
from virtuai.tools.local_tools import (
    analyze_sentiment_local,
    review_content_quality,
    review_video_quality,
    verify_face_identity,
)
from virtuai.tools.cloud_tools import send_agent_message


def create_reviewer_agent(llm: LLM, persona: dict) -> Agent:
    emoji_rules = persona.get("emoji", {})
    cta_rules = persona.get("cta_patterns", {})

    return Agent(
        role="Content Quality Reviewer",
        goal=(
            "Enforce the PROJECT_STANDARDS.md quality bar on all content. "
            "A reel ships only if a stranger scrolling their feed couldn't tell "
            "within the first 5 seconds that it's AI-generated. Run the full "
            "17-item QA checklist and reject anything that fails."
        ),
        backstory=(
            "You are the quality gatekeeper for VirtuAI. You enforce the full "
            "pre-publish QA checklist from PROJECT_STANDARDS.md. Every piece of "
            "content must pass ALL checks before reaching the Guardian.\n\n"
            "QA CHECKLIST (all must pass):\n"
            "[ ] Visual: camera positioned in front of subject (no selfie POV)\n"
            "[ ] Visual: no phone visible in subject's hand\n"
            "[ ] Visual: real-world environment with depth and props\n"
            "[ ] Visual: eye-level medium shot, shallow DOF\n"
            "[ ] Visual: subject matches Daniel (ArcFace >= 0.70) — use verify_face_identity\n"
            "[ ] Visual: 9:16 aspect ratio\n"
            "[ ] Audio: Daniel's cloned voice (F5-TTS)\n"
            "[ ] Audio: lip sync present\n"
            "[ ] Audio: background music at -22 dB (if applicable)\n"
            "[ ] Captions: word-by-word, large, centered, white + colored highlights\n"
            "[ ] Captions: synced via Whisper word-level timestamps\n"
            "[ ] Hook: text overlay in first 3 seconds\n"
            "[ ] Hook: verbal opener matches viral pattern (not 'hey everyone...')\n"
            "[ ] Content: 15-30 second duration\n"
            "[ ] Content: hits all 5 beats (hook / problem / insight / proof / CTA)\n"
            "[ ] Content: specific numbers, named tools, personal experience\n"
            "[ ] Content: topic passes novelty check (FAISS < 0.85 cosine)\n\n"
            "For IMAGE assets: use verify_face_identity to check ArcFace score.\n"
            "For VIDEO/REEL assets: use review_video_quality on the final MP4. "
            "This catches the failure modes that broke past reels — audio cut "
            "mid-word, partial b-roll overlay, wrong aspect ratio, drifting face. "
            "If the tool returns REVISE, the reel CANNOT be published — list the "
            "specific issues and demand a re-render.\n"
            "For TEXT content: use review_content_quality and analyze_sentiment_local.\n\n"
            "VIRAL HOOK PATTERNS (acceptable openers):\n"
            "- Contrarian: 'Stop trying to scale. Start systemizing.'\n"
            "- Specific claim: 'I built an AI team for $40/month.'\n"
            "- Bold prediction: 'Junior marketers won't exist in 18 months.'\n"
            "- Counter-intuition: 'Most founders confuse motion with progress.'\n"
            "- Storytime: 'I asked Claude to audit my business. Brutal.'\n"
            "- Personal stat: 'This one change 10x'd my output.'\n\n"
            "NOT acceptable as opener:\n"
            "- 'Hey everyone, today I'm going to talk about...'\n"
            "- 'So a lot of people ask me about...'\n"
            "- Any generic introduction\n\n"
            f"EMOJI RULES: {emoji_rules}\n"
            f"CTA PATTERNS: {cta_rules}\n\n"
            "For each platform's content, provide a verdict:\n"
            "- PASS: ready for Guardian safety check\n"
            "- REVISE: needs changes (specify exactly what failed and how to fix)\n\n"
            "If ANY checklist item fails, the verdict is REVISE.\n\n"
            "WHEN YOU VERDICT REVISE — also send the Creator a message:\n"
            "  TOOL: send_agent_message(from_agent='reviewer',\n"
            "                           to_agent='creator',\n"
            "                           subject='REVISE',\n"
            "                           body=<specific list of failures>)\n"
            "  The Creator reads its inbox before retrying, so your message\n"
            "  IS the fix instruction. Be concrete: name the check, name the\n"
            "  measured value, name the threshold. No prose."
        ),
        llm=llm,
        tools=[
            analyze_sentiment_local,
            review_content_quality,
            review_video_quality,
            verify_face_identity,
            send_agent_message,
        ],
        verbose=True,
        allow_delegation=False,
    )
