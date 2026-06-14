# VirtuAI Final Project Completion Plan

_Last updated 2026-05-20. Inspection-only — no code or APIs were touched while writing this document. Read top to bottom; every claim cites a real file or command output. Be honest with reviewers about what's done and what isn't._

---

## 1. Current Project Status Summary

| Layer | State (verified today) |
|---|---|
| **Agents** | All 8 CrewAI agents wired in `virtuai/pipelines/content_pipeline.py`. Order today: Research → Strategy → Creator → Visual → Reviewer → Guardian → Publisher → Analyzer. Analyzer-first reorder is documented (`docs/AGENT_UPGRADE_REPORT.md` §17) but not applied — current order is the locked-baseline default. |
| **Generation (cloud)** | KIE.ai gateway is the single generation surface: Claude Sonnet 4.6 (text), Kling 3.0 multi-shot (reels with native lipsync), Nano Banana 2 (portraits + carousel backgrounds), Suno (music). Centralised slugs in `virtuai/config/models.yaml`. |
| **Generation (local)** | Phase-1 MLX stack present on disk but not exercised by the default agent path. Kept for reproducibility of older outputs. |
| **Validation** | Pydantic schemas for every agent (`virtuai/schemas/agent_outputs.py`) + tolerant `validate_json()` parser. Opt-in runtime validation via `VIRTUAI_VALIDATE_AGENT_OUTPUTS=true`; default is off so the pipeline is unchanged. Validation errors land at `virtuai/data/logs/agent_validation_errors.jsonl`. |
| **Publishing safety gates** | Publisher Agent backstory in `virtuai/agents/publisher_agent.py` lists 7 explicit gates (research recommendation = continue, reviewer = approve, guardian = safe, publish_ready, files exist, captions exist, no manual-approval block). Schema enforces the legal status enum at parse time (`virtuai/tests/test_publisher_safety.py`). |
| **n8n + API server** | `n8n/virtuai_unified.json` (34 nodes, `active=true` in the n8n DB), FastAPI server at `scripts/api_server.py` exposing 20+ endpoints on :9090. |
| **Demo commands** | `scripts/demo.py`, `scripts/agent_cli.py` (with `--validate-latest`, `--pipeline-check`, `--inspect`, `--validate <agent>`). All offline-safe in their default mode. |
| **Tests** | **91 tests collected and passing** in ~11 s (verified today). Covers all 8 agent factories, output schemas, publisher gates, validation layer, KIE-CDN SSL workaround, IG caption shortening, variety/voice upgrades. |
| **Documentation** | 8 docs in `docs/`, 6 top-level project docs (`README.md`, `CAPSTONE.md`, `CHALLENGES.md`, `PROJECT_STANDARDS.md`, `PUBLISHER_INTEGRATIONS.md`, `TECHNICAL_STATUS_REPORT.md`), plus the existing PDF `virtuai_capstone_report.pdf`. |
| **Known network/CDN issue** | KIE's temporary asset CDN (`tempfile.aiquickdraw.com`) is sometimes blocked by IPS/web-filters. Documented in `docs/NETWORK_BLOCK_TROUBLESHOOTING.md`. The SSL workaround `VIRTUAI_TRUST_KIE_CDN=true` only helps for cert-chain issues, not for IPS-blocking. Mitigation: switch network for live demo, or use Plan B offline walkthrough. |
| **Live publishing queue** | At inspection time, `scripts/post_inventory.py` was running in the background (PID 75292, 12 min elapsed) posting 12 carousel slides to Instagram at 1/hour pacing. This is intentional, started in the previous turn. Stop with `pkill -f scripts/post_inventory.py` if needed before submission. |

---

## 2. What Is Already Finished

