"""
prep_voice_reference.py — Trims the reference MP3 to an F5-TTS-optimal segment.

F5-TTS produces best zero-shot results with:
  - 8 to 15 seconds of clean speech
  - Cut at a silence boundary (no mid-word truncation)
  - 24 kHz mono WAV
  - Matched transcript of EXACTLY what's spoken in the trimmed clip

This script:
  1. Loads daniel_voice_ref.mp3
  2. Resamples to 24 kHz mono
  3. Detects silences via librosa.effects.split (energy-based)
  4. Picks the best cut point near a target duration (default 12 s)
  5. Writes daniel_voice_ref.wav (the trimmed clip F5-TTS will use)
  6. Writes daniel_voice_ref_trimmed.txt (placeholder — user must trim
     the transcript to match what's actually spoken in the WAV)
"""
from __future__ import annotations

import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[3]
VOICE_DIR = ROOT / "virtuai" / "persona" / "voice_sample"
SOURCE_MP3 = VOICE_DIR / "daniel_voice_ref.mp3"
OUTPUT_WAV = VOICE_DIR / "daniel_voice_ref.wav"
OUTPUT_TXT_FULL = VOICE_DIR / "daniel_voice_ref.txt"
OUTPUT_TXT_TRIMMED = VOICE_DIR / "daniel_voice_ref_trimmed.txt"

TARGET_SR = 24_000          # F5-TTS native sample rate
TARGET_DURATION_S = 11.0    # under F5-TTS's 12s internal clip threshold
MIN_DURATION_S = 8.0        # never cut shorter than this
MAX_DURATION_S = 11.8       # stay just under F5-TTS's 12s threshold

# librosa.effects.split parameters
TOP_DB = 30                 # threshold below ref to consider silence
FRAME_LENGTH = 2048
HOP_LENGTH = 512


def find_best_cut(audio: np.ndarray, sr: int, target_s: float) -> int:
    """
    Returns sample index for the best cut point — the silence boundary
    nearest to target_s that falls within [MIN, MAX] duration.
    """
    intervals = librosa.effects.split(
        audio, top_db=TOP_DB, frame_length=FRAME_LENGTH, hop_length=HOP_LENGTH
    )
    if len(intervals) == 0:
        return min(len(audio), int(target_s * sr))

    # Each interval is [start, end] of non-silent speech. The cut should land
    # at the END of a speech interval (so we cut at silence after a word).
    speech_ends = [end for _, end in intervals]

    target_sample = int(target_s * sr)
    min_sample = int(MIN_DURATION_S * sr)
    max_sample = int(MAX_DURATION_S * sr)

    # Pick the speech-end that's closest to target while inside [min, max]
    candidates = [s for s in speech_ends if min_sample <= s <= max_sample]
    if not candidates:
        # No silence in range — fall back to the latest end <= max_sample
        capped = [s for s in speech_ends if s <= max_sample]
        if capped:
            return capped[-1]
        return target_sample

    best = min(candidates, key=lambda s: abs(s - target_sample))
    return best


def main() -> None:
    if not SOURCE_MP3.exists():
        sys.exit(f"Source not found: {SOURCE_MP3}")
    if not OUTPUT_TXT_FULL.exists():
        sys.exit(f"Full transcript missing: {OUTPUT_TXT_FULL}")

    print(f"Loading {SOURCE_MP3.name}...")
    audio, sr = librosa.load(SOURCE_MP3, sr=TARGET_SR, mono=True)
    print(f"  Loaded: {len(audio) / sr:.2f} s @ {sr} Hz mono")

    cut_idx = find_best_cut(audio, sr, TARGET_DURATION_S)
    cut_s = cut_idx / sr
    print(f"\nBest cut point: sample {cut_idx} ({cut_s:.2f} s)")
    print(f"  → trimmed clip is {cut_s:.2f} s long")

    trimmed = audio[:cut_idx]

    # Apply a short fade-out (10 ms) to avoid a click at the cut
    fade_samples = int(0.01 * sr)
    if len(trimmed) > fade_samples:
        fade = np.linspace(1.0, 0.0, fade_samples, dtype=trimmed.dtype)
        trimmed[-fade_samples:] = trimmed[-fade_samples:] * fade

    sf.write(OUTPUT_WAV, trimmed, sr, subtype="PCM_16")
    print(f"\n✓ Wrote {OUTPUT_WAV}")

    # Estimate which words landed in the trim, based on speech rate
    full_text = OUTPUT_TXT_FULL.read_text(encoding="utf-8").strip()
    full_audio_duration = len(audio) / sr
    fraction = cut_s / full_audio_duration
    words = full_text.split()
    estimated_word_count = max(1, int(len(words) * fraction))
    estimated_text = " ".join(words[:estimated_word_count])

    OUTPUT_TXT_TRIMMED.write_text(estimated_text + "\n", encoding="utf-8")
    print(f"\n✓ Estimated transcript for trimmed clip → {OUTPUT_TXT_TRIMMED.name}")
    print(f"  ({estimated_word_count} words, est. based on uniform speech rate)")
    print()
    print("─── ESTIMATED TRIMMED TRANSCRIPT ───")
    print(estimated_text)
    print("────────────────────────────────────")
    print()
    print("ACTION REQUIRED: open the WAV in any audio app, listen to it,")
    print(f"then edit {OUTPUT_TXT_TRIMMED.name} so it matches EXACTLY what was spoken.")
    print("F5-TTS quality depends heavily on transcript-audio alignment.")


if __name__ == "__main__":
    main()
