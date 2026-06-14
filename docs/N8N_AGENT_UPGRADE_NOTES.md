# n8n Agent Upgrade Notes

_Last updated 2026-05-19. How n8n fits with the upgraded 8-agent CrewAI pipeline. The workflow JSON (`n8n/virtuai_unified.json`, 34 nodes, `active=true`) was **not changed** in this pass — these notes document the contract between n8n and the agent system as it is today, plus the role n8n must NOT step into._

---

## How n8n currently fits

n8n is the **trigger plane** and the **transport** between cron / webhooks and the FastAPI server. It does not make creative or strategic decisions.

The workflow has 5 trigger paths:

| Trigger | Default Schedule | What it calls |
|---|---|---|
| `Schedule 09:00` | Daily 09:00 | `POST :9090/n8n/run-reel-and-publish` |
| `Schedule 17:00` | Daily 17:00 | Same — full pack → publish path |
| `Manual run` | n/a | One-click full pack |
| `Webhook /virtuai-agent-run` | external | `POST :9090/agents/{name}/run-sync` |
| `Webhook /virtuai-model-call` | external | `POST :9090/models/{endpoint}` |

The agent-run webhook is the surface the upgraded agents are exposed on.

---

## What n8n SHOULD do

1. **Trigger** the workflow on schedule or webhook.
2. **Forward** niche / persona / platform-preference context as JSON in the request body.
3. **Receive** the structured `WorkflowResult` (or per-agent JSON) and route on the typed status fields.
4. **Branch** on the Analyzer's `last post: positive?` IF gate (already wired).
5. **Branch** on text-gate failures (Reviewer TEXT, Guardian TEXT) before the expensive Visual render (already wired).
6. **Notify** on terminal states: `published`, `manual_approval_required`, `failed`, `skipped`.
7. **Persist** the response somewhere (existing JSONL logs under `virtuai/data/logs/`).

---

## What n8n MUST NOT do

- **Do not decide the platform.** That is the Strategy Agent's job — its output names `primary_platform` and `selected_platforms`.
- **Do not decide the format.** Strategy emits `content_type`.
- **Do not decide the posting time.** Strategy emits `recommended_posting_time`.
- **Do not decide whether a trend is valid.** Research Agent emits `recommendation: "continue" | "skip"`.
- **Do not bypass safety gates.** Even if the workflow's Manual run trigger fires, the Reviewer + Guardian still run.
- **Do not add a new social media platform to the workflow.** The active set is `instagram / linkedin / x / youtube_shorts` only.

If n8n tries to override any of these (e.g. by injecting a `force_platform=tiktok` field), the agents will reject it — there is no TikTok publisher to route to. Don't add one.

---

## How n8n should call the improved workflow

### Full pack run (the existing path — unchanged)

```http
POST http://localhost:9090/n8n/run-reel-and-publish
Content-Type: application/json

{
  "persona": "virtuai_mentor",
  "niche":   "AI + automation in business"
}
```

Response (eventually, after polling `/status/{task_id}`):

```json
{
  "task_id": "...",
  "state":   "done",
  "result":  { ... full WorkflowResult ... }
}
```

### Single-agent invocation (Webhook /virtuai-agent-run)

```http
POST http://localhost:9090/agents/research/run-sync
Content-Type: application/json

{
  "context": {
    "niche":       "AI + automation in business",
    "persona":     "virtuai_mentor",
    "analyzer_verdict": "positive"
  }
}
```

The response body matches one of the Pydantic models in `virtuai/schemas/agent_outputs.py`:

| Endpoint slug | Response schema |
|---|---|
| `/agents/analyzer/run-sync`  | `AnalyzerOutput`  |
| `/agents/research/run-sync`  | `ResearchOutput`  |
| `/agents/strategy/run-sync`  | `StrategyOutput`  |
| `/agents/creator/run-sync`   | `CreatorOutput`   |
| `/agents/visual/run-sync`    | `VisualOutput`    |
| `/agents/reviewer/run-sync`  | `ReviewerOutput`  |
| `/agents/guardian/run-sync`  | `GuardianOutput`  |
| `/agents/publisher/run-sync` | `PublisherOutput` |

