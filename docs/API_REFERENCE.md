# VirtuAI — API Reference

_Last updated 2026-05-20. Single source of truth for every external API the project talks to, every model slug it uses, every endpoint it exposes, and every n8n trigger path. For the broader status / completion plan, see [`FINAL_PROJECT_COMPLETION_PLAN.md`](FINAL_PROJECT_COMPLETION_PLAN.md). For the reviewer entrypoint, see [`SUBMISSION.md`](SUBMISSION.md)._

---

## 1. Core gateway (one vendor for all generation)

| API / Service | Endpoint(s) | Used For | Where in Code | Auth | Status |
|---|---|---|---|---|---|
| **KIE.ai** (unified gateway) | `https://api.kie.ai/api/v1/jobs/createTask` + `recordInfo`; `https://api.kie.ai/claude/v1/messages` | All cloud generation + LLM reasoning | `virtuai/tools/cloud_tools.py`, `virtuai/tools/kie_kling.py` | `Authorization: Bearer KIE_API_KEY` | LIVE |
| **KIE File Upload** | `https://kieai.redpandaai.co/api/file-stream-upload` | Public-URL host for face / audio assets (3-day TTL) | `virtuai/tools/kie_upload.py` | Same KIE key | LIVE |

## 2. Generation models (all via KIE)

| Logical Name | KIE slug | Used For | Source of truth |
|---|---|---|---|
| `reel_video` | `kling-3.0/video` | Production reels with native lipsync + audio | `virtuai/config/models.yaml` |
| `image_post` | `nano-banana-2` | Production portraits + carousel backgrounds | same |
| `music_underbed` | `suno-v3.5` | Instrumental underbed for reels | same |
| `script_writer` | `claude-sonnet-4-6` | Script writing + viral idea funnel | same |

Resolve slugs in code:

```python
from virtuai.utils.config_loader import model_slug, kie_endpoint
model_slug("reel_video")           # → "kling-3.0/video"
kie_endpoint("create_task")        # → "https://api.kie.ai/api/v1/jobs/createTask"
kie_endpoint("claude")             # → "https://api.kie.ai/claude/v1/messages"
```

## 3. Publishing

### 3.0 Platform status overview (verified 2026-05-21)

| Platform | Status | Notes |
|---|---|---|
| **YouTube Shorts** | ✅ **LIVE** | Verified `tRhZVZQxbwo` uploaded 2026-05-21 via direct OAuth (new GCP project, consent in Production). |
| **Instagram** | ✅ **LIVE** | Verified 7 / 7 posts (reel + portrait + 5 carousel slides) 2026-05-20. |
| **Facebook** | ⚠ **TEXT-ONLY** | Text post verified 2026-05-21. Media posts (photo + video) return Meta anti-abuse **code 368 / subcode 4854002** — Page Identity Verification required at the platform level for new Pages. Integration code is correct; gate is platform-side. |
| **LinkedIn** | ✗ **PLATFORM-POLICY-BLOCKED** | LinkedIn flagged the persona account during OAuth re-authentication and demands **government-ID verification** before granting third-party API access. Integration code is in place and wrapped with `auth_guard`; will resume working as soon as the platform-side gate clears. |

All four platforms are wrapped with the circuit-breaker and audit-log layer described in [`AUTH_GUARD_REPORT_SECTION.md`](AUTH_GUARD_REPORT_SECTION.md). Run `python scripts/publisher_healthcheck.py` for a real-time probe.

### 3.1 Detailed publishing surface

