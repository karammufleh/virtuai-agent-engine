"""
prepare_dataset.py — Build fine-tuning JSONL from VirtuAI persona + platform configs.

Reads:
  - virtuai/config/personas/virtuai_mentor.yaml
  - virtuai/config/platforms/*.yaml

Outputs:
  - virtuai/models/finetune/data/train.jsonl   (80% of generated pairs)
  - virtuai/models/finetune/data/valid.jsonl   (20% of generated pairs)

These supplement (not replace) the hand-crafted examples already in those files.
Run this if you want to regenerate or expand the dataset.

Usage:
    python -m virtuai.models.finetune.prepare_dataset
"""

import json
import random
import yaml
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.parent.parent  # capstone 101/
PERSONAS_DIR = ROOT / "virtuai" / "config" / "personas"
PLATFORMS_DIR = ROOT / "virtuai" / "config" / "platforms"
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_system_prompt(persona: dict) -> str:
    voice = persona.get("voice", {})
    do_rules = persona.get("do", [])
    dont_rules = persona.get("dont", [])
    power_words = persona.get("vocabulary", {}).get("power_words", [])
    banned = persona.get("vocabulary", {}).get("banned_phrases", [])

    return (
        f"You are {persona.get('name', 'VirtuAI Mentor')} — "
        f"{persona.get('role', 'AI business authority')}. "
        f"Your voice is {', '.join(voice.get('tone', ['direct', 'motivational']))}. "
        f"Style: {', '.join(voice.get('style', ['no fluff', 'authority-driven']))}. "
        f"Sentence structure: {voice.get('sentence_structure', 'short to medium')}. "
        f"Use power words: {', '.join(power_words[:6])}. "
        f"ALWAYS: {'; '.join(do_rules[:4])}. "
        f"NEVER: {'; '.join(dont_rules[:4])}. "
        f"Banned phrases: {', '.join(f'\"{p}\"' for p in banned[:5])}. "
        f"Every post must open with a strong hook and close with one CTA."
    )


def build_platform_examples(persona: dict, platforms: dict) -> list[dict]:
    """Generate instruction-response pairs for each platform."""
    system = build_system_prompt(persona)
    examples = []

    platform_tasks = {
        "linkedin": [
            ("Write a LinkedIn post about how AI gives small businesses an unfair advantage.", "LinkedIn post — long-form, professional edge, framework-style, 3-5 hashtags at bottom."),
            ("Write a LinkedIn post about the cost of not adopting AI in your business.", "LinkedIn post — direct, data-implied, no weak openers, ends with engagement CTA."),
        ],
        "x_twitter": [
            ("Write a single impactful tweet about AI and business leverage.", "One tweet — max 280 chars, punchy, 1-2 hashtags integrated naturally."),
            ("Write an X thread (5 tweets) about how to 10x your output using AI.", "Twitter thread — strong opener, each tweet stands alone, closing CTA."),
        ],
        "instagram": [
            ("Write an Instagram caption for a post about founder discipline.", "Instagram caption — hook first line, line breaks, 15-20 hashtags in first comment block."),
            ("Write an Instagram caption for a dark aesthetic workspace photo.", "Instagram caption — visual-first, punchy, save CTA, hashtag block at end."),
        ],
        "tiktok": [
            ("Write a 30-second TikTok script about one AI tool that changes how you work.", "TikTok script — hook in first 2 seconds, spoken delivery style, fast pace."),
            ("Write a TikTok hook for a video about why most entrepreneurs fail.", "TikTok hook only — first 3 seconds, stops the scroll immediately."),
        ],
        "youtube_shorts": [
            ("Write a 45-second YouTube Shorts script about building systems, not grinding.", "YouTube Shorts script — educational, one clear takeaway, subscribe CTA at end."),
        ],
        "medium": [
            ("Write a Medium article introduction about AI agent pipelines for entrepreneurs.", "Medium intro — analytical, structured, authority tone, sets up the full article."),
        ],
    }

    content_pillars = [p["name"] for p in persona.get("content_pillars", [])]
    pillar_examples = []

    for pillar in content_pillars:
        for platform_id, tasks in platform_tasks.items():
            platform_cfg = platforms.get(platform_id, {})
            platform_name = platform_cfg.get("name", platform_id.replace("_", " ").title())
            for task_prompt, task_hint in tasks[:1]:  # one per pillar/platform combo
                pillar_examples.append({
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": f"[Pillar: {pillar}] {task_prompt}"},
                        {
                            "role": "assistant",
                            "content": (
                                f"[{platform_name} — {pillar}]\n\n"
                                f"[This is a training placeholder. In production, this would be "
                                f"generated by the full pipeline. Format hint: {task_hint}]"
                            ),
                        },
                    ]
                })

    examples.extend(pillar_examples)
    return examples