n8n's Function nodes can validate the response by parsing it as JSON and checking the typed fields directly — no Python required.

---

## JSON contract — what n8n receives back

The wrapper the pipeline returns to n8n looks like this (matches `WorkflowResult` in `virtuai/schemas/agent_outputs.py`):

```json
{
  "workflow_status":        "completed|stopped|failed|manual_review_required",
  "final_decision":         "approved|rejected|needs_revision|manual_review_required|skipped",
  "reason":                 "<one-line>",
  "selected_trend":         { ... ResearchOutput excerpt ... },
  "selected_platform":      "instagram|linkedin|x|youtube_shorts",
  "selected_content_type":  "reel|portrait|carousel|post",
  "publish_ready":          true,
  "manual_review_required": false,
  "retries_used":           0,
  "errors":                 []
}
```

n8n's IF nodes can branch on `workflow_status`, `final_decision`, `publish_ready`, and `manual_review_required` without parsing nested objects.

---

## Manual approval flow

When `Guardian.safety_status === "manual_review"` OR Strategy includes a `risks[]` entry the Publisher considers blocking:

1. The Publisher Agent returns `publisher_status: "manual_approval_required"`.
2. `WorkflowResult.manual_review_required = true`.
3. n8n branches to an `Email send` node (the existing `Notify BLOCKED` / `Notify text fail` patterns in the workflow can be re-used).
4. The notify email includes the staged file paths and the caption so a human can review without booting the project.
5. To resume the publish manually, call:
   ```http
   POST http://localhost:9090/publish-reel
   {"package_path": "...", "approved_by": "human", "dry_run": false}
   ```

---

## Publisher fit with Composio + direct YouTube

The Publisher Agent is the **only** place that actually calls Composio or YouTube. n8n should never call those services directly — go through `/n8n/run-reel-and-publish` so the safety gates run.

- Composio handles Instagram / LinkedIn / Facebook / X.
- YouTube direct OAuth handles YouTube Shorts (Composio's YT wrapper is broken — it drops the COPPA flag).

When Composio is in DRY-RUN mode (no `COMPOSIO_API_KEY`), the same code path runs but writes intent to `virtuai/data/logs/composio_dry_run.jsonl` instead of posting. n8n still gets a `publisher_status` back — it'll be `"dry_run"` rather than `"published"`.

---

## Warning summary

| ⚠️ | Why it matters |
|---|---|
| Do NOT add a new platform to the workflow | Only IG / LinkedIn / Facebook / YouTube Shorts have publishers (Facebook replaced X on 2026-05-21). Adding TikTok or Medium here will route to a tool that doesn't exist. |
| Do NOT make n8n the strategy brain | All platform / format / timing / angle decisions belong to the Strategy Agent. n8n only triggers and transports. |
| Do NOT skip the Guardian by branching around it | Even cosmetic-looking posts can violate AI-disclosure policy on a given platform. Guardian's per-platform-risk note is the gate. |
| Do NOT auto-retry indefinitely | The pipeline's retry budget is bounded inside `content_pipeline.py`. n8n should respect the response's `retries_used` and stop on terminal failure. |
| DRY-RUN status is real | If you see `publisher_status: "dry_run"` in your monitoring, it means nothing was posted live — investigate before assuming success. |

---

## To verify n8n is wired correctly

```bash
# 1) Healthy
curl -s http://localhost:5678/healthz
curl -s http://localhost:9090/healthz

# 2) Workflow active in SQLite
sqlite3 ~/.n8n/database.sqlite "SELECT id,name,active FROM workflow_entity WHERE id='virtuai-unified';"
# Expected: virtuai-unified|VirtuAI — Unified Automation|1

# 3) Trigger one manual run via the n8n UI:
#    http://localhost:5678/workflow/virtuai-unified  → click ▶ Manual run

# 4) Tail both logs to watch the chain react
tail -f /tmp/virtuai_n8n.log
tail -f /tmp/virtuai_api.log
```

If the manual run completes with `workflow_status: "completed"` and `final_decision: "approved"`, the upgrade is live for n8n.
