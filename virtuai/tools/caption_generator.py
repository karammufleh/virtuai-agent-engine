"""
caption_generator.py — Whisper word-timestamps → ASS subtitle generator.

Produces CapCut-style word-by-word reveal captions for reel_builder.py
to burn into the final video via FFmpeg's ass filter.

Standards enforced (from PROJECT_STANDARDS.md §3):
  - Word-by-word reveal, 1-3 words per card
  - Centered in lower-middle third (Y ≈ 70% from top)
  - Bold sans-serif (Montserrat Black / Inter ExtraBold / Anton)
  - White text, yellow/lime highlight on key words
  - Black outline (4px) + drop shadow for legibility
  - Pop animation per word group (scale 80% → 100%, 100ms)
  - Timing from Whisper word-level timestamps (≤50ms lag)

Public API:
    create_captions(audio_path, output_path=None, highlight_keywords=None)
        -> Path to generated .ass file
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("virtuai.tools.caption_generator")

# ── ASS constants ────────────────────────────────────────────────────────────

# 720×1280 vertical video (9:16)
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 1280

# Fonts in preference order — FFmpeg picks the first available on the system
FONT_NAME = "Montserrat Black"
# Industry standard for top creators (Hormozi, Welsh, Sanchez): 90-120px on
# 1080×1920. Scaled to our 720×1280 canvas: 60-80px. Bumped from 56→72 for
# punchier readability above the TikTok UI overlay.
FONT_SIZE = 72

# ASS colors are in &HBBGGRR& format
WHITE = "&H00FFFFFF"
YELLOW = "&H0000FFFF"      # highlight colour 1
LIME = "&H0000FF00"        # highlight colour 2
OUTLINE_COLOR = "&H00000000"  # black
SHADOW_COLOR = "&H80000000"   # semi-transparent black

OUTLINE_PX = 4
SHADOW_PX = 2

# Pop animation: scale 80→100% over 100ms
POP_IN = r"{\fscx80\fscy80\t(0,100,\fscx100\fscy100)}"

# Words that signal "highlight the group" — numbers, tools, strong nouns
_HIGHLIGHT_PATTERNS = [
    re.compile(r"\d"),                     # any digit
    re.compile(r"\$"),                     # dollar amounts
    re.compile(r"(?i)(ai|gpt|claude|agent|prompt|automate|scale|10x|replace|save|built|deleted|audit)"),
]


def _is_highlight_word(word: str) -> bool:
    return any(p.search(word) for p in _HIGHLIGHT_PATTERNS)


# ── Word grouping ────────────────────────────────────────────────────────────

def _group_words(words: list[dict], max_per_group: int = 2) -> list[dict]:
    """
    Group transcribed words into 1-3 word caption cards.

    Rules:
      - Default 2 words per group
      - Punctuation-ending words always close a group
      - Short articles/prepositions attach forward (don't appear alone)
    """
    SHORT_WORDS = {"a", "an", "the", "in", "on", "at", "to", "of", "for", "is",
                   "it", "my", "i", "we", "or", "and", "but", "so", "if", "by",
                   "no", "not", "with", "this", "that"}

    groups: list[dict] = []
    current: list[dict] = []

    for w in words:
        current.append(w)
        text = w["word"].strip()

        ends_with_punct = text and text[-1] in ".!?,;:"
        is_short = text.lower().strip(".,!?;:") in SHORT_WORDS
        at_limit = len(current) >= max_per_group

        if ends_with_punct or (at_limit and not is_short):
            groups.append(_merge_group(current))
            current = []

        # Safety: don't let a group exceed 3 words
        if len(current) >= 3:
            groups.append(_merge_group(current))
            current = []

    if current:
        groups.append(_merge_group(current))

    return groups


def _merge_group(words: list[dict]) -> dict:
    text = " ".join(w["word"].strip() for w in words)
    return {
        "text": text,
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "has_highlight": any(_is_highlight_word(w["word"]) for w in words),
    }


# ── ASS generation ───────────────────────────────────────────────────────────

def _format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _build_ass(
    groups: list[dict],
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    highlight_keywords: set[str] | None = None,
) -> str:
    """Build a complete ASS subtitle string from word groups."""

    # Y position: industry standard ~50-55% from top puts the text in the
    # vertical center, above the TikTok UI footer but below the face.
    # MarginV is distance from bottom (alignment 2 = bottom-center anchor).
    margin_v = int(height * 0.42)

    header = f"""[Script Info]
Title: VirtuAI Captions
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT_NAME},{FONT_SIZE},{WHITE},&H000000FF,{OUTLINE_COLOR},{SHADOW_COLOR},-1,0,0,0,100,100,0,0,1,{OUTLINE_PX},{SHADOW_PX},2,10,10,{margin_v},1
Style: Highlight,{FONT_NAME},{FONT_SIZE},{YELLOW},&H000000FF,{OUTLINE_COLOR},{SHADOW_COLOR},-1,0,0,0,100,100,0,0,1,{OUTLINE_PX},{SHADOW_PX},2,10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []
    for g in groups:
        start = _format_ass_time(g["start"])
        end = _format_ass_time(g["end"])
        text = g["text"].upper()

        # Decide style
        use_highlight = g["has_highlight"]
        if highlight_keywords:
            for kw in highlight_keywords:
                if kw.lower() in g["text"].lower():
                    use_highlight = True

        style = "Highlight" if use_highlight else "Default"

        # Pop animation override tag
        line = f"Dialogue: 0,{start},{end},{style},,0,0,0,,{POP_IN}{text}"
        lines.append(line)

    return header + "\n".join(lines) + "\n"


