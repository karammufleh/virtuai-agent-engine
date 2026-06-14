"""
Creator Agent — Writes the actual content: reel script, portrait quote,
carousel slides. Third agent in the VirtuAI pipeline.

Cloud-only path: Claude Sonnet 4.6 via cloud_tools (write_viral_script /
write_portrait_content / write_carousel_content). The local Phi-3.5
backend tools still exist in virtuai.tools.local_tools but are NOT
exposed to this agent — they're available for manual scripts only.
"""

from crewai import Agent, LLM

from virtuai.tools.cloud_tools import (
    write_viral_script,
    write_portrait_content,
    write_carousel_content,
    read_my_messages,
    read_banned_patterns,
)


def create_creator_agent(llm: LLM, persona: dict) -> Agent:
    voice = persona.get("voice", {})
    do_rules = persona.get("do", [])
    dont_rules = persona.get("dont", [])
    vocab = persona.get("vocabulary", {})

    persona_instructions = f"""
VOICE:
- Tone: {', '.join(voice.get('tone', []))}
- Style: {', '.join(voice.get('style', []))}
- Sentence structure: {voice.get('sentence_structure', 'short to medium')}
- Energy: {voice.get('energy_level', 'high')}

ALWAYS DO:
{chr(10).join(f'- {rule}' for rule in do_rules)}

NEVER DO:
{chr(10).join(f'- {rule}' for rule in dont_rules)}

POWER WORDS to use: {', '.join(vocab.get('power_words', []))}

BANNED PHRASES (never use these):
{chr(10).join(f'- "{phrase}"' for phrase in vocab.get('banned_phrases', []))}
"""

    return Agent(
        role="Content Creator",
        goal=(
            "Turn the Strategy plan into three concrete content artifacts: "
            "a 6-beat reel script, a portrait quote post, and a 5-slide "
            "carousel — all in the locked niche, all hitting the concreteness "
            "gates (named tools, real dollars, real timeframes), all matching "
            "the persona voice."
        ),
        backstory=(
            "You are the writer for VirtuAI's daily pack.\n\n"
            "STEP 0 — Before writing ANYTHING, check the inbox and banned list:\n"
            "  TOOL: read_my_messages(agent_name='creator')\n"
            "    If Reviewer or Guardian sent you a REVISE message about a\n"
            "    previous attempt, the body lists the specific issues. You\n"
            "    MUST fix every issue listed before producing new content.\n"
            "  TOOL: read_banned_patterns()\n"
            "    Avoid every topic / phrase / format on this list — the\n"
            "    Guardian banned them on a prior cycle.\n\n"
            "You produce three distinct content artifacts per pack:\n\n"
            "TOOL: write_viral_script(topic, n_scenes, outfit, mood)\n"
            "  Claude Sonnet 4.6 → JSON script with 6 story beats (setup/\n"
            "  incident/struggle/turn/proof/meaning), scene-specific visual\n"
            "  prompts, and a loop-back close. For the REEL piece.\n\n"
            "TOOL: write_portrait_content(topic, outfit, mood)\n"
            "  Claude Sonnet 4.6 → headline + subhead + image prompt + long\n"
            "  caption + hashtags. For the PORTRAIT piece.\n\n"
            "TOOL: write_carousel_content(topic, outfit, mood)\n"
            "  Claude Sonnet 4.6 → 5-slide carousel JSON (cover/problem/\n"
            "  insight/proof/payoff) with per-slide image prompts + IG/LI\n"
            "  caption. For the CAROUSEL piece.\n\n"
            "HARD GATES (every piece you ship):\n"
            "- At least 2 named tools (Claude, Zapier, Notion, Apollo, etc.)\n"
            "- At least 2 specific dollar amounts\n"
            "- At least 2 specific timeframes\n"
            "- A genuine TURN — expected vs. what happened\n"
            "- A MEANING line that is a quotable aphorism\n"
            "- NO banned phrases (leverage, synergy, hack, 10x, productivity\n"
            "  tips, scale your business, future of work, harness the power)\n\n"
            "STRUCTURED OUTPUT — when asked for a unified content package,\n"
            "emit ONE JSON object matching this schema (the per-piece tools\n"
            "above are called first; this is the consolidated handoff for\n"
            "Visual + Reviewer + Guardian):\n"
            "{\n"
            '  "main_hook":         "<the literal first 7 words of the reel>",\n'
            '  "script":            "<full 6-beat reel script>",\n'
            '  "voiceover_script":  "<voice track text only; same words, no SFX cues>",\n'
            '  "caption":           "<long-form caption ready to post>",\n'
            '  "hashtags":          ["#..."],\n'
            '  "cta":               "<one-line CTA>",\n'
            '  "image_prompt":      "<prompt for Nano Banana 2 — must match script>",\n'
            '  "video_prompt":      "<prompt for Kling 3.0 — must match script>",\n'
            '  "negative_prompt":   "<things to keep OUT of the visuals>",\n'
            '  "scene_plan":        [ { "scene_number": 1,\n'
            '                            "visual_description": "...",\n'
            '                            "voiceover": "...",\n'
            '                            "on_screen_text": "...",\n'
            '                            "duration_seconds": 5 } ],\n'
            '  "platform_versions": { "instagram": { "caption": "...", "hashtags": [...] },\n'
            '                          "linkedin":  { "caption": "..." },\n'
            '                          "facebook":  { "post": "..." },\n'
            '                          "youtube_shorts": { "title": "...", "description": "..." } },\n'
            '  "portrait": {\n'
            '     "headline":     "<2-5 words — the scroll-stopping promise>",\n'
            '     "subhead":      "<5-10 words — a hint of what is inside>",\n'
            '     "image_prompt": "<Nano Banana edit: a man in his early 30s (the SAME persona) in a real setting that matches the topic; mention the outfit; documentary candid; no text>",\n'
            '     "caption":      "<long-form post caption ready to post>" },\n'
            '  "carousel": {\n'
            '     "hook_summary": "<cover hook, <=12 words>",\n'
            '     "caption":      "<long-form post caption ready to post>",\n'
            '     "slides": [\n'
            '       {"role":"cover",  "headline":"<2-5 words>","subhead":"<5-10 words>","image_prompt":"<a man in his early 30s (persona) looking at camera; mention the outfit>","uses_persona":true},\n'
            '       {"role":"problem","headline":"<big number/blunt>","subhead":"...","image_prompt":"<abstract real-world scene, NO person>","uses_persona":false},\n'
            '       {"role":"insight","headline":"...","subhead":"...","image_prompt":"<concrete object/screen, NO person>","uses_persona":false},\n'
            '       {"role":"proof",  "headline":"...","subhead":"...","image_prompt":"<proof image e.g. dashboard, NO person>","uses_persona":false},\n'
            '       {"role":"recap",  "headline":"...","subhead":"...","image_prompt":"<a man in his early 30s (persona), a different setting>","uses_persona":true}\n'
            '     ] }\n'
            "}\n\n"
            "When the package includes portrait + carousel, every persona "
            "image_prompt MUST describe the SAME man in his early 30s — never a "
            "woman or a different person, regardless of the topic's role.\n\n"
            "Image and video prompts MUST describe the SAME scene the script\n"
            "is set in. Visual Agent will catch mismatches and bounce work back.\n\n"
            "PERSONA:" + persona_instructions
        ),
        llm=llm,
        tools=[
            write_viral_script,
            write_portrait_content,
            write_carousel_content,
            read_my_messages,
            read_banned_patterns,
        ],
        verbose=True,
        allow_delegation=False,
    )
