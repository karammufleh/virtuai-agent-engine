# VirtuAI — Capstone Final Report

**Daniel Calder** is an autonomous AI persona that posts native-format content
across LinkedIn, X, Instagram, Instagram Reels, TikTok, YouTube Shorts, and Medium.
The face, the voice, and the persona "voice" are all locked to identity-conditioned
local models we trained or cloned ourselves. Nothing is rendered through
HeyGen / ElevenLabs / Imagen / Veo at runtime.

The project is a fully-local persona consistency pipeline running on Apple Silicon.

---

## TL;DR

| Layer | Tech | Where it lives | Result |
|---|---|---|---|
| **Text** | Phi-3.5-mini-instruct + LoRA (fused) | `virtuai/models/finetune/fused_model/` | Trained on 52 entrepreneur/AI examples; serves all 7 platform texts |
| **Face / Image** | Z-Image-Turbo + Daniel Calder LoRA (Apple MLX, 8-bit, 512px) | `virtuai/persona/training_runs_*/checkpoints/`<br>`virtuai/persona/training_runs/_extracted/0000455_checkpoint_adapter.safetensors` | 30 ref photos → 455 training steps → 0.664 mean ArcFace match (vs 0.301 baseline, **+120%**) |
| **Voice** | F5-TTS v1 Base (zero-shot clone) | `virtuai/persona/voice_sample/daniel_voice_ref.wav` | 9.77 sec reference → ~5× realtime synthesis on MPS |
| **Video (Phase 2)** | Kling V1-6 multi-image2video + lip-sync API | `virtuai/tools/kling_omni.py` | 4 ref photos → 10 s 9:16 reel; ArcFace 0.696 mean across frames |
| **Video (Phase 1)** | Wav2Lip + SadTalker on LoRA-generated still | `virtuai/persona/wav2lip/` | 3-4× realtime lip-sync; head motion via SadTalker stack |
| **Captions** | Whisper word-timestamps → ASS subtitle generator | `virtuai/tools/caption_generator.py` | CapCut-style pop animation, keyword highlighting, word-by-word sync |
| **Reel builder** | FFmpeg 7 pipeline (clips + captions + hook overlay) | `virtuai/tools/reel_builder.py` | H.264 MP4, hook text fade, -22 dB bg music mix |
| **Kling prompts** | Gemini 2.5 Flash → rich cinematography prompts | `virtuai/tools/prompt_writer.py` | 80-120 word Kling-optimised scene descriptions |
| **Anti-repetition** | FAISS over `sentence-transformers/all-MiniLM-L6-v2` | `virtuai/persona/topic_memory.py`<br>store: `virtuai/persona/topic_memory_data/topic.index` | 18 demo posts seeded; novelty checks at 0.85 cosine threshold |
| **Consistency eval** | InsightFace ArcFace `buffalo_l` + sentence-transformers | `virtuai/persona/eval/` | Per-image identity scoring + before/after delta report |
| **Multi-agent orchestration** | CrewAI 1.14 (8 agents incl. Guardian + Publisher) | `virtuai/agents/`, `virtuai/pipelines/content_pipeline.py` | Research → Strategy → Creator → Visual → Reviewer → Guardian → Publisher → Analyzer |
| **Agent LLM** | Gemini 2.5 Flash (free tier) or local Phi-3.5-mini | `content_pipeline.py` `_create_gemini_llm()` | Cloud reasoning + local generation tools; fixes Apple Silicon OOM |
| **Backend** | FastAPI on port 8765 | `virtuai/models/backend.py` | `/generate`, `/generate-image`, `/generate-voice`, `/generate-talking-head`, `/analyze-image`, `/safety-check`, `/analyze-sentiment`, `/unload-vision` |
| **Showcase site** | FastAPI + Jinja on port 8080 | `virtuai/website/` | Persona-stack live demo grid + per-platform native UI mockups |

---

## The Persona Consistency Delta (capstone deliverable)

This is the core empirical claim:

