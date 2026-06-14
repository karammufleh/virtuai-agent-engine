"""
script_writer.py — DeepSeek-powered viral script writer.

Takes a topic seed (or asks the model to pick one) and returns a
structured scene-by-scene script ready for Kling 3.0 + ElevenLabs.

Output schema (validated):
    {
      "topic": "...",
      "hook_summary": "...",        # tagline for thumbnail/preview
      "total_words": int,
      "estimated_seconds": float,
      "scenes": [
        {
          "id": 1,
          "audio_text": "...",       # what the speaker says (1-3 sentences)
          "visual_prompt": "...",    # Kling 3.0 cinematic shot prompt
          "duration_hint": 5         # target seconds, 4-8
        },
        ...
      ],
      "loop_back_line": "..."        # optional: final line that echoes opening
    }

Public API:
    write_script(topic=None, n_scenes=5, persona_brief=None) -> dict
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

logger = logging.getLogger("virtuai.tools.script_writer")

KIE_LLM_BASE = "https://kieai.erweima.ai/api/v1"          # OpenAI-compat (DeepSeek)
KIE_CLAUDE_BASE = "https://api.kie.ai/claude/v1"          # Anthropic-compat (Claude)
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()

# Claude Sonnet 4.6 = best balance of creativity + instruction-following on KIE.
# Opus 4.6 is stronger but slower/pricier. Sonnet for routine, Opus for hero.
DEFAULT_LLM_MODEL = "claude-sonnet-4-6"

DEFAULT_PERSONA_BRIEF = (
    "A 28-year-old man with short dark wavy hair and light stubble, "
    "wearing a dark polo shirt. Confident, direct, slightly contrarian "
    "tone. Speaks like a founder dropping hard truth on a podcast clip."
)

# Locked content niche. The script writer must always pick a topic that
# fits this brief — concrete, contrarian, anchored in real business+AI
# practice (not abstract tech-bro futurism, not generic productivity).
LOCKED_NICHE_BRIEF = """
NICHE — MANDATORY, do not deviate:
The reel is for an AI/automation business creator. Every topic must be
about building, scaling, hiring, selling, or operating a business that
uses AI/automation as leverage. Examples of acceptable topics:

- "I replaced my $80k VA with AI agents. Here's the math."
- "Why most 'AI businesses' fail in 90 days."
- "The one workflow I'd automate before hiring anyone."
- "I used to charge by the hour. Then I built an AI that did the work."
- "Founders aren't competing on product anymore. They're competing on systems."
- "The fastest way to lose money in 2026: hiring before automating."
- "What I learned shipping 6 AI agents into client workflows."
- "Stop building AI tools. Start building AI processes."

NEVER pick topics outside this niche:
- NOT generic "productivity tips" or "morning routines"
- NOT "habits of successful people" or self-help
- NOT pure tech demos ("here's how to use ChatGPT")
- NOT diet, fitness, relationships, philosophy
- NOT abstract "future of AI" musings — must be operator-level

The speaker's voice: founder/operator who has actually shipped AI work,
not a guru, not a teacher. Talks like an investor's portfolio call, not
a YouTube tutorial.
"""

FEW_SHOT_VIRAL_SCRIPTS = """
REFERENCE — these are the calibre of script you are competing against.
Study the specificity, story arc, hook, and payoff:

EXAMPLE 1 — "The $48 agent that replaced my COO"
Hook: "I just paid an AI $48 to do what my $180k COO couldn't."
Story: "She quit three weeks ago — I was panicking. So I gave Claude every Loom recording she'd ever made and asked it to run our weekly ops review. Forty-eight bucks, four hours, zero PowerPoints. The agent caught two leaks she'd been hiding. I'm not hiring a replacement."
Payoff: "Stop trying to replace people. Replace processes."