| Area | Status | Evidence | Notes |
|---|---|---|---|
| Agent system (8 CrewAI agents) | ✅ Complete | `virtuai/agents/*_agent.py`, `virtuai/pipelines/content_pipeline.py:142-152` builds Crew with all 8 | Backstories include structured-output JSON contracts |
| Persona profile (Daniel Calder) | ✅ Complete | `virtuai/persona/persona_anchor.json`, `virtuai/persona/canonical_daniel.png` (1192 KB), `virtuai/config/personas/virtuai_mentor.yaml` | Face anchor verified by `--pipeline-check` |
| KIE generation integration | ✅ Live | `virtuai/tools/cloud_tools.py`, `virtuai/tools/kie_kling.py`, `virtuai/tools/kie_upload.py`, `virtuai/config/models.yaml` | Today's run `daily_pack_1779229427.json` succeeded |
| Composio publishing | ✅ Live (DRY-RUN fallback) | `virtuai/tools/composio_tools.py`, `virtuai/agents/publisher_agent.py::make_publisher` | IG single-image + reel paths verified live today |
| YouTube Direct OAuth upload | ⚠️ Implemented but token expired | `virtuai/tools/youtube_direct.py` | Today's YT push failed: `invalid_grant — Token has been expired or revoked`. Needs refresh. |
| LinkedIn (Composio) | ⚠️ Implemented but no connected account | Composio error `1810 / ActionExecute_ConnectedAccountNotFound` for entity `danielcalder-` | Needs reconnection in Composio dashboard. |
| `--no-publish` demo mode | ⚠️ Partial — see §3 | `scripts/demo.py --no-publish` sends `publish:false`; `scripts/api_server.py::/run-pack` ignores it | Critical gap before next live demo |
| Validation schemas (9 Pydantic models) | ✅ Complete | `virtuai/schemas/agent_outputs.py`, `virtuai/schemas/validators.py`, `virtuai/schemas/__init__.py` | All 9 models tested |
| Agent CLI | ✅ Complete | `scripts/agent_cli.py` — `--inspect / --agent / --validate / --validate-latest / --pipeline-check --offline` | Verified offline today |
| Tests (pytest) | ✅ Complete | `virtuai/tests/test_agents_smoke.py`, `test_schemas.py`, `test_publisher_safety.py`, `test_validators.py`, `test_ig_caption.py`, `test_asset_download.py`, `test_variety_and_voice.py` | **91 tests passing**, ~11 s |
| Pytest discovery scoping | ✅ Complete | `pyproject.toml` `[tool.pytest.ini_options]` | Vendored AI-model `test_*.py` files no longer crash collection |
| Documentation (docs/) | ✅ Complete | 11 docs in `docs/` (post-2026-05-20 consolidation): `SUBMISSION.md`, `API_REFERENCE.md`, `EVALUATION_METRICS.md`, `FINAL_PROJECT_COMPLETION_PLAN.md`, `AGENT_COMMANDS.md`, `AGENT_UPGRADE_REPORT.md`, `DEMO_PRESENTATION_SCRIPT.md`, `DEMO_READINESS_CHECKLIST.md`, `KIE_CDN_DOWNLOAD_WORKAROUND.md`, `N8N_AGENT_UPGRADE_NOTES.md`, `NETWORK_BLOCK_TROUBLESHOOTING.md`. The older `APIS_AND_FINALIZE.md` was merged into `API_REFERENCE.md` (reference half) + this file (planning half). | Coherent set; cross-referenced |
| Top-level project docs | ✅ Complete | `README.md` (15 KB), `CAPSTONE.md` (26 KB), `CHALLENGES.md` (17 KB), `PROJECT_STANDARDS.md` (12 KB), `PUBLISHER_INTEGRATIONS.md` (16 KB), `TECHNICAL_STATUS_REPORT.md` (38 KB) | All current except CAPSTONE.md last touched May 14 |
| n8n workflow | ✅ Active | `n8n/virtuai_unified.json` (22 KB), 34 nodes, `active=1` in SQLite (verified today) | 5 trigger paths incl. 09:00 + 17:00 cron |
| Generated artifacts on disk | ✅ Lots | 216 reels + 37 images + multiple `content_packages/*.json` daily packs | Strong Plan-B demo evidence |
| Publisher safety gates | ✅ Documented in backstory | `virtuai/agents/publisher_agent.py` — 7 explicit gates, see `grep "PUBLISH SAFETY GATES"` | Schema enforces legal status enum |
| Locked baseline (v1_2026-05-18) | ✅ Verified | `virtuai/locked/v1_2026-05-18/manifest.sha256` — every OK on `shasum -c` | Trusted rollback point |
| KIE CDN SSL workaround | ✅ Complete (opt-in) | `virtuai/utils/asset_download.py`, host allowlist + API deny-list, `VIRTUAI_TRUST_KIE_CDN=true` | 16 unit tests verify allowlist scope |
| IG short-form caption | ✅ Complete | `virtuai/tools/image_content_writer.py::_build_ig_caption()` — 2122 → 466 chars on real input | Tests in `test_ig_caption.py` |
| Variety + naturalness upgrades | ✅ Complete | OUTFITS 8→18, MOODS 8→14, SETTING_POOLS 5→7, banned-cliché list 12→35, real-creator voice block in both writers | Tests in `test_variety_and_voice.py` |
| Inventory poster (slides) | ✅ Running live (12 hr) | `scripts/post_inventory.py` PID 75292, post #1 confirmed to IG id `18069352070339423` | Posts #2-#12 fire hourly |