| Metric | Before (Imagen, n=29) | After (Z-Image-Turbo + Daniel LoRA, n=10) |
|---|---|---|
| Mean ArcFace identity similarity to Daniel | **0.301** | **0.664** (+120%) |
| Median | 0.303 | (above acceptable threshold) |
| Strong matches (≥ 0.65) | 0 / 29 | **8 / 10** |
| Acceptable (≥ 0.45) | 0 / 29 | **10 / 10** |
| Identity drift (< 0.30) | **13 / 29** | **0 / 10** |

Per-platform individual scores after LoRA:

| Platform | ArcFace similarity to reference bank |
|---|---|
| LinkedIn | **0.765** (very strong) |
| Instagram | 0.725 |
| Medium | 0.710 |
| X | 0.666 |
| Earlier eval set (6 images) | 0.49 – 0.72 |

The InsightFace ArcFace `buffalo_l` model ran on CPU via ONNX. The "before"
baseline used the legacy Imagen 4.0-generated images that existed in
`virtuai/data/generated_images/` from before the persona-LoRA pivot.

Full report: [`virtuai/persona/eval/_reports/final_consistency.json`](virtuai/persona/eval/_reports/final_consistency.json).

---

## Phase 2: End-to-End Reel Production

Phase 2 replaced the Wav2Lip-only video pipeline with a cloud+local hybrid:

**Production chain (7 minutes end-to-end):**

| Step | Tool | Time | Output |
|---|---|---|---|
| 1. Voice | F5-TTS (local backend) | 47 s | Daniel's cloned voice WAV |
| 2. Upload | catbox.moe | 2 s | Public audio URL for Kling |
| 3. Prompt | Gemini 2.5 Flash | 3 s | 124-word Kling scene prompt |
| 4. Video | Kling multi-image2video (4 ref photos) | ~170 s | 10 s base clip, 9:16 |
| 5. Lip-sync | Kling lip-sync API | ~190 s | Daniel speaks the script |
| 6. Captions | Whisper base (word timestamps) | 2 s | ASS subtitles, keyword highlights |
| 7. Reel | FFmpeg 7 (captions + hook overlay) | 1 s | 2.3 MB publish-ready MP4 |

**ArcFace identity across reel frames:**

| Frame | ArcFace similarity | Verdict |
|---|---|---|
| 0.5 s | 0.710 | PASS (≥ 0.70) |
| 2.0 s | 0.696 | ACCEPTABLE |
| 4.0 s | 0.672 | ACCEPTABLE |
| 7.0 s | 0.708 | PASS |
| 9.0 s | 0.688 | ACCEPTABLE |
| **Mean** | **0.695** | Matches Phase 1 LoRA peak (0.664) |

The reel includes: hook text overlay with fade animation in first 3 seconds,
word-by-word CapCut-style captions with pop-in animation and keyword highlighting
(AI terms, dollar amounts auto-detected), and H.264 encoding at CRF 20.

Output: [`virtuai/data/generated_videos/daniel_reel_final_1778673989.mp4`](virtuai/data/generated_videos/daniel_reel_final_1778673989.mp4)

---

## Demo content — what the site shows

Every asset under `virtuai/persona/demo/<platform>/` is end-to-end ours:

| Platform | Asset type | Render path | Render time |
|---|---|---|---|
| LinkedIn | image (1024×1024) | Z-Image-Turbo + dnlcldr LoRA, 4 steps | 7.0 min |
| X | image (1024×1024) | Z-Image-Turbo + dnlcldr LoRA, 4 steps | 7.6 min |
| Instagram | image (1024×1024) | Z-Image-Turbo + dnlcldr LoRA, 4 steps | 7.3 min |
| Medium | image (1024×1024) | Z-Image-Turbo + dnlcldr LoRA, 4 steps | 7.3 min |
| TikTok | video (43 sec) | F5-TTS → Wav2Lip on Daniel hero still | 173 sec render |
| Instagram Reels | video (51 sec) | F5-TTS → Wav2Lip on Daniel hero still | 189 sec render |
| YouTube Shorts | video (71 sec) | F5-TTS → Wav2Lip on Daniel hero still | 245 sec render |
| **Hero showcase** | video (3.7 sec, 1024×1024) | SadTalker max + GFPGAN → Wav2Lip refinement (stacked pipeline) | 75 min + 19 sec |

