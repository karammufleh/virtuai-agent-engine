# VirtuAI — Capstone Submission

> Reviewer entrypoint. Open this file first.

VirtuAI is a multi-agent AI virtual persona engine that researches, writes, generates visuals, validates, and publishes content for the synthetic creator **Daniel Calder** — autonomously, across Instagram / LinkedIn / Facebook / YouTube Shorts. The full pipeline is 8 CrewAI agents → KIE.ai cloud generation → Composio + YouTube Direct publishing → analytics feedback. Triggered by n8n on a 09:00 / 17:00 cron, or by CLI on demand.

---

## 1. Quick verification (3 commands, ~30 seconds, no network)

```bash
# Pre-demo readiness — 23 / 23 checks must pass
python scripts/agent_cli.py --pipeline-check --offline

# Validate the latest content package against Pydantic schemas
python scripts/agent_cli.py --validate-latest

# Run the full test suite
pytest
# Expected: 140 passed, ~2 min wall-clock
```

All three exit zero. None of them call a paid API.

## 2. End-to-end live demo (≈ 7 min wall-clock, **no publishing**)

```bash
# Verify the no-publish safety gate first (13 / 13 must pass)
pytest virtuai/tests/test_no_publish.py -v

# Restart API server with patched no-publish gate active
pkill -f "uvicorn scripts.api_server" || true
sleep 2
nohup ./.venv/bin/python -m uvicorn scripts.api_server:app \
  --host 0.0.0.0 --port 9090 > /tmp/virtuai_api.log 2>&1 &
until curl -sf -o /dev/null http://localhost:9090/healthz; do sleep 2; done

# Run the actual safe demo — generation only, ZERO publishing
python scripts/demo.py --no-publish
```

Expected after ≈ 7 minutes:

- A new mp4 in `virtuai/data/generated_videos/`
- A new portrait PNG + 5 carousel slides under `virtuai/data/generated_images/posts/pack_*_<ts>/`
- A new manifest at `virtuai/data/content_packages/daily_pack_<ts>.json` with every `publish.*` block reading `"platform_status": "skipped"`
- Three NO-PUBLISH warning lines in `/tmp/virtuai_api.log`
- **Zero** Composio / YouTube Direct calls (verified by the absence of `composio_dry_run.jsonl` and no IG / YT / LI ids in the manifest)

## 3. Production publishing — verified live 2026-05-20

A separate one-shot script (`scripts/publish_pack.py --live`) actually published a full pack to Instagram today:

| # | Piece | IG post ID |
|---|---|---|
| 1 | Reel | `18099241174959159` |
| 2 | Portrait | `17853516975671914` |
| 3 | Carousel slide 1 (cover) | `17917609524364522` |
| 4 | Carousel slide 2 (problem) | `17864460717567554` |
| 5 | Carousel slide 3 (insight) | `18189068242327656` |
| 6 | Carousel slide 4 (proof) | `18129748813587214` |
| 7 | Carousel slide 5 (payoff) | `17857233120645162` |

All 7 succeeded. Captions are short-form (≤ 500 chars), page numbers stripped, real-creator voice. Per-post manifest at `/tmp/virtuai_publish_pack.jsonl`.

## 4. What's where

| Asset | Path |
|---|---|
| Main entry point | `main.py`, `scripts/demo.py`, `scripts/daily_pack.py`, `scripts/api_server.py` |
| 8 CrewAI agents | `virtuai/agents/*_agent.py` |
| Pipeline manager | `virtuai/pipelines/content_pipeline.py` |
| Cloud tools (KIE.ai) | `virtuai/tools/cloud_tools.py`, `kie_kling.py`, `kie_upload.py` |
| Composio publisher | `virtuai/tools/composio_tools.py`, `virtuai/agents/publisher_agent.py` |
| YouTube Direct | `virtuai/tools/youtube_direct.py` |
| IG carousel direct path | `virtuai/tools/ig_carousel.py` |
| Pydantic schemas | `virtuai/schemas/agent_outputs.py`, `validators.py` |
| n8n workflow | `n8n/virtuai_unified.json` (34 nodes, `active=true`) |
| Persona | `virtuai/persona/persona_anchor.json`, `canonical_daniel.png` |
| Locked baseline | `virtuai/locked/v1_2026-05-18/` (SHA-256 verified) |
| Tests | `virtuai/tests/test_*.py` (12 files, 140 tests) |
| Auth guard + healthcheck | `virtuai/tools/auth_guard.py`, `scripts/publisher_healthcheck.py` |
| Generated content | `virtuai/data/generated_videos/`, `generated_images/`, `content_packages/` |

## 5. Read in this order if you want the deep dive

