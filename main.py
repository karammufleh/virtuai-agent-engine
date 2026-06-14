"""
VirtuAI — Main Entry Point
Multi-agent content generation pipeline for the Daniel Calder AI persona.

LLM modes:
  --llm kie    (default) Agent reasoning via KIE.ai (Claude Sonnet 4.6).
  --llm local            Agent reasoning via local Phi-3.5-mini (needs 16 GB+ RAM).

Production stack:
  - Claude Sonnet 4.6 (via KIE)   — script writing + viral idea funnel
  - Kling 3.0 multi-shot (via KIE) — reels with native lipsync
  - Nano Banana 2 (via KIE)        — portraits + carousels
  - Suno (via KIE)                 — instrumental underbed
  - Composio                       — Instagram / LinkedIn / Facebook / X publishing
  - YouTube Direct OAuth           — YouTube Shorts upload (COPPA-correct)

Usage:
    python main.py                                        # Default, all platforms
    python main.py --llm local --platforms linkedin x     # Fully local, 2 platforms
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables (used only by optional publishers — core pipeline runs fully local)
load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from virtuai.pipelines.content_pipeline import build_content_crew


VALID_PLATFORMS = ["linkedin", "instagram", "facebook", "youtube_shorts"]


def _split_per_platform(result_text: str, platforms: list[str]) -> dict[str, str]:
    """
    Best-effort split of the crew's final string output into per-platform chunks.

    The Creator/Reviewer/Guardian agents emit per-platform JSON blocks in their
    output. This walks the final result and pulls out a per-platform slice when
    the platform name appears as a heading or JSON key. Non-matches fall back to
    saving the full result under the platform key — no information is lost.
    """
    chunks: dict[str, str] = {}
    for p in platforms:
        # Look for "platform_id": "...content..." or ## platform_id heading style
        # Tolerate underscores and the "x"/"x_twitter" alias.
        aliases = [p]
        if p == "x":
            aliases.append("x_twitter")
        if p == "x_twitter":
            aliases.append("x")

        slice_text = None
        for alias in aliases:
            # Try JSON-style key match first, fall back to heading match
            pattern = rf'"{alias}"\s*:\s*({{.*?}}|"[^"]*"|\[.*?\])'
            m = re.search(pattern, result_text, re.DOTALL | re.IGNORECASE)
            if m:
                slice_text = m.group(0)
                break
            heading = re.search(
                rf'(?:^|\n)\s*#+\s*{re.escape(alias)}\b.*?(?=\n#+\s|\Z)',
                result_text,
                re.DOTALL | re.IGNORECASE,
            )
            if heading:
                slice_text = heading.group(0).strip()
                break
        chunks[p] = slice_text or result_text  # graceful fallback
    return chunks


def main():
    parser = argparse.ArgumentParser(description="VirtuAI Content Generation Pipeline")
    parser.add_argument(
        "--platforms",
        nargs="+",
        choices=VALID_PLATFORMS,
        default=None,
        help="Target platforms (default: all enabled)",
    )
    parser.add_argument(
        "--persona",
        default="virtuai_mentor",
        help="Persona config name (default: virtuai_mentor)",
    )
    parser.add_argument(
        "--llm",
        choices=["local", "kie"],
        default="kie",
        help="Agent reasoning LLM: 'local' (Phi-3.5 via local backend) "
             "or 'kie' (KIE.ai DeepSeek). Default: kie.",
    )
    args = parser.parse_args()

    llm_label = {
        "local": "Phi-3.5-mini-instruct (4-bit, fine-tuned LoRA)",
        "kie": "KIE.ai DeepSeek (no daily quota) + local tools",
    }[args.llm]

    print("=" * 60)
    print("  VirtuAI — Multi-Agent Content Generation Pipeline")
    print(f"  Agent LLM: {llm_label}")
    print(f"  Scripting: Claude Sonnet 4.6 (KIE.ai gateway)")
    print(f"  Image:     Nano Banana 2 (KIE.ai)")
    print(f"  Reel:      Kling 3.0 → Seedance 2.0 fallback (KIE.ai)")
    print(f"  Music:     Suno (KIE.ai)")
    print(f"  Persona:   {args.persona}")
    print(f"  Platforms: {args.platforms or 'all enabled'}")
    print(f"  Started:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()

    # Build and run the crew
    crew = build_content_crew(
        target_platforms=args.platforms,
        persona_name=args.persona,
        llm_provider=args.llm,
    )

    result = crew.kickoff()

    # ── Save results ────────────────────────────────────────────────────────────
    output_dir = Path("virtuai/data/content_packages")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    platforms_used = args.platforms or VALID_PLATFORMS

    # Combined run file (kept for backward compatibility)
    combined_file = output_dir / f"run_{timestamp}.json"
    combined_payload = {
        "timestamp": timestamp,
        "persona": args.persona,
        "platforms": platforms_used,
        "result": str(result),
    }
    with open(combined_file, "w", encoding="utf-8") as f:
        json.dump(combined_payload, f, indent=2, ensure_ascii=False)

    # Per-platform breakdown (one JSON file per platform under run_<timestamp>/)
    per_platform_dir = output_dir / f"run_{timestamp}"
    per_platform_dir.mkdir(parents=True, exist_ok=True)
    chunks = _split_per_platform(str(result), platforms_used)
    for platform, content in chunks.items():
        path = per_platform_dir / f"{platform}.json"
        payload = {
            "timestamp": timestamp,
            "persona": args.persona,
            "platform": platform,
            "content": content,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 60)
    print(f"  Pipeline complete!")
    print(f"  Combined:    {combined_file}")
    print(f"  Per-platform: {per_platform_dir}/<platform>.json")
    print(f"  Next: scripts/daily_pack.py (full pack) or scripts/demo.py --no-publish (safe demo)")
    print("=" * 60)

    return result


if __name__ == "__main__":
    main()
