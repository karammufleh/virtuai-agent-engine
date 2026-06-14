# VirtuAI Demo Readiness Checklist

_Run through this list 30 minutes before a live demo. Every step is offline-safe — nothing in this checklist publishes or burns API credits._

---

## 1. Pre-Demo Safety Commands

```bash
python scripts/agent_cli.py --validate-latest
python scripts/agent_cli.py --pipeline-check --offline
```

Both must exit `0`. `--pipeline-check` must report **23 / 23 checks passed**.

If anything is red, the failing row tells you exactly what's missing (agent factory, data file, env var, etc.). Fix that one row before continuing.

### 1a. Verify the no-publish safety gate (NEW — 2026-05-20)

Before any live demo, prove that `--no-publish` actually skips publishing:

```bash
# Runs every no-publish-safety test (mocks Composio + YouTube; no live calls)
.venv/bin/python -m pytest virtuai/tests/test_no_publish.py -v
```

Must report **13 passed**. This test suite verifies the full chain:

```
scripts/demo.py --no-publish
    → POST /run-pack {"publish": false}
    → RunPackRequest.publish_allowed() == False
    → _run_pack(publish=False)
    → daily_pack.main(publish=False)
    → publish helpers NEVER called  (Composio + YouTube blocked)
```

If any of the 13 tests fail, **do not run the live demo** — publishing safety is not guaranteed.

---

## 2. Test Suite Sanity

```bash
.venv/bin/python -m pytest virtuai/tests/ -q
```

Expected: **65 passed**. ~11 s. Covers all 8 agent factories, output schemas, publisher safety gates, validators, and the KIE CDN SSL workaround.

If any test fails, do NOT demo until it's resolved.

---

## 3. Services Up

```bash
# API server (the CrewAI bridge for n8n + the demo script)
curl -sf http://localhost:9090/healthz && echo " api OK"

# n8n (the trigger plane)
curl -sf http://localhost:5678/healthz && echo " n8n OK"

# n8n workflow active flag
sqlite3 ~/.n8n/database.sqlite "SELECT id,name,active FROM workflow_entity WHERE id='virtuai-unified';"
# expected: virtuai-unified|VirtuAI — Unified Automation|1
```

If the API server is down:

```bash
nohup ./.venv/bin/python -m uvicorn scripts.api_server:app --host 0.0.0.0 --port 9090 > /tmp/virtuai_api.log 2>&1 &
until curl -sf -o /dev/null http://localhost:9090/healthz; do sleep 2; done
echo "api healthy"
```

If n8n is down:

```bash
nohup env N8N_SECURE_COOKIE=false N8N_RUNNERS_ENABLED=true npx --yes n8n > /tmp/virtuai_n8n.log 2>&1 &
until curl -sf -o /dev/null http://localhost:5678/healthz; do sleep 2; done
echo "n8n healthy"
```

---

## 4. Locked Baseline Integrity

```bash
( cd virtuai/locked/v1_2026-05-18 && shasum -a 256 -c manifest.sha256 )
```

Every line must read `OK`. Eleven files: 8 agents + cloud_tools + local_tools + n8n workflow.

If anything fails, the locked baseline has drifted — investigate before demo.

---

## 5. Secrets Audit (no real keys leaked)

```bash
# .env.example must contain placeholders only — no real keys
grep -E "^[A-Z_]+=[A-Za-z0-9_-]{8,}$" .env.example && echo "REAL KEY LEAKED" || echo "✓ .env.example clean"
```

Expected: `✓ .env.example clean`.

---

## 6. Network Reachability to the KIE CDN

KIE's temporary asset CDN must be reachable for an end-to-end demo with live generation. If you're on a restricted network, the asset-download step will fail (see `docs/NETWORK_BLOCK_TROUBLESHOOTING.md`).

```bash
# Quick reachability probe — must NOT print a 307 to an internal IP
curl -kI --connect-timeout 6 https://tempfile.aiquickdraw.com/ 2>&1 | head -3
```

