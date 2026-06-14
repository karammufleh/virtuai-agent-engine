# VirtuAI Evaluation Metrics

_Quantitative outcomes used to evaluate the VirtuAI capstone. Every number here is reproducible from the commands or files cited._

---

## 1. Test coverage

| Metric | Value | Reproduce |
|---|---|---|
| Unit + integration tests collected | **140** (as of 2026-06-14, with the agent-planner and reel-fallback suites) | `pytest --collect-only -q` |
| Tests passing | **140 / 140** | `pytest` |
| Wall-clock for full suite | ≈ 15 s | `pytest` |
| Live API calls in test suite | **0** | All Composio / KIE / YouTube paths are mocked |
| Test files | 11 | `ls virtuai/tests/test_*.py` |

Test files in scope:

- `test_agents_smoke.py` (9 tests) — every agent factory builds
- `test_schemas.py` (13 tests) — Pydantic output schemas accept happy + reject malformed
- `test_validators.py` (19 tests) — env-gated validation hook
- `test_publisher_safety.py` (8 tests) — Publisher refuses unsafe / dry-run / manual-approval paths
- `test_asset_download.py` (16 tests) — KIE CDN SSL workaround allowlist + deny-list scope
- `test_ig_caption.py` (10 tests) — IG caption shortener (essay → punchy)
- `test_variety_and_voice.py` (16 tests) — outfit/mood/setting pools + naturalness banned-cliché list
- `test_no_publish.py` (13 tests) — no-publish safety gate end-to-end
- `test_demo_polling.py` (6 tests) — demo polling recognises every terminal state (added 2026-05-20)
- `test_auth_guard.py` (17 tests) — audit-log circuit breaker for publisher auth (added 2026-05-21). See [AUTH_GUARD_REPORT_SECTION.md](AUTH_GUARD_REPORT_SECTION.md) for the full design write-up.

Evidence captures (raw command output) are in `docs/evidence/`:
- `pytest_output.txt` — full pytest run
- `pipeline_check.txt` — `--pipeline-check --offline`
- `validate_latest.txt` — `--validate-latest`
- `no_publish_tests.txt` — `pytest test_no_publish.py -v`
- `locked_baseline_verify.txt` — `shasum -a 256 -c manifest.sha256`

## 2. Pipeline readiness check

| Metric | Value | Reproduce |
|---|---|---|
| Pre-demo offline checks passing | **23 / 23** | `python scripts/agent_cli.py --pipeline-check --offline` |
| Wall-clock | ≈ 5 s | same |
| Coverage | 8 agent factories + 9 schemas + 3 data files + 4 platform configs + 4 KIE models + persona PNG + persona JSON + n8n workflow file + locked baseline manifest + 2 env keys + publisher safety gates + n8n notes + validation log dir |

## 3. Locked baseline integrity

| Metric | Value | Reproduce |
|---|---|---|
| Locked files | **11** | `ls virtuai/locked/v1_2026-05-18/` |
| SHA-256 manifest verifies | ✅ all OK | `cd virtuai/locked/v1_2026-05-18 && shasum -a 256 -c manifest.sha256` |

## 4. Pack generation — production cloud path (KIE.ai)

