# VirtuAI ↔ n8n — Unified Automation

**One workflow** with five parallel trigger paths and every agent + model
exposed as its own visible node. Open it at http://localhost:5678.

## What's in the workflow

```
ROW 1 — Reel schedule (daily 09:00)
  Schedule → POST /run-reel-and-publish → poll → Shape ─┐
                                                          │
ROW 2 — Image schedule (daily 17:00)                      │
  Schedule → POST /run-images-and-publish → poll → Shape ─┤
                                                          ▼
                                       LinkedIn amplify (shared)
                                                ↓
                                       Notify success email (shared)

ROW 3 — Webhook /virtuai-agent-run
  Webhook → Parse → POST /agents/{name}/run → poll → Return output

ROW 4 — Webhook /virtuai-model-call
  Webhook → Parse → POST /models/{endpoint} → return

ROW 5 — Manual: Full Agent Crew (visible 8-agent pipeline)
  🔎 Research → 📋 Strategy → ✍️ Creator → 🎬 Visual
   → 🔍 Reviewer → 🛡️ Guardian → 📤 Publisher → 📊 Analyzer
   → Notify crew done
```

The eight agent nodes are **visually separate** so you can see exactly
which step is running. Click **Manual — Full Agent Crew** to execute
the full crew sequentially.

## Triggers

| Trigger | When it fires | What it does |
|---|---|---|
| **Schedule 09:00 — Reel** | cron daily 09:00 | Reel → YouTube + IG + LinkedIn |
| **Schedule 17:00 — Images** | cron daily 17:00 | Portrait + Carousel → IG + LinkedIn |
| **Webhook /virtuai-agent-run** | external HTTP POST | Invoke a single agent |
| **Webhook /virtuai-model-call** | external HTTP POST | Direct model call (Claude / Kling / Nano Banana / Suno / TTS / etc.) |
| **Manual — Full Agent Crew** | click in n8n UI | All 8 agents in sequence |

## All 8 agents visible as nodes (row 5)

| Node | Agent | Tools |
|---|---|---|
| 🔎 **Research Agent** | research | `discover_trending_topic`, `search_trending_topics_local`, `analyze_platform_signals_local` |
| 📋 **Strategy Agent** | strategy | `read_autopilot_history` |
| ✍️ **Creator Agent** | creator | `write_viral_script`, `write_portrait_content`, `write_carousel_content`, `generate_platform_content` |
| 🎬 **Visual Agent** | visual | `generate_cinematic_reel`, `render_image_post`, `generate_image_local`, `generate_talking_head_local`, `generate_video_local`, `analyze_image_for_content`, `generate_captions`, `build_reel` |
| 🔍 **Reviewer Agent** | reviewer | `analyze_sentiment_local`, `review_content_quality`, `review_video_quality`, `verify_face_identity` |
| 🛡️ **Guardian Agent** | guardian | `content_safety_check_local`, `check_persona_compliance_local` |
| 📤 **Publisher Agent** | publisher | Composio (LinkedIn/IG/X/Facebook/Medium) + `publish_reel_to_youtube`, `publish_reel_to_instagram`, `publish_image_to_instagram`, `publish_post_to_linkedin`, `YOUTUBE_DIRECT_UPLOAD` |
| 📊 **Analyzer Agent** | analyzer | `read_autopilot_history`, `fetch_instagram_post_metrics` |

## All models accessible (via `POST /webhook/virtuai-model-call`)

```bash
curl -X POST http://localhost:5678/webhook/virtuai-model-call \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude",
    "endpoint": "/models/claude/chat",
    "payload": {
      "system": "You are a viral hook writer.",
      "prompt": "Give me 5 contrarian AI/automation hooks.",
      "max_tokens": 800
    }
  }'
```

Available `endpoint` values:
| Model | Endpoint | Underlying |
|---|---|---|
| **Claude Sonnet 4.6** | `/models/claude/chat` | KIE → Anthropic |
| **Kling 3.0 multi-shot reel** | `/models/kling/reel` | KIE → Kling |
| **Kling 3.0 image-to-video** | `/models/kling/i2v` | KIE → Kling |
| **Nano Banana 2 edit** | `/models/nano-banana/edit` | KIE → Google |
| **Suno V3.5 music** | `/models/suno/music` | KIE → Suno |
| **ElevenLabs TTS** | `/models/elevenlabs/tts` | KIE → ElevenLabs |
| **ElevenLabs voice-changer** | `/models/elevenlabs/voice-changer` | ElevenLabs direct |
| **Whisper captions** | `/models/whisper/captions` | OpenAI Whisper (local) |
| **YouTube upload** | `/platforms/youtube/upload` | YouTube Data API v3 |
| **IG single image** | `/platforms/instagram/post-image` | Composio |
| **IG 5-slide carousel** | `/platforms/instagram/post-carousel` | Meta Graph direct |
| **IG reel** | `/platforms/instagram/post-reel` | Composio |
| **LinkedIn post** | `/platforms/linkedin/post` | Composio |

`GET http://localhost:9090/models` lists them all programmatically.

## Setup

1. **Start the VirtuAI API:**
   ```bash
   cd /path/to/virtuai-agent-engine     # the repo root
   source .venv/bin/activate
   uvicorn scripts.api_server:app --host 0.0.0.0 --port 9090
   ```

2. **Start n8n** (already running, keep up):
   ```bash
   npx --yes n8n   # listens on :5678
   ```

3. **Open n8n:** http://localhost:5678

4. **One-time setup inside n8n:**
   - Settings → Variables:
     - `VIRTUAI_API_BASE` = `http://localhost:9090`
     - `NOTIFY_EMAIL` = your email
   - Credentials → New → SMTP → fill in (Gmail / SendGrid / Resend)

5. **Activate the workflow** — toggle in the top right corner.

## Smoke tests

```bash
# List the 8 agents + their tools
curl http://localhost:9090/agents | python3 -m json.tool

# List all available models
curl http://localhost:9090/models | python3 -m json.tool

# Trigger the LinkedIn amplifier (this posts!)
curl -X POST http://localhost:9090/webhook/linkedin-amplify \
  -H "Content-Type: application/json" \
  -d '{"text":"smoke test","source":"manual"}'

# Run an agent from anywhere
curl -X POST "http://localhost:5678/webhook/virtuai-agent-run" \
  -H "Content-Type: application/json" \
  -d '{"agent":"research","prompt":"Find a contrarian take on AI sales tools."}'

# Call a model directly
curl -X POST "http://localhost:5678/webhook/virtuai-model-call" \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint": "/models/claude/chat",
    "payload": {
      "system": "You are a marketing copy editor.",
      "prompt": "Rewrite this hook to sound more contrarian: \"Stop trying to scale.\"",
      "max_tokens": 200
    }
  }'
```

## Port layout

| Service | Port |
|---|---|
| **n8n** | 5678 |
| **VirtuAI API** | 9090 |
| Showcase + dashboard | 8080 — optional Phase-1 showcase |
| Local persona backend | 8765 — Phase-1 only, archived under `_archive/`; **not used by the cloud workflow** |

## Files

- **`virtuai_unified.json`** — the one workflow (40 nodes, all triggers + agents + models)
- **`_archive/`** — the 4 split workflows kept for record