1. **`README.md`** (project overview + quickstart)
2. **`docs/EVALUATION_METRICS.md`** (every number you'd put in a rubric)
3. **`docs/DEMO_PRESENTATION_SCRIPT.md`** (10-minute walkthrough, two plans)
4. **`docs/AGENT_UPGRADE_REPORT.md`** (what we changed in the agents and why)
5. **`docs/AGENT_COMMANDS.md`** (per-agent CLI commands)
6. **`docs/FINAL_PROJECT_COMPLETION_PLAN.md`** (status + remaining tasks honestly)
7. **`docs/N8N_AGENT_UPGRADE_NOTES.md`** (n8n's role + what it must NOT do)
8. **`docs/DEMO_READINESS_CHECKLIST.md`** (the day-of checklist)
9. **`docs/NETWORK_BLOCK_TROUBLESHOOTING.md`** + **`docs/KIE_CDN_DOWNLOAD_WORKAROUND.md`** (operational gotchas)
10. **`TECHNICAL_STATUS_REPORT.md`** (top-level — the full engineering audit from May 19)
11. **`CAPSTONE.md`** + **`CHALLENGES.md`** + **`PROJECT_STANDARDS.md`** + **`PUBLISHER_INTEGRATIONS.md`** (long-form project notes from earlier iterations)

## 6. Honest known issues (do not surprise the examiner)

### 6.1 Live platform status (verified 2026-05-21)

| Platform | Status | Reason |
|---|---|---|
| **YouTube Shorts** | ✅ LIVE | Refresh token in current GCP project, OAuth consent in "Production". Uploaded `tRhZVZQxbwo` 2026-05-21 as proof. |
| **Instagram** | ✅ LIVE | 7 / 7 posts shipped 2026-05-20 (see §3). |
| **Facebook** | ⚠ TEXT-ONLY | Text posts work (verified 2026-05-21). Media (photo + video) posts return Meta anti-abuse **code 368 / subcode 4854002** — Page Identity Verification required for newly created Pages. Platform-side gate; the integration code is correct and verified. |
| **LinkedIn** | ✗ PLATFORM-POLICY-BLOCKED | LinkedIn's anti-fake-account heuristic flagged the persona account and requires **government-ID verification** before granting third-party OAuth. Not a code bug; the platform itself refuses the persona use case. Integration code is in place and wrapped with the circuit breaker — will work as soon as the platform-side gate clears. |

This is framed deliberately as a **Guardian-style refusal at the auth layer** — see [`docs/AUTH_GUARD_REPORT_SECTION.md`](AUTH_GUARD_REPORT_SECTION.md) §5 for the academic framing. The same circuit-breaker pattern that protects against repeated 401/403 storms also prevents the Publisher Agent from compounding the platforms' suspicion scores after these refusals.

### 6.2 Operational notes

- **Local network may block KIE's CDN.** `tempfile.aiquickdraw.com` was IPS-filtered earlier; documented in `docs/NETWORK_BLOCK_TROUBLESHOOTING.md`. On a phone hotspot the demo runs end-to-end.
- **5-slide carousel needs `IG_ACCESS_TOKEN`** for true swipe; without it, we publish individual slides (which engage better on IG anyway).
- **Daily health check** (`scripts/publisher_healthcheck.py`) surfaces token expiry without ever publishing, so blocked platforms are visible at probe time instead of at publish time.

These are documented, not hidden. See `docs/EVALUATION_METRICS.md` §12.

## 7. What was NOT changed for the submission pass

- `virtuai/pipelines/content_pipeline.py` (the manager) — agent order remains `Research → Strategy → Creator → Visual → Reviewer → Guardian → Publisher → Analyzer`. The recommended Analyzer-first reorder is documented in `docs/AGENT_UPGRADE_REPORT.md` §17 but not applied.
- `n8n/virtuai_unified.json` (the workflow) — 34 nodes, `active=true`, untouched
- Composio integration code — untouched
- YouTube Direct integration code — untouched
- The locked baseline `virtuai/locked/v1_2026-05-18/` — checksums still verify

## 8. Submission checklist (consolidated)

- [x] All 140 tests pass (127 prior + 13 new for the agent-planner and reel-fallback suites)
- [x] 23 / 23 readiness checks pass
- [x] Locked baseline checksum verifies
- [x] Safe demo runs end-to-end (no publishing) — verified live 2026-05-20
- [x] Live publishing demonstrated — 7 IG posts on 2026-05-20, YT short on 2026-05-21
- [x] `.env.example` covers every key the project uses
- [x] No-publish gate enforced + 13 tests pin it
- [x] **Auth-guard + healthcheck shipped** — academic write-up in [`docs/AUTH_GUARD_REPORT_SECTION.md`](AUTH_GUARD_REPORT_SECTION.md)
- [x] Documentation complete (10 docs + 6 top-level)
- [x] Honest limitations documented, including platform-policy refusals for FB media + LinkedIn
- [ ] 60-second screencast recorded (manual step — `scripts/demo.py --no-publish` is the obvious take)
- [ ] PDF report addendum covering May 19-21 work (optional — this doc + `EVALUATION_METRICS.md` + `AUTH_GUARD_REPORT_SECTION.md` cover the same ground)

When the bottom two are ticked, submit.