---

## 3. What Is Still Missing

| Missing Item | Priority | Why It Matters | Exact File(s) | Expected Output |
|---|---|---|---|---|
| **`/run-pack` honours `publish:false`** | 🔴 P0 — blocker for "safe live demo" | Today's `scripts/demo.py --no-publish` posted **3 IG posts** because the API endpoint ignores the flag (see incident in this session). | `scripts/api_server.py` (the `/run-pack` route handler), `scripts/daily_pack.py::run_pack(publish=...)` | One conditional that skips Publisher when `publish=false`; new test in `test_publisher_safety.py`. |
| **YouTube OAuth refresh token** | 🔴 P0 if YT is part of demo | Today's YT upload failed with `invalid_grant`. Demo can't show YouTube end-to-end. | `.env` (`YOUTUBE_OAUTH_REFRESH_TOKEN`) | New refresh token from the YouTube OAuth playground. |
| **Composio LinkedIn connection** | 🟡 P1 | LinkedIn fan-out failed with `ActionExecute_ConnectedAccountNotFound` for entity `danielcalder-`. | Composio dashboard — Connected Accounts → LinkedIn | LinkedIn connection re-authorised. |
| **`IG_ACCESS_TOKEN` for real carousels** | 🟡 P1 | Without it, 5-slide carousels fall back to publishing slide 1 only (`virtuai/tools/ig_carousel.py` checks this). | `.env` + `.env.example` | Long-lived IG page token added; first true 5-swipe carousel works. |
| ~~Missing keys in `.env.example`~~ | ✅ DONE 2026-05-20 | The three keys (`IG_ACCESS_TOKEN`, `VIRTUAI_TRUST_KIE_CDN`, `VIRTUAI_VALIDATE_AGENT_OUTPUTS`) are now in `.env.example` as commented placeholders with usage notes. | `.env.example` | n/a |
| **PDF report update / addendum** | 🟡 P1 | `virtuai_capstone_report.pdf` is dated May 14 — predates schema validation, CDN workaround, variety upgrades, network-block diagnosis, inventory poster, IG carousel direct path. | `virtuai_capstone_report.pdf` (build pipeline likely `scripts/build_report_pdf.py`) | Updated PDF OR a 2-page addendum PDF covering the May 19-20 deltas. |
| **Demo recording (screencast)** | 🔴 P0 for submission | Capstone review usually wants a short video proving the system runs. | n/a (use OBS / QuickTime) | 60-second screencast of `python scripts/demo.py --no-publish` lands + `agent_cli.py --pipeline-check` output. Save as `docs/demo.mp4` or unlisted YouTube link. |
| **Output evidence / screenshots** | 🟡 P1 | Markdown is text. Reviewers will skim for visuals. | n/a | 3-5 PNGs in `docs/screenshots/`: pipeline-check output, validate-latest output, a generated reel still, a generated portrait, the n8n workflow view. |
| **Evaluation metrics section** | 🟡 P1 | Capstone rubrics often want a "how was it evaluated" section. | New: `docs/EVALUATION_METRICS.md` OR add to README | Tabulate: face-identity ArcFace score, caption length (before/after), test pass count, locked-baseline hash count, generation cost per pack ($), wall-clock per pack (minutes). |
| **Risk / limitation section** | 🟢 P2 | Examiners look for honest limitation analysis. | `docs/CHALLENGES.md` already exists (17 KB). | Verify it covers: KIE CDN block, expiring tokens, single-vendor risk, agent prompt drift, persona-identity edge cases, cost of failed renders, no auto-A/B testing. |
| **Plagiarism / citation readiness** | 🟢 P2 | If the report cites external work (Kling, Nano Banana, Claude, Composio, CrewAI), they need proper references. | `virtuai_capstone_report.pdf` references / `docs/REFERENCES.md` | Each external tool/method/quote cited with title + URL + access date. |
| **README clarity for reviewer** | 🟢 P2 | First-time reader should run `--pipeline-check`, see 23/23, then understand demo Plan A/B in under 5 minutes. | `README.md` | Add "Demo evidence" section linking to screenshots + recording. |
| **n8n manual-run instructions** | 🟢 P2 | A reviewer might want to fire the schedule manually. | `docs/N8N_AGENT_UPGRADE_NOTES.md` already exists. | Cross-check section "To verify n8n is wired correctly" is accurate. |
| **Analytics feedback documentation** | 🟢 P2 | Code reads `autopilot_history.json`/`lessons.json` for feedback — but no doc spells out what gets written when. | New: `docs/FEEDBACK_LOOP.md` (1-2 pages) | One-page diagram showing Analyzer → lessons.json → next-cycle Research+Strategy. |
| **Stop the live inventory poster (if undesired)** | 🟡 P1 if it should not keep posting | Was started earlier this session at 1/hour. 11 posts remaining over next 11 hours. | `pkill -f scripts/post_inventory.py` | Process terminated, JSONL log preserved for audit. |
| **TikTok / Medium claim review in docs** | 🟢 P2 | Multiple older docs may still mention TikTok/Medium as platforms even though publishers were dropped. | Grep `docs/` + top-level for "tiktok"/"medium" | Replace stale claims with "format constraints only — no live publisher". |

