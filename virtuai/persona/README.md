# VirtuAI Persona Lock — Daniel Calder

Single-source-of-truth persona consistency pipeline. Every modality (image, voice,
video, text) is bound to one identity through models you train and own. No HeyGen,
no ElevenLabs at runtime, no Veo, no Imagen — all open-source, all local on
Apple Silicon.

---

## Layers

| Layer | Status | Engine | Locked by |
|---|---|---|---|
| Text | ✅ done | Phi-3.5-mini-instruct + LoRA (fused) | `virtuai/models/finetune/fused_model/` |
| Face (images) | 🟡 Phase 1 — training in background, config tuned for 4-bit/768 px | Z-Image-Turbo + Daniel face LoRA | `training_runs/<run-id>/.../*.safetensors` |
| Voice | ✅ Phase 2 done | F5-TTS v1 Base, zero-shot cloning, MPS | `voice_sample/daniel_voice_ref.wav` + `daniel_voice_ref_trimmed.txt` |
| Talking head | ✅ Phase 3 done | SadTalker (isolated venv) | face image + cloned-voice audio |
| Anti-repetition | ✅ Phase 4 done | FAISS over `sentence-transformers/all-MiniLM-L6-v2` | `topic_memory_data/topic.index` (18 posts seeded) |
| Consistency eval | ✅ Phase 5 done | InsightFace ArcFace + sentence-transformers | `eval/_cache/`, baseline report in `eval/_reports/` |

---

## Phase 1 — Face LoRA (CURRENT)

### What's done

- 30 reference photos preprocessed: number labels cropped, center-squared, upscaled to 1024×1024 → `face_dataset/`
- Each image paired with a randomized caption containing the trigger token `dnlcldr`
- `persona_anchor.json` written matching the actual face in the photos (Mediterranean appearance, dark wavy hair, medium beard, hazel-brown eyes, warm olive skin)
- `training_config.json` validated against `mflux-train --dry-run`
- Backend (`virtuai/models/backend.py`) switched from FLUX.1-schnell to Z-Image-Turbo and wired to auto-load the persona LoRA from `training_runs/` once it exists

### To run training

```bash
source ~/virtuai-venv/bin/activate
cd "/Users/karammufleh/Desktop/capstone  101"
mflux-train --config virtuai/persona/training_config.json
```

- Base: `z-image-turbo` (8-bit quantized)
- 30 images × 60 epochs × batch size 1 = 1800 steps
- Checkpoints every 10 epochs into `training_runs/<timestamp>/`
- Preview image generated every 10 epochs from `face_dataset/preview.txt`
- On an M-series Mac, expect roughly **2–4 hours** total (depends on RAM tier and battery state)

When training finishes, the backend automatically picks up the newest `*.safetensors` adapter on next startup. Verify with:

```bash
curl http://localhost:8765/health | jq .persona_lora_loaded
```

### Inference (after training)

Always start the prompt with the trigger token. The `image_prefix` in `persona_anchor.json` (`"a photo of dnlcldr man,"`) is the canonical opener:

```bash
mflux-generate \
  --model z-image-turbo -q 8 \
  --lora-paths virtuai/persona/training_runs/<run-id>/.../adapter.safetensors \
  --prompt "a photo of dnlcldr man, standing on a rooftop at golden hour, looking at city skyline, cinematic" \
  --steps 4 --width 1024 --height 1024 \
  --output out.png
```

### Why these design choices

**Why Z-Image-Turbo instead of FLUX.1-schnell?**
mflux dropped LoRA training support for FLUX.1 — only Z-Image, FLUX.2, and Qwen-Image have training adapters. Z-Image-Turbo is the closest 4-step distilled model with the fullest mflux training pipeline.

**Why a trigger token (`dnlcldr`)?**
Standard DreamBooth pattern. A rare token sequence forces the model to learn a *new identity* rather than overwriting "man" or "person". Captions never describe Daniel's specific features — those should come from the LoRA, not the text encoder.

**Why captions vary per image?**
If every caption were identical the LoRA learns "man + caption" as a fused concept and overfits to the training scenes. Varying scene + lighting + framing across captions forces the LoRA to bind the *face* to the trigger and treat scene as orthogonal.

**Why the training resolution is 1024 even though source is 256?**
mflux-train is built around 1024px. The LANCZOS-upscaled images don't add real face detail but they match the model's native resolution and avoid resolution-mismatch artifacts. If face sharpness is poor after training, the next iteration is to regenerate the dataset at native 1024 (e.g. via Z-Image-Turbo + ControlNet conditioned on the existing references).

---

## Phase 2 — Voice Clone (DONE)

F5-TTS v1 Base, zero-shot voice cloning. No training required — the reference audio + matched transcript IS the speaker conditioning.

### What's done

