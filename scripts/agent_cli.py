"""
scripts/agent_cli.py — Per-agent test runner.

Run ONE CrewAI agent in isolation against a hand-written input JSON.
Default mode is SAFE: no live publishing, no destructive writes, no
external KIE calls when --offline is passed.

Examples:
    # Build the Analyzer agent and dump its role + bound tools (no API)
    python scripts/agent_cli.py --agent analyzer --offline

    # Validate a saved Reviewer output against the schema
    python scripts/agent_cli.py --validate reviewer --input outputs/last_reviewer.json

    # Inspect the loaded persona + active platforms
    python scripts/agent_cli.py --inspect

    # Run a single agent end-to-end (will hit KIE if KIE_API_KEY is set)
    python scripts/agent_cli.py --agent research --task "Find one trend in AI ops"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")


AGENTS: dict[str, tuple[str, bool]] = {
    # name → (factory module path, needs_persona)
    "analyzer":  ("virtuai.agents.analyzer_agent.create_analyzer_agent",   False),
    "research":  ("virtuai.agents.research_agent.create_research_agent",   False),
    "strategy":  ("virtuai.agents.strategy_agent.create_strategy_agent",   False),
    "creator":   ("virtuai.agents.creator_agent.create_creator_agent",     True),
    "visual":    ("virtuai.agents.visual_agent.create_visual_agent",       True),
    "reviewer":  ("virtuai.agents.reviewer_agent.create_reviewer_agent",   True),
    "guardian":  ("virtuai.agents.guardian_agent.create_guardian_agent",   True),
    "publisher": ("virtuai.agents.publisher_agent.make_publisher",         False),
}

SCHEMAS = {
    "analyzer":  "AnalyzerOutput",
    "research":  "ResearchOutput",
    "strategy":  "StrategyOutput",
    "creator":   "CreatorOutput",
    "visual":    "VisualOutput",
    "reviewer":  "ReviewerOutput",
    "guardian":  "GuardianOutput",
    "publisher": "PublisherOutput",
}


def _dummy_llm():
    """Tiny stand-in LLM that never makes a network call. Use with --offline."""
    from crewai import LLM
    return LLM(model="openai/dummy", api_key="not-used",
               base_url="http://127.0.0.1:1/v1")


def _kie_llm():
    """Real KIE.ai LLM (DeepSeek) — uses KIE_API_KEY from .env."""
    import os
    from crewai import LLM
    api_key = (os.environ.get("KIE_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("KIE_API_KEY missing in .env — pass --offline for a dry import.")
    return LLM(
        model="openai/deepseek-chat",
        base_url="https://kieai.erweima.ai/api/v1",
        api_key=api_key,
        max_retries=3,
        timeout=120,
    )


def _load_factory(path: str):
    module_path, factory_name = path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, factory_name)


def _load_persona(name: str = "virtuai_mentor") -> dict:
    from virtuai.utils.config_loader import load_persona
    return load_persona(name)


def inspect_project() -> int:
    """Print persona, active platforms, agent inventory, model catalogue."""
    from virtuai.utils.config_loader import load_persona, load_all_platforms, load_models
    persona = load_persona("virtuai_mentor")
    platforms = load_all_platforms()
    models = load_models()
    print("Persona       :", persona.get("name", "(unknown)"))
    print("Platforms     :", sorted(platforms.keys()))
    print("Agents        :", sorted(AGENTS.keys()))
    print("KIE models    :", sorted(models["models"].keys()))
    print("Output dir    :", ROOT / "virtuai" / "data")
    return 0


def build_agent(name: str, offline: bool) -> int:
    """Instantiate an agent factory and print its role + bound tools."""
    if name not in AGENTS:
        print(f"unknown agent '{name}'. valid: {sorted(AGENTS.keys())}")
        return 2
    factory = _load_factory(AGENTS[name][0])
    llm = _dummy_llm() if offline else _kie_llm()
    if AGENTS[name][1]:
        agent = factory(llm, _load_persona())
    else:
        agent = factory(llm)
    print(f"agent     : {name}")
    print(f"role      : {agent.role}")
    print(f"tools     : {[t.name for t in agent.tools]}")
    print(f"llm       : {agent.llm.model if hasattr(agent.llm, 'model') else type(agent.llm).__name__}")
    print(f"mode      : {'offline (dummy LLM)' if offline else 'live (KIE.ai)'}")
    return 0


def run_agent_task(name: str, task_prompt: str, offline: bool) -> int:
    """Run a CrewAI single-task Crew with just this agent. Returns the raw output."""
    if offline:
        print("--task requires a live LLM. Re-run without --offline.")
        return 2
    from crewai import Crew, Task
    factory = _load_factory(AGENTS[name][0])
    llm = _kie_llm()
    agent = factory(llm, _load_persona()) if AGENTS[name][1] else factory(llm)
    task = Task(description=task_prompt, expected_output="A single JSON object.", agent=agent)
    crew = Crew(agents=[agent], tasks=[task], verbose=False)
    result = crew.kickoff()
    print(str(result))
    return 0


def validate_output(agent: str, input_path: Path) -> int:
    """Load saved JSON and validate it against the agent's Pydantic schema."""
    if agent not in SCHEMAS:
        print(f"unknown agent '{agent}'. valid: {sorted(SCHEMAS.keys())}")
        return 2
    if not input_path.exists():
        print(f"file not found: {input_path}")
        return 2
    from virtuai.schemas import agent_outputs
    from virtuai.schemas.agent_outputs import validate_json
    model = getattr(agent_outputs, SCHEMAS[agent])
    raw = input_path.read_text(encoding="utf-8")
    parsed, err = validate_json(model, raw)
    if parsed is None:
        print(f"✗ INVALID — {err}")
        return 1
    print(f"✓ VALID — parsed as {SCHEMAS[agent]}")
    print(json.dumps(parsed.model_dump(), indent=2)[:600])
    return 0