EXAMPLE 2 — "I fired my SDR. Here's what I built instead."
Hook: "My sales rep quit on Sunday. By Wednesday I'd built her replacement for $32."
Story: "I dumped six months of her email threads into a custom GPT. Trained it on her tone. Hooked it to Apollo and Calendly. It now sends 200 personalized outreach emails a day and books 8 demos a week. She used to book 3."
Payoff: "Junior roles aren't disappearing. They've already disappeared."

EXAMPLE 3 — "The most expensive thing I built was an AI tool"
Hook: "I spent $40,000 building an AI agent. It made me zero dollars."
Story: "Six months of engineering, three contractors, a custom interface. Beautiful product. Nobody used it. Then I deleted the whole thing and wrote a 12-line Zapier workflow. That single workflow has saved 600 hours this year."
Payoff: "You don't need an AI tool. You need an AI process. There's a difference."

EXAMPLE 4 — "Why your AI startup is dying"
Hook: "Three of my AI-startup friends shut down this month. They all made the same mistake."
Story: "They built wrappers around GPT-4 and called them products. The moment OpenAI shipped a feature, their moats evaporated. I learned this in 2023. I stopped building tools and started building SOPs that AI could execute. My margins tripled."
Payoff: "Software was the moat. Workflows are the new moat."

EXAMPLE 5 — "I deleted every productivity app I own"
Hook: "I had 14 productivity apps. I deleted them all. My output went up 3x."
Story: "Apps weren't the problem. Decisions were. I built one agent that wakes up at 6am, reads my calendar, my inbox, my Notion, and tells me the three things to do today. Then it monitors whether I do them. That's it. No interfaces, no notifications, no streaks."
Payoff: "Stop optimizing your stack. Start automating your judgment."

WHY THESE WORK:
1. First sentence has a specific dollar amount or specific person who left
2. Real situation, real tools named (Claude, Apollo, GPT-4, Zapier, Notion)
3. There's a TURN at the midpoint — what they expected didn't happen, then they did X
4. Payoff is a quotable line, not a generic CTA
5. Every scene shows the speaker in a DIFFERENT real location that COMMENTS on the story
"""


VISUAL_PROMPT_SPEC = """
VISUAL PROMPT FORMAT — every scene's visual_prompt must include all 5 layers:

[1] COMPOSITION: shot type + lens + angle + POSTURE
    Examples: "medium close-up, 35mm, eye-level, leaning slightly forward with elbows on table" /
              "over-shoulder wide, 28mm, slight low angle, walking with one hand in jacket pocket"

[2] SETTING: specific location with TANGIBLE PROPS
    Bad:  "office"
    Good: "rooftop office at dusk, exposed concrete walls, single brass desk lamp, a Moleskine notebook open on the table"

[3] LIGHT: named source + time of day + quality
    Bad:  "natural light"
    Good: "low warm key-light from a south-facing window, 7am golden hour, soft fill from a white wall"

[4] LIVE BACKGROUND: 2-3 specific moving elements — MANDATORY
    Bad:  "people moving"
    Good: "a barista wiping down the counter behind him, steam rising from an espresso machine, ambient figures walking past the window in soft focus"
    REJECT ANY SCENE WITH A STATIC BACKGROUND. The scene must visibly have something moving (people walking, cars driving past, leaves rustling, steam rising, fabric flapping, water rippling). NEVER a frozen still.

[5] CAMERA + MOOD: motion type + emotional quality — MANDATORY MOTION
    Required keywords: handheld drift / slow push-in / slow dolly / orbit / tracking / pan / parallax.
    Bad:  "documentary, locked-off camera"
    Good: "slow 1-foot handheld dolly forward, subject leaning forward slightly, intimate confessional energy"
    REJECT "locked-off" alone — there must be at least subtle camera movement.

POSTURE VARIETY — each scene MUST use a DIFFERENT body posture from the previous scene. Acceptable postures: seated leaning forward / standing arms crossed / walking with selfie stick / sitting with elbow on knee / leaning against wall / half-turned looking back / both hands on counter / hand on chin / open hands / pointing.