- F5-TTS installed in venv (`pip install f5-tts` pulled torchaudio, vocos, librosa, soundfile, accelerate, etc.)
- Reference MP3 (49 sec ElevenLabs preset clip) trimmed to 9.77 sec at a silence boundary, resampled to 24 kHz mono → `voice_sample/daniel_voice_ref.wav`
- Matched transcript saved to `voice_sample/daniel_voice_ref_trimmed.txt` (you should listen + verify this matches exactly)
- `clone_voice.py` CLI for one-off generation, with auto-DYLD-fix re-exec
- Backend endpoint `POST /generate-voice` lazy-loads F5-TTS on first call (~6 sec model load) and generates at ~5× realtime on MPS
- `/health` reports `voice_capable`, `voice_loaded`, `voice_model`
- `persona_anchor.json → voice_clone` populated

### How to use

**CLI (one-off):**
```bash
cd "/Users/karammufleh/Desktop/capstone  101"
python virtuai/persona/scripts/clone_voice.py "Build systems instead of trading time."
# → virtuai/persona/voice_clone/generated/daniel_<timestamp>.wav
```

**Backend endpoint (production):**
```bash
curl -X POST http://localhost:8765/generate-voice \
  -H "Content-Type: application/json" \
  -d '{"text": "Build systems instead of trading time.", "speed": 1.0, "seed": 42}'
```

### FFmpeg quirk (one-time fix already applied)

torchaudio 2.11 → torchcodec → libavutil. Brew's default ffmpeg is v8 (libavutil.60); torchcodec 0.11 supports up to FFmpeg 7 (libavutil.59). We installed `ffmpeg@7` (keg-only) and the scripts re-exec themselves with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/ffmpeg@7/lib` automatically. You don't need to set anything.

### Tradeoffs / known issues

- The trimmed transcript was generated from a uniform-speech-rate estimate. If voice quality sounds off, **listen to `daniel_voice_ref.wav` and edit `daniel_voice_ref_trimmed.txt`** so it matches exactly word-for-word. F5-TTS quality scales sharply with transcript-audio alignment.
- F5-TTS first call downloads ~1.4 GB to `~/.cache/huggingface/`. Subsequent calls are fully offline.
- MPS performance: ~5× realtime (e.g. 4 sec of audio in 17 sec). CPU fallback is much slower.

---

## Phase 3 — Talking-Head Video (DONE — SadTalker, not LivePortrait)

**Engine pivot:** LivePortrait is a face-reenactment tool — it animates a still from a *driving video*, not from audio. SadTalker takes audio + face → talking video directly, which is what we actually need. The LivePortrait clone is kept under `liveportrait/` as an optional refinement stage for later.

### What's done

- SadTalker cloned into `virtuai/persona/sadtalker/` (OpenTalker fork)
- Isolated venv at `~/virtuai-sadtalker-venv` (Python 3.10, 2023-era pinned deps) so SadTalker's `numpy 1.23 / scipy 1.10 / librosa 0.9 / kornia 0.6` don't clobber the main env
- 2.3 GB of model checkpoints downloaded (mapping, SadTalker 256/512, GFPGAN enhancers)
- One known basicsr/torchvision compat patch applied (`torchvision.transforms.functional_tensor` → `functional` for `rgb_to_grayscale`)
- `scripts/talking_head.py` CLI wrapper
- Backend endpoint `POST /generate-talking-head` lives in main env, shells out to the isolated venv for SadTalker
- VLM client method `generate_talking_head` and CrewAI tool `generate_talking_head_local` exposed
- `visual_agent.py` rewritten to produce talking-head scripts for TikTok / IG Reels / YouTube Shorts and to enforce the `dnlcldr` trigger token on every image prompt

### How to use

**Backend:**
```bash
curl -X POST http://localhost:8765/generate-talking-head \
  -H "Content-Type: application/json" \
  -d '{"text": "Build systems instead of trading time. AI is real leverage.", "size": 256, "still": false}'
```

**CLI (one-off):**
```bash
python virtuai/persona/scripts/talking_head.py --text "Build systems instead of trading time."
```

End-to-end pipeline (per request):
```
text → /generate-voice (F5-TTS) → audio.wav
audio.wav + face_dataset/00.png → SadTalker (isolated venv) → talking_head.mp4
```

**Note:** SadTalker on Apple Silicon defaults to CPU — MPS support is partial in the FaceVid2Vid module. Generation time is ~2-5 min per 10s clip on M-series. Running SadTalker concurrently with LoRA training will OOM — wait for training to finish first.

---

## Phase 4 — Anti-Repetition (DONE — local FAISS)

`virtuai/persona/topic_memory.py` — FAISS `IndexFlatIP` over 384-dim sentence-transformers embeddings.

**Already seeded with the 18 demo posts.** Storage is at `topic_memory_data/` (JSON metadata + binary FAISS index). Survived a pickle→JSON migration so future versions don't break on the `__main__` qualified-name pickle issue.

