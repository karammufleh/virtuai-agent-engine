# VirtuAI Demo Presentation Script

_A timed walkthrough for a 10-minute capstone demo. Two plans: A (live render on a clean network) and B (offline walkthrough when the network blocks KIE's CDN). Both end with the same takeaways. Nothing in this script publishes content live._

---

## Overall Arc

| Minute | Phase | What's on screen |
|---|---|---|
| 0:00–1:00 | What VirtuAI is | One paragraph + persona portrait |
| 1:00–2:30 | Architecture | Diagram + the 3 orchestration planes |
| 2:30–4:00 | The 8 agents | Sequential flow + inter-agent messaging |
| 4:00–6:30 | Safety + quality | Pre-flight + safety gates + schema validation |
| 6:30–9:00 | Output proof | Either live render (Plan A) or saved artifacts (Plan B) |
| 9:00–10:00 | Wrap | What's done, what's pending, repo tour |

---

## 0:00 — Opening (60 s)

> "VirtuAI is an autonomous AI persona content engine. Daniel Calder — a synthetic AI/automation business creator — researches the niche, writes the post, films the reel, fact-checks itself, and ships to Instagram, LinkedIn, X, and YouTube Shorts on a cron schedule. Zero humans in the loop, all in about twelve minutes per pack."

Show the persona portrait: `virtuai/persona/canonical_daniel.png` (open it in Preview).

---

## 1:00 — Architecture (90 s)

Open `README.md` to the architecture diagram. Three orchestration planes:

1. **n8n** (`:5678`) — the trigger plane. Cron at 09:00 and 17:00, plus webhooks for one-off agent runs. **Does not** decide platform / topic / format.
2. **FastAPI** (`scripts/api_server.py`, `:9090`) — the bridge. 20+ endpoints. n8n calls into this; so does the CLI demo.
3. **CrewAI** (`virtuai/pipelines/content_pipeline.py`) — the manager. Builds + runs the 8-agent crew sequentially.

> "n8n is just the cron. The actual creative + safety decisions are made by the agents."

---

## 2:30 — The 8 Agents (90 s)

Open `docs/AGENT_UPGRADE_REPORT.md` §1.

Walk through the production order:

```
Research → Strategy → Creator → Visual → Reviewer → Guardian → Publisher → Analyzer
```

Mention the **recommended future order** (Analyzer first) is documented in §8 but not applied because the current feedback-loop architecture is the locked baseline.

Highlight inter-agent state files:

- `virtuai/data/agent_messages.jsonl` — Reviewer / Guardian REVISE inbox
- `virtuai/data/banned_patterns.json` — Guardian's permanent BLOCK list
- `virtuai/data/lessons.json` — Analyzer's verdict trail

```bash
# Show the state files exist + non-empty
ls -lh virtuai/data/{agent_messages.jsonl,banned_patterns.json,autopilot_history.json}
```

---

## 4:00 — Safety + Quality (150 s)

Three things to show on screen, in this exact order:

### a. Pre-flight readiness — 23 / 23 checks

```bash
python scripts/agent_cli.py --pipeline-check --offline
```

> "Before any live demo I run this. It builds every agent factory offline, validates the schemas, checks all data files, confirms the env keys are set, and verifies the locked baseline. 23 / 23 green means we ship."

### b. Validate the latest content package

```bash
python scripts/agent_cli.py --validate-latest
```

> "The schema layer means n8n and the API server can validate every agent's output before passing it downstream. This proves it works on a real package generated last week."

### c. Tests

```bash
.venv/bin/python -m pytest virtuai/tests/ -q
```

> "65 tests. All offline. Covers every agent factory, the 9 output schemas, the publisher safety gates, the validation layer, and the SSL workaround for KIE's CDN. Zero live API calls in the test suite. The tests would catch a publisher trying to push unreviewed content."

Highlight one safety-gate test (open `virtuai/tests/test_publisher_safety.py`):

```python
def test_skipped_when_reviewer_rejects():
    ...
    assert parsed.publisher_status == "skipped"
```

> "Publisher returns `skipped` (not `published`) when Reviewer rejects. That's a hard contract — the Pydantic schema would refuse a status like `published_secretly` at parse time."

---

## 6:30 — Output Proof (150 s)

### Plan A — Live render (network clear, KIE CDN reachable)

```bash
python scripts/demo.py --no-publish
```

While the wall-clock runs (~12 min), pivot to the architecture deep-dive and let it complete in the background. The script prints scene-by-scene state updates. End with `outputs/.../<reel>.mp4` open in QuickTime.

> "About six dollars in KIE credits per pack. The pipeline burned the credits to generate; the --no-publish flag stops before Composio sees it."

### Plan B — Offline walkthrough (network IPS is blocking KIE's CDN)

State the limitation honestly:

> "The lab network's web filter blocks KIE's temporary CDN host. That's diagnosed in `docs/NETWORK_BLOCK_TROUBLESHOOTING.md`. Generation works — KIE renders fine — but downloading the finished asset requires either a different network or the SSL workaround on a non-MITM-ed connection."

Then show what already exists on disk:

```bash
ls -lt virtuai/data/generated_videos/ | head -5
ls -lt virtuai/data/generated_images/ | head -5
```

Open the most recent reel mp4 in QuickTime. Walk through it.

> "216 reels generated to date with this exact pipeline. The schema layer would have caught any one of them that didn't conform — this is the kind of structured output the upgraded agents now emit."

Open one daily-pack JSON in the editor:

```bash
ls -lt virtuai/data/content_packages/ | head -1
```

Point to the `reel.asset.video_master`, `reel.publish.youtube.url`, and `reel.publish.linkedin` fields:

> "Each pack is fully traceable. YouTube URL, LinkedIn URN, Instagram media ID — every published asset is logged."

---

## 9:00 — Wrap (60 s)

### What's done

- 8 CrewAI agents with structured Pydantic outputs
- Credit-aware n8n workflow (cheap text gates before expensive renders)
- Production stack: KIE.ai (Claude / Kling 3.0 / Nano Banana 2 / Suno) + Composio + YouTube Direct OAuth
- SHA-256-verified locked baseline (`virtuai/locked/v1_2026-05-18/`)
- 65 / 65 tests passing
- Optional schema validation layer (env-gated, never required)
- Optional KIE CDN SSL workaround (env-gated, host-allowlisted)

### What's pending

Read from `docs/FINAL_PROJECT_COMPLETION_PLAN.md` §3-§4:

- (X was retired and replaced by Facebook Page publishing on 2026-05-21 — no extra token needed)
- Initialize a git repo + first commit
- 60-second screencast recording

### Repo tour

Pop open `README.md` and scroll the **Repository layout** section.

---

## If a Question Lands Mid-Demo

| Question | Where to point |
|---|---|
| "What if a generated post is unsafe?" | Guardian's `safety_status: reject` + permanent `banned_patterns.json` entry — show the JSON file |
| "How do you keep persona consistency?" | Locked persona anchor + canonical face + ArcFace ≥ 0.70 in Reviewer (`verify_face_identity`) |
| "What if KIE goes down?" | DRY-RUN fallbacks exist for Composio; KIE is on the critical path for generation — by design, only one gateway to manage |
| "How does n8n know when something's blocked?" | n8n's IF gates read `publish_ready` / `final_decision` / `manual_review_required` from the WorkflowResult schema |
| "Why isn't Analyzer first?" | Locked-baseline preservation; the recommended future order is documented in `AGENT_UPGRADE_REPORT.md` §17 |
| "What about TikTok / Medium?" | Removed — no publisher, no content target. Active platforms are Instagram, LinkedIn, X, YouTube Shorts only |
| "Can you show the safety code?" | Open `virtuai/agents/publisher_agent.py` and grep for `PUBLISH SAFETY GATES` — 7 gates listed in the backstory |

---

## Demo Don'ts

- Don't `python main.py` without `--platforms` — it defaults to all enabled and (if Composio is live) WILL publish for real
- Don't run the demo on a network you haven't checked (Section 6 of the readiness checklist)
- Don't commit `VIRTUAI_TRUST_KIE_CDN=true` to `.env` — it's an opt-in workaround, not a permanent setting
- Don't enable `EXPERIMENTAL_MODEL_TRIAL` — that path was removed
- Don't claim live publishing during the demo — `--no-publish` is the only invocation in either plan
