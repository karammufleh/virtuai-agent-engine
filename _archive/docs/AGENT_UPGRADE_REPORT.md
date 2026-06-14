# VirtuAI Agent Upgrade Report

_Last updated 2026-05-19. Documents an additive upgrade pass over the existing CrewAI agents. No live workflow logic was replaced; no new platforms or APIs were added._

---

## 1. Existing workflow file detected

`virtuai/pipelines/content_pipeline.py` (423 lines, `Process.sequential`). It is the manager/control layer and was **not modified** in this pass.

The HTTP plane (`scripts/api_server.py`, 20+ endpoints on :9090) and the n8n workflow (`n8n/virtuai_unified.json`, 34 nodes, `active=true`) were also untouched.

## 2. Existing agents found

All 8 agents already existed in `virtuai/agents/`:

| Agent | File | Role |
|---|---|---|
| Analyzer | `analyzer_agent.py` | Performance verdict reader |
| Research | `research_agent.py` | Trend Research Specialist (5-step viral funnel) |
| Strategy | `strategy_agent.py` | Content Scheduling Director |
| Creator | `creator_agent.py` | Content Creator (writes scripts / portraits / carousels) |
| Visual | `visual_agent.py` | Visual Content Designer (Kling + Nano Banana 2) |
| Reviewer | `reviewer_agent.py` | Content Quality Reviewer (text + video) |
| Guardian | `guardian_agent.py` | Ethics and Safety Guardian |
| Publisher | `publisher_agent.py` (`make_publisher`) | Composio + YouTube-direct publisher |

## 3. Role mapping confirmed

- Creator = writer / script / caption / prompt agent
- Visual = image + video + voice + editing (KIE-cloud only after the 2026-05-18 trim)
- Reviewer = technical quality
- Guardian = safety / ethics / policy
- Publisher = Composio (IG/LinkedIn/X/Facebook) + YouTube Direct OAuth
- Analyzer = previous-performance reader
- Research = niche trend detector
- Strategy = platform / format / timing / angle

No agents were renamed. No duplicate agents were created.

## 4. Files changed

| Path | Change |
|---|---|
| `virtuai/agents/analyzer_agent.py` | Backstory extended with the structured-output JSON spec |
| `virtuai/agents/research_agent.py` | Added 7-dimensional trend scoring spec + explicit skip rules |
| `virtuai/agents/strategy_agent.py` | Added platform/timing/angle structured-output spec + "n8n does not pick platform" reminder |
| `virtuai/agents/creator_agent.py` | Added consolidated CreatorOutput spec (script + prompts + scene_plan + platform_versions) |
| `virtuai/agents/visual_agent.py` | Added post-render checks + structured-output spec |
| `virtuai/agents/reviewer_agent.py` | Added 11-dimensional scores + `revise_agent` routing field |
| `virtuai/agents/guardian_agent.py` | Added per-platform-risk schema + post-render-review duty |
| `virtuai/agents/publisher_agent.py` | Added 7 explicit publish safety gates + manual-approval rule + dry-run mode |
| `virtuai/schemas/__init__.py` (new) | Public exports for the 9 Pydantic models + `validate_json` helper |
| `virtuai/schemas/agent_outputs.py` (new) | Pydantic models for every agent output + tolerant `validate_json` parser |
| `scripts/agent_cli.py` (new) | Per-agent test runner with `--offline` mode |
| `virtuai/tests/test_schemas.py` (new) | 13 schema validation tests |
| `virtuai/tests/test_publisher_safety.py` (new) | 8 safety-gate tests |
| `docs/AGENT_UPGRADE_REPORT.md` (this file) | New |
| `docs/AGENT_COMMANDS.md` (new) | All test commands per agent |
| `docs/N8N_AGENT_UPGRADE_NOTES.md` (new) | What n8n decides vs what agents decide |

## 5. Prompts improved

Each agent's backstory now ends with an explicit STRUCTURED OUTPUT block that names every field, its type, and the allowed values for enum-like fields. The wording is deterministic — the agent reads the schema verbatim and emits matching JSON.

Key additions:

