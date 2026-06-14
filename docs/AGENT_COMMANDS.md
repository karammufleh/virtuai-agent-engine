# VirtuAI Agent Commands

_All commands assume the project venv is activated (`source .venv/bin/activate`) and the working directory is the project root. Every per-agent command defaults to a SAFE mode — no live publishing happens unless you explicitly invoke a publishing entry-point._

---

## 1. Run the full existing workflow normally

```bash
python main.py
```

Runs the locked 8-agent CrewAI pipeline (`virtuai/pipelines/content_pipeline.py`) for the locked persona on all enabled platforms.

Flags:
- `--platforms instagram linkedin` — restrict to a subset
- `--persona virtuai_mentor` — choose a persona by config name
- `--llm kie` — agent reasoning via the KIE.ai gateway (the final workflow; default). A `local` Phi-3.5 path from Phase-1 still exists but is not used in the final system.

The other production entry-points behave the same:

```bash
python scripts/daily_pack.py        # scheduled-daily orchestrator
python scripts/autopilot.py         # production autopilot loop
```

## 2. Run the full workflow safely in dry-run / test mode

```bash
python scripts/demo.py --no-publish
```

`scripts/demo.py` is the single-command demo runner. With `--no-publish` it kicks off `/run-pack` against the FastAPI server but stops before the Publisher step. Generation still happens; nothing is pushed to Instagram / LinkedIn / Facebook / YouTube.

If `COMPOSIO_API_KEY` is unset, the Publisher Agent additionally falls back to a DRY-RUN that logs to `virtuai/data/logs/composio_dry_run.jsonl`.

## 3. Run Analyzer Agent only

```bash
python scripts/agent_cli.py --agent analyzer --offline
```

Builds the Analyzer factory with a stub LLM — verifies the agent + tool list assemble cleanly. No KIE call.

Live (uses KIE):

```bash
python scripts/agent_cli.py --agent analyzer --task "Read autopilot_history.json and produce the verdict JSON."
```

## 4. Run Research Agent only

```bash
python scripts/agent_cli.py --agent research --offline
python scripts/agent_cli.py --agent research --task "Find one fresh trend in AI ops, score it, recommend continue or skip."
```

## 5. Run Strategy Agent only

```bash
python scripts/agent_cli.py --agent strategy --offline
python scripts/agent_cli.py --agent strategy --task "Given the topic <X>, pick platform, format, posting time, and angle."
```

## 6. Run Creator Agent only

```bash
python scripts/agent_cli.py --agent creator --offline
python scripts/agent_cli.py --agent creator --task "Write the unified content package for topic <X>."
```

## 7. Run Visual Agent only

```bash
python scripts/agent_cli.py --agent visual --offline
# Live calls hit KIE (Kling 3.0 + Nano Banana 2 + Suno) — burns credits.
python scripts/agent_cli.py --agent visual --task "Render reel using the Creator JSON at <path>."
```

## 8. Run Reviewer Agent only

```bash
python scripts/agent_cli.py --agent reviewer --offline
python scripts/agent_cli.py --agent reviewer --task "Review the latest content package at <path>; emit the structured verdict."
```

## 9. Run Guardian Agent only

```bash
python scripts/agent_cli.py --agent guardian --offline
python scripts/agent_cli.py --agent guardian --task "Inspect the rendered package at <path>; per-platform risk + final_decision."
```

## 10. Run Publisher Agent in dry-run only

```bash
python scripts/agent_cli.py --agent publisher --offline
```

The CLI never invokes Composio or YouTube. To exercise the existing DRY-RUN Composio path explicitly:

```bash
# 1) Make sure COMPOSIO_API_KEY is unset in the shell
unset COMPOSIO_API_KEY

# 2) Run the full pipeline with --no-publish (safest)
python scripts/demo.py --no-publish
```

For a real Publisher invocation against the existing FastAPI endpoint with no live posting, use the existing dry-run flag:

```bash
curl -s -X POST http://localhost:9090/publish-reel \
  -H "Content-Type: application/json" \
  -d '{"package_path": "virtuai/data/content_packages/<file>.json", "dry_run": true}'
```

## 11. Validate the latest content package

```bash
# Show the structure of the latest content package
ls -lt virtuai/data/content_packages/ | head -3

# Validate any saved agent output JSON against its Pydantic schema
python scripts/agent_cli.py --validate reviewer --input <path.json>
python scripts/agent_cli.py --validate guardian --input <path.json>
python scripts/agent_cli.py --validate publisher --input <path.json>
```

Valid agents for `--validate`: `analyzer`, `research`, `strategy`, `creator`, `visual`, `reviewer`, `guardian`, `publisher`.

## 12. View the latest autopilot history

```bash
# Pretty-print the most recent run
python -c "import json; d=json.load(open('virtuai/data/autopilot_history.json')); \
           import sys; sys.stdout.write(json.dumps(d[-1] if isinstance(d,list) else d, indent=2))"

# Tail the inter-agent inbox
tail -20 virtuai/data/agent_messages.jsonl 2>/dev/null

# View the persistent banned-patterns + lessons
cat virtuai/data/banned_patterns.json
cat virtuai/data/lessons.json 2>/dev/null
```

The FastAPI server also exposes the history:

```bash
curl -s http://localhost:9090/history | python -m json.tool | head -40
curl -s http://localhost:9090/tasks   | python -m json.tool | head -40
```

## 13. Debug a failed workflow run

```bash
# 1) Inspect global state
python scripts/agent_cli.py --inspect

# 2) Check both services are up
curl -s http://localhost:9090/healthz
curl -s http://localhost:5678/healthz

# 3) Tail the API server log (started via uvicorn)
tail -f /tmp/virtuai_api.log

# 4) Tail the n8n log
tail -f /tmp/virtuai_n8n.log

# 5) Re-run JUST the failing agent in offline mode to confirm the factory is OK
python scripts/agent_cli.py --agent <name> --offline

# 6) Validate the last JSON the failing agent emitted
python scripts/agent_cli.py --validate <name> --input <path-to-saved-json>
```

## Test suite

```bash
.venv/bin/python -m pytest virtuai/tests/ -v
```

30 tests, all green, no external API calls.

## Where `--no-publish` is honoured

| Entry-point | Defaults to live publish? | How to dry-run |
|---|---|---|
| `python main.py` | Yes — runs the full pipeline incl. Publisher | use `--platforms` to limit scope; for true dry-run, unset `COMPOSIO_API_KEY` |
| `python scripts/demo.py` | No — but it WILL publish unless you pass `--no-publish` | `python scripts/demo.py --no-publish` |
| `python scripts/daily_pack.py` | Yes | unset `COMPOSIO_API_KEY` (falls back to Composio DRY-RUN) |
| `python scripts/autopilot.py` | Yes | unset `COMPOSIO_API_KEY` |
| `python scripts/agent_cli.py --agent publisher` | **No** — CLI never publishes | n/a — it only builds the agent |

**Rule:** if you don't want a real post to go live, run `python scripts/demo.py --no-publish` and look at the trace under `outputs/` (or `virtuai/data/content_packages/`) before re-running with publish enabled.
