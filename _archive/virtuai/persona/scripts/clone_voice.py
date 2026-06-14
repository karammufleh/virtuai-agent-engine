"""
clone_voice.py — Generate Daniel Calder speech from text using F5-TTS.

Loads the locked reference WAV + transcript and runs F5-TTS in zero-shot
voice-cloning mode. Output is a 24 kHz WAV file in voice_clone/generated/.

Usage:
    python clone_voice.py "Text to speak"
    python clone_voice.py --text "..." --out custom.wav --seed 42

The first run downloads the F5TTS_v1_Base checkpoint to ~/.cache/huggingface/
(~1.4 GB). Subsequent runs are fully offline.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# torchaudio 2.11 → torchcodec → libavutil from FFmpeg. Brew's default ffmpeg
# is v8 (libavutil.60) but torchcodec 0.11 only supports up to FFmpeg 7.
# Re-exec ourselves with DYLD_FALLBACK_LIBRARY_PATH pointing at the keg-only
# ffmpeg@7 install, so dyld finds libavutil.59 when torchcodec loads.
# The env var must be set BEFORE process start, so we re-exec once.
_FFMPEG7_LIB = "/opt/homebrew/opt/ffmpeg@7/lib"
if Path(_FFMPEG7_LIB).is_dir():
    _existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if _FFMPEG7_LIB not in _existing.split(":"):
        _new_env = os.environ.copy()
        _new_env["DYLD_FALLBACK_LIBRARY_PATH"] = (
            f"{_FFMPEG7_LIB}:{_existing}" if _existing else _FFMPEG7_LIB
        )
        os.execvpe(sys.executable, [sys.executable] + sys.argv, _new_env)

import torch  # noqa: E402  (import after re-exec guarantees correct dyld env)

ROOT = Path(__file__).resolve().parents[3]
PERSONA_DIR = ROOT / "virtuai" / "persona"
REF_WAV = PERSONA_DIR / "voice_sample" / "daniel_voice_ref.wav"
REF_TXT = PERSONA_DIR / "voice_sample" / "daniel_voice_ref_trimmed.txt"
OUT_DIR = PERSONA_DIR / "voice_clone" / "generated"

DEFAULT_SEED = 42


def pick_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_model(device: str):
    from f5_tts.api import F5TTS
    return F5TTS(model="F5TTS_v1_Base", device=device)


def synthesize(
    text: str,
    out_path: Path,
    seed: int = DEFAULT_SEED,
    speed: float = 1.0,
    nfe_step: int = 32,
) -> dict:
    if not REF_WAV.exists():
        sys.exit(f"Reference WAV missing: {REF_WAV}\n"
                 "Run prep_voice_reference.py first.")
    if not REF_TXT.exists():
        sys.exit(f"Reference transcript missing: {REF_TXT}")

    ref_text = REF_TXT.read_text(encoding="utf-8").strip()
    if not ref_text:
        sys.exit(f"Reference transcript is empty: {REF_TXT}")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    device = pick_device()
    print(f"[F5-TTS] device={device}, loading model...")
    t0 = time.time()
    f5 = load_model(device)
    print(f"[F5-TTS] model loaded in {time.time() - t0:.1f}s")

    print(f"[F5-TTS] ref={REF_WAV.name} ({len(ref_text.split())} words ref text)")
    print(f"[F5-TTS] gen={len(text.split())} words → {out_path.name}")

    t0 = time.time()
    f5.infer(
        ref_file=str(REF_WAV),
        ref_text=ref_text,
        gen_text=text,
        file_wave=str(out_path),
        seed=seed,
        speed=speed,
        nfe_step=nfe_step,
        remove_silence=False,
    )
    elapsed = time.time() - t0

    import soundfile as sf
    info = sf.info(str(out_path))
    print(f"[F5-TTS] ✓ generated in {elapsed:.1f}s — "
          f"{info.duration:.2f}s of audio @ {info.samplerate} Hz")

    return {
        "audio_path": str(out_path),
        "duration_s": info.duration,
        "sample_rate": info.samplerate,
        "generation_time_s": elapsed,
        "seed": seed,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Daniel Calder speech via F5-TTS.")
    p.add_argument("text", nargs="?", help="Text to synthesize. If omitted, --text is required.")
    p.add_argument("--text", dest="text_flag", help="Alternative way to pass text.")
    p.add_argument("--out", default=None, help="Output WAV path (default: auto-named in voice_clone/generated/)")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier (1.0 = natural)")
    p.add_argument("--nfe", type=int, default=32, help="F5-TTS NFE steps (higher = better, slower)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    text = args.text or args.text_flag
    if not text:
        sys.exit("Usage: python clone_voice.py \"<text to speak>\"")

    if args.out:
        out_path = Path(args.out)
    else:
        ts = int(time.time())
        out_path = OUT_DIR / f"daniel_{ts}.wav"

    synthesize(text=text, out_path=out_path, seed=args.seed, speed=args.speed, nfe_step=args.nfe)


if __name__ == "__main__":
    main()