- **Analyzer** now distinguishes `historical_data_available: false` (cold-start) from a real `negative` verdict, and lists `"collect analytics"` in `recommendations_for_next_post` when metrics are missing.
- **Research** now has explicit SKIP RULES: freshness < 4 OR niche_relevance < 4 OR risk > 7 → emit `recommendation: "skip"`. The pipeline manager can short-circuit on this.
- **Strategy** explicitly forbids platforms outside `{instagram, linkedin, x, youtube_shorts}` and tells the agent that n8n does not pick platform/timing.
- **Creator** outputs a unified content package (`main_hook`, `script`, `voiceover_script`, `caption`, `hashtags`, `cta`, `image_prompt`, `video_prompt`, `negative_prompt`, `scene_plan`, `platform_versions`) so Visual + Reviewer + Guardian see the same JSON.
- **Visual** now flags `problems[]` when the rendered asset is missing, 0 bytes, or off-aspect — Reviewer reads this directly.
- **Reviewer** scores 11 dimensions 0-10 and names `revise_agent` (creator / visual / strategy / research / none) so the manager knows where to send revisions.
- **Guardian** scores per-platform risk and gets an explicit POST-RENDER REVIEW duty: inspect the final asset, not just the script.
- **Publisher** has 7 hard gates and three explicit refusal statuses: `skipped`, `manual_approval_required`, `dry_run`.

## 6. Commands added

A single `scripts/agent_cli.py` covers every per-agent operation. See [`docs/AGENT_COMMANDS.md`](AGENT_COMMANDS.md) for the full list. Highlights:

```bash
python scripts/agent_cli.py --inspect                                # show persona + platforms + models
python scripts/agent_cli.py --agent analyzer --offline               # build agent w/o API
python scripts/agent_cli.py --agent research --task "..."            # run with live KIE
python scripts/agent_cli.py --validate reviewer --input <path.json>  # validate a saved JSON
```

The existing `scripts/demo.py`, `scripts/daily_pack.py`, `scripts/autopilot.py`, `scripts/api_server.py` are unchanged.

## 7. Decision logic improved

The schema layer makes routing decisions deterministic. Downstream code can now do:

```python
from virtuai.schemas import ResearchOutput, validate_json
parsed, err = validate_json(ResearchOutput, raw_text)
if parsed and parsed.should_skip:
    # short-circuit the cycle
```

The `should_skip` property bundles the four skip-rules (recommendation == "skip" OR freshness < 4 OR niche_relevance < 4 OR risk > 7) into one boolean — n8n's IF nodes can read it without re-implementing the logic.

## 8. Recommended target order

```
Analyzer → Research → Strategy → Creator → Visual → Reviewer → Guardian → Publisher
```

## 9. Was the pipeline order changed?

**No — only documented.** The live order in `virtuai/pipelines/content_pipeline.py` is:

```
Research → Strategy → Creator → Visual → Reviewer → Guardian → Publisher → Analyzer
```