Acceptable:
- `HTTP/2 200`, `HTTP/2 403`, or `HTTP/1.1 200` — host is reachable
- `SSL certificate problem: unable to get local issuer certificate` from `curl` alone is **OK** — that's the cert-chain issue our workaround handles

NOT acceptable:
- `Location: https://10.*` → IPS gateway is blocking; download will fail
- `Connection timed out` → routing blocked; download will fail

If the host is blocked, switch to **Plan B (offline-only walkthrough)** in the presentation script — do not attempt a live render.

---

## 7. Decide Demo Mode

| Mode | When | What you show |
|---|---|---|
| **Plan A — Live render** | network reachability passes Section 6 | `python scripts/demo.py --no-publish` end-to-end (~12 min) |
| **Plan B — Offline walkthrough** | network reachability fails Section 6 | Pre-flight + validate-latest + saved artifacts under `virtuai/data/generated_videos/` |

Both plans use the same scripts. Plan B is **fully demonstrable** — there are 216 generated videos and 37 generated images already on disk from prior runs, and `--validate-latest` proves the schema layer works.

---

## 8. Pre-Demo Asset Inventory (Plan B safety net)

```bash
ls -lt virtuai/data/generated_videos/ | head -5    # newest reels
ls -lt virtuai/data/generated_images/ | head -5    # newest images
ls -lt virtuai/data/content_packages/ | head -3    # latest daily packs
```

Pick the most recent reel mp4 and have it open in QuickTime for the demo "previously generated" reveal.

---

## 9. Final 1-Minute Smoke

```bash
# This single command exercises factories, schemas, env vars, files,
# locked baseline, and n8n notes — all offline.
python scripts/agent_cli.py --pipeline-check --offline | tail -3
```

Expected last line: `└─ 23 / 23 checks passed ───────────────────────────┘`

---

## 10. Optional: Enable the KIE CDN SSL Workaround

Only if Section 6 showed a cert-chain failure (NOT a network block):

```bash
# In the shell that will run the API server:
pkill -f "uvicorn scripts.api_server"; sleep 2
nohup env VIRTUAI_TRUST_KIE_CDN=true ./.venv/bin/python -m uvicorn scripts.api_server:app --host 0.0.0.0 --port 9090 > /tmp/virtuai_api.log 2>&1 &
```

Remember to roll it back afterwards:

```bash
pkill -f "uvicorn scripts.api_server"; sleep 2
nohup ./.venv/bin/python -m uvicorn scripts.api_server:app --host 0.0.0.0 --port 9090 > /tmp/virtuai_api.log 2>&1 &
```

The workaround stays opt-in and is documented in [`docs/KIE_CDN_DOWNLOAD_WORKAROUND.md`](KIE_CDN_DOWNLOAD_WORKAROUND.md).

---

## Quick Reference — All Demo Commands

| Command | What it does | Time |
|---|---|---|
| `python scripts/agent_cli.py --pipeline-check --offline` | 23-check readiness audit | < 5 s |
| `python scripts/agent_cli.py --validate-latest` | Validate the latest content package | < 1 s |
| `python scripts/agent_cli.py --inspect` | Show persona / platforms / agents / models | < 2 s |
| `python scripts/agent_cli.py --agent <name> --offline` | Build one agent with a dummy LLM | < 3 s |
| `.venv/bin/python -m pytest virtuai/tests/ -q` | Run all 65 tests | ~11 s |
| `python scripts/demo.py --no-publish` | Live end-to-end (skip publish) | ~12 min |

---

## Demo-Day Go/No-Go

You are clear to demo when:

- [ ] `--pipeline-check --offline` is 23 / 23
- [ ] `--validate-latest` exits 0
- [ ] Tests are 65 / 65
- [ ] API server is up on :9090
- [ ] n8n is up on :5678 with `active=1`
- [ ] Locked baseline manifest verifies
- [ ] `.env.example` has no real keys
- [ ] Network reachability decided (Plan A or Plan B chosen)
- [ ] Newest reel/image on disk identified for the artifact reveal
