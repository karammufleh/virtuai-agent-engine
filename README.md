# VirtuAI — Autonomous AI Persona Content Engine

> **Daniel Calder** — a synthetic AI/automation business creator that researches, writes, films, validates, and publishes itself to YouTube Shorts + Instagram + Facebook + LinkedIn on autopilot.

VirtuAI is a multi-agent capstone project: an 8-agent CrewAI pipeline that produces a daily content pack (one cinematic reel + one portrait quote + one 5-slide carousel) in ≈ 12 minutes, in the locked niche "AI + automation in business", and ships it to live social platforms with no human in the loop.

---

## TL;DR — one command

```bash
python scripts/demo.py
```

End-to-end demo run. Pre-flights the services, kicks off `/run-pack`, polls until the reel + portrait + carousel are ready, prints the asset paths. Add `--no-publish` to render without pushing live.

Other useful entry points:

```bash
python main.py                       # full CrewAI 8-agent crew, all platforms
python scripts/daily_pack.py         # the scheduled-daily orchestrator
python scripts/autopilot.py          # production autopilot loop
uvicorn scripts.api_server:app --host 0.0.0.0 --port 9090   # HTTP API for n8n
```

---

## What it does, step by step

1. **📊 Analyzer** — reads metrics from the last published post and outputs a verdict (`positive` → do similar, `negative` → do different, `neutral` → free pick). Writes `lessons.json`.
2. **🔎 Research** — runs a 5-step viral-idea funnel: industry signals → 10 candidates → angle spin → virality score → hook archetype. Reads `banned_patterns.json` + `lessons.json`.
3. **📋 Strategy** — decides WHEN to publish and WHAT format mix (reel / portrait / carousel) plus per-piece outfit / mood / setting variety against the last 4–6 packs.
4. **✍️ Creator** — writes a 6-beat reel script + portrait quote + 5-slide carousel via Claude Sonnet 4.6. Enforces concreteness gates (≥ 2 named tools, ≥ 2 dollar amounts, ≥ 2 timeframes).
5. **🔍 Reviewer (text)** — checks hook strength, banned phrases, sentiment, concreteness. Sends REVISE messages to Creator on failure.
6. **🛡️ Guardian (text)** — ethics / policy / persona-compliance gate. Writes BLOCKed patterns to `banned_patterns.json` so future cycles avoid them.
7. **🎬 Visual** — renders the actual MP4 + PNGs via KIE.ai: Kling 3.0 multi-shot (face-locked, native voice + lipsync) with an automatic Seedance 2.0 fallback when Kling is unavailable, Nano Banana 2 (slide backgrounds), Suno (music underbed).
8. **🔍 Reviewer (video)** — post-render check: ffmpeg pacing, audio-gap, 9:16 aspect, ArcFace face match ≥ 0.70, lipsync correctness.
9. **📤 Publisher** — pushes APPROVE'd items via Composio (Instagram / LinkedIn / Facebook) and YouTube Direct OAuth (with COPPA flag).
10. **📊 Analyzer** (next cycle) — loop closes — the next run starts here.

Inter-agent messaging is real: Reviewer and Guardian write REVISE notes into `agent_messages.jsonl`, which the API server auto-injects into the Creator's context on its next retry. Guardian's BLOCK list and Analyzer's verdict trail persist across runs.

**Wall-clock per pack:** ≈ 12 minutes. **Spend:** ≈ $5–7 in KIE credits (Kling Pro reels + Claude + Suno + Nano Banana 2).

---

## Tech stack — production only

After the 2026-05-19 cleanup, the project runs on exactly four API surfaces. No experimental models, no Gemini, no JSON2Video, no Creatomate / Shotstack, no Kling-direct, no TikTok / Medium publishers.