Total compute time for the full demo set: roughly **2 hours** of overnight rendering on
an M-series Mac. Total external cost: **$0**.

---

## The journey — what we tried and why we picked Wav2Lip

We evaluated multiple talking-head approaches on Apple Silicon. The full ladder:

| Tool | Quality | Time per second of output | Verdict |
|---|---|---|---|
| **HeyGen API** | SOTA realism | ~2 sec | ❌ Conflicts with capstone — "you used a SaaS" critique |
| **EchoMimic v2 (CVPR 2025)** via HF Space | Excellent | n/a | ❌ The free public Space is "CPU showcase" — refuses to actually generate |
| **SadTalker default (256, no enhancer)** | Mediocre, "AI face" feel | ~1.5× realtime | ❌ User rejected the realism |
| **SadTalker max (512 + GFPGAN + still)** | Sharp, natural face dynamics | **~20× realtime** (75 min for 3.7 sec audio) | ❌ Quality is great but unworkable for 165 sec of content |
| **Wav2Lip (Improved quality)** | Sharp lip sync, identity preserved | **~4× realtime** | ✅ **Picked.** Best speed/quality tradeoff on Apple Silicon |
| **Wav2Lip stacked on SadTalker max** | Combines SadTalker face polish + Wav2Lip lip sync | SadTalker's 75 min + 19 sec | ⭐ Used for the hero showcase only |
| **Hallo3 / Hallo2 / EchoMimic v3 / LiveAvatar** | SOTA | n/a | ❌ All require NVIDIA H100/A100; no working MPS port exists |
| **MuseTalk 1.5** | Good | (untested — would compete with rest of system) | 🟡 Future work |
| **VideoReTalking (SIGGRAPH 2022)** | Good lip sync | (cloned, not tested) | 🟡 Future work |
| **JoyVASA** | Good (LivePortrait-based) | n/a | ❌ Documented CUDA only |

**The honest answer for "best free pretrained on Apple Silicon" in 2026 is Wav2Lip**,
plus stacking with SadTalker max for hero shots. Modern diffusion-based talking heads
(Hallo, EchoMimic, MuseTalk, VASA-1, etc.) all target NVIDIA-class hardware and have
no usable MPS port today.

---

## Architectural pivots we made (worth defending in the panel)

1. **HeyGen → fully local persona stack.**
   We started the project willing to integrate HeyGen + ElevenLabs for the talking-head
   layer. Rejected because the capstone has to demonstrate ML engineering, not API
   integration. Built our own face LoRA + voice clone instead.

2. **FLUX.1-schnell → Z-Image-Turbo.**
   `mflux` dropped LoRA training support for FLUX.1 mid-2026; only `z_image`,
   `flux2`, and `qwen` still have training adapters. Switching to Z-Image-Turbo also
   improved per-step training speed on Apple Silicon.

3. **LivePortrait → SadTalker → Wav2Lip.**
   Initially picked LivePortrait. Discovered mid-implementation that LivePortrait is
   image-driven (needs a driving video, not audio), so it can't power audio-driven
   talking heads. Pivoted to SadTalker (works but slow). Finally settled on Wav2Lip
   for production batch + SadTalker-stacked Wav2Lip for the hero showcase.

4. **Pinecone → local FAISS.**
   ~100 posts is well under FAISS-flat's scale. Pinecone would be an unnecessary
   external dependency.

5. **Imagen 4.0 → Z-Image-Turbo + dnlcldr LoRA.**
   Imagen produced a different-looking face every time (mean ArcFace similarity 0.301
   to the canonical Daniel). The LoRA-conditioned Z-Image-Turbo holds identity
   (mean 0.664 — measurable and reproducible).

---

## Engineering oddities we hit and fixed

1. **mflux training output_path is CWD-relative, not config-file-relative.** Final
   training output landed at `<project_root>/training_runs_<timestamp>/`, not under
   `virtuai/persona/training_runs/`. Backend's `_find_persona_lora()` walks every
   `training_runs*` dir to handle this.