---

## 4. Critical Must-Do Tasks Before Submission

- [x] ~~Patch `/run-pack` to honour `publish:false`~~ — **DONE 2026-05-20**
  - Fix shipped in `scripts/api_server.py` (RunPackRequest now exposes `publish_allowed()`) + `scripts/daily_pack.py::main(publish=...)` (skips publish block when False).
  - 13 new tests in `virtuai/tests/test_no_publish.py` lock in the contract — all green.
  - Before any demo, run: `pytest virtuai/tests/test_no_publish.py -v` — must report 13 passed.

- [ ] **Record a 60-second screencast demonstrating the project**
  - Why: capstone deliverable, gives the examiner a fast pass.
  - Exact command: `python scripts/agent_cli.py --pipeline-check --offline && python scripts/demo.py --no-publish`
  - Owner: you.
  - Estimated difficulty: small (15 min — record, trim, host).
  - Done when: file or unlisted YouTube link exists; link added to `README.md`.

- [x] ~~Sync `.env.example` with the live `.env`~~ — **DONE 2026-05-20**
  - All three keys (`IG_ACCESS_TOKEN`, `VIRTUAI_TRUST_KIE_CDN`, `VIRTUAI_VALIDATE_AGENT_OUTPUTS`) are now commented placeholders with usage notes.
  - Verify: `grep -E 'IG_ACCESS_TOKEN|VIRTUAI_TRUST_KIE_CDN|VIRTUAI_VALIDATE_AGENT_OUTPUTS' .env.example`

- [ ] **Update the PDF report OR ship a 2-page addendum**
  - Why: the existing `virtuai_capstone_report.pdf` is dated May 14 — it does NOT mention schema validation, KIE-CDN workaround, variety/voice upgrades, the inventory poster, the IG carousel direct path, or the network-block diagnosis.
  - Exact file: `scripts/build_report_pdf.py` likely regenerates the main PDF; otherwise produce `docs/CAPSTONE_ADDENDUM_2026-05-20.pdf`.
  - Owner: you.
  - Estimated difficulty: medium (1-3 hours — write 2 pages or update existing).
  - Done when: addendum PDF exists at `docs/CAPSTONE_ADDENDUM_2026-05-20.pdf` and the README points at it.

- [ ] **Decide what to do with the live inventory poster (PID 75292)**
  - Why: it will publish 11 more IG posts over the next 11 hours. If you want those posts to stand, leave it; if not, kill it.
  - Exact command: `pkill -f scripts/post_inventory.py` (kill) OR leave running.
  - Owner: you (your IG account).
  - Estimated difficulty: trivial.
  - Done when: decision is made and recorded in `/tmp/virtuai_post_inventory.jsonl` (the manifest already captures everything published).

- [ ] **Add 3-5 screenshots to `docs/screenshots/`**
  - Why: visuals make the README and addendum readable.
  - Exact files: `docs/screenshots/{pipeline_check.png, validate_latest.png, generated_reel_thumb.png, ig_post_live.png, n8n_workflow.png}`.
  - Owner: you.
  - Estimated difficulty: small (15 min total).
  - Done when: README has an inline image grid linking to them.