```bash
# Check if a candidate topic is too close to past posts
python virtuai/persona/topic_memory.py check "Why most marketers waste their budget on cold leads."

# Add a post to the index after publication
python virtuai/persona/topic_memory.py bootstrap   # one-time seed from demo_content.json
```

Programmatic:
```python
from virtuai.persona.topic_memory import get_topic_memory
mem = get_topic_memory()
is_novel, sim, nearest = mem.is_novel("candidate post text", threshold=0.85)
```

---

## Phase 5 — Consistency Evaluation (DONE — capstone deliverable)

`virtuai/persona/eval/` — face + text similarity metrics for proving persona consistency over time.

| Metric | Tool | Output | Threshold |
|---|---|---|---|
| Face similarity vs Daniel reference bank | InsightFace ArcFace `buffalo_l` (CPU ONNX) | mean cosine sim across all generated faces | strong ≥0.65, ok ≥0.45, drift <0.30 |
| Text style similarity vs persona centroid | sentence-transformers/all-MiniLM-L6-v2 | cosine sim to centroid of approved corpus | strong ≥0.55, on-brand ≥0.45 |

### Baseline (before LoRA)

Generated on 2026-04-26, with current images coming from the legacy Imagen pipeline (no persona LoRA active):

- **Face similarity: mean 0.301** across 29 detectable faces — predictably below the drift threshold. This is the *before* number.
- **Text similarity: mean 0.636** self-similarity, σ=0.122 — corpus is tight enough to use as a style anchor.

### Run a fresh report

```bash
python -m virtuai.persona.eval.run_consistency_report
# → virtuai/persona/eval/_reports/consistency_<timestamp>.{json,md}
```

Once the persona LoRA finishes training, regenerate a batch of images using `image_prefix = "a photo of dnlcldr man,"` and re-run the report. The before/after delta is the primary capstone artifact defending against the "you just called an API" critique.

### CLI tools

```bash
python virtuai/persona/eval/face_similarity.py bootstrap                  # one-time
python virtuai/persona/eval/face_similarity.py score path/to/face.png
python virtuai/persona/eval/face_similarity.py score-dir virtuai/data/generated_images
python virtuai/persona/eval/text_style.py bootstrap                       # one-time
python virtuai/persona/eval/text_style.py score "Text to grade for on-brand voice."
```

---

## Files in this directory

```
virtuai/persona/
├── persona_anchor.json                  # single source of truth — every pipeline reads from here
├── training_config.json                 # mflux-train config for the face LoRA
├── topic_memory.py                      # Phase 4 — FAISS anti-repetition module
├── README.md                            # you are here
├── face_dataset/
│   ├── 00.png … 54.png                  # 30 preprocessed training images, 1024×1024
│   ├── 00.txt … 54.txt                  # paired captions with trigger token
│   └── preview.txt                      # caption used for training-time preview renders
├── voice_sample/
│   ├── daniel_voice_ref.mp3             # original 49s ElevenLabs clip (data prep only)
│   ├── daniel_voice_ref.wav             # trimmed 9.77s @ 24 kHz mono — the F5-TTS reference
│   ├── daniel_voice_ref.txt             # full transcript (all 49s of the MP3)
│   └── daniel_voice_ref_trimmed.txt     # transcript of the trimmed WAV (verify against audio)
├── voice_clone/
│   └── generated/                       # backend writes daniel_<ts>.wav here
├── topic_memory_data/                   # FAISS index + JSON metadata (Phase 4)
│   ├── topic.index
│   └── records.json
├── eval/                                # Phase 5 consistency suite
│   ├── face_similarity.py               # ArcFace via InsightFace
│   ├── text_style.py                    # MiniLM centroid
│   ├── run_consistency_report.py        # composes the dashboard
│   ├── _cache/                          # cached reference bank + centroid
│   └── _reports/                        # timestamped JSON + markdown reports
├── sadtalker/                           # cloned upstream — talking-head model code
│   ├── checkpoints/                     # 1.6 GB of model weights
│   └── gfpgan/weights/                  # 0.7 GB of face enhancer weights
├── liveportrait/                        # cloned upstream — kept for future face-stylization use
├── training_runs/                       # mflux-train writes timestamped checkpoints here
├── training_logs/                       # background training log files + current.pid
└── scripts/
    ├── preprocess_face_dataset.py
    ├── prep_voice_reference.py
    ├── clone_voice.py
    └── talking_head.py                  # Phase 3 — text → audio → talking-head mp4
```

External venv (NOT under `virtuai/`):
```
~/virtuai-sadtalker-venv/                # isolated Python 3.10 venv for SadTalker only
                                         # (numpy 1.23, scipy 1.10, kornia 0.6 — incompatible with main venv)
```
