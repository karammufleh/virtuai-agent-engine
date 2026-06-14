# VirtuAI — Complete Project Handoff Document
> **Purpose:** Give this file to Claude Code at the start of any new session. It contains everything needed to understand, run, and continue the project with zero re-explaining.

---

## 1. WHAT THIS PROJECT IS

**VirtuAI** is an AI Engineering capstone project. It is a fully local, multi-agent content generation pipeline that:

1. Runs a **pretrained Vision Language Model locally** on Apple Silicon (no external APIs)
2. **Fine-tunes** the text model on persona data using LoRA
3. Serves both models via a **FastAPI backend** with an OpenAI-compatible endpoint
4. Runs a **7-agent CrewAI pipeline** that researches, strategies, creates, reviews, safety-checks, and analyzes content for a social media persona
5. Outputs **publish-ready content** for 6 platforms: LinkedIn, X, Instagram, TikTok, YouTube Shorts, Medium

**Instructor requirement:** No external APIs. Everything runs locally on the MacBook Pro (Apple Silicon / M-series chip). The pivot from Google Gemini/Imagen to local MLX-based models is complete and working.

**Status: FULLY WORKING END-TO-END.** The pipeline runs and completes all 7 agents successfully.

---

## 2. THE PERSONA

**Name:** Daniel Calder  
**Brand:** VirtuAI Mentor  
**Niche:** AI in business, entrepreneurship, self-improvement  
**Voice:** Direct, motivational, intense — no fluff, authority-driven, future-focused  
**Core Principles:** Leverage over effort · Systems over hustle · Execution over ideas · AI as competitive advantage  
**Power words:** build, scale, automate, execute, dominate, unlock, compound, leverage, ship, iterate  
**Banned phrases:** "game-changer", "revolutionary", "in today's fast-paced world", "it's important to note", "dive deep", "let's unpack", "I'm excited to share"  
**Visual:** Brunette, well-groomed beard, 25–30, dark navy/blue outfits, silver watch, dark moody background  
**Brand colors:** Near-black base · Electric blue #007AFF · Neon green #00D46A

---

## 3. SYSTEM ARCHITECTURE

```
┌─────────────────────────────────────────────────────────┐
│                    TERMINAL 1 (always on)                │
│                   python run_backend.py                  │
│                                                          │
│   FastAPI server on port 8765                            │
│   ├── Phi-3.5-mini (fused/fine-tuned) — text generation  │
│   └── LLaVA 1.5 7B (mlx-vlm)         — vision tasks      │
│                                                          │
│   Endpoints:                                             │
│   POST /v1/chat/completions  ← OpenAI-compatible (CrewAI)│
│   POST /generate             ← direct text generation    │
│   POST /analyze-image        ← vision analysis           │
│   POST /analyze-sentiment    ← tone analysis             │
│   POST /safety-check         ← content moderation        │
│   GET  /health               ← status check              │
│   GET  /v1/models            ← model listing (LiteLLM)   │
└─────────────────────────────────────────────────────────┘
                          ↕ HTTP
┌─────────────────────────────────────────────────────────┐
│                    TERMINAL 2                            │
│                    python main.py                        │
│                                                          │
│   CrewAI 7-Agent Pipeline (sequential):                  │
│   1. Research Agent    → trending topics brief           │
│   2. Strategy Agent    → content strategy per platform   │
│   3. Creator Agent     → actual post content             │
│   4. Visual Agent      → image prompts & video direction │
│   5. Reviewer Agent    → quality check + sentiment       │
│   6. Guardian Agent    → safety gate (tools fire here)   │
│   7. Analyzer Agent    → KPI framework & recommendations  │
│                                                          │
│   LLM: LiteLLM → openai/phi-3.5-mini → localhost:8765   │
└─────────────────────────────────────────────────────────┘
```

---

## 4. TECH STACK

