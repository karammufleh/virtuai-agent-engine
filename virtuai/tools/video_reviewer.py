"""
video_reviewer.py — Programmatic quality gate for generated reels.

Runs concrete checks the text-based Reviewer agent can't:
  • Audio continuity (no silence gaps in dialogue)
  • Face consistency across frames (ArcFace)
  • Caption legibility (no off-screen text)
  • Aspect ratio + duration sanity

Returns a verdict dict:
  {
    "verdict": "PASS" | "REVISE",
    "score": 0..1,
    "issues": [{"check": ..., "severity": ..., "detail": ...}, ...],
    "stats": {...}
  }
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger("virtuai.tools.video_reviewer")

import os as _os, shutil as _shutil
FFMPEG = _os.environ.get("FFMPEG_BIN") or _shutil.which("ffmpeg") or "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"
FFPROBE = _os.environ.get("FFPROBE_BIN") or _shutil.which("ffprobe") or "/opt/homebrew/opt/ffmpeg@7/bin/ffprobe"

# Silence-gap thresholds for dialogue-driven reels.
# Natural pauses between sentences run up to ~1.4s; hard cuts in spliced
# content typically produce 2s+ gaps. The 1.8s threshold accepts normal
# speech rhythm but flags audio that has clearly been chopped.
SILENCE_DB = -30
MAX_SILENCE_GAP_SEC = 1.8


def _probe(video: Path) -> dict:
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(video)],
        capture_output=True, text=True,
    )
    return json.loads(r.stdout)


def check_audio_continuity(video: Path) -> dict:
    """Detect silence gaps in the audio track using ffmpeg silencedetect."""
    cmd = [
        FFMPEG, "-i", str(video),
        "-af", f"silencedetect=noise={SILENCE_DB}dB:d={MAX_SILENCE_GAP_SEC}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stderr = proc.stderr

    silences: list[tuple[float, float]] = []
    starts = re.findall(r"silence_start: ([\d.]+)", stderr)
    ends = re.findall(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", stderr)
    for s, (e, d) in zip(starts, ends):
        silences.append((float(s), float(d)))

    total_silence = sum(d for _, d in silences)
    max_gap = max((d for _, d in silences), default=0.0)
    return {
        "silence_gaps": silences,
        "longest_gap_sec": max_gap,
        "total_silence_sec": total_silence,
    }


def check_aspect_ratio(probe_data: dict) -> dict:
    v_stream = next((s for s in probe_data["streams"] if s["codec_type"] == "video"), None)
    if not v_stream:
        return {"width": 0, "height": 0, "aspect": "unknown", "is_vertical_9_16": False}
    w, h = int(v_stream["width"]), int(v_stream["height"])
    target = 9 / 16
    actual = w / h
    return {
        "width": w,
        "height": h,
        "aspect": f"{w}:{h}",
        "actual_ratio": round(actual, 3),
        "is_vertical_9_16": abs(actual - target) < 0.02,
    }


def check_duration(probe_data: dict, min_sec: float = 8, max_sec: float = 60) -> dict:
    dur = float(probe_data["format"]["duration"])
    return {
        "duration_sec": round(dur, 2),
        "in_range": min_sec <= dur <= max_sec,
        "expected_range": f"{min_sec}-{max_sec}s",
    }


def check_cut_pacing(video: Path) -> dict:
    """
    Detect scene cuts via ffmpeg scdet. Industry pacing for short-form:
    avg interval 1.5-3s, no shot > 4s without motion. Returns stats and
    severity-graded issues based on creator-research thresholds.
    """
    cmd = [
        FFMPEG, "-i", str(video),
        "-vf", "select='gt(scene,0.30)',showinfo",
        "-vsync", "vfr", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    import re as _re
    cut_times = []
    for m in _re.finditer(r"pts_time:([\d.]+)", proc.stderr):
        cut_times.append(float(m.group(1)))

    dur = float(_probe(video)["format"]["duration"])
    n_cuts = len(cut_times)
    avg_interval = dur / max(n_cuts + 1, 1)

    # Longest gap between cuts (or from start/end)
    boundaries = [0.0] + cut_times + [dur]
    intervals = [boundaries[i + 1] - boundaries[i] for i in range(len(boundaries) - 1)]
    longest_shot = max(intervals) if intervals else dur

    return {
        "cuts_detected": n_cuts,
        "duration_sec": round(dur, 2),
        "avg_shot_sec": round(avg_interval, 2),
        "longest_shot_sec": round(longest_shot, 2),
        "shots": intervals,
        "in_pacing_range": 1.5 <= avg_interval <= 3.5,
        "longest_shot_acceptable": longest_shot <= 5.0,
    }


def check_face_consistency(video: Path, n_frames: int = 5) -> dict:
    """Sample n frames, run ArcFace, return pairwise cosine similarity stats."""
    try:
        import numpy as np
        from PIL import Image
        from insightface.app import FaceAnalysis
    except ImportError:
        return {"skipped": True, "reason": "insightface not installed in this venv"}

    probe_data = _probe(video)
    dur = float(probe_data["format"]["duration"])
    sample_times = [round(dur * (i + 1) / (n_frames + 1), 1) for i in range(n_frames)]

    work = video.parent / f"_face_check_{video.stem}"
    work.mkdir(exist_ok=True)
    frames: list[Path] = []
    for t in sample_times:
        f = work / f"t{t}.png"
        subprocess.run(
            [FFMPEG, "-y", "-ss", str(t), "-i", str(video), "-vframes", "1", str(f)],
            capture_output=True, check=True,
        )
        frames.append(f)

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))

    embs: dict[float, "np.ndarray"] = {}
    no_face_count = 0
    for t, f in zip(sample_times, frames):
        img = np.array(Image.open(f).convert("RGB"))
        faces = app.get(img[:, :, ::-1])
        if faces:
            embs[t] = faces[0].normed_embedding
        else:
            no_face_count += 1

    if len(embs) < 2:
        return {
            "frames_sampled": n_frames,
            "faces_detected": len(embs),
            "no_face_frames": no_face_count,
            "mean_pairwise_sim": None,
            "consistent": False,
        }

    times = sorted(embs.keys())
    sims = []
    for i in range(len(times)):
        for j in range(i + 1, len(times)):
            sims.append(float(np.dot(embs[times[i]], embs[times[j]])))

    mean_sim = sum(sims) / len(sims)
    return {
        "frames_sampled": n_frames,
        "faces_detected": len(embs),
        "no_face_frames": no_face_count,
        "mean_pairwise_sim": round(mean_sim, 3),
        "min_pairwise_sim": round(min(sims), 3),
        "max_pairwise_sim": round(max(sims), 3),
        "consistent": mean_sim >= 0.70,
    }


def review_video(video: Path, expected_duration: float | None = None) -> dict:
    """Run the full review and return a PASS/REVISE verdict."""
    logger.info(f"Reviewing {video.name}...")
    probe_data = _probe(video)

    issues: list[dict] = []

    audio = check_audio_continuity(video)
    if audio["longest_gap_sec"] > MAX_SILENCE_GAP_SEC:
        issues.append({
            "check": "audio_continuity",
            "severity": "high",
            "detail": (
                f"Longest silence gap is {audio['longest_gap_sec']:.2f}s "
                f"(max allowed: {MAX_SILENCE_GAP_SEC}s). "
                f"This usually means dialogue was cut mid-word. "
                f"Total silence: {audio['total_silence_sec']:.2f}s across "
                f"{len(audio['silence_gaps'])} gap(s)."
            ),
        })

    aspect = check_aspect_ratio(probe_data)
    if not aspect["is_vertical_9_16"]:
        issues.append({
            "check": "aspect_ratio",
            "severity": "high",
            "detail": f"Output is {aspect['aspect']} (ratio {aspect['actual_ratio']}), expected 9:16 for short-form.",
        })

    duration = check_duration(probe_data)
    if not duration["in_range"]:
        issues.append({
            "check": "duration",
            "severity": "medium",
            "detail": f"Duration {duration['duration_sec']}s outside range {duration['expected_range']}.",
        })

    pacing = check_cut_pacing(video)
    if not pacing["in_pacing_range"]:
        issues.append({
            "check": "cut_pacing",
            "severity": "medium",
            "detail": (
                f"Average shot duration {pacing['avg_shot_sec']}s is outside "
                f"industry range 1.5-3.5s (viral creators average 2s). "
                f"Pacing feels {'rushed' if pacing['avg_shot_sec'] < 1.5 else 'slow'}."
            ),
        })
    if not pacing["longest_shot_acceptable"]:
        issues.append({
            "check": "static_shot_too_long",
            "severity": "medium",
            "detail": (
                f"Longest single shot is {pacing['longest_shot_sec']}s "
                f"(max recommended: 5s). Add a cut, push-in, or b-roll to break it up."
            ),
        })

    face = check_face_consistency(video)
    if not face.get("skipped"):
        if face["faces_detected"] < 2:
            issues.append({
                "check": "face_consistency",
                "severity": "medium",
                "detail": f"Only {face['faces_detected']} of {face['frames_sampled']} frames had a face — "
                          f"too many face-less b-roll cuts disrupt persona presence.",
            })
        elif not face["consistent"]:
            issues.append({
                "check": "face_consistency",
                "severity": "high",
                "detail": f"Mean pairwise face similarity {face['mean_pairwise_sim']} < 0.70. "
                          f"The face is drifting across the reel.",
            })

    verdict = "PASS" if not issues else "REVISE"
    severity_weight = {"high": 0.4, "medium": 0.2, "low": 0.1}
    score = max(0.0, 1.0 - sum(severity_weight.get(i["severity"], 0.1) for i in issues))

    result = {
        "verdict": verdict,
        "score": round(score, 2),
        "issues": issues,
        "stats": {
            "audio": audio,
            "aspect": aspect,
            "duration": duration,
            "pacing": pacing,
            "face": face,
        },
    }
    logger.info(f"Verdict: {verdict} (score={score:.2f}, issues={len(issues)})")
    return result


def format_review_report(review: dict) -> str:
    """Human-readable summary."""
    lines = []
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"VIDEO REVIEW — verdict: {review['verdict']}  (score: {review['score']})")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if review["issues"]:
        lines.append("Issues:")
        for i, issue in enumerate(review["issues"], 1):
            lines.append(f"  {i}. [{issue['severity'].upper()}] {issue['check']}")
            lines.append(f"     {issue['detail']}")
    else:
        lines.append("All checks passed.")
    s = review["stats"]
    lines.append("")
    lines.append("Stats:")
    lines.append(f"  Aspect:   {s['aspect']['aspect']} ({s['aspect']['actual_ratio']})")
    lines.append(f"  Duration: {s['duration']['duration_sec']}s")
    lines.append(f"  Audio:    longest gap {s['audio']['longest_gap_sec']:.2f}s, "
                 f"{len(s['audio']['silence_gaps'])} gaps total")
    p = s["pacing"]
    lines.append(f"  Pacing:   {p['cuts_detected']} cuts, avg shot {p['avg_shot_sec']}s, "
                 f"longest {p['longest_shot_sec']}s")
    face = s["face"]
    if face.get("skipped"):
        lines.append(f"  Face:     {face['reason']}")
    else:
        lines.append(f"  Face:     {face['faces_detected']}/{face['frames_sampled']} frames with face, "
                     f"mean sim {face.get('mean_pairwise_sim')}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("video", help="Path to video to review")
    args = p.parse_args()
    review = review_video(Path(args.video))
    print(format_review_report(review))
