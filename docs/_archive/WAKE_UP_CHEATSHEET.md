# 🌅 Wake-up cheatsheet

Read this first. Everything else is in `CAPSTONE.md`.

## 30-second demo (in this exact order)

1. **Open the showcase site** — http://localhost:8080
   - Top section is the hero (1024×1024, stacked SadTalker + Wav2Lip)
   - Middle is the 7-platform grid: LinkedIn / X / Instagram / Medium (image) and TikTok / IG Reels / YouTube Shorts (talking-head video)
   - Click any video to play it — mp4 with synced audio

2. **Show the consistency numbers** — open `virtuai/persona/eval/_reports/final_consistency.json`
   ```
   before: mean 0.301, 0/29 strong, 13/29 drift
   after:  mean 0.664 (+120%), 8/10 strong, 0/10 drift
   ```

3. **Show the architecture** — open `CAPSTONE.md` and scroll to "Architectural pivots"
   - HeyGen → fully local
   - FLUX.1 → Z-Image-Turbo
   - LivePortrait → SadTalker → Wav2Lip
   - Imagen → Z-Image-Turbo + dnlcldr LoRA

## If something is wrong

| Problem | Fix |
|---|---|
| Site won't load | `python run_website.py` from project root |
| Backend not responding | `python run_backend.py` from project root |
| Want to regenerate one platform's video | `python virtuai/persona/scripts/wav2lip_render.py --face virtuai/persona/face_dataset/daniel_hero.png --audio virtuai/persona/demo/<platform>/audio.wav` |
| Want to regenerate one platform's image | Edit `PLATFORM_SCENES` in `virtuai/persona/scripts/render_platform_images.py` and `--platforms <name>` |
| Want a fresh consistency report | `python virtuai/persona/eval/run_consistency_report.py` |
| Want to check what's healthy | `python virtuai/persona/scripts/status.py` |

## What lives where

- **Final demo content the panel sees:** `virtuai/persona/demo/<platform>/`
- **Trained LoRA:** `virtuai/persona/training_runs/_extracted/0000455_checkpoint_adapter.safetensors`
- **Voice clone reference:** `virtuai/persona/voice_sample/daniel_voice_ref.wav` + `.txt`
- **Hero showcase mp4:** `virtuai/persona/talking_head/hero_showcase_stacked.mp4`
- **Persona anchor (single source of truth):** `virtuai/persona/persona_anchor.json`
- **Consistency eval reports:** `virtuai/persona/eval/_reports/`

## Probable panel questions and one-line answers

| Q | A |
|---|---|
| Why didn't you use HeyGen? | "It's a closed-source SaaS — no fine-tuning, no LoRA, nothing for me to defend as my own engineering. We trained the face LoRA, cloned the voice locally, and built the eval framework so the persona is reproducibly ours." |
| Why Wav2Lip if Hallo3 / EchoMimic v3 are SOTA? | "All current SOTA talking-head models target NVIDIA H100/A100. There is no working MPS port. We picked the best Apple Silicon-compatible option — Wav2Lip — and stacked it with SadTalker max for the hero showcase to demonstrate maximum quality." |
| What's the consistency claim? | "Mean ArcFace identity match jumped from 0.301 (Imagen baseline) to 0.664 (Z-Image-Turbo + Daniel LoRA). 8 of 10 generated images are strong matches; zero drift below 0.30." |
| Did you train anything? | "Three things: (1) Phi-3.5-mini text LoRA on 52 entrepreneur examples, fused into the inference model; (2) Z-Image-Turbo face LoRA on 30 Daniel reference photos, 455 training steps via mflux on Apple Silicon; (3) F5-TTS zero-shot voice clone from a 9.77-second reference clip." |
| What about ethics / Guardian Agent? | "It's the 6th of 7 CrewAI agents. Phi-3.5 safety-check endpoint at `/safety-check` returns APPROVE / REVISE / BLOCK. The Reviewer agent runs after Guardian for persona-voice compliance. Built per professor requirement." |

— Last updated end of overnight build (2026-04-28)