Photo-real, NEVER cinematic-fantasy. NEVER mention "AI", "neon", "futuristic", "glowing data".
"""


SYSTEM_PROMPT = """You write viral short-form video scripts for Instagram Reels / TikTok / YouTube Shorts. Industry-standard creator tactics, NOT amateur AI slop.

""" + LOCKED_NICHE_BRIEF + FEW_SHOT_VIRAL_SCRIPTS + VISUAL_PROMPT_SPEC + """


Your output is ONLY a valid JSON object (no markdown, no commentary).

Schema:
{
  "topic": "string — the core idea, 5-10 words",
  "hook_summary": "string — one-line tagline, the scroll-stopping promise",
  "total_words": number,
  "estimated_seconds": number,
  "scenes": [
    {
      "id": 1,
      "audio_text": "string — what the speaker actually says in THIS scene, 1-3 sentences, conversational, contractions OK",
      "visual_prompt": "string — a cinematic Kling 3.0 video prompt for THIS scene; describe the SETTING (location, light, mood) where the SAME PERSON delivers this segment. Reference the person as '@daniel' for face consistency. Include camera framing (medium close-up / over-shoulder / wide), motion (locked-off / slow push-in), and lighting. Photo-real, candid documentary style — NOT cinematic-fantasy.",
      "duration_hint": 5
    }
  ],
  "loop_back_line": "string OR null"
}

CRITICAL RULES — viral creator standard:

1. HOOK (Scene 1): 1-2 sentences, must STOP scroll in <2s. Use ONE of:
   - Contrarian claim: "Everyone says X. They're wrong."
   - Specific number shock: "I made $X doing Y."
   - Pain-point question: "Tired of being broke at 30?"
   - Curiosity gap: "There's one habit that 10x'd my output."
   - In-media-res: "So I'm sitting across from a $40M founder when he says..."
   NEVER start with "Hey guys", "Today I'll show you", or any intro.

2. STORY ARC (mandatory) — a complete story, not just a punchline. Hit ALL six beats below in order, one beat per scene (or compress two adjacent beats if scenes < 6):
   (a) SETUP — who you are + your specific situation. Concrete.
   (b) INCITING INCIDENT — the thing that forced you to act. A real event with a date or trigger.
   (c) THE STRUGGLE — what you tried first, and why it didn't work. Includes a specific cost or pain.
   (d) THE TURN — the unexpected discovery / pivot that changed things. This is where the viewer leans in.
   (e) PROOF — what actually happened after the turn, with concrete numbers and named tools.
   (f) MEANING — the underlying lesson framed as a quotable aphorism. NOT a CTA. The viewer should be able to repeat this line to a friend tomorrow.
A reel that skips (b), (c), (d), or (f) is REJECTED. It reads as a tweet, not a story.

3. CONCRETE DETAILS (mandatory minimum per script):
   - At least ONE specific dollar amount ($40, $48, $180k)
   - At least ONE named tool (Claude, Zapier, Notion, GPT-4, Apollo, Calendly, Loom)
   - At least ONE timeframe (Sunday, six months, four hours, three weeks)
   - At least ONE real role or human reference (my SDR, my COO, three friends, my accountant)
   No vague "scale your business", no "leverage", no "synergy", no "productivity hacks".

4. PACING: target 180-220 words per minute spoken. Total reel 18-28 seconds.

5. ENDING — NO yellow PART 2 cards, NO "follow me" begging, NO "comment X". Top creators end with:
   - FLAT LANDING: "That's it. That's the whole thing." Cut clean.
   - IMPERATIVE: "Go do it." 2-3 word verb close.
   - APHORISM: a quotable line that IS the close.
   - LOOP-BACK: final line completes the opening hook (forces replay).
   Pick ONE.