2. **Z-Image-Turbo inference needs a different CLI from the generic one.** The
   generic `mflux-generate --model z-image-turbo` looks for a FLUX-style
   `text_encoder_2/` subdirectory that doesn't exist in the official
   `Tongyi-MAI/Z-Image-Turbo` HF repo. The dedicated `mflux-generate-z-image-turbo`
   subcommand uses the right loader path. Backend wired to the dedicated CLI.

3. **F5-TTS via torchaudio 2.11 → torchcodec → libavutil from FFmpeg.** Brew default
   ffmpeg is v8 (libavutil.60); torchcodec 0.11 supports up to FFmpeg 7
   (libavutil.59). Fix: `brew install ffmpeg@7` (keg-only) and have
   `clone_voice.py` / `backend.py` re-exec themselves with
   `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/ffmpeg@7/lib`.

4. **basicsr / torchvision compat break.** SadTalker and Wav2Lip both depend on
   `basicsr` which imports the removed `torchvision.transforms.functional_tensor`.
   Patched in their isolated venvs to use
   `torchvision.transforms.functional`.

5. **Wav2Lip checkpoint is CUDA-tagged.** `easy_functions._load()` only used CPU
   `map_location` when `device == "cpu"`; on `device == "mps"` it tried to load
   on CUDA and crashed. Patched to map to CPU first regardless of MPS/CPU; the
   model gets moved to MPS afterwards.

6. **macOS swap thrashing during long-running training.** A single
   `mflux-train` overnight run accumulated enough state that swap usage hit
   11.4 GB / 13 GB and per-step time ballooned from 33 s to 1294 s. Killed at step
   455 (well past visual convergence at step 300) and used that LoRA. Documented
   in `persona_anchor.json → face_lora.stopped_reason`.

7. **InsightFace + faiss-cpu + torch + sentence-transformers all bring their own
   OpenMP.** Concurrent calls segfaulted on Apple Silicon. Setting
   `OMP_NUM_THREADS=1`, `KMP_DUPLICATE_LIB_OK=TRUE`,
   `TOKENIZERS_PARALLELISM=false` at the top of `topic_memory.py` /
   `face_similarity.py` prevents the segfault.

---

## How to run it

**One-time setup** (already done in this repo):
```bash
# Persona LoRA training (~75 min on M-series, run once)
mflux-train --config virtuai/persona/training_config.json

# F5-TTS reference + transcript prep (already in voice_sample/)
python virtuai/persona/scripts/prep_voice_reference.py

# SadTalker venv + 2.3 GB checkpoints (already done — see persona/sadtalker/)
# Wav2Lip extras into the same venv (already done)
```

**Running the demo:**
```bash
# Backend on port 8765
python run_backend.py

# Showcase site on port 8080
python run_website.py
# → open http://localhost:8080
```

**Regenerate everything from scratch:**
```bash
# Generate scripts + F5-TTS audio for all 7 platforms
python virtuai/persona/scripts/generate_platform_content.py

# Render Wav2Lip videos for all 3 video platforms
python virtuai/persona/scripts/render_all_platforms.py

# Render Z-Image-Turbo + LoRA images for all 4 image platforms
python virtuai/persona/scripts/render_platform_images.py

# Re-run consistency eval
python virtuai/persona/eval/run_consistency_report.py
```

**Status check at any time:**
```bash
python virtuai/persona/scripts/status.py
```

---

## Repo map