The Analyzer is currently at the END (feedback-loop position — reads the last-published post's metrics, writes to `lessons.json`). The next cycle's Research and Strategy read those lessons.

Moving Analyzer to the FRONT would require:
1. Editing the `Task(...)` list in `content_pipeline.py`.
2. Ensuring Analyzer's verdict is passed as context to Research (CrewAI `context=[task_analyze]`).
3. Verifying the n8n IF gates still route correctly on `Last post: positive?`.

That edit is reasonable but non-trivial. **It is documented here as a future change**; the live pipeline keeps working as-is.

## 10. Publisher safety gates improved

The Publisher's backstory now lists 7 hard gates. The PublisherOutput schema enforces only the legal statuses:

```
"published" | "scheduled" | "failed" | "manual_approval_required" | "skipped" | "dry_run"
```

A status like `"published_secretly"` is rejected at parse time. See `virtuai/tests/test_publisher_safety.py`.

## 11. What was NOT changed

- `virtuai/pipelines/content_pipeline.py` — manager logic preserved
- `n8n/virtuai_unified.json` — 34 nodes, `active=true`, unchanged
- `scripts/api_server.py` — endpoints preserved
- Any tool's signature or `tools=[...]` list on any agent
- Composio integration (`virtuai/tools/composio_tools.py`)
- YouTube direct OAuth (`virtuai/tools/youtube_direct.py`)
- KIE.ai integration (`virtuai/tools/cloud_tools.py`, `kie_kling.py`)
- Persona system (`virtuai/persona/`)
- The locked baseline (`virtuai/locked/v1_2026-05-18/` — SHA-256 still verifies)

## 12. Confirmation: no new social media platforms added

Active platforms remain exactly:
- Instagram
- LinkedIn
- X / Twitter
- YouTube Shorts

TikTok and Medium were removed in an earlier round (2026-05-19) and were not re-added. Facebook is reachable through Composio as an existing cross-post target — not a new platform.

## 13. Confirmation: existing API integrations preserved

- **KIE.ai** — used as before via `cloud_tools.py` + `kie_kling.py`. Slugs in `virtuai/config/models.yaml`.
- **Composio** — preserved (LIVE mode when `COMPOSIO_API_KEY` set, DRY-RUN otherwise).
- **YouTube Direct OAuth** — preserved via `youtube_direct.py`.
- **X / Twitter Tweepy direct** — preserved in `virtuai/publishers/x_publisher.py`.

No new AI providers added. No new gateways.

## 14. Risks

- **`composio_tools.py` caches `COMPOSIO_API_KEY` at module-import time.** Popping the env var at runtime doesn't switch the live → dry-run path. This is a pre-existing limitation, not introduced here. The `test_publisher_factory_builds` test in `virtuai/tests/test_publisher_safety.py` documents it.
- **Schema validation is opt-in.** The Pydantic models exist but the running CrewAI pipeline does not call `validate_json` automatically. To enforce schemas at runtime, wrap each `task.execute()` (or the FastAPI response) in `validate_json`. That change is reserved for the next pass to avoid touching the live pipeline.
- **Analyzer-first reordering is documented but not applied.** If the lessons-feedback loop ever needs to be tighter (Analyzer steering the SAME cycle's Research instead of the next one), this requires the Task-list edit described in §9.
- **Composio DRY-RUN path needs verification** when `COMPOSIO_API_KEY` is unset — see the test note above.

## 15. Testing commands

```bash
# Run all 49 tests
.venv/bin/python -m pytest virtuai/tests/ -v

# Schema tests only
.venv/bin/python -m pytest virtuai/tests/test_schemas.py -v

# Safety-gate tests only
.venv/bin/python -m pytest virtuai/tests/test_publisher_safety.py -v

# Validator tests only (NEW)
.venv/bin/python -m pytest virtuai/tests/test_validators.py -v

# Inspect the project state without touching anything
python scripts/agent_cli.py --inspect

# Pre-demo readiness check (NEW)
python scripts/agent_cli.py --pipeline-check --offline

# Validate the latest saved content package (NEW)
python scripts/agent_cli.py --validate-latest

# Build any agent without an API call
python scripts/agent_cli.py --agent <name> --offline
```

All 49 tests pass in ≈ 11 s. None hit a live API.

---

## 16. Optional runtime schema validation (NEW — 2026-05-19 pass 2)

Schema validation is now **available** at runtime, opt-in via env var:

```bash
# Default — preserves current behavior (no validation overhead)
unset VIRTUAI_VALIDATE_AGENT_OUTPUTS

# Opt-in — every call to validate_and_log runs validation
export VIRTUAI_VALIDATE_AGENT_OUTPUTS=true
```

When **on**, the helper:
1. Validates each agent output against its Pydantic model.
2. Logs failures to `virtuai/data/logs/agent_validation_errors.jsonl`
   (truncates the raw output to 2 KB so the log doesn't balloon).
3. **Never crashes the pipeline.** Returns `(parsed_or_none, ok_bool)`
   so callers can decide whether to retry, branch, or ignore.
4. Treats unknown agent names as a soft pass (logs at INFO level only).

When **off** (default), `validate_and_log` is a near-no-op — returns
`(None, True)` immediately so the running pipeline keeps moving.

### Where the hook lives

- `virtuai/schemas/validators.py` — single source of truth.
- Public surface: `is_validation_enabled()`, `validate_agent_output(agent, raw)`,
  `validate_and_log(agent, raw, *, critical=False)`, `log_validation_error(...)`,
  `recent_errors(limit=10)`, `ERROR_LOG_PATH`.
- Re-exported from `virtuai.schemas.__init__` for easy import.

### Why it's not auto-wired into `content_pipeline.py` yet

The validator helpers are available, but they are **not** invoked
automatically from the live `content_pipeline.py`. Reason: doing so
would mean touching the manager file and the CrewAI Task definitions,
which the user explicitly asked not to risk. The hook is ready and the
contract is documented — wiring it in is a 5-line follow-up in
`content_pipeline.py` (call `validate_and_log(agent_name, task.output.raw)`
inside an `on_task_complete` callback, or after `crew.kickoff()`).

### Where validation errors land

```
virtuai/data/logs/agent_validation_errors.jsonl
```

One JSON object per line:

```json
{
  "ts":       "2026-05-19T20:38:02.108276Z",
  "agent":    "reviewer",
  "error":    "schema validation error: 1 validation error for ReviewerOutput …",
  "raw_head": "<first 2 KB of the agent's output, for diagnosis>"
}
```

Read with `virtuai.schemas.validators.recent_errors(limit=10)` or
`tail virtuai/data/logs/agent_validation_errors.jsonl`.

---

## 17. Why Analyzer-first was documented but not applied

Two reasons, in priority order:

1. **The current order is a valid feedback-loop architecture.** Analyzer
   runs at the end of cycle N, reads the post that just shipped, writes
   the verdict to `lessons.json` and `autopilot_history.json`. Cycle N+1
   starts by reading both files via Research and Strategy. The feedback
   reaches the next cycle, just deferred by one cron tick. This is the
   pattern your locked baseline at `virtuai/locked/v1_2026-05-18/`
   verifies, so it's known-working.

2. **Moving Analyzer to the front requires editing the live manager.**
   Specifically:
   - Reorder the `tasks=[...]` list in `content_pipeline.py`.
   - Add `context=[task_analyze]` to `task_research`, `task_strategy`,
     `task_create` so they can read the verdict synchronously.
   - Re-test the n8n IF gate (`Last post: positive?`) which currently
     reads the *previous* cycle's verdict from `autopilot_history.json`.
   - Re-verify the locked baseline still applies.

   The first 2 are small. The third is the risky one — n8n's IF gate
   today branches on a record that didn't yet exist when this cycle
   started; flipping to "verdict of the same cycle" requires re-wiring
   that node. Documented as a future change.

If you want to apply it later, the safe order is:
   1. Add `task_analyze` at the head of the task list.
   2. Add `context=[task_analyze]` to the next three tasks.
   3. Run `python scripts/agent_cli.py --pipeline-check --offline`.
   4. Run `python scripts/demo.py --no-publish` end-to-end.
   5. If clean, update the n8n IF-gate expression and re-import.

---

## 18. Exact commands to run before a demo

```bash
# 1. Project readiness — agents, schemas, data, env, gates (10 s)
python scripts/agent_cli.py --pipeline-check --offline

# 2. Validate the latest existing content package (under 1 s)
python scripts/agent_cli.py --validate-latest

# 3. Full test suite (≈ 11 s)
.venv/bin/python -m pytest virtuai/tests/ -q

# 4. End-to-end dry-run demo (full pipeline, no publishing)
python scripts/demo.py --no-publish
```

If steps 1-3 are green and step 4 lands an mp4 under
`virtuai/data/generated_videos/`, you are demo-ready. If anything fails
in step 1, the per-row output tells you exactly which file or env var
needs attention.

---

## 19. KIE CDN download workaround (added 2026-05-20)

KIE's temporary asset CDN (`tempfile.aiquickdraw.com`) occasionally
serves an incomplete TLS certificate chain. When that happens, the
download step in `scripts/produce_reel_v16.py::download_first()` fails
with `SSL: CERTIFICATE_VERIFY_FAILED` even though the KIE API calls
themselves succeed.

**Default behavior is unchanged**: SSL verification stays ON for every
download.

To opt into the temporary workaround for one demo run:

```bash
export VIRTUAI_TRUST_KIE_CDN=true
python scripts/demo.py --no-publish
unset VIRTUAI_TRUST_KIE_CDN
```

The workaround:
- Only engages when **both** `VIRTUAI_TRUST_KIE_CDN=true` AND the URL's
  hostname is in the KIE CDN allowlist (currently just
  `tempfile.aiquickdraw.com`).
- Has a hard deny-list — `api.kie.ai`, the file upload host, and every
  other API endpoint **never** get relaxed verification, even with the
  env var on.
- Logs a WARNING with the hostname every time it engages.

See [`docs/KIE_CDN_DOWNLOAD_WORKAROUND.md`](KIE_CDN_DOWNLOAD_WORKAROUND.md)
for the full breakdown.