| Component | Technology | Notes |
|-----------|-----------|-------|
| ML Framework | **MLX 0.31.x** (Apple) | Apple Silicon only — M-series chips |
| Text model | **mlx-lm** + Phi-3.5-mini-instruct-4bit | 3.8B params, 4-bit quantized |
| Vision model | **mlx-vlm** + LLaVA 1.5 7B | Inference only (can't fine-tune) |
| Fine-tuning | **LoRA via mlx-lm** | 600 iterations, 39 training pairs |
| API server | **FastAPI + Uvicorn** | port 8765 |
| Agent framework | **CrewAI 1.13.0** | 7 sequential agents |
| LLM bridge | **LiteLLM** | makes CrewAI talk to local endpoint |
| HTTP client | **httpx** | used in VLMClient and health checks |
| Python venv | `~/virtuai-venv/` | always activate before running |

---

## 5. PROJECT FILE STRUCTURE

```
capstone 101/
├── main.py                          ← Run the full pipeline
├── run_backend.py                   ← Start the VLM server
├── requirements.txt                 ← All dependencies
├── setup_mlx.sh                     ← MLX setup script
├── .env                             ← Environment variables
│
├── virtuai/
│   ├── agents/
│   │   ├── research_agent.py        ← Trend Research Specialist
│   │   ├── strategy_agent.py        ← Content Strategy Director
│   │   ├── creator_agent.py         ← Content Creator
│   │   ├── visual_agent.py          ← Visual Content Designer
│   │   ├── reviewer_agent.py        ← Content Quality Reviewer
│   │   ├── guardian_agent.py        ← Ethics & Safety Guardian
│   │   └── analyzer_agent.py        ← Performance Analyzer
│   │
│   ├── models/
│   │   ├── backend.py               ← ⭐ CORE: FastAPI dual-model server
│   │   ├── vlm_client.py            ← HTTP client for backend calls
│   │   └── finetune/
│   │       ├── train_lora.sh        ← LoRA fine-tuning script
│   │       ├── prepare_dataset.py   ← Dataset preparation
│   │       ├── data/
│   │       │   ├── train.jsonl      ← 39 training examples
│   │       │   └── valid.jsonl      ← Validation split
│   │       ├── adapters/            ← LoRA adapter files (TRAINED ✓)
│   │       └── fused_model/         ← Merged model (BUILT ✓)
│   │
│   ├── pipelines/
│   │   └── content_pipeline.py      ← ⭐ CORE: CrewAI pipeline setup
│   │
│   ├── tools/
│   │   ├── local_tools.py           ← ⭐ CORE: 8 CrewAI @tool functions
│   │   ├── content_tools.py         ← (legacy, not used)
│   │   ├── guardian_tools.py        ← (legacy, not used)
│   │   └── search_tools.py          ← (legacy, not used)
│   │
│   └── config/
│       └── config_loader.py         ← Config utilities
│
└── VirtuAI_Publish_Ready_Content_Package.docx  ← Generated content output
```

---

## 6. KEY FILES — WHAT EACH DOES

### `virtuai/models/backend.py` ⭐ Most Important
The FastAPI server. Loads two models at startup:
- **Phi-3.5-mini** (text): loads `fused_model/` if it exists, else base + adapters
- **LLaVA 1.5 7B** (vision): loaded via mlx-vlm

**CRITICAL BUG ALREADY FIXED:** mlx-lm 0.31.x removed `temp=` and `temperature=` as direct parameters. Temperature must now be passed via a `sampler`:
```python
# CORRECT (already fixed in this file):
from mlx_lm.sample_utils import make_sampler
sampler = make_sampler(temp=temperature)
output = lm_generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, sampler=sampler, verbose=False)

# WRONG (old broken code — do not use):
output = lm_generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, temperature=temperature)
```

### `virtuai/pipelines/content_pipeline.py` ⭐
Builds the CrewAI Crew. Key function:
```python
def _create_local_llm():
    return LLM(
        model="openai/phi-3.5-mini",
        base_url="http://localhost:8765/v1",
        api_key="local",
        timeout=180
    )
```
Also calls `_check_backend()` before starting — raises an error if backend isn't running.

### `virtuai/tools/local_tools.py` ⭐
8 CrewAI `@tool` functions that call the backend via HTTP:
- `generate_platform_content(platform, topic, format_type)` → POST /generate
- `analyze_image_for_content(image_path, platform)` → POST /analyze-image
- `analyze_sentiment_local(text)` → POST /analyze-sentiment
- `review_content_quality(content, platform)` → POST /generate (JSON)
- `content_safety_check_local(content, platform)` → POST /safety-check
- `check_persona_compliance_local(content)` → rule-based + POST /generate
- `analyze_platform_signals_local(platform, niche)` → POST /generate
- `search_trending_topics_local(niches)` → POST /generate

### `virtuai/models/vlm_client.py`
Singleton HTTP client (`VLMClient`) that all tools use to call the backend. Has `get_client()` module-level factory.

### `virtuai/models/finetune/train_lora.sh`
Runs LoRA fine-tuning. **Already completed** (600 iterations done). Only needs re-running if training data changes.

### `main.py`
Entry point. Accepts `--platforms` and `--persona` args. Calls `build_content_crew()` and runs `.kickoff()`.

### `run_backend.py`
Starts uvicorn on port 8765. Must be running before `main.py`.

---

## 7. HOW TO RUN THE PROJECT

### Prerequisites (one-time setup, already done)
```bash
# Virtual environment at ~/virtuai-venv — already created & packages installed
# Models downloaded to HuggingFace cache — already downloaded
# LoRA training done — adapters and fused model already exist
```

### Every time you want to run:
```bash
# Terminal 1 — Start the backend (keep this running)
source ~/virtuai-venv/bin/activate
cd "/Users/karammufleh/Desktop/capstone  101"
python run_backend.py

# Wait for: "Backend ready at http://localhost:8765"
# This takes 30–60 seconds (model loading)

# Terminal 2 — Run the pipeline
source ~/virtuai-venv/bin/activate
cd "/Users/karammufleh/Desktop/capstone  101"
python main.py
```

### Health check (verify backend is alive)
```bash
curl http://localhost:8765/health
# Should return: {"status":"ok","text_model":"...","vision_model":"...","vision_capable":true}
```

### Re-run fine-tuning (only if you change training data)
```bash
source ~/virtuai-venv/bin/activate
cd "/Users/karammufleh/Desktop/capstone  101"
./virtuai/models/finetune/train_lora.sh
```

---

## 8. ALL BUGS FIXED (DO NOT REINTRODUCE)

| # | Bug | Fix Applied |
|---|-----|------------|
| 1 | `mlx.__version__` AttributeError | Use `importlib.metadata.version('mlx')` |
| 2 | Wrong Python (3.9 system vs 3.13 venv) | Always `source ~/virtuai-venv/bin/activate` first |
| 3 | `python -m mlx_lm.lora` deprecated | Use `python -m mlx_lm lora` (space, not dot) |
| 4 | `--lora-layers` flag removed | Use `--num-layers` in train_lora.sh |
| 5 | LLaVA can't be LoRA fine-tuned | Switch fine-tuning model to Phi-3.5-mini ✓ |
| 6 | `--de-quantize` flag | Correct flag is `--dequantize` ✓ |
| 7 | `litellm` not installed | Installed: `pip install litellm` ✓ |
| 8 | `generate_step() unexpected kwarg 'temp'` | Use `make_sampler(temp=t)` + `sampler=sampler` ✓ |
| 9 | `generate_step() unexpected kwarg 'temperature'` | Same fix — mlx-lm 0.31.x API changed ✓ |

---

## 9. KNOWN LIMITATIONS (FOR DEMO CONTEXT)

1. **`<|end|><unk>` token artifacts in output** — The 4-bit quantized Phi-3.5-mini (3.8B params) occasionally generates past its EOS token. This is expected behavior for small quantized models. Production fix: full-precision model or larger model.

2. **Output quality** — The model produces reasonable but not polished content. The pipeline logic and tool calls all work correctly. Quality improves with a larger model or more training data.

3. **LLaVA vision** — LLaVA 1.5 7B is loaded but the Visual Agent uses it for image direction briefs (text only), not actual image generation. mlx-vlm supports image analysis but not generation.

4. **Content only for one topic** — The pipeline focuses on "AI tools for entrepreneurship" (relevance score 8.5). Multiple topics would require multiple pipeline runs or a modified prompt.

5. **Manual publishing** — X API access was not obtained. The `x_publisher.py` exists but is inactive. All content is saved to JSON and the DOCX package for manual posting.

---

## 10. WHAT HAS BEEN COMPLETED

- [x] Complete project pivot from Google Gemini API to fully local MLX architecture
- [x] Fine-tuning pipeline: 39 training examples, 600 LoRA iterations, adapters saved, fused model built
- [x] FastAPI backend with dual-model architecture (Phi text + LLaVA vision)
- [x] OpenAI-compatible `/v1/chat/completions` endpoint for CrewAI/LiteLLM
- [x] 8 CrewAI `@tool` functions all backed by backend HTTP calls
- [x] All 5 agent files updated with local tools
- [x] All 7 agents run sequentially end-to-end ✓
- [x] Results saved to `virtuai/data/content_packages/run_YYYYMMDD_HHMMSS.json`
- [x] Publish-ready content package DOCX generated for all 6 platforms
- [x] Persona visual identity locked (Daniel Calder photo + characteristics file)

---

## 11. WHAT STILL COULD BE IMPROVED (NEXT STEPS)

1. ~~**Add `repetition_penalty`**~~ ✅ Applied April 25 — `_generate_text()` now uses `make_logits_processors(repetition_penalty=1.15)` plus a post-generation sweep that strips leftover Phi-3 chat-template tokens.

2. ~~**Increase `max_tokens`**~~ ✅ Applied April 25 — `/v1/chat/completions` default raised from 512 → 1024.

3. **Add more training data** to `train.jsonl` and re-run `train_lora.sh` for better persona alignment.

4. **Add streaming support** to `/v1/chat/completions` for faster perceived response time.

5. ~~**Save structured JSON output per platform**~~ ✅ Applied April 25 — `main.py` now writes both the combined `run_<timestamp>.json` AND a `run_<timestamp>/<platform>.json` per platform.

6. **More publishers** — only `x_publisher.py` exists; LinkedIn / Instagram / TikTok / YouTube Shorts / Medium are not wired in. Manual publishing via the DOCX package is the current workflow.

---

## 12. FILES YOU NEED TO SEND CLAUDE CODE

**The capstone 101 folder contains everything.** The only things OUTSIDE the folder that matter are:

| What | Location | Notes |
|------|----------|-------|
| Virtual environment | `~/virtuai-venv/` | Packages already installed — do NOT recreate |
| HuggingFace model cache | `~/.cache/huggingface/` | Models already downloaded — do NOT re-download |
| This briefing file | `capstone 101/VIRTUAI_HANDOFF_FOR_CLAUDE_CODE.md` | The file you're reading now |
| Persona characteristics | `persona final text file .pages` + persona photo | Already documented in Section 2 above |

**When starting a new Claude Code session, share:**
1. This file (`VIRTUAI_HANDOFF_FOR_CLAUDE_CODE.md`)
2. The specific file(s) you want Claude Code to edit (e.g., `backend.py`, `local_tools.py`)
3. Any error output if you're debugging

**You do NOT need to share:**
- The entire capstone 101 folder (too large)
- The fused_model/ or adapters/ folders (binary ML weights)
- The HuggingFace cache

---

## 13. ENVIRONMENT DETAILS

| Item | Value |
|------|-------|
| Machine | MacBook Pro — Apple Silicon (M-series) |
| Python | 3.13 (in `~/virtuai-venv/`) |
| MLX version | 0.31.x |
| mlx-lm version | 0.31.x |
| CrewAI version | 1.13.0 |
| Backend port | 8765 |
| Pipeline output | `virtuai/data/content_packages/` |
| Project root | `/Users/karammufleh/Desktop/capstone  101/` |
| Venv path | `/Users/karammufleh/virtuai-venv/` |
| Text model | `mlx-community/Phi-3.5-mini-instruct-4bit` |
| Vision model | `mlx-community/llava-1.5-7b-4bit` |
| Fine-tuned output | `virtuai/models/finetune/fused_model/` |

---

*Last updated: April 25, 2026 — Cleanup pass: README rewritten to match local-MLX stack, .env.example purged of Gemini, all stale Qwen2.5-7B references replaced with Phi-3.5-mini, repetition_penalty applied, per-platform output added, legacy/unused tool files moved to `virtuai/tools/_legacy/`, requirements.txt consolidated. Pipeline still runs end-to-end. All 7 agents complete successfully.*

---

## 14. APRIL 25, 2026 — CLEANUP PASS (LOG)

A thirteen-day-old handoff doc said the pipeline was working but the surrounding docs and code comments hadn't caught up to the local-MLX pivot. Today's cleanup:

| Area               | Change                                                                                                |
|--------------------|-------------------------------------------------------------------------------------------------------|
| `README.md`        | Rewritten end-to-end. Now describes the actual three-model local stack, two-terminal workflow, hardware requirements, and troubleshooting. No more references to GPT-4o / DALL-E / Veo / n8n. |
| `.env.example`     | Removed the (incorrect) "GEMINI_API_KEY required" line. The core pipeline needs zero API keys; only optional publishers do. |
| `main.py`          | Banner now says Phi-3.5-mini. Pipeline output now also splits per platform under `run_<timestamp>/<platform>.json`. |
| `backend.py`       | All log lines / docstrings / model IDs corrected to Phi-3.5-mini. `_generate_text()` gains `repetition_penalty=1.15` via `make_logits_processors` plus a post-strip of stray chat-template tokens. `/v1/chat/completions` default `max_tokens` raised to 1024. |
| `content_pipeline.py` | Stale Qwen2.5-7B comments and the misleading "Gemini LLM" comment removed.                       |
| `creator_agent.py` | Docstring no longer talks about replacing the Gemini API.                                              |
| `local_tools.py`   | Docstring updated; legacy-tools breadcrumb added.                                                     |
| `tools/_legacy/`   | New directory. `search_tools.py`, `content_tools.py`, `guardian_tools.py`, plus the four one-off `generate_*.py` / `create_post_images.py` batch scripts moved here, with a README explaining why they're kept. |
| `requirements.txt` | Removed commented-out `google-genai`. Added `moviepy`, `mflux`, `jinja2` which the code actually imports/uses. |
| `website/templates/about.html` | Model card now says Phi-3.5-mini.                                                          |

After the pass: `python -m py_compile` is clean across every active source file; no agent imports a removed file.