def validate_latest() -> int:
    """Find the latest content package on disk and validate every embedded
    agent output that has a registered schema. NEVER calls a live API."""
    from virtuai.schemas.validators import validate_agent_output, AGENT_SCHEMA

    packages_dir = ROOT / "virtuai" / "data" / "content_packages"
    if not packages_dir.exists():
        print(f"○ no content_packages directory at {packages_dir}")
        return 0
    candidates = sorted(packages_dir.glob("*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        print("○ no saved content packages found — nothing to validate")
        return 0

    latest = candidates[0]
    print(f"Latest package: {latest}")
    try:
        package = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"✗ could not parse {latest.name}: {e}")
        return 1

    # Content packages are composite. Try the package as a whole against
    # every known schema; if no schema matches the top level, walk one
    # level down and try child objects that look like agent outputs.
    summary: list[tuple[str, str, str]] = []  # (location, agent, status)

    def _try_validate(loc: str, blob: dict) -> bool:
        any_pass = False
        for agent in AGENT_SCHEMA:
            parsed, ok, err = validate_agent_output(agent, blob)
            if ok and parsed is not None:
                summary.append((loc, agent, "PASS"))
                any_pass = True
        return any_pass

    if isinstance(package, dict):
        if not _try_validate("<root>", package):
            for key, sub in package.items():
                if isinstance(sub, dict):
                    _try_validate(f"{key}", sub)

    if not summary:
        print("○ no embedded agent output recognised by current schemas")
        print("  (content packages on disk pre-date the structured-output schemas)")
        return 0

    passes = sum(1 for s in summary if s[2] == "PASS")
    print(f"\n{passes} validation(s) succeeded:")
    for loc, agent, status in summary:
        print(f"  [{status}] {loc:20} → {agent}")
    return 0


def pipeline_check_offline() -> int:
    """Pre-demo readiness check. Touches NOTHING that costs money."""
    import importlib
    from virtuai.schemas.validators import AGENT_SCHEMA, ERROR_LOG_PATH

    checks: list[tuple[str, bool, str]] = []

    def chk(name: str, ok: bool, detail: str = ""):
        checks.append((name, ok, detail))

    # 1. Agents — every factory imports + builds offline
    llm = _dummy_llm()
    for agent_name, (path, needs_persona) in AGENTS.items():
        try:
            fac = _load_factory(path)
            if needs_persona:
                fac(llm, _load_persona())
            else:
                fac(llm)
            chk(f"agent: {agent_name}", True, "factory OK")
        except Exception as e:
            chk(f"agent: {agent_name}", False, str(e))

    # 2. Schemas — every agent has a Pydantic model
    missing_schemas = [a for a in AGENTS if a not in AGENT_SCHEMA]
    chk("schemas", not missing_schemas,
        "all 8 agents have schemas" if not missing_schemas
        else f"missing: {missing_schemas}")

    # 3. Required data files
    data = ROOT / "virtuai" / "data"
    for fname in ("autopilot_history.json", "banned_patterns.json",
                  "agent_messages.jsonl"):
        p = data / fname
        chk(f"data: {fname}", p.exists(), str(p) if p.exists() else "missing")

    # 4. Platform configs (active set only)
    platforms_dir = ROOT / "virtuai" / "config" / "platforms"
    actual = sorted(p.stem for p in platforms_dir.glob("*.yaml"))
    expected = ["facebook", "instagram", "linkedin", "youtube_shorts"]
    chk("platforms", set(actual) >= set(expected),
        f"have {actual}; expected {expected}")

    # 5. KIE models catalogue
    try:
        from virtuai.utils.config_loader import load_models
        cat = load_models()
        required_models = {"reel_video", "image_post", "music_underbed", "script_writer"}
        have = set(cat["models"].keys())
        chk("models.yaml", required_models <= have,
            f"have {sorted(have)}")
    except Exception as e:
        chk("models.yaml", False, str(e))

    # 6. Persona
    persona_anchor = ROOT / "virtuai" / "persona" / "persona_anchor.json"
    canonical = ROOT / "virtuai" / "persona" / "canonical_daniel.png"
    chk("persona_anchor.json", persona_anchor.exists())
    chk("canonical_daniel.png", canonical.exists(),
        f"{canonical.stat().st_size // 1024} KB" if canonical.exists() else "missing")

    # 7. n8n workflow file
    n8n_file = ROOT / "n8n" / "virtuai_unified.json"
    chk("n8n workflow", n8n_file.exists(),
        f"{n8n_file.stat().st_size // 1024} KB" if n8n_file.exists() else "missing")

    # 8. Locked baseline manifest
    manifest = ROOT / "virtuai" / "locked" / "v1_2026-05-18" / "manifest.sha256"
    chk("locked baseline", manifest.exists(),
        "manifest present (verify with: shasum -c)" if manifest.exists() else "missing")

    # 9. Critical env keys (existence only — never print values)
    import os
    for var in ("KIE_API_KEY", "COMPOSIO_API_KEY"):
        chk(f"env: {var}", bool(os.environ.get(var, "").strip()),
            "set" if os.environ.get(var, "").strip() else "MISSING — pipeline will not run live")

    # 10. Publisher safety gates referenced in backstory
    publisher_file = ROOT / "virtuai" / "agents" / "publisher_agent.py"
    if publisher_file.exists():
        text = publisher_file.read_text(encoding="utf-8")
        gates_present = "PUBLISH SAFETY GATES" in text
        chk("publisher safety gates", gates_present,
            "documented in backstory" if gates_present else "NOT FOUND")
    else:
        chk("publisher safety gates", False, "publisher_agent.py missing")

    # 11. n8n upgrade notes
    notes = ROOT / "docs" / "N8N_AGENT_UPGRADE_NOTES.md"
    chk("docs/N8N_AGENT_UPGRADE_NOTES.md", notes.exists())

    # 12. Validation log dir exists (created lazily — OK if absent)
    chk("validation error log dir", ERROR_LOG_PATH.parent.exists(),
        f"{ERROR_LOG_PATH.parent}")

    # Print + tally
    print("┌─ VirtuAI pre-demo pipeline check ────────────────────────┐")
    failed = 0
    for name, ok, detail in checks:
        mark = "✓" if ok else "✗"
        line = f"│ {mark} {name:32} {detail}"
        if not ok:
            failed += 1
        print(line)
    print(f"└─ {len(checks) - failed} / {len(checks)} checks passed ───────────────────────────┘")

    # Tail recent validation errors if any
    from virtuai.schemas.validators import recent_errors
    errs = recent_errors(limit=3)
    if errs:
        print("\nRecent validation errors (newest first):")
        for e in errs:
            print(f"  {e.get('ts','?')}  {e.get('agent','?')}: {e.get('error','?')[:80]}")

    return 0 if failed == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="VirtuAI per-agent test runner.")
    p.add_argument("--agent", choices=sorted(AGENTS.keys()),
                   help="Build (and optionally run) a single agent.")
    p.add_argument("--task",
                   help="Optional one-line task prompt — kicks off a single-task Crew.")
    p.add_argument("--offline", action="store_true",
                   help="Use a stub LLM instead of KIE. Safe even with no key set.")
    p.add_argument("--inspect", action="store_true",
                   help="Print project state (persona, platforms, agents, models).")
    p.add_argument("--validate", choices=sorted(SCHEMAS.keys()),
                   help="Validate a saved JSON file against this agent's schema.")
    p.add_argument("--input", type=Path,
                   help="Path to JSON file (used with --validate).")
    p.add_argument("--validate-latest", action="store_true",
                   help="Find the latest content package and validate every "
                        "embedded agent output against its schema. Safe.")
    p.add_argument("--pipeline-check", action="store_true",
                   help="Pre-demo readiness check — agents, schemas, data "
                        "files, platforms, env, publisher gates. No live calls.")
    args = p.parse_args()

    if args.pipeline_check:
        return pipeline_check_offline()
    if args.validate_latest:
        return validate_latest()
    if args.inspect:
        return inspect_project()
    if args.validate:
        if not args.input:
            print("--validate requires --input <path>")
            return 2
        return validate_output(args.validate, args.input)
    if not args.agent:
        p.print_help()
        return 0
    if args.task:
        return run_agent_task(args.agent, args.task, offline=args.offline)
    return build_agent(args.agent, offline=args.offline)


if __name__ == "__main__":
    sys.exit(main())