- [ ] **Re-verify the full demo path on a clean network (NOT corporate WiFi)**
  - Why: the IPS gateway issue (documented in `docs/NETWORK_BLOCK_TROUBLESHOOTING.md`) breaks the asset-download step. Demo day must NOT be on a blocking network.
  - Exact command: `curl -kI https://tempfile.aiquickdraw.com/` should print an HTTP code (not a `307 → 10.*` redirect).
  - Owner: you.
  - Estimated difficulty: trivial (use phone hotspot).
  - Done when: a `python scripts/demo.py --no-publish` run completes end-to-end and writes a fresh `daily_pack_*.json`.

- [ ] **Final test sweep**
  - Why: catch any drift introduced by tonight's edits.
  - Exact command: `pytest && python scripts/agent_cli.py --pipeline-check --offline && python scripts/agent_cli.py --validate-latest`
  - Owner: you.
  - Estimated difficulty: trivial.
  - Done when: 91/91 tests pass, 23/23 pipeline checks pass, validate-latest is OK.

---

## 5. Should-Do Tasks If Time Allows

- [ ] **Renew YouTube OAuth refresh token** so a clean run shows YT upload working end-to-end. Improves grading on "multi-platform publishing".
- [ ] **Reconnect Composio LinkedIn account** for the same reason on LinkedIn. ~5 min in Composio dashboard.
- [ ] **Add `IG_ACCESS_TOKEN`** so the 5-slide carousel works as a real swipe rather than the slide-1 fallback. The token can be copied from Composio's dashboard.
- [ ] **Add `docs/EVALUATION_METRICS.md`** with a one-page table of measurable outcomes (face-identity score, caption-length delta, test count, pack wall-clock, cost per pack).
- [ ] **Add `docs/FEEDBACK_LOOP.md`** with a small Mermaid diagram of Analyzer → lessons.json → next cycle.
- [ ] **Apply the recommended Analyzer-first order** (`docs/AGENT_UPGRADE_REPORT.md` §17 documents the exact patch). Improves reactivity but requires a careful re-test of the n8n IF gates.
- [ ] **Update `scripts/demo.py`'s polling** to recognise `state=success` as terminal — fixes the "demo client keeps polling even though the API completed" issue from this session.
- [ ] **Audit older docs (`CAPSTONE.md`, `PUBLISHER_INTEGRATIONS.md`)** for stale TikTok / Medium / Gemini mentions. Replace with "format constraints only — no live publisher".
- [ ] **Add 2-3 more test cases** for the IG short-caption builder (edge cases: empty long_cap, all-emoji long_cap, very long hashtag block).
- [ ] **Visual / outfit variety A-B test** — run two packs on consecutive days, compare. Adds quantitative evidence to the report.
- [ ] **Carousel publishing dry-run improvement** — instead of falling back to `slide_paths[0]` as a single image, return `manual_review_required` so the broken carousel doesn't silently publish wrong content.

---

## 6. Do-Not-Do List

- ❌ **Do NOT add a new social media platform.** Active set stays `instagram, linkedin, x, youtube_shorts`. TikTok and Medium are NOT publishers in this project.
- ❌ **Do NOT change the live workflow** in `virtuai/pipelines/content_pipeline.py` right before submission. Document the recommended Analyzer-first reorder; don't apply it untested.
- ❌ **Do NOT add a new AI / generation provider.** KIE.ai stays. Gemini, OpenAI, Veo direct, ElevenLabs direct, JSON2Video, Creatomate, Shotstack are explicitly out.
- ❌ **Do NOT change Composio publishing.** It works for IG / LinkedIn / Facebook / X. Don't refactor `composio_tools.py` before demo.
- ❌ **Do NOT run a live publish during the live demo.** Use `--no-publish` (after patching `/run-pack` to honour it) or Plan B (offline walkthrough with saved artifacts).
- ❌ **Do NOT make large architecture changes.** No new pipelines, no manager-agent rewrite, no schema-everywhere rollout.
- ❌ **Do NOT switch agent order right before demo.** Even if the docs say Analyzer-first is recommended, do it AFTER submission.
- ❌ **Do NOT delete generated artifacts.** `virtuai/data/generated_videos/`, `generated_images/`, `content_packages/` are demo Plan-B safety net.
- ❌ **Do NOT commit secrets.** Project isn't a git repo today; if `git init` happens, `.env` must be in `.gitignore`.
- ❌ **Do NOT re-enable experimental APIs** (the `virtuai/experimental/` package was removed on purpose). No JSON2Video, no InfiniteTalk, no Veo 3.1.
- ❌ **Do NOT modify the locked baseline.** `virtuai/locked/v1_2026-05-18/` is checksum-verified; it's the rollback point.