| Surface | Role | Auth |
|---|---|---|
| **KIE.ai** | All generation: Claude Sonnet 4.6 (text), Kling 3.0 multi-shot (reels), Nano Banana 2 (images), Suno (music) — plus the file-stream upload host for public asset URLs | `KIE_API_KEY` |
| **Composio** | Publishing to Instagram / Facebook / LinkedIn via a single SDK. Falls back to DRY-RUN (logs to JSONL) when the key is missing. | `COMPOSIO_API_KEY` + `COMPOSIO_USER_ID` |
| **YouTube Data API v3 (direct OAuth)** | YouTube Shorts uploads with the COPPA `selfDeclaredMadeForKids` flag set correctly (Composio's wrapper drops it and the upload fails silently). | `YOUTUBE_OAUTH_*` refresh-token flow |
| **Facebook Page (Composio)** | Photo + video posts to a Facebook Page (replaces X as of 2026-05-21). | `FB_PAGE_ID` + Composio FB connection |

KIE catalogue slugs are centralized in `virtuai/config/models.yaml`. The Python loader (`virtuai/utils/config_loader.py`) exposes `model_slug(key)` and `kie_endpoint(name)`.

```python
from virtuai.utils.config_loader import model_slug, kie_endpoint

model_slug("reel_video")     # → "kling-3.0/video"
model_slug("image_post")     # → "nano-banana-2"
model_slug("script_writer")  # → "claude-sonnet-4-6"
kie_endpoint("create_task")  # → "https://api.kie.ai/api/v1/jobs/createTask"
kie_endpoint("claude")       # → "https://api.kie.ai/claude/v1/messages"
```

---

## Architecture

```
                  ┌──────────────┐
                  │  TRIGGERS    │
                  └──────┬───────┘
       ┌────────────┬───┴───┬────────────────┐
       ▼            ▼       ▼                ▼
   n8n cron     Manual   python main.py   /run-pack
   (09:00 /     /virtuai-                 (FastAPI)
    17:00)     agent-run
       │       webhook       │               │
       └───────┬─────────────┴───────────────┘
               ▼
       ┌────────────────────────────────────────────┐
       │   scripts/api_server.py  (FastAPI :9090)   │
       │   20+ endpoints                             │
       └─────────────────┬──────────────────────────┘
                         ▼
       ┌────────────────────────────────────────────┐
       │   virtuai/pipelines/content_pipeline.py    │
       │   CrewAI Crew (8 agents)                   │
       └─────────────────┬──────────────────────────┘
                         ▼
   📊 Analyzer → 🔎 Research → 📋 Strategy → ✍️ Creator
                         │
                         ▼
   🔍 Reviewer(text) → 🛡️ Guardian(text) → 🎬 Visual
   → 🔍 Reviewer(video) → 📤 Publisher
                         │
                         ▼
        ┌─ KIE (Kling/Nano/Suno/Claude) ─┐
        └─ Composio + YouTube direct ────┘
                         │
                         ▼
   virtuai/data/{generated_videos, generated_images,
                 content_packages, autopilot_history.json}
                         │
                         ▼
       📊 Analyzer ◀── feedback loop (next cycle)
```

### Credit-aware n8n workflow

The unified n8n workflow (`n8n/virtuai_unified.json`, 34 nodes, `active=true`) runs **all cheap text gates before any expensive render**, so a Reviewer or Guardian rejection costs ~$0.10 in Claude tokens — not $3–5 in Kling credits.

```
📊 Analyzer → IF positive?
   ├─ YES → 🔎 Research (similar)
   └─ NO  → 🔎 Research (different)
         → 📋 Strategy → ✍️ Creator
         → 🔍 Reviewer TEXT (cheap) → IF Text PASS?
            ├─ NO  → ✍️ Creator retry → 🔍 Reviewer retry → notify-fail
            └─ YES → 🛡️ Guardian TEXT → IF Ethics APPROVE?
                     ├─ NO  → notify-BLOCKED
                     └─ YES → 🎬 Visual (EXPENSIVE — runs ONCE)
                              → 🔍 Reviewer VIDEO → IF Video PASS?
                                 ├─ NO  → notify post-render fail
                                 └─ YES → 📤 Publisher
                                          → LinkedIn amplify
                                          → notify pack live
```

Five trigger paths: `Schedule 09:00`, `Schedule 17:00`, `Manual run`, `Webhook /virtuai-agent-run`, `Webhook /virtuai-model-call`.

---

## Quickstart

```bash
# 1. Install  (cloud-only — no GPU / Apple-Silicon / MLX needed)
#    Prereq: ffmpeg on PATH →  brew install ffmpeg  (macOS)  |  apt install ffmpeg  (Linux)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

```bash
# 2. Add credentials — copy the template and fill in
cp .env.example .env
```

Minimum keys needed:

```bash
KIE_API_KEY=...                  # required — all cloud generation
COMPOSIO_API_KEY=...             # required for live publishing (DRY-RUN otherwise)
COMPOSIO_USER_ID=...
IG_USER_ID=...
FB_PAGE_ID=...
YOUTUBE_OAUTH_CLIENT_ID=...
YOUTUBE_OAUTH_CLIENT_SECRET=...
YOUTUBE_OAUTH_REFRESH_TOKEN=...
```

```bash
# 3. Boot the API server (n8n calls this)
uvicorn scripts.api_server:app --host 0.0.0.0 --port 9090

# 4. Run the demo
python scripts/demo.py
```

---

## Active platforms

| Platform ID | Config | Publisher | Status |
|---|---|---|---|
| `instagram` | `virtuai/config/platforms/instagram.yaml` | Composio (Reels + image + carousel) | LIVE |
| `linkedin` | `virtuai/config/platforms/linkedin.yaml` | Composio | LIVE |
| `youtube_shorts` | `virtuai/config/platforms/youtube_shorts.yaml` | YouTube Direct OAuth | LIVE |
| `facebook` | `virtuai/config/platforms/facebook.yaml` | Composio (`FACEBOOK_CREATE_VIDEO_POST` + `FACEBOOK_CREATE_PHOTO_POST`) | LIVE — replaced X on 2026-05-21 |

Facebook is reachable through Composio as a cross-post target — it's not a primary platform but the publisher knows how to post there if asked.

---

## Repository layout

| Path | What it is |
|---|---|
| `main.py` | CLI entry: `--platforms / --persona / --llm` |
| `requirements.txt` | Pinned **cloud-only** dependencies (crewai, composio, fastapi, httpx, pydantic, …). No GPU/MLX deps. |
| `_archive/` | Superseded Phase-1 material — the local MLX backend (`run_backend.py`, `virtuai/models/backend.py`), MLX installer (`setup_mlx.sh`), LoRA fine-tuning, and on-device render scripts. **Not used by the final cloud workflow**; kept for history only. |
| `.env.example` | Template for the production keys (KIE, Composio, IG, FB, YouTube) |
| `virtuai/agents/` | 8 CrewAI agent factories (one file per agent) |
| `virtuai/tools/` | Tool implementations — `cloud_tools.py`, `local_tools.py`, `kie_kling.py`, `kie_upload.py`, etc. |
| `virtuai/pipelines/content_pipeline.py` | The CrewAI crew builder |
| (X / Twitter dropped 2026-05-21) | Replaced by Facebook Page publishing via Composio |
| `virtuai/persona/` | `persona_anchor.json` + `canonical_daniel.png` + LoRA training dataset + eval set |
| `virtuai/config/personas/virtuai_mentor.yaml` | The Daniel Calder persona spec |
| `virtuai/config/platforms/` | Per-platform format constraints |
| `virtuai/config/models.yaml` | Centralized KIE model catalogue |
| `virtuai/website/` | **Optional Phase-1 showcase site** (`app.py` + templates). Not imported by the pipeline; safe to ignore for the cloud system. |
| `virtuai/locked/v1_2026-05-18/` | SHA-256–verified frozen baseline of 11 production files |
| `virtuai/data/` | Persisted state: `autopilot_history.json`, `lessons.json`, `banned_patterns.json`, `agent_messages.jsonl`, `generated_videos/`, `generated_images/`, `content_packages/` |
| `virtuai/tests/` | Smoke tests (pytest) |
| `scripts/` | Entry-point scripts (`api_server.py`, `autopilot.py`, `daily_pack.py`, `demo.py`, `publish_v16.py`, …) |
| `scripts/_archive/` | Historical one-off run scripts (do not import) |
| `virtuai/tools/_legacy/` | Pre-Phase-3 helpers kept for reproducibility (do not import) |
| `n8n/virtuai_unified.json` | The 34-node n8n workflow |
| `docs/API_REFERENCE.md` | Full external-API inventory + endpoint table |
| `docs/SUBMISSION.md` | Single reviewer entrypoint — start here for submission |
| `docs/EVALUATION_METRICS.md` | Quantitative outcomes for grading rubrics |
| `docs/FINAL_PROJECT_COMPLETION_PLAN.md` | Current state + completed/remaining tasks |
| `TECHNICAL_STATUS_REPORT.md` | Engineering status report |
| `CAPSTONE.md`, `CHALLENGES.md`, `PROJECT_STANDARDS.md`, `PUBLISHER_INTEGRATIONS.md` | Long-form project notes |

---

## FastAPI endpoints (`scripts/api_server.py` on :9090)

| Endpoint | Method | Purpose |
|---|---|---|
| `/healthz` | GET | Liveness probe |
| `/history` | GET | Recent autopilot history |
| `/tasks` | GET | Active + recent tasks |
| `/status/{task_id}` | GET | Per-task progress |
| `/run-pack` | POST | Daily pack: reel + portrait + carousel |
| `/run-reel` | POST | Just a reel |
| `/run-portrait` | POST | Just a portrait still |
| `/run-carousel` | POST | Just a 5-slide carousel |
| `/publish-reel` | POST | Publish an existing reel |
| `/publish-image-post` | POST | Publish an existing image/carousel |
| `/agents` | GET | List all 8 agents with roles + tools |
| `/agents/{agent_name}/run-sync` | POST | Run a single agent synchronously (used by n8n) |
| `/agents/{agent_name}/run` | POST | Run a single agent asynchronously |
| `/platforms/youtube/upload` | POST | YouTube direct OAuth upload |
| `/platforms/instagram/post-reel` | POST | IG reel via Composio |
| `/platforms/instagram/post-image` | POST | IG image via Composio |
| `/platforms/instagram/post-carousel` | POST | IG carousel via Composio |
| `/platforms/linkedin/post` | POST | LinkedIn via Composio |
| `/n8n/run-reel-and-publish` | POST | n8n entry — full reel pipeline + publish |
| `/n8n/trigger-pack` | POST | n8n entry — full pack pipeline + publish |

---

## Testing

```bash
pytest
```

**140 tests** across 12 files covering: every agent factory, the output schemas, Publisher safety gates, env-gated validation layer, KIE-CDN SSL workaround, IG short-form captioning, the 8-agent planner and reel-fallback suites, no-publish safety gate, and demo polling. Runs in ~2 minutes. **Zero external API calls** in the test suite.

Captured evidence of the last clean run is in `docs/evidence/` — `pytest_output.txt`, `pipeline_check.txt`, `validate_latest.txt`, `no_publish_tests.txt`, `locked_baseline_verify.txt`.

---

## Demo Readiness

For final demo preparation, see:

- [`docs/DEMO_READINESS_CHECKLIST.md`](docs/DEMO_READINESS_CHECKLIST.md)
- [`docs/DEMO_PRESENTATION_SCRIPT.md`](docs/DEMO_PRESENTATION_SCRIPT.md)
- [`docs/NETWORK_BLOCK_TROUBLESHOOTING.md`](docs/NETWORK_BLOCK_TROUBLESHOOTING.md)

Before a demo, run:

```bash
python scripts/agent_cli.py --validate-latest
python scripts/agent_cli.py --pipeline-check --offline
```

Expected: `--pipeline-check` ends with `23 / 23 checks passed`. Both commands are offline and never publish.

---

## What's done vs what's pending

✅ **Done (final state 2026-05-20)**
- All 8 agents wired into a single CrewAI pipeline
- Credit-aware n8n workflow imported and `active=true`
- KIE production stack (Claude Sonnet 4.6 + Kling 3.0 multi-shot + Nano Banana 2 + Suno) verified live
- Publishers: Composio LIVE for IG / LinkedIn / Facebook / X; YouTube Direct OAuth for Shorts
- Inter-agent messaging via persistent JSON/JSONL files, auto-injected by the API server
- Locked baseline at `virtuai/locked/v1_2026-05-18/` with SHA-256 manifest — still verifies
- 216+ generated videos + dozens of images + multiple content packages on disk
- **110 / 110 tests passing** (10 test files, ~11 s, zero live API calls)
- **23 / 23 pipeline-readiness checks** passing offline
- Centralized model catalogue (`virtuai/config/models.yaml`)
- All experimental APIs removed (no Gemini / JSON2Video / Creatomate / Shotstack / Kling-direct / TikTok / Medium)
- **`/run-pack` honours `publish: false`** — no-publish safety gate verified by 13 tests
- **IG short-form captioning** — 2122 → 466 chars typical, real-creator voice
- **Variety pools doubled** — 18 outfits, 14 moods, 7 setting pools, 35-phrase banned-cliché list
- **KIE-CDN SSL workaround** — opt-in, host-allowlisted (only `tempfile.aiquickdraw.com`)
- **End-to-end safe demo verified** today (task `e422c36713de`, 7 min wall-clock, zero publishing)
- **Production publishing demonstrated** today — 7 IG posts shipped (reel + portrait + 5 individual slides, page numbers stripped)

⬜ **Optional pre-submission polish**
<!-- X / Twitter was dropped on 2026-05-21 and replaced with Facebook Page publishing. -->
- (X retired — see Facebook row above for the 4th platform.)
- Renew the YouTube OAuth refresh token (only matters if YT is part of demo)
- Reconnect LinkedIn in the Composio dashboard (only matters if LI is part of demo)
- `git init` + first commit (the working tree is not under version control yet)
- Record a 60-second demo screencast (manual step — `scripts/demo.py --no-publish` is the obvious take)

See [`docs/SUBMISSION.md`](docs/SUBMISSION.md) for the reviewer entrypoint, [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) for the API inventory, and [`docs/FINAL_PROJECT_COMPLETION_PLAN.md`](docs/FINAL_PROJECT_COMPLETION_PLAN.md) for the live submission-readiness checklist.

---

## License

Capstone project — Karam Mufleh, AI Engineering 2026.