```
virtuai/
├── agents/                          7 CrewAI agents (Guardian for ethics is the prof requirement)
├── models/
│   ├── backend.py                   FastAPI server, all model endpoints
│   ├── vlm_client.py                Python client for the backend
│   └── finetune/fused_model/        Phi-3.5-mini LoRA fused (text)
├── pipelines/content_pipeline.py    CrewAI orchestration
├── persona/                         The persona-stack work (this report's subject)
│   ├── persona_anchor.json          Single source of truth for identity
│   ├── face_dataset/                30 training photos + daniel_hero.png (LoRA-generated canonical face)
│   ├── voice_sample/                F5-TTS reference WAV + transcript
│   ├── training_config.json         mflux-train config (Z-Image-Turbo + dnlcldr LoRA)
│   ├── training_runs/_extracted/    Latest extracted *.safetensors LoRA weights
│   ├── topic_memory.py              FAISS anti-repetition module
│   ├── topic_memory_data/           FAISS index + JSON metadata
│   ├── sadtalker/                   SadTalker + 2.3 GB checkpoints (talking-head v1)
│   ├── wav2lip/                     Wav2Lip + 1.2 GB checkpoints (talking-head v2 — production)
│   ├── liveportrait/, video_retalking/  Cloned but unused — kept as future-work
│   ├── eval/                        Face + text consistency eval suite
│   │   ├── face_similarity.py       InsightFace ArcFace
│   │   ├── text_style.py            sentence-transformers centroid
│   │   ├── run_consistency_report.py
│   │   └── _reports/                Timestamped consistency reports
│   ├── demo/                        Final per-platform demo content the website shows
│   │   ├── linkedin/   text.md, image.png, manifest.json
│   │   ├── x/, instagram/, medium/  same shape — image platforms
│   │   ├── tiktok/     text.md, audio.wav, video.mp4, manifest.json
│   │   ├── instagram_reels/, youtube_shorts/  same shape — video platforms
│   │   └── render_summary.json, image_render_summary.json, summary.json
│   ├── talking_head/
│   │   ├── hero_showcase_stacked.mp4   SadTalker max + Wav2Lip stacked, 1024×1024
│   │   ├── sadtalker_max/              First SadTalker max output (2026-04-27)
│   │   ├── wav2lip/                    All Wav2Lip outputs by timestamp
│   │   └── generated/                  Earlier basic-SadTalker outputs (kept for comparison)
│   └── scripts/                     All the small utility scripts
│       ├── preprocess_face_dataset.py    Crops + upscales the 30 training photos
│       ├── prep_voice_reference.py       Trims the 49s MP3 to 9.77s + transcript
│       ├── clone_voice.py                F5-TTS CLI
│       ├── talking_head.py               SadTalker wrapper
│       ├── wav2lip_render.py             Wav2Lip wrapper (production talking-head)
│       ├── render_all_platforms.py       Renders all 3 video platforms via Wav2Lip
│       ├── render_platform_images.py     Renders all 4 image platforms via Z-Image-Turbo + LoRA
│       ├── generate_platform_content.py  Phi → text + F5-TTS → audio for all 7 platforms
│       ├── eval_lora_checkpoint.py       Before/after consistency eval helper
│       └── status.py                     One-shot health check across the whole stack
├── tools/                           CrewAI tool wrappers
├── website/                         Showcase site (port 8080)
└── data/                            Legacy demo content + content packages
```

External venvs (NOT under `virtuai/`):
```
~/virtuai-venv/                   Main project venv — Phi, MLX, CrewAI, FastAPI
~/virtuai-sadtalker-venv/         Isolated Python 3.10 venv for SadTalker + Wav2Lip
                                  (their pinned numpy 1.23 / scipy 1.10 / kornia 0.6 are
                                   incompatible with the main venv)
```

---

## What's left for "future work" (out of capstone scope)

- **Multi-clip reels (>10 s).** Kling max duration is 10 s per clip. Reels over 10 s
  require generating 2-3 clips and concatenating. The reel builder supports this but
  cross-clip identity consistency is unvalidated.
- **Kling V2-Master upgrade.** We used V1-6 (available on our tier). V2-Master
  produces higher-quality motion and scene detail.
- **Full agent autopilot.** The CrewAI pipeline runs end-to-end with all 8 agents,
  but human-in-the-loop is still required for final publish approval.
- **Gemini quota upgrade.** Free tier limits to 20 requests/day for Gemini 2.5 Flash.
  A paid tier or API key with higher quota would support multiple pipeline runs per day.
- **MuseTalk 1.5 MPS port.** Latent-space lip sync; potential local alternative to
  Kling lip-sync API if a Mac port lands.

---

## Acknowledgements / dependencies