6. VISUAL PROMPTS: photo-realistic, natural light, candid documentary style. Each scene shows the SAME PERSON in a DIFFERENT REAL LOCATION (cafe, park, office, walking street, kitchen, etc.). NO fantasy/sci-fi/glowing neural nets. NO floating dashboards. REAL LIFE only.

   SCENE-VARIETY DIMENSIONS — the 4-6 scenes in a script MUST diversify
   along these axes (no two adjacent scenes share more than two of them):
     • location              (cafe / park / desk / sidewalk / kitchen / studio / car / gym / hallway / rooftop / hotel lobby)
     • camera angle          (eye-level / low / high / over-shoulder / Dutch / handheld POV)
     • framing               (close-up / medium / wide / extreme wide)
     • lens feel             (35mm / 50mm / 85mm portrait / wide-anamorphic)
     • lighting              (golden-hour / soft window / single practical / overcast diffuse / neon ambient / midday hard)
     • time of day           (morning / noon / golden hour / dusk / blue hour / night)
     • activity / posture    (sitting / walking / standing / leaning / pacing / pouring coffee / typing / cooking)
     • props                 (notebook + pen / laptop / phone / coffee / book / blueprint / dumbbells / shopping bag)
     • mood                  (calm / urgent / contemplative / amused / determined / mid-laugh)

7. NEVER mention AI, prompts, chatbots, or anything that signals the speaker is fake. The speaker is a real human.

8. Length: 4-6 scenes. Total ~22-28 seconds of speech.

9. REAL-CREATOR VOICE (audio_text + visual_prompt):
   • Write the way a real creator who already has the result would talk —
     not the way a how-to-go-viral guide tells you to.
   • Contractions ON ("you're", "I'd", "it's"). Short declaratives.
     One concrete noun beats five abstract adjectives.
   • No clichés — including: "unlock your potential", "in today's
     fast-paced world", "game changer", "embark on a journey",
     "elevate your", "supercharge", "revolutionize", "delve into",
     "next-level", "buckle up", "without further ado", "in conclusion".
   • No hashtag mentions in audio. No "smash like". No "guys".
   • Avoid emoji in the script body.
   • If a line could be auto-generated by 3 different AI tools, rewrite it.