Numbers from `daily_pack_1779278497.json` (today's verified safe-demo run):

| Metric | Value | Notes |
|---|---|---|
| Wall-clock per full pack | **≈ 7 min** | 1 reel + 1 portrait + 5-slide carousel |
| Pieces generated | **1 reel + 1 portrait + 5 carousel slides** | Per pack |
| Reel mp4 size | 8.21 MB (master) / 8.46 MB (IG-encoded) | h.264 mp4 |
| Portrait PNG size | 1.30 MB | 1080 × 1350 |
| Carousel slide size | 1.29 - 1.46 MB each | 1080 × 1350 |
| Models used | Kling 3.0 multi-shot (reel) + Nano Banana 2 (images) + Suno (underbed) + Claude Sonnet 4.6 (text) | All via KIE.ai single gateway |
| Spend per pack (est.) | ≈ $5-7 in KIE credits | Kling Pro renders dominate |

## 5. Persona identity consistency

| Metric | Value | Reproduce |
|---|---|---|
| Face-identity threshold (ArcFace) | ≥ 0.70 | Enforced by Reviewer's `verify_face_identity` tool |
| Canonical face image size | 1192 KB (`virtuai/persona/canonical_daniel.png`) | n/a |
| Persona anchor JSON fields | identity / face / voice / vocabulary / forbidden_topics / safety_rules | `virtuai/persona/persona_anchor.json` |

## 6. Caption shortening (post-2026-05-20 fix)

| Metric | Before | After | Delta |
|---|---|---|---|
| IG caption length (typical carousel) | 2122 chars | **466 chars** | −78% |
| Captions kept long for LinkedIn | yes | yes (unchanged) | n/a |
| Caption tone | essay / verbose | hook + 1-2 short paragraphs + aphorism + 5 hashtags | n/a |

Reproduce on saved content:

```bash
python -c "
import json
from virtuai.tools.image_content_writer import write_image_caption
c = json.load(open('virtuai/data/generated_images/posts/pack_carousel_1779278074/content.json'))
print('IG  :', len(write_image_caption(c)['instagram']), 'chars')
print('LI  :', len(write_image_caption(c)['linkedin']), 'chars')
"
```

## 7. Variety pools (post-2026-05-20 upgrade)

| Pool | Before | After | Source |
|---|---|---|---|
| OUTFITS | 8 | **18** | `scripts/autopilot.py:OUTFITS` |
| MOODS | 8 | **14** | `scripts/autopilot.py:MOODS` |
| SETTING_POOLS | 5 (6 settings each) | **7** (6 settings each) | `scripts/autopilot.py:SETTING_POOLS` |
| Banned-cliché list | 12 phrases | **35 phrases** | `scripts/autopilot.py:_BANNED_CLICHES` + same in `image_content_writer.py` and `script_writer.py` |
| Recent-history look-back window | 5-6 runs | **8-10 runs** | `scripts/daily_pack.py:recent_outfits/recent_moods` |

## 8. Production publishing — verified-live 2026-05-20

7 Instagram posts published in one batch from the safe-demo pack, via `scripts/publish_pack.py --live`:

| # | Piece | IG ID | Composio result |
|---|---|---|---|
| 1 | Reel | `18099241174959159` | success (3 retries — IG ingestion delay) |
| 2 | Portrait | `17853516975671914` | success |
| 3 | Slide 1 (cover) | `17917609524364522` | success |
| 4 | Slide 2 (problem) | `17864460717567554` | success |
| 5 | Slide 3 (insight) | `18189068242327656` | success |
| 6 | Slide 4 (proof) | `18129748813587214` | success |
| 7 | Slide 5 (payoff) | `17857233120645162` | success |

Failure rate: **0 / 7**. Wall-clock for the 7-post batch: ≈ 6 min (30 s gap between posts).

## 9. Safety gates

| Gate | Coverage | Reproduce |
|---|---|---|
| No-publish flag honoured | `/run-pack` → `_run_pack` → `daily_pack.main(publish=False)` → no Composio call | `pytest virtuai/tests/test_no_publish.py -v` (13/13) |
| Publisher schema enforces legal status enum | `published / scheduled / failed / manual_approval_required / skipped / dry_run` only | `pytest virtuai/tests/test_publisher_safety.py -v` (8/8) |
| KIE CDN SSL workaround is allowlist-bounded | Only `tempfile.aiquickdraw.com`; `api.kie.ai` + every other API host always uses full SSL | `pytest virtuai/tests/test_asset_download.py -v` (16/16) |
| Schema validation logs failures, never crashes pipeline | Errors → `virtuai/data/logs/agent_validation_errors.jsonl` | `pytest virtuai/tests/test_validators.py -v` (19/19) |

## 10. Existing artifact corpus

Numbers from `virtuai/data/` as of 2026-05-20:

| Directory | Count | Note |
|---|---|---|
| `generated_videos/` (mp4 + mp3) | **216** files | Reels + intermediate Kling shots + Suno underbeds |
| `generated_images/posts/` (carousel + portrait runs) | **23** run dirs | Each has slides + content.json + captions.json |
| `content_packages/` (daily pack manifests) | **4** packs | Full publish records per pack |
| `autopilot_history.json` | 1 file with 12+ run entries | Each entry: ts, kind, topic, outfit, mood, results.{youtube, instagram_id, linkedin_urn} |

## 11. Cost-aware design

The n8n workflow at `n8n/virtuai_unified.json` runs **cheap text checks before any expensive render**:

```
Analyzer → Research → Strategy → Creator → Reviewer(text) → Guardian(text)
   ↓                                                              ↓
[history + lessons]                                  IF text PASS → Visual (≈ $4)
                                                     IF text FAIL → retry (≈ $0.10)
```

Each pre-render rejection costs **≈ $0.10 in Claude tokens** instead of **≈ $4 in Kling credits**.

## 12. Honest limitations (see also `docs/NETWORK_BLOCK_TROUBLESHOOTING.md`)

- KIE's temporary CDN (`tempfile.aiquickdraw.com`) is filterable by corporate IPS gateways. Workaround docs are present; the workaround can't bypass an actual network block.
- 5-slide IG carousels need `IG_ACCESS_TOKEN` (direct Graph API) — Composio's wrapper hardcodes `image_url` which conflicts with carousel parent semantics.
- YouTube refresh token expires every ~6 months; LinkedIn requires periodic reconnection in Composio dashboard.
- The variety pools are finite — over a 60-day run the same outfit/mood combo will reappear, by design.
- Schema validation is opt-in via `VIRTUAI_VALIDATE_AGENT_OUTPUTS` to keep the default pipeline crash-free.

## 13. Live platform reach (verified 2026-05-21)

| Platform | Status | Notes |
|---|---|---|
| YouTube Shorts | ✅ LIVE | `tRhZVZQxbwo` uploaded 2026-05-21 |
| Instagram | ✅ LIVE | 7 / 7 posts shipped 2026-05-20 (see §8) |
| Facebook | ⚠ TEXT-ONLY | Code 368 / 4854002 blocks media until Page identity verified |
| LinkedIn | ✗ PLATFORM-POLICY-BLOCKED | Government-ID verification gate on persona OAuth |

**Live platform reach: 2 / 4 fully live, 1 / 4 text-only, 1 / 4 platform-policy-blocked.** The two refusals are documented as Guardian-style refusals at the auth layer — see [`AUTH_GUARD_REPORT_SECTION.md`](AUTH_GUARD_REPORT_SECTION.md) §5. Integration code for all four is in place and circuit-breaker-wrapped.

## 14. Auth-guard + healthcheck (added 2026-05-21)

Full design write-up: [`AUTH_GUARD_REPORT_SECTION.md`](AUTH_GUARD_REPORT_SECTION.md).

| Metric | Value | Reproduce |
|---|---|---|
| Unit tests for circuit breaker | **17 / 17** | `pytest virtuai/tests/test_auth_guard.py -v` |
| Publish paths wrapped | 4 files (`youtube_direct.py`, `publish_v16.py`, `publish_images.py`, `ig_carousel.py`) | grep `auth_guard\.gate\|auth_guard\.record` |
| Health-probe wall-clock (all 4 platforms) | ≈ 9 s | `python scripts/publisher_healthcheck.py` |
| Audit-log entries per publish call | ≥ 1 | `tail virtuai/data/logs/auth_audit.jsonl` |
| Circuit trip threshold | **2** consecutive auth failures within 24 h | `VIRTUAI_AUTH_FAIL_LIMIT` env var |
| Non-auth failures (5xx, network) that count toward trip | **0** | classification rule in `virtuai/tools/auth_guard.py::classify_error` |

---

## Submission-ready summary

| Dimension | Status |
|---|---|
| Tests | **140 / 140 passing** |
| Pipeline readiness | **23 / 23** |
| Locked baseline | ✅ checksum-verified |
| Production pack generation | ✅ 7 min wall-clock |
| Production publishing | ✅ 7 IG posts + 1 YT Short shipped live |
| Auth-guard + healthcheck | ✅ 17 / 17 unit tests, all publish paths wrapped |
| Safety gates | ✅ no-publish + schema + SSL + validation gates all tested |
| Documentation | ✅ 10 docs in `docs/` + 6 top-level project docs |
| Locked baseline | ✅ unchanged since 2026-05-18 |