- Phi-3.5-mini-instruct (Microsoft) — 4-bit MLX weights from `mlx-community`
- LLaVA 1.5 7B — 4-bit MLX weights from `mlx-community`
- Z-Image-Turbo (Tongyi-MAI) — text-to-image base
- F5-TTS v1 Base (Shanghai AI Lab) — zero-shot voice clone
- Kling V1-6 API (Kuaishou) — multi-image-to-video + lip-sync
- Gemini 2.5 Flash (Google) — Kling prompt writing + agent reasoning
- SadTalker (OpenTalker) — talking-head v1 (Phase 1, hero showcase)
- Easy-Wav2Lip (anothermartz) — Wav2Lip Mac fork (Phase 1, production lip-sync)
- Whisper (OpenAI) — word-level timestamps for caption generation
- InsightFace `buffalo_l` — ArcFace identity verification
- sentence-transformers `all-MiniLM-L6-v2` — text style + topic memory
- mflux (filipstrand) — Apple MLX wrapper for diffusion training/inference
- CrewAI 1.14 — 8-agent orchestration
- Composio SDK — LinkedIn/Facebook/Instagram publishing
- Apple MLX, PyTorch, FAISS, FastAPI, FFmpeg 7

— *Phase 2 reel pipeline (Kling V1-6 + lip-sync).*

---

## Phase 3 — Cloud-native autopilot + 3-format content pack (May 14)

After Phase 2 shipped working reels, the project pivoted again to address
three remaining gaps:

1. **Lip-sync drift** at the end of multi-segment reels (the compositing
   seam between Kling V1-6 render + separate lip-sync pass).
2. **Static-background "AI portrait" feel** — Avatar Pro animates a still
   image, so the environment never moves. Top creators (e.g. @andyhan.ai)
   use Veo 3 / Kling 3.0 with native joint generation precisely to avoid
   this.
3. **Single-format output** — Phase 2 produced reels only. Real creator
   feeds run a daily mix of reels + portraits + carousels.

### Architecture changes

**Single-pass video.** Kling 3.0 multi-shot replaced the V1-6 + lip-sync
two-step. The model jointly generates video, native speech, and lip-sync
in one render, so there is no compositing seam to drift. Two parallel
renders (3 shots × 5 s each) are concatenated with FFmpeg
`aresample=async=1` + CFR re-encode to lock audio to video timestamps.

**Claude Sonnet 4.6 script writer.** Replaced Gemini-2.5-Flash for script
ideation. Two-phase generation: brainstorm 5 candidate topics → pick
winner → write the full 6-beat story arc (setup / incident / struggle /
turn / proof / meaning). Hard concreteness gates reject scripts that
lack named tools, real dollar amounts, real timeframes, or use a banned
phrase (leverage, synergy, 10x, productivity tips, scale your business).

**Static image content.** A new pipeline produces portraits and 5-slide
carousels alongside reels:

- *Portraits* — Nano Banana 2 places the canonical Daniel face in a real
  environment with the right outfit; PIL renders typography overlay.
- *Carousels* — 5 slides (cover / problem / insight / proof / payoff)
  with persona on slides 1 and 5, environmental concept on 2/3/4.

**Daily pack orchestrator.** `scripts/daily_pack.py` runs all three
content types in parallel:

| Layer | Tool |
|---|---|
| Topic seeds | 14-entry rotation with history avoidance |
| Outfit / mood / setting | rotation pools, 3 distinct picks per pack |
| Reel | Claude → Kling 3.0 multi-shot ×2 → Suno → audio resync |
| Portrait | Claude → Nano Banana → PIL |
| Carousel | Claude → Nano Banana ×5 → PIL ×5 |
| Publish | YouTube Direct + Composio (IG + LinkedIn), parallel |
| History | `autopilot_history.json` appended on every run |

Wall-clock: ≈ 12 minutes per pack. Cost: ≈ $5–7 per pack.

### Reliability fixes

- **KIE file-stream-upload** replaces tmpfiles.org / catbox.moe for every
  asset KIE itself has to fetch. Eliminates the "Image fetch failed.
  Check access settings" mid-job errors.
