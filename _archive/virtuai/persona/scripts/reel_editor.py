"""
reel_editor.py — Turn a static talking-head clip into a reel-style video.

Motion comes from EDITING, not generation. Pipeline:
  1. Whisper transcribes the audio with word-level timestamps.
  2. We write CapCut-style ASS subtitles (1-2 words per line, bold, animated).
  3. We plan an Edit Decision List (EDL) — alternating talking-head segments
     (with subtle punch-in zoom) and B-roll cutaways (with Ken Burns), every
     1.5-2.5 seconds, matching the reel patterns the user observed.
  4. ffmpeg renders 9:16 vertical mp4 with subs burnt in.

All inputs are assets we already have:
  - audio:           virtuai/persona/voice_clone/generated/* OR demo/<plat>/feed/<id>/audio.wav
  - talking head:    demo/<plat>/feed/<id>/video.mp4 (the existing Wav2Lip / stacked output)
  - B-roll pool:     the 4 LoRA-generated platform images (LinkedIn/X/IG/Medium)
                     — same identity (dnlcldr LoRA), different scenes

No new model inference. Just ffmpeg + Whisper.

Usage:
    python virtuai/persona/scripts/reel_editor.py \\
        --audio path/to/audio.wav \\
        --talking-head path/to/video.mp4 \\
        --out path/to/reel.mp4
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PERSONA = ROOT / "virtuai" / "persona"
DEMO = PERSONA / "demo"
# Default brew ffmpeg (8.1) was compiled without libass and libfreetype, so the
# `subtitles` and `drawtext` filters aren't available. We installed ffmpeg@7 for
# F5-TTS earlier — it has both. Use that binary explicitly.
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

# Default B-roll pool — the 4 LoRA-generated platform stills. Same Daniel face,
# different scenes. Falls back gracefully if any are missing.
DEFAULT_BROLL_POOL = [
    DEMO / "linkedin"  / "feed" / "2026-04-27_220921__the-unfair-advantage-ai-gives-to-operato" / "image.png",
    DEMO / "x"         / "feed" / "2026-04-27_221658__why-i-ll-start-when-i-m-ready-is-the-mos" / "image.png",
    DEMO / "instagram" / "feed" / "2026-04-27_222415__three-systems-that-compounded-my-output"  / "image.png",
    DEMO / "medium"    / "feed" / "2026-04-27_223132__the-difference-between-leverage-and-busy" / "image.png",
]


# ── Whisper transcription ───────────────────────────────────────────────────

@dataclass
class Word:
    word: str
    start: float
    end: float


def transcribe_words(audio_path: Path, model_name: str = "base") -> list[Word]:
    """Run Whisper, return word-level timestamps."""
    import whisper
    print(f"[whisper] loading model={model_name}...")
    model = whisper.load_model(model_name)
    print(f"[whisper] transcribing {audio_path.name}...")
    result = model.transcribe(str(audio_path), word_timestamps=True, verbose=False)
    words: list[Word] = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            words.append(Word(
                word=w["word"].strip(),
                start=float(w["start"]),
                end=float(w["end"]),
            ))
    print(f"[whisper] {len(words)} words")
    return words


# ── CapCut-style subtitle generator ─────────────────────────────────────────

def words_to_ass(words: list[Word], out_path: Path, *, video_w: int = 1080, video_h: int = 1920) -> None:
    """
    Write an ASS subtitle file with 1-2 word chunks, bold + bright + outlined,
    bottom-center, with a snappy fade-in. Mimics the modern reel/TikTok caption
    style described in the references.
    """
    # Group consecutive words into chunks of 1-2 words.
    chunks: list[list[Word]] = []
    i = 0
    while i < len(words):
        # 1 word for short ones, 2 for slightly longer / connector words
        chunk_size = 2 if (i + 1 < len(words) and len(words[i].word) <= 4 and len(words[i + 1].word) <= 5) else 1
        chunks.append(words[i:i + chunk_size])
        i += chunk_size

    # Style: large bold, white fill, thick black outline. PrimaryColour, OutlineColour are BGR hex.
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Reel,Helvetica,96,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,8,2,2,80,80,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def fmt_time(t: float) -> str:
        # ASS time: H:MM:SS.cs (centiseconds)
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t - 60 * (60 * h + m)
        return f"{h}:{m:02d}:{s:05.2f}"

    lines: list[str] = []
    for idx, chunk in enumerate(chunks):
        start = chunk[0].start
        # End at start of next chunk (no gaps), or last word's end + 0.25s
        if idx + 1 < len(chunks):
            end = chunks[idx + 1][0].start
        else:
            end = chunk[-1].end + 0.25
        text = " ".join(w.word for w in chunk).upper()
        # Strip trailing punctuation that hurts reel rhythm
        text = text.rstrip(",.")
        # Snap fade and small scale-pop for energy
        # Use \fad(80,80) for fade and \t for a tiny scale punch on entry
        effect = r"{\fad(60,60)\t(0,150,\fscx110\fscy110)\t(150,250,\fscx100\fscy100)}"
        lines.append(f"Dialogue: 0,{fmt_time(start)},{fmt_time(end)},Reel,,0,0,0,,{effect}{text}")

    out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"[subs] wrote {len(chunks)} caption chunks → {out_path.name}")


# ── EDL planner ─────────────────────────────────────────────────────────────

@dataclass
class Segment:
    start: float          # in the OUTPUT timeline
    duration: float
    kind: str             # 'talking' or 'broll'
    src_start: float = 0  # for talking: where in the talking-head clip to take from
    broll_idx: int = 0    # for broll: index into broll pool
    zoom: float = 1.0     # ending zoom level for Ken Burns / punch-in (start = 1.0)


def plan_edl(duration: float, n_broll: int, *, beat: float = 1.8) -> list[Segment]:
    """
    Build a beat-driven cut sequence. Every ~beat seconds we change shot:
      - Mostly talking-head with subtle punch-in zooms
      - Every 3rd or 4th beat: B-roll cutaway with Ken Burns
      - First beat: bigger punch-in for the hook
    Talking-head segments use src_start = output time, so the audio/lip-sync
    stays aligned. B-roll segments hold the audio underneath but show a still.
    """
    rng = random.Random(42)
    segs: list[Segment] = []
    t = 0.0
    seg_idx = 0
    next_broll = 0
    while t < duration:
        # Last segment fits remaining time
        seg_dur = min(beat + rng.uniform(-0.3, 0.4), duration - t)
        if seg_dur < 0.6:
            seg_dur = duration - t
            if seg_dur < 0.3:
                break

        # Decide shot type. First beat is always talking + hook punch-in.
        # Then ~25% B-roll cadence (every ~4th beat).
        if seg_idx == 0:
            kind = "talking"
            zoom = 1.18  # hook punch-in
        elif n_broll > 0 and seg_idx % 4 == 3:
            kind = "broll"
            zoom = 1.0 + rng.uniform(0.06, 0.14)  # gentle Ken Burns
        else:
            kind = "talking"
            zoom = 1.0 + rng.uniform(0.0, 0.10)  # subtle punch-in

        seg = Segment(
            start=t,
            duration=seg_dur,
            kind=kind,
            src_start=t if kind == "talking" else 0,
            broll_idx=next_broll if kind == "broll" else 0,
            zoom=zoom,
        )
        segs.append(seg)
        if kind == "broll":
            next_broll = (next_broll + 1) % max(n_broll, 1)
        t += seg_dur
        seg_idx += 1

    return segs


# ── ffmpeg compositor ───────────────────────────────────────────────────────

def render_segment(seg: Segment, talking_head: Path, broll: list[Path], tmp_dir: Path,
                   *, idx: int, audio: Path, video_w: int = 1080, video_h: int = 1920) -> Path:
    """Render ONE EDL segment to a temp mp4 (silent — audio gets mixed at the end)."""
    out = tmp_dir / f"seg_{idx:03d}.mp4"
    duration = seg.duration

    if seg.kind == "talking":
        # Trim talking head to matching window, scale to fill 9:16 with face
        # centered (scale to 1920 height first, then crop 1080 wide). Subtle
        # punch-in zoom: zoompan animates from z=1.0 to z=seg.zoom over the
        # segment.
        n_frames = max(int(duration * 30), 1)
        z_end = seg.zoom
        # zoompan animates across `d` output frames; we use the segment's full duration
        zp = (
            f"scale=1920:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,"
            f"zoompan=z='1.0+({z_end}-1.0)*on/{n_frames}':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={n_frames}:s={video_w}x{video_h}:fps=30"
        )
        cmd = [
            FFMPEG, "-y", "-loglevel", "error",
            "-ss", f"{seg.src_start:.3f}", "-t", f"{duration:.3f}",
            "-i", str(talking_head),
            "-an",
            "-vf", zp,
            "-r", "30",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(out),
        ]
    else:
        # B-roll still with Ken Burns. zoompan operates on the looped image.
        if not broll:
            raise RuntimeError("No B-roll images configured")
        img = broll[seg.broll_idx % len(broll)]
        n_frames = max(int(duration * 30), 1)
        z_end = seg.zoom
        zp = (
            f"scale=1920:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,"
            f"zoompan=z='1.0+({z_end}-1.0)*on/{n_frames}':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={n_frames}:s={video_w}x{video_h}:fps=30"
        )
        cmd = [
            FFMPEG, "-y", "-loglevel", "error",
            "-loop", "1", "-t", f"{duration:.3f}",
            "-i", str(img),
            "-vf", zp,
            "-r", "30",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(out),
        ]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not out.exists():
        sys.stderr.write(proc.stderr)
        raise RuntimeError(f"ffmpeg failed for segment {idx}: {seg}")
    return out


def concat_and_finalize(segment_paths: list[Path], audio: Path, ass_path: Path,
                        out_path: Path, *, total_duration: float) -> None:
    """Concat segment mp4s, mux original audio, burn subs."""
    # ffmpeg's `subtitles` filter is finicky with absolute paths (treats `:`
    # as a filter-arg separator). Workaround: chdir to the dir holding subs.ass
    # and reference it by basename only. Same for the segments list.
    work_dir = ass_path.parent
    list_path = work_dir / "_segments.txt"
    list_path.write_text("\n".join(f"file '{p.resolve()}'" for p in segment_paths), encoding="utf-8")

    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", list_path.name,
        "-i", str(audio.resolve()),
        "-vf", f"subtitles={ass_path.name}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{total_duration:.3f}",
        str(out_path.resolve()),
    ]
    print(f"[ffmpeg] concat + mux + sub burn → {out_path.name}")
    proc = subprocess.run(cmd, cwd=str(work_dir), capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        print("--- ffmpeg cmd ---")
        print(" ".join(cmd))
        print("--- ffmpeg stderr ---")
        print(proc.stderr)
        print("--- ffmpeg stdout ---")
        print(proc.stdout)
        raise RuntimeError(f"Final ffmpeg failed (exit {proc.returncode})")


def ffprobe_duration(path: Path) -> float:
    cmd = [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)]
    return float(subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip())


# ── Main ────────────────────────────────────────────────────────────────────

def render_reel(
    audio_path: Path,
    talking_head: Path,
    out_path: Path,
    *,
    broll: list[Path] | None = None,
    whisper_model: str = "base",
    keep_intermediates: bool = False,
) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_path.parent / f"_reel_tmp_{out_path.stem}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    duration = ffprobe_duration(audio_path)
    print(f"[reel] audio duration {duration:.2f}s, talking head {ffprobe_duration(talking_head):.2f}s")

    # 1. Words → ASS
    words = transcribe_words(audio_path, model_name=whisper_model)
    ass_path = tmp_dir / "subs.ass"
    words_to_ass(words, ass_path)

    # 2. EDL
    pool = [p for p in (broll or DEFAULT_BROLL_POOL) if Path(p).exists()]
    if not pool:
        print("[reel] WARNING: no B-roll images available; talking-only edits.")
    edl = plan_edl(duration, n_broll=len(pool))
    print(f"[reel] {len(edl)} segments — {sum(1 for s in edl if s.kind == 'broll')} B-roll cutaways")

    # 3. Render each segment
    seg_files: list[Path] = []
    for i, seg in enumerate(edl):
        path = render_segment(seg, talking_head, pool, tmp_dir, idx=i, audio=audio_path)
        seg_files.append(path)
        kind_marker = "🎤" if seg.kind == "talking" else "🖼"
        print(f"  {kind_marker} {i+1}/{len(edl)}  {seg.kind:8} {seg.duration:.2f}s  zoom→{seg.zoom:.2f}")

    # 4. Concat + audio mux + subtitle burn
    concat_and_finalize(seg_files, audio_path, ass_path, out_path, total_duration=duration)

    # 5. Cleanup
    if not keep_intermediates:
        for f in seg_files:
            f.unlink(missing_ok=True)
        (tmp_dir / "_segments.txt").unlink(missing_ok=True)
        ass_path.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass

    return {
        "out": str(out_path),
        "duration_s": duration,
        "n_segments": len(edl),
        "n_broll": sum(1 for s in edl if s.kind == "broll"),
        "n_words": len(words),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--audio", required=True)
    p.add_argument("--talking-head", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--whisper-model", default="base", choices=["tiny", "base", "small"])
    p.add_argument("--keep-intermediates", action="store_true")
    args = p.parse_args()
    res = render_reel(
        Path(args.audio),
        Path(args.talking_head),
        Path(args.out),
        whisper_model=args.whisper_model,
        keep_intermediates=args.keep_intermediates,
    )
    print(f"\n✓ {res['n_segments']} segments, {res['n_broll']} B-roll, {res['n_words']} words → {res['out']}")


if __name__ == "__main__":
    main()
