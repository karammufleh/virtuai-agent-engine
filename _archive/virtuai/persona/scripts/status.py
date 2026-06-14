"""
status.py — One-shot health check for the VirtuAI persona pipeline.

Reports on every layer: text model, image LoRA training, voice clone, talking
head, topic memory, eval framework. Run anytime to see what's ready and what
still needs attention.

Usage:
    python virtuai/persona/scripts/status.py
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PERSONA = ROOT / "virtuai" / "persona"

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RST = "\033[0m"


def line(label: str, status: str, detail: str = "") -> None:
    color = {"ok": GREEN, "wait": YELLOW, "fail": RED}.get(status, "")
    badge = {"ok": "✓", "wait": "…", "fail": "✗"}.get(status, "?")
    extra = f"  {DIM}{detail}{RST}" if detail else ""
    print(f"  {color}{badge}{RST} {label}{extra}")


def section(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{title}{RST}")


def main() -> None:
    print(f"{BOLD}VirtuAI Persona Status — {ROOT.name}{RST}")

    # ── Text model (Phi-3.5 LoRA) ────────────────────────────────────────────
    section("Text — Phi-3.5-mini-instruct (LoRA fused)")
    fused = ROOT / "virtuai" / "models" / "finetune" / "fused_model"
    if fused.exists() and any(fused.iterdir()):
        line("fused model present", "ok", str(fused.relative_to(ROOT)))
    else:
        line("fused model present", "fail", "run train_lora.sh")

    # ── Persona anchor ───────────────────────────────────────────────────────
    section("Persona anchor")
    anchor_path = PERSONA / "persona_anchor.json"
    if anchor_path.exists():
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        line("persona_anchor.json", "ok",
             f"trigger='{anchor.get('trigger_token')}' image_prefix='{anchor['prompts']['image_prefix']}'")
    else:
        line("persona_anchor.json", "fail", "missing")

    # ── Face LoRA training ───────────────────────────────────────────────────
    section("Face LoRA — Z-Image-Turbo + Daniel face")
    pid_file = PERSONA / "training_logs" / "current.pid"
    runs = sorted(PERSONA.glob("training_runs/*/**/*.safetensors"))
    if pid_file.exists():
        pid = pid_file.read_text().strip()
        try:
            # Include `pid` in the format so we can detect liveness via output match.
            r = subprocess.run(["ps", "-p", pid, "-o", "pid,etime,pcpu,pmem"], capture_output=True, text=True)
            # Liveness: data row exists past the header
            rows = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
            running = len(rows) >= 2  # header + at least one data row
            if running:
                stats = rows[-1].strip()
                line("training process", "wait", f"PID {pid}, {stats}")
            else:
                line("training process", "fail", f"PID {pid} not running (stale current.pid)")
        except Exception:
            line("training process", "fail", "ps failed")
    else:
        line("training process", "ok", "not running (training is finished or not started)")

    # mflux writes checkpoints to <project_root>/training_runs*/ (CWD-relative
    # output_path), NOT to virtuai/persona/training_runs/. Look at both for safety.
    checkpoint_zips: list[Path] = []
    for parent in ROOT.glob("training_runs*"):
        ckpt_dir = parent / "checkpoints"
        if ckpt_dir.is_dir():
            checkpoint_zips.extend(ckpt_dir.glob("*_checkpoint.zip"))
    extracted = list((PERSONA / "training_runs" / "_extracted").glob("*.safetensors"))
    if extracted:
        latest = max(extracted, key=lambda p: p.stat().st_mtime)
        size_mb = latest.stat().st_size / 1e6
        line("extracted LoRA (inference-ready)", "ok", f"{latest.relative_to(ROOT)} ({size_mb:.1f} MB)")
    elif checkpoint_zips:
        latest_zip = max(checkpoint_zips, key=lambda p: p.stat().st_mtime)
        line("checkpoint zips", "ok",
             f"{len(checkpoint_zips)} found, newest: {latest_zip.relative_to(ROOT)}")
        line("extracted LoRA", "wait", "run eval_lora_checkpoint.py to extract + test")
    elif runs:
        latest = runs[-1]
        size_mb = latest.stat().st_size / 1e6
        line("legacy LoRA weights", "ok", f"{latest.relative_to(ROOT)} ({size_mb:.1f} MB)")
    else:
        line("trained LoRA weights", "wait", "no checkpoints yet")

    # ── Voice clone ──────────────────────────────────────────────────────────
    section("Voice clone — F5-TTS v1 Base")
    ref_wav = PERSONA / "voice_sample" / "daniel_voice_ref.wav"
    ref_txt = PERSONA / "voice_sample" / "daniel_voice_ref_trimmed.txt"
    if ref_wav.exists() and ref_txt.exists():
        # Get duration via soundfile if available
        try:
            import soundfile as sf
            d = sf.info(str(ref_wav)).duration
            line("reference WAV + transcript", "ok", f"{d:.2f} s @ 24 kHz, transcript {ref_txt.stat().st_size} bytes")
        except Exception:
            line("reference WAV + transcript", "ok", "ref + txt both present")
    else:
        line("reference WAV + transcript", "fail", "run prep_voice_reference.py")

    gen_dir = PERSONA / "voice_clone" / "generated"
    if gen_dir.exists():
        wavs = list(gen_dir.glob("*.wav"))
        if wavs:
            line("generated voice clips", "ok", f"{len(wavs)} files in voice_clone/generated/")
        else:
            line("generated voice clips", "wait", "none yet (call POST /generate-voice)")

    # ── Talking head (SadTalker) ─────────────────────────────────────────────
    section("Talking-head — SadTalker (isolated venv)")
    sad_py = Path("/Users/karammufleh/virtuai-sadtalker-venv/bin/python")
    sad_ckpt = PERSONA / "sadtalker" / "checkpoints" / "SadTalker_V0.0.2_512.safetensors"
    line("sadtalker venv", "ok" if sad_py.exists() else "fail", str(sad_py))
    line("sadtalker checkpoints", "ok" if sad_ckpt.exists() else "fail",
         f"{sad_ckpt.stat().st_size / 1e6:.0f} MB" if sad_ckpt.exists() else "missing")
    th_dir = PERSONA / "talking_head" / "generated"
    if th_dir.exists():
        mp4s = list(th_dir.rglob("*.mp4"))
        line("generated talking-head videos",
             "ok" if mp4s else "wait",
             f"{len(mp4s)} mp4 files" if mp4s else "none yet (call POST /generate-talking-head)")

    # ── Topic memory ─────────────────────────────────────────────────────────
    section("Topic memory — FAISS anti-repetition (Phase 4)")
    tm_index = PERSONA / "topic_memory_data" / "topic.index"
    tm_meta = PERSONA / "topic_memory_data" / "records.json"
    if tm_index.exists() and tm_meta.exists():
        meta = json.loads(tm_meta.read_text(encoding="utf-8"))
        n = len(meta.get("records", []))
        line("FAISS index", "ok", f"{n} posts indexed")
    else:
        line("FAISS index", "wait", "not bootstrapped — run topic_memory.py bootstrap")

    # ── Eval ─────────────────────────────────────────────────────────────────
    section("Consistency eval — Phase 5")
    face_bank = PERSONA / "eval" / "_cache" / "daniel_reference_bank.npy"
    text_centroid = PERSONA / "eval" / "_cache" / "text_style_centroid.npy"
    line("face reference bank", "ok" if face_bank.exists() else "wait",
         "daniel_reference_bank.npy" if face_bank.exists() else "run face_similarity.py bootstrap")
    line("text style centroid", "ok" if text_centroid.exists() else "wait",
         "text_style_centroid.npy" if text_centroid.exists() else "run text_style.py bootstrap")
    reports = sorted((PERSONA / "eval" / "_reports").glob("consistency_*.md"), key=lambda p: p.stat().st_mtime)
    if reports:
        line("latest report", "ok", str(reports[-1].relative_to(ROOT)))
    else:
        line("latest report", "wait", "run run_consistency_report.py")

    # ── Backend ──────────────────────────────────────────────────────────────
    section("Backend (port 8765)")
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:8765/health", timeout=3) as r:
            data = json.load(r)
        for k in ("text_model", "image_model", "voice_model", "voice_loaded",
                  "persona_lora_loaded", "talking_head_capable"):
            v = data.get(k)
            if isinstance(v, bool):
                line(k, "ok" if v else "wait", "True" if v else "False")
            else:
                line(k, "ok" if v else "wait", str(v) if v else "—")
    except Exception as e:
        line("backend reachable", "fail", str(e))


if __name__ == "__main__":
    main()