| API / Service | Used For | Where in Code | Auth | Status |
|---|---|---|---|---|
| **Composio SDK** | Cross-platform publishing | `virtuai/tools/composio_tools.py`, `virtuai/agents/publisher_agent.py` | `COMPOSIO_API_KEY` + `COMPOSIO_USER_ID` | LIVE (DRY-RUN fallback) |
| **YouTube Data API v3** (direct OAuth) | Shorts upload with COPPA flag | `virtuai/tools/youtube_direct.py` | `YOUTUBE_OAUTH_*` refresh-token flow | ✅ LIVE — verified 2026-05-21 |
| **Instagram Graph API** (via Composio) | Reels + image posts | `INSTAGRAM_CREATE_MEDIA_CONTAINER` → `INSTAGRAM_CREATE_POST` | Composio (`IG_USER_ID`) | ✅ LIVE — 7 / 7 posts confirmed 2026-05-20 |
| **Instagram Graph API** (direct, carousel only) | 5-slide swipe carousels (Composio wrapper can't do parents) | `virtuai/tools/ig_carousel.py` | `IG_ACCESS_TOKEN` (currently not set — falls back to single-image flow) | PARTIAL — falls back gracefully |
| **LinkedIn API** (via Composio) | Posts | `LINKEDIN_CREATE_LINKED_IN_POST` | Composio (URN cached) | ✗ PLATFORM-POLICY-BLOCKED — gov-ID gate (see §3.0) |
| **Facebook Page text post** (via Composio) | Page text posts | `FACEBOOK_CREATE_POST` | Composio (`FB_PAGE_ID`) | ✅ LIVE — verified 2026-05-21 |
| **Facebook Page media post** (via Composio) | Page photo + video posts | `scripts/publish_v16.py::publish_facebook_reel` + `scripts/publish_images.py::publish_facebook_image` | Composio (`FB_PAGE_ID`) | ⚠ TEXT-ONLY — code 368 identity gate (see §3.0) |

## 4. On-device utilities (no external API)

The final cloud workflow routes all generation through the KIE.ai gateway. Of the
on-device components below, only **Whisper** (caption timing) and **ArcFace** (persona
face verification) are used by the final system. The rest are **Phase-1 local
generators, retained for history but not used in the final workflow** (see `_archive/`
and the report's iteration journey).

| Component | Used By | Status |
|---|---|---|
| **Whisper** | `caption_generator.py` | ✅ used — caption word-timing |
| **ArcFace** | `verify_face_identity` (Reviewer Agent) | ✅ used — face match ≥ 0.70 |
| Phi-3.5-mini + LoRA | `local_tools.generate_platform_content` | Phase-1 — not used |
| Z-Image-Turbo + Daniel LoRA | `local_tools.generate_image_local` | Phase-1 — not used |
| F5-TTS | `local_tools.generate_talking_head_local` | Phase-1 — not used |
| SadTalker / Wav2Lip | Talking-head render | Phase-1 — not used |
| LLaVA 1.5 7B | `local_tools.analyze_image_for_content` | Phase-1 — not used |
| **ffmpeg** | `reel_builder.py`, `matte_video.py` |
| **PIL** | `slide_renderer.py` (carousel typography) |

The locked baseline at `virtuai/locked/v1_2026-05-18/` does NOT route to any local model — production runs entirely cloud-first.

## 5. Active social platforms

| Platform ID | Config | Publisher | Status |
|---|---|---|---|
| `instagram` | `virtuai/config/platforms/instagram.yaml` | Composio + (carousel) direct Graph | ✅ LIVE — 7/7 posts 2026-05-20 |
| `linkedin` | `virtuai/config/platforms/linkedin.yaml` | Composio | ✗ PLATFORM-POLICY-BLOCKED — gov-ID gate (see §3.0) |
| `facebook` | `virtuai/config/platforms/facebook.yaml` | Composio (`FACEBOOK_CREATE_POST` text; `FACEBOOK_CREATE_VIDEO_POST` + `FACEBOOK_CREATE_PHOTO_POST` media) | ⚠ TEXT-ONLY — code 368 identity gate on media (replaced X on 2026-05-21) |
| `youtube_shorts` | `virtuai/config/platforms/youtube_shorts.yaml` | YouTube Direct OAuth | ✅ LIVE — verified 2026-05-21 |

Facebook is reachable via Composio but is not a top-level "platform" in persona configs — cross-post target only.

**TikTok and Medium are intentionally NOT publishers** — their config YAML files were removed on 2026-05-19. Format constraints could be added back if a real publisher is ever shipped, but until then the project doesn't claim them.

## 6. FastAPI endpoints (`scripts/api_server.py` on :9090)

| Endpoint | Method | Purpose |
|---|---|---|
| `/healthz` | GET | Liveness probe |
| `/history` | GET | Recent autopilot history |
| `/tasks` | GET | Active + recent tasks |
| `/status/{task_id}` | GET | Per-task progress (returns `state=success` on completion) |
| `/run-pack` | POST | Daily pack: reel + portrait + carousel. **Honours `publish: false` / `dry_run: true` / `no_publish: true` in body** (2026-05-20 fix). |
| `/run-reel` | POST | Just a reel |
| `/run-portrait` | POST | Just a portrait still |
| `/run-carousel` | POST | Just a 5-slide carousel |
| `/publish-reel` | POST | Publish an existing reel |
| `/publish-image-post` | POST | Publish an existing image / carousel |
| `/agents` | GET | List all 8 agents with roles + tools |
| `/agents/{name}/run-sync` | POST | Run a single agent synchronously (used by n8n) |
| `/agents/{name}/run` | POST | Run a single agent asynchronously |
| `/platforms/youtube/upload` | POST | YouTube direct OAuth upload |
| `/platforms/instagram/post-reel` | POST | IG reel via Composio |
| `/platforms/instagram/post-image` | POST | IG image via Composio |
| `/platforms/instagram/post-carousel` | POST | IG carousel via Composio (falls back to single without `IG_ACCESS_TOKEN`) |
| `/platforms/linkedin/post` | POST | LinkedIn via Composio |
| `/n8n/run-reel-and-publish` | POST | n8n entry — full reel pipeline + publish |
| `/n8n/trigger-pack` | POST | n8n entry — full pack pipeline + publish |

## 7. n8n triggers (`n8n/virtuai_unified.json`, 34 nodes, `active=true`)

| Trigger | Path | Default Schedule |
|---|---|---|
| `Schedule 09:00` | full pack → publish → LinkedIn amplify | Daily 09:00 |
| `Schedule 17:00` | full pack → publish → LinkedIn amplify | Daily 17:00 |
| `Manual run` | one-click full pack | n/a |
| `Webhook /virtuai-agent-run` | POST → single agent | external trigger |
| `Webhook /virtuai-model-call` | POST → single KIE model | external trigger |

The workflow is **credit-aware**: cheap text gates (Reviewer text + Guardian text) run before the expensive Visual render. A reject before Visual costs ≈ $0.10 in Claude tokens instead of ≈ $4 in Kling credits.

## 8. Environment variables

Every key the project reads, with source-of-truth file paths:

| Variable | Required? | Used by |
|---|---|---|
| `KIE_API_KEY` | **yes** | `virtuai/tools/cloud_tools.py`, `kie_kling.py`, `kie_upload.py` |
| `COMPOSIO_API_KEY` | yes for live publish | `virtuai/tools/composio_tools.py` |
| `COMPOSIO_USER_ID` | yes for live publish | same |
| `IG_USER_ID` | for IG posts | `virtuai/agents/publisher_agent.py`, `virtuai/tools/ig_carousel.py` |
| `IG_ACCESS_TOKEN` | optional — only for true 5-swipe carousels | `virtuai/tools/ig_carousel.py` |
| `FB_PAGE_ID` | for FB cross-post | Composio |
| `YOUTUBE_OAUTH_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` | for YT Shorts | `virtuai/tools/youtube_direct.py` |
| ~~X_*~~ | dropped 2026-05-21 — replaced by Facebook (no extra env beyond `FB_PAGE_ID`) | n/a |
| `VIRTUAI_TRUST_KIE_CDN` | optional opt-in | `virtuai/utils/asset_download.py` |
| `VIRTUAI_VALIDATE_AGENT_OUTPUTS` | optional opt-in | `virtuai/schemas/validators.py` |
| `VIRTUAI_BACKEND_URL` | rarely | Local backend override |

Template: [`.env.example`](../.env.example).

## 9. What's been removed (do not re-add)

- TikTok publisher — removed 2026-05-19, no publisher exists in current code
- Medium publisher — same
- Gemini reasoning option — removed; KIE is the only agent LLM
- JSON2Video editor — removed
- Creatomate / Shotstack editors — removed
- Kling direct API client (V1-6 era) — moved to `virtuai/tools/_legacy/`, deleted
- Experimental Veo 3.1 / InfiniteTalk / Nano Banana Pro / Kling 2.6 i2v / Kling Motion Control / ElevenLabs-via-KIE — removed (the entire `virtuai/experimental/` package)

If any of these need to come back, do it AFTER capstone submission and document the deltas in a separate addendum.