# ── Whisper transcription ────────────────────────────────────────────────────

def _transcribe_words(audio_path: str | Path, model_size: str = "base") -> list[dict]:
    """
    Run Whisper on an audio file and return word-level timestamps.

    Returns list of {"word": str, "start": float, "end": float}.
    """
    import whisper

    logger.info(f"Loading Whisper '{model_size}' model...")
    model = whisper.load_model(model_size)

    logger.info(f"Transcribing {audio_path} with word timestamps...")
    result = model.transcribe(
        str(audio_path),
        word_timestamps=True,
        language="en",
        fp16=False,  # MPS/CPU safe
    )

    words = []
    for segment in result.get("segments", []):
        for w in segment.get("words", []):
            words.append({
                "word": w["word"],
                "start": w["start"],
                "end": w["end"],
            })

    logger.info(f"Transcribed {len(words)} words, duration {words[-1]['end']:.1f}s")
    return words


# ── Public API ───────────────────────────────────────────────────────────────

def create_captions(
    audio_path: str | Path,
    output_path: str | Path | None = None,
    highlight_keywords: set[str] | None = None,
    whisper_model: str = "base",
    video_width: int = DEFAULT_WIDTH,
    video_height: int = DEFAULT_HEIGHT,
    words_per_group: int = 2,
) -> Path:
    """
    Generate an ASS subtitle file from audio using Whisper word timestamps.

    Args:
        audio_path:         Path to WAV/MP3 audio file.
        output_path:        Where to write the .ass file. Defaults to
                            same directory as audio with .ass extension.
        highlight_keywords: Extra words to force-highlight (yellow).
        whisper_model:      Whisper model size: tiny/base/small/medium.
        video_width:        Target video width (default 720).
        video_height:       Target video height (default 1280).
        words_per_group:    Words per caption card (default 2, max 3).

    Returns:
        Path to the generated .ass file.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if output_path is None:
        output_path = audio_path.with_suffix(".ass")
    output_path = Path(output_path)

    words = _transcribe_words(audio_path, model_size=whisper_model)
    groups = _group_words(words, max_per_group=words_per_group)

    logger.info(f"{len(words)} words → {len(groups)} caption groups")

    ass_content = _build_ass(
        groups,
        width=video_width,
        height=video_height,
        highlight_keywords=highlight_keywords,
    )

    output_path.write_text(ass_content)
    logger.info(f"Wrote ASS captions to {output_path}")
    return output_path


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    p = argparse.ArgumentParser(description="Generate CapCut-style ASS captions from audio")
    p.add_argument("audio", help="Path to WAV/MP3 audio file")
    p.add_argument("-o", "--output", help="Output .ass path (default: same dir as audio)")
    p.add_argument("--model", default="base", choices=["tiny", "base", "small", "medium"],
                   help="Whisper model size (default: base)")
    p.add_argument("--words", type=int, default=2, help="Words per caption group (default: 2)")
    p.add_argument("--highlight", nargs="*", help="Extra words to highlight in yellow")
    args = p.parse_args()

    result = create_captions(
        audio_path=args.audio,
        output_path=args.output,
        whisper_model=args.model,
        words_per_group=args.words,
        highlight_keywords=set(args.highlight) if args.highlight else None,
    )
    print(f"\nCaption file: {result}")