Return ONLY the JSON object."""


def _headers() -> dict:
    if not KIE_API_KEY:
        raise RuntimeError("KIE_API_KEY not set in .env")
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def _strip_code_fence(s: str) -> str:
    """Remove ```json ... ``` wrappers if present."""
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


_BANNED_PHRASES = [
    # legacy bans (kept)
    "leverage", "synergy", "level up", "game changer", "hack your", "10x your",
    "productivity tips", "morning routine", "scale your business",
    "AI is changing", "future of work", "harness the power",
    # 2026-05-20 expansion — the AI-creator clichés viewers immediately
    # smell as machine-generated copy
    "unlock your potential", "in today's fast-paced world",
    "embark on a journey", "elevate your", "supercharge",
    "revolutionize", "disrupt the", "next level", "boost your",
    "skyrocket", "the power of", "delve into", "navigate the",
    "in conclusion", "to sum up", "let's dive", "buckle up",
    "without further ado", "groundbreaking", "transformative",
    "cutting edge", "best-in-class", "world-class",
]
_NAMED_TOOLS = [
    "claude", "chatgpt", "gpt", "openai", "zapier", "notion", "apollo",
    "calendly", "loom", "salesforce", "hubspot", "airtable", "make.com",
    "n8n", "retool", "linear", "stripe", "intercom", "slack", "gemini",
    "cursor", "lovable", "v0", "bolt", "replit", "vercel", "figma",
]

# Words too generic to count toward a banned-pattern keyword match.
_BAN_STOPWORDS = {
    "the", "and", "for", "with", "without", "that", "this", "your", "you",
    "are", "was", "its", "into", "from", "days", "day", "have", "they",
}


def _load_banned_patterns() -> list[dict]:
    """Load the Guardian's DYNAMIC block-list (banned_patterns.json).

    The Guardian appends here on every BLOCK. Unlike the static cliché list,
    these are confirmed policy/safety blocks and are enforced as HARD rejects
    so a rejected pattern can never be re-rendered and published.
    """
    p = Path(__file__).resolve().parents[2] / "virtuai/data/banned_patterns.json"
    try:
        return json.loads(p.read_text()).get("patterns", [])
    except Exception:
        return []


def _banned_pattern_hit(text: str, patterns: list[dict]):
    """Return (pattern, reason) if `text` strongly matches a banned pattern.

    Matches on either (a) the pattern as a near-literal substring (the `X`
    placeholder is stripped), or (b) ALL of the pattern's distinctive
    keywords (>=4 chars, non-stopword) appearing in the text.
    """
    t = re.sub(r"\s+", " ", text.lower())
    for entry in patterns:
        pat = (entry.get("pattern") or "").lower().strip()
        if not pat:
            continue
        lit = re.sub(r"\bx\b", "", pat)
        lit = re.sub(r"\s+", " ", lit).strip(" -—")
        if len(lit) >= 8 and lit in t:
            return entry.get("pattern"), entry.get("reason", "")
        kws = [w for w in re.findall(r"[a-z]{4,}", pat) if w not in _BAN_STOPWORDS]
        if len(kws) >= 2 and all(w in t for w in kws):
            return entry.get("pattern"), entry.get("reason", "")
    return None


def _validate(script: dict) -> None:
    required_top = {"topic", "hook_summary", "scenes"}
    missing = required_top - script.keys()
    if missing:
        raise ValueError(f"Script missing top-level keys: {missing}")
    if not isinstance(script["scenes"], list) or len(script["scenes"]) < 3:
        raise ValueError(f"Need at least 3 scenes, got {len(script.get('scenes', []))}")
    for i, sc in enumerate(script["scenes"]):
        if "audio_text" not in sc or "visual_prompt" not in sc:
            raise ValueError(f"Scene {i} missing audio_text or visual_prompt")
        if len(sc["audio_text"].strip()) < 10:
            raise ValueError(f"Scene {i} audio_text too short: {sc['audio_text']!r}")

    # Concreteness checks across the full script
    full_text = " ".join(sc["audio_text"] for sc in script["scenes"]).lower()
    if not re.search(r"\$\d|\d+\s?(hour|day|week|month|year|x|k|grand)", full_text):
        logger.warning("⚠ Script lacks a specific number/dollar/timeframe")
    if not any(tool in full_text for tool in _NAMED_TOOLS):
        logger.warning("⚠ Script lacks a named tool (claude/zapier/notion/etc.)")
    banned_hit = [p for p in _BANNED_PHRASES if p in full_text]
    if banned_hit:
        logger.warning(f"⚠ Script contains banned cliches: {banned_hit}")

    # DYNAMIC enforcement: the Guardian's banned_patterns.json entries are
    # confirmed BLOCKs (policy/safety). A match is a HARD reject — the script
    # is never rendered or published — not a warning.
    check_text = " ".join([
        script.get("topic", ""), script.get("hook_summary", ""), full_text,
    ])
    hit = _banned_pattern_hit(check_text, _load_banned_patterns())
    if hit:
        raise ValueError(
            f"BLOCKED by Guardian banned-pattern {hit[0]!r}: {hit[1]} "
            f"— refusing to render/publish."
        )

    # Visual prompt richness check
    for i, sc in enumerate(script["scenes"]):
        vp = sc["visual_prompt"].lower()
        # Must reference framing/lens and a moving background element
        has_framing = any(w in vp for w in
            ["close-up", "medium shot", "wide", "over-shoulder", "lens", "mm",
             "framing", "low angle", "eye-level", "high angle"])
        has_motion = any(w in vp for w in
            ["walking", "moving", "passing", "drifts", "rises", "rustl",
             "wiping", "stirring", "behind him", "background", "ambient",
             "cars", "pedestrian", "leaves", "steam", "barista", "dolly",
             "handheld", "push-in", "tracking"])
        if not has_framing:
            logger.warning(f"⚠ Scene {i} visual_prompt missing framing detail")
        if not has_motion:
            logger.warning(f"⚠ Scene {i} visual_prompt missing live motion")


def _call_claude(system_msg: str, user_msg: str, model: str,
                 temperature: float, max_tokens: int = 10000) -> str:
    """Invoke a Claude model via KIE's Anthropic-compat endpoint."""
    resp = httpx.post(
        f"{KIE_CLAUDE_BASE}/messages",
        headers=_headers(),
        json={
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_msg,
            "messages": [{"role": "user", "content": user_msg}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    body = resp.json()
    # Anthropic response: {content: [{type: "text", text: "..."}, ...]}
    content = body.get("content", [])
    if not content:
        raise RuntimeError(f"Claude returned empty content: {body}")
    text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
    return "\n".join(text_parts).strip()


def _call_openai_compat(system_msg: str, user_msg: str, model: str,
                        temperature: float, max_tokens: int = 3000) -> str:
    """Invoke DeepSeek (or any OpenAI-compat) via KIE."""
    resp = httpx.post(
        f"{KIE_LLM_BASE}/chat/completions",
        headers=_headers(),
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def write_script(
    topic: Optional[str] = None,
    n_scenes: int = 5,
    persona_brief: Optional[str] = None,
    *,
    recent_topics: Optional[list[str]] = None,
    outfit: str = "dark polo shirt",
    mood: Optional[str] = None,
    setting_pool: Optional[list[str]] = None,
    temperature: float = 0.85,
    model: str = DEFAULT_LLM_MODEL,
    recent_outfits: Optional[list[str]] = None,
    recent_moods: Optional[list[str]] = None,
    recent_scenes: Optional[list[str]] = None,
    recent_hooks: Optional[list[str]] = None,
) -> dict:
    """
    Generate a structured scene-by-scene script using the strongest LLM.

    Args:
        topic: Specific topic, or None to let the model pick.
        n_scenes: Target scene count (3-6).
        persona_brief: Description of the on-screen speaker.
        recent_topics: Topics already used — model must pick a NEW pattern.
        outfit: What the speaker wears in THIS reel (e.g. "navy hoodie",
                "white linen shirt", "grey crewneck sweater").
        mood: Optional creative direction (e.g. "personal regret",
              "contrarian rant", "case study", "live observation").
        setting_pool: Optional 6-tag list of settings to draw from
                      (urban street, sunlit cafe, rooftop, gym, etc.).
        temperature: 0.7-0.9 for creative variety.
        model: KIE model id.

    Returns:
        Validated script dict.
    """
    # Inject the outfit into the persona description so it propagates
    # to every visual_prompt the model generates.
    base_persona = persona_brief or (
        "A 28-year-old man with short dark wavy hair and light stubble. "
        "Confident, direct, slightly contrarian tone. Speaks like a founder "
        "dropping hard truth on a podcast clip."
    )
    persona = base_persona + f"\nIn THIS reel he is wearing: {outfit}."
    use_claude = model.startswith("claude-")

    # PHASE 1 — Topic ideation (skip if topic given)
    if not topic:
        avoid_clause = ""
        if recent_topics:
            avoid_clause = (
                "\n\nDO NOT repeat or paraphrase any of these PREVIOUSLY USED "
                "topics or topic patterns — pick something structurally "
                "different:\n"
                + "\n".join(f"  ✗ {t}" for t in recent_topics[-8:])
                + "\n\nIf you find yourself reaching for 'I fired my X for an "
                "AI', PICK A DIFFERENT PATTERN. Use one of these instead: "
                "an unexpected failure I shipped, a contrarian prediction, a "
                "specific automation that made/saved money in an unusual way, "
                "a hot take on a popular AI tool, a regret from building an "
                "AI product, a hidden cost no one talks about, a comparison "
                "between two approaches, a step-by-step of an unusual workflow."
            )
        # 2026-05-20 — extra variety memory across other dimensions
        for label, items in (
            ("outfit descriptions",      recent_outfits),
            ("narrative moods",          recent_moods),
            ("scene locations / props",  recent_scenes),
            ("opening hook patterns",    recent_hooks),
        ):
            if items:
                avoid_clause += (
                    f"\n\nRecently-used {label} — do NOT repeat:\n"
                    + "\n".join(f"  ✗ {x}" for x in items[-8:] if x)
                )
        mood_clause = f"\n\nCREATIVE DIRECTION FOR THIS REEL: {mood}." if mood else ""

        ideation_user = (
            f"Persona on screen: {persona}\n\n"
            "Step 1 of 2: Brainstorm 5 candidate topic ideas. Each must satisfy "
            "the locked niche (AI/automation in business). Each must be:\n"
            "- a specific anecdote, not a concept\n"
            "- contain a CONCRETE detail in the first sentence (dollar amount, "
            "  named tool, role, timeframe)\n"
            "- contrarian (contradicts common AI-bro advice)\n"
            "- operator-level (someone who has actually shipped this)\n"
            "- structurally different from past topics (see avoid list)\n\n"
            "Then pick the BEST one based on hook strength + replay value.\n\n"
            "Reply in this format:\n"
            "CANDIDATES:\n"
            "1. [hook sentence]\n"
            "2. [hook sentence]\n"
            "3. [hook sentence]\n"
            "4. [hook sentence]\n"
            "5. [hook sentence]\n\n"
            "WINNER: [number] — [one-line justification]\n"
            + avoid_clause + mood_clause
        )
        if use_claude:
            ideation_raw = _call_claude(
                SYSTEM_PROMPT, ideation_user, model, temperature, max_tokens=1500
            )
        else:
            ideation_raw = _call_openai_compat(
                SYSTEM_PROMPT, ideation_user, model, temperature, max_tokens=1500
            )
        logger.info(f"Brainstorm:\n{ideation_raw}\n")
        # Best-effort: pluck the WINNER line
        m = re.search(r"WINNER:\s*(\d+)\s*[—-]?\s*(.+)", ideation_raw)
        if m:
            winner_num = int(m.group(1))
            cand_match = re.findall(r"^\s*(\d+)\.\s*(.+)", ideation_raw, re.M)
            for n, c in cand_match:
                if int(n) == winner_num:
                    topic = c.strip()
                    break
        if not topic:
            # Fallback: just use the first candidate
            cand_match = re.findall(r"^\s*\d+\.\s*(.+)", ideation_raw, re.M)
            topic = (cand_match[0].strip() if cand_match
                     else "I fired my SDR and built her replacement in 4 days for $32")
        logger.info(f"Selected topic: {topic!r}")

    # PHASE 2 — Write the full script
    # Default to a 6-scene structure when the caller asks for ≥5 scenes —
    # 6 scenes = full setup/incident/struggle/turn/proof/meaning arc.
    target_words = 100 if n_scenes >= 5 else 70
    setting_clause = ""
    if setting_pool:
        setting_clause = (
            "\nFor THIS reel, each scene must use a DIFFERENT setting drawn "
            "from this pool (pick the most fitting ones for the story):\n"
            + "\n".join(f"  - {s}" for s in setting_pool) + "\n"
        )
    script_user = (
        f"Persona on screen: {persona}\n\n"
        f"Hook / topic: {topic}\n"
        f"Target scenes: {n_scenes} (each ~5 seconds of speech)\n"
        f"Target total spoken words: {target_words}-{target_words + 30}\n"
        f"REQUIRED outfit (mention in every visual_prompt): {outfit}\n"
        f"{setting_clause}\n"
        "Write the full script as JSON matching this exact schema:\n"
        "{\n"
        '  "topic": "string — 5-10 word title",\n'
        '  "hook_summary": "string — the first spoken line, no longer than 12 words",\n'
        '  "total_words": number,\n'
        '  "estimated_seconds": number,\n'
        '  "scenes": [\n'
        '    {\n'
        '      "id": 1,\n'
        '      "story_beat": "setup | incident | struggle | turn | proof | meaning",\n'
        '      "audio_text": "string — what the speaker says in THIS scene, ~15-20 words",\n'
        '      "visual_prompt": "string — must include all 5 layers from the system prompt",\n'
        '      "duration_hint": 5\n'
        '    }\n'
        "  ],\n"
        '  "loop_back_line": "string OR null — only if the final line completes the hook"\n'
        "}\n\n"
        "REQUIREMENTS — each is a HARD GATE:\n"
        f"- Script length: {target_words}-{target_words + 30} spoken words total\n"
        "- Each scene contains ONE story beat from {setup, incident, struggle, turn, proof, meaning}\n"
        "- The 6-beat arc is mandatory if n_scenes ≥ 5; combine adjacent beats only if fewer scenes\n"
        "- At least 2 named tools in dialogue (Claude, Zapier, Notion, Loom, Apollo, GPT, Cursor, etc.)\n"
        "- At least 2 specific dollar amounts ($4M, $340, $11k, etc.)\n"
        "- At least 2 specific timeframes (Sunday, four weeks, six hours, last Tuesday, etc.)\n"
        "- A genuine TURN — what you expected vs. what actually happened\n"
        "- A MEANING beat that is a quotable aphorism (the viewer would tweet this line)\n"
        "- NO banned phrases (leverage, synergy, hack your, 10x, productivity tips, scale your business)\n"
        "- Each scene = different real location with TANGIBLE props (cafe with espresso machine, rooftop with cityscape, kitchen with Moleskine, gym with kettlebell, park with bench, garage workshop, etc.)\n"
        "- Visual prompts MUST follow the 5-layer format. Same person, different real environments.\n"
        "- The audio_text of consecutive scenes must FLOW as one continuous story — when read end to end, they form ONE paragraph that a listener follows from setup to meaning\n\n"
        "Output ONLY the JSON object."
    )

    logger.info(f"Writing script via {model}...")
    if use_claude:
        raw = _call_claude(SYSTEM_PROMPT, script_user, model, temperature)
    else:
        raw = _call_openai_compat(SYSTEM_PROMPT, script_user, model, temperature)
    raw = _strip_code_fence(raw)

    try:
        script = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"LLM returned invalid JSON: {raw[:500]}")
        raise ValueError(f"Script JSON parse failed: {e}") from e

    _validate(script)

    # Compute totals if missing
    total_words = sum(len(s["audio_text"].split()) for s in script["scenes"])
    if "total_words" not in script:
        script["total_words"] = total_words
    if "estimated_seconds" not in script:
        # ~200 WPM = 3.33 wps
        script["estimated_seconds"] = round(total_words / 3.33, 1)

    logger.info(
        f"Script ready: topic='{script['topic']}', "
        f"{len(script['scenes'])} scenes, "
        f"{script['total_words']} words, "
        f"~{script['estimated_seconds']}s"
    )
    return script


def full_audio_text(script: dict) -> str:
    """Concatenate all scene audio texts into a single string for ElevenLabs."""
    return " ".join(s["audio_text"].strip() for s in script["scenes"])


def kling_multi_scene_prompt(script: dict) -> str:
    """
    Build a single Kling 3.0 multi-shots prompt that chains all scenes.
    Used when kling_3.0/video is invoked with multi_shots=True.
    """
    parts = []
    for i, sc in enumerate(script["scenes"], 1):
        parts.append(f"Shot {i}: {sc['visual_prompt']}")
    return "\n\n".join(parts)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--topic", default=None, help="Optional topic seed")
    p.add_argument("--scenes", type=int, default=5)
    p.add_argument("--out", default="virtuai/data/scripts/latest.json")
    args = p.parse_args()

    script = write_script(topic=args.topic, n_scenes=args.scenes)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(script, indent=2))
    print(f"Wrote {out}")
    print(json.dumps(script, indent=2))