- **Audio resync at concat** — silence-trim each render to its exact
  video duration, then re-encode the concat at constant 30 fps + 48 kHz
  audio. Lip-sync now holds through the full 30 s.
- **Mandatory motion in visual prompts** — Claude prompts now require
  explicit camera motion + 2-3 moving background elements. No more
  static "frozen photo" shots.
- **Posture / outfit rotation** — tracked in history; no repeat outfits
  or postures within the last 4-6 packs.

### Agent ↔ tool ownership refactor

Phase 3 also cleaned up the agent layer. Every tool is now owned by the
agent that calls it, and the two unused agents (research, analyzer) were
wired with real tools instead of being stubs:

- `cloud_tools.py` — new file holding `@tool`-decorated wrappers for the
  KIE-cloud pipeline. Agents pull from here for production.
- `local_tools.py` — the original local-backend `@tool` wrappers, kept
  for the Phase-1 fallback path.
- Each agent's `tools=[...]` list and backstory was rewritten to match
  the current pipeline. See README.md "Eight CrewAI agents" table.

### n8n integration

`scripts/api_server.py` (FastAPI) exposes the daily pack as HTTP:

- `POST /run-pack` — kick off a full daily pack in background, returns
  `task_id`.
- `GET /status/{task_id}` — poll for state + final URLs.
- `GET /history` — return the last N published runs.
- `POST /run-reel`, `/run-portrait`, `/run-carousel` — produce-only
  endpoints.
- `POST /publish-reel`, `/publish-image-post` — publish an already-
  produced asset.

n8n connects via HTTP nodes: a schedule trigger fires `/run-pack` daily,
polls `/status/{id}` until success, then notifies on Slack/email.

### Phase 3 verified output

Two daily packs landed clean on live platforms:

| Pack | Reel | Portrait | Carousel |
|---|---|---|---|
| #1 | "AI proposal generator killed my conversion rate" | "Automating Email Is Usually Wrong First" | "Most AI Assistants Will Be Unemployed by 2027" |
| #2 | "AI tool I killed because it destroyed my margins" | "Junior dev replaced by Cursor costs more" | "Stop Prompting Claude Like A Search Engine" |

Both packs were produced in ≈ 11–12 minutes start to finish and posted
to YouTube + Instagram + LinkedIn with zero human touch. Variety
rotation hit 3 distinct outfits, moods, and topic seeds per pack.

### What Phase 3 leaves open

- **X / Twitter publishing.** Composio requires a one-time OAuth dance
  for the X toolkit under `user_id=danielcalder-`. Until that's done,
  the pack publishes to YouTube + IG + LinkedIn (3 of 4 platforms).
- **True IG carousel.** Composio's `INSTAGRAM_CREATE_MEDIA_CONTAINER`
  validator hardcodes `image_url` as required even for `media_type=
  CAROUSEL` parents, so the swipe-through carousel falls back to a
  single-image post of slide 1. The full 5-slide swipe needs a direct
  Meta Graph API call (~50 lines, future work).
- **Voice cloning to "Liam" via ElevenLabs Speech-to-Speech.** The
  pack currently ships with Kling 3.0's native voice. The optional
  `voice_change_to_liam` step is wired in `produce_reel_v16.py` and
  fires when `ELEVENLABS_API_KEY` is set in `.env`.

---

## Additional acknowledgements (Phase 3)

- Claude Sonnet 4.6 (Anthropic, via KIE.ai) — script writing
- Kling 3.0 multi-shot (Kuaishou, via KIE.ai) — native joint video+audio
- Nano Banana 2 (Google, via KIE.ai) — face-locked scene editing
- Suno V3.5 (via KIE.ai) — instrumental underbeds
- ElevenLabs Speech-to-Speech (via direct API) — optional voice cloning
- FastAPI + Uvicorn — orchestration HTTP layer for n8n
- Pillow — slide typography rendering
- KIE.ai's `redpandaai.co` CDN — reliable asset hosting (replaces tmpfiles.org)

— *Last updated 2026-05-14, Phase 3 daily-pack autopilot complete.*