def build_review_examples(persona: dict) -> list[dict]:
    """Generate persona review/safety check instruction pairs."""
    system = build_system_prompt(persona)

    weak_examples = [
        "I think AI could potentially be very useful for some businesses.",
        "It's important to note that leveraging technology in today's fast-paced world can perhaps help entrepreneurs.",
        "At the end of the day, AI is a game-changer that we need to dive deep into.",
    ]

    examples = []
    for weak_text in weak_examples:
        examples.append({
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Review this content for VirtuAI persona compliance:\n\n\"{weak_text}\"",
                },
                {
                    "role": "assistant",
                    "content": (
                        "REVIEW: FAIL — Persona violations detected.\n\n"
                        "Issues:\n"
                        "1. Soft/uncertain language — violates 'no fluff, authority-driven' style requirement.\n"
                        "2. Weak opener — no hook, no scroll-stopping first line.\n"
                        "3. Filler phrases detected — see banned phrase list.\n"
                        "4. No CTA — post ends without directing the reader.\n\n"
                        "Verdict: REWRITE REQUIRED\n"
                        "Action: Apply strong hook + direct tone + clear CTA pattern."
                    ),
                },
            ]
        })
    return examples


def build_image_analysis_examples(persona: dict) -> list[dict]:
    """Generate image analysis + caption instruction pairs."""
    system = build_system_prompt(persona)
    visual = persona.get("visual_identity", {})
    colors = visual.get("color_palette", {})
    img_style = visual.get("image_style", {})

    scenes = [
        "A dark minimal office with a laptop on a clean desk, soft blue ambient light, city view.",
        "A person in a black turtleneck at a standing desk, serious expression, evening light.",
        "Close-up of hands on a mechanical keyboard, dark background, green LED accents.",
        "An empty premium workspace at night: single desk lamp, notebook, laptop, no clutter.",
    ]

    examples = []
    for scene in scenes:
        examples.append({
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Analyze this image and suggest a content angle + caption:\n\nScene: {scene}",
                },
                {
                    "role": "assistant",
                    "content": (
                        f"IMAGE ANALYSIS:\n"
                        f"Visual mood: aligned with brand aesthetic — "
                        f"{img_style.get('mood', 'powerful, clean, modern')}.\n"
                        f"Color check: {colors.get('primary', '#0A0A0A')} background, "
                        f"accent lighting consistent with brand palette.\n\n"
                        f"CONTENT ANGLE: Execution over performance. The environment signals discipline.\n\n"
                        f"SUGGESTED CAPTION:\n"
                        f"The work that builds empires doesn't look glamorous.\n\n"
                        f"It looks like this.\n\n"
                        f"Focused. Intentional. Relentless.\n\n"
                        f"Build in silence. Let the results speak.\n\n"
                        f"Save this. 🎯"
                    ),
                },
            ]
        })
    return examples


def split_and_save(examples: list[dict], train_ratio: float = 0.8):
    """Shuffle and split examples into train/valid sets, appending to existing files."""
    random.seed(42)
    random.shuffle(examples)

    split = int(len(examples) * train_ratio)
    train_examples = examples[:split]
    valid_examples = examples[split:]

    train_path = OUTPUT_DIR / "train.jsonl"
    valid_path = OUTPUT_DIR / "valid.jsonl"

    # Append to existing files (hand-crafted examples stay at top)
    with open(train_path, "a", encoding="utf-8") as f:
        for ex in train_examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    with open(valid_path, "a", encoding="utf-8") as f:
        for ex in valid_examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    print(f"Dataset prepared:")
    print(f"  Train: {len(train_examples)} new examples appended → {train_path}")
    print(f"  Valid: {len(valid_examples)} new examples appended → {valid_path}")


def main():
    print("Loading persona config...")
    persona = load_yaml(PERSONAS_DIR / "virtuai_mentor.yaml")

    print("Loading platform configs...")
    platforms = {}
    for p in PLATFORMS_DIR.glob("*.yaml"):
        platforms[p.stem] = load_yaml(p)

    print("Building examples...")
    examples = []
    examples.extend(build_platform_examples(persona, platforms))
    examples.extend(build_review_examples(persona))
    examples.extend(build_image_analysis_examples(persona))

    print(f"Total generated examples: {len(examples)}")
    split_and_save(examples)
    print("Done. Dataset ready for MLX LoRA fine-tuning.")


if __name__ == "__main__":
    main()