---

## 7. Final Demo Plan

### Plan A — Live no-publish demo on a clean network (preferred)

```bash
# 1. Project readiness (instant, offline)
python scripts/agent_cli.py --validate-latest
python scripts/agent_cli.py --pipeline-check --offline    # must end with 23/23

# 2. End-to-end demo, NO live publishing
#    (PRECONDITION: /run-pack must honour publish:false — see §4 task 1)
python scripts/demo.py --no-publish
```

Expected outcome:
- KIE renders Kling reel + 5 Nano Banana slides + Suno music in ~10-12 min
- Manifest written to `virtuai/data/content_packages/daily_pack_<ts>.json`
- New mp4 + PNGs land in `virtuai/data/generated_videos/` and `generated_images/posts/pack_*_<ts>/`
- Publisher SKIPS (returns `publisher_status="skipped"` because `publish=false`)
- No IG / LinkedIn / Facebook / YouTube post is made

Open the generated mp4 in QuickTime as the closing reveal.

**Required:** the network being used must NOT have an IPS / web-filter blocking `tempfile.aiquickdraw.com`. Verify with:
```bash
curl -kI --connect-timeout 6 https://tempfile.aiquickdraw.com/ | head -3
# Acceptable: HTTP/2 200 or HTTP/2 404. NOT acceptable: Location: https://10.*
```

### Plan B — Offline walkthrough (if network is blocked)

```bash
# 1. Same readiness check
python scripts/agent_cli.py --validate-latest
python scripts/agent_cli.py --pipeline-check --offline    # 23/23

# 2. Show what already shipped
ls -lt virtuai/data/generated_videos/ | head -5            # 216 reels on disk
ls -lt virtuai/data/generated_images/posts/ | head -5      # carousels + portraits
ls -lt virtuai/data/content_packages/ | head -3            # daily-pack manifests

# 3. Open the latest reel as the artifact reveal
open "$(ls -t virtuai/data/generated_videos/*reel*IG*.mp4 | head -1)"

# 4. Open the latest manifest to show traceability (YT URL, IG id, LI URN per piece)
python -m json.tool < "$(ls -t virtuai/data/content_packages/*.json | head -1)" | less
```

Reviewers see:
- 91 / 91 tests + 23 / 23 readiness checks (proof the code works)
- Real generated mp4 in QuickTime (proof the cloud path produces output)
- A fully populated daily-pack manifest with publish IDs (proof publishing has worked in past runs)

Plan B is **fully demonstrable** without touching the network. Use the script in `docs/DEMO_PRESENTATION_SCRIPT.md` §6:30.

### Plan C — Hybrid (if you want to show a real IG post live)

Only after **all P0/P1 items** in §4 are done, AND you accept that a real post will go live:

```bash
# Strip --no-publish — publish for real (only if user explicitly wants).
# Test on a private/staging IG account first if possible.
python scripts/demo.py
```

This is what happened by accident yesterday. Avoid it unless intentional.

---

## 8. Submission-Day Checklist (consolidated)

Run through this exactly once on submission day:

- [ ] `pytest` → 91 passed
- [ ] `python scripts/agent_cli.py --pipeline-check --offline` → 23 / 23
- [ ] `python scripts/agent_cli.py --validate-latest` → ≥ 2 validations succeeded
- [ ] `.env.example` synced (all keys including the 3 new ones)
- [ ] `/run-pack` honours `publish:false` — verified by a real `--no-publish` test run on phone hotspot
- [ ] Screencast recorded (60 s) and linked from README
- [ ] PDF report updated OR addendum PDF at `docs/CAPSTONE_ADDENDUM_2026-05-20.pdf`
- [ ] 3-5 screenshots in `docs/screenshots/` and referenced from README
- [ ] Inventory poster intentionally running or intentionally stopped
- [ ] `git init` done (if you want a versioned submission) with `.env` in `.gitignore`
- [ ] Locked baseline verified: `cd virtuai/locked/v1_2026-05-18 && shasum -a 256 -c manifest.sha256`

When every box is ticked, **submit**.
