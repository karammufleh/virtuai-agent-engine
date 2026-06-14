# Final Submission Checklist — VirtuAI Capstone

## Must do before submission

- [ ] **Refresh Composio API key** — current key returns 401. Get a new one from composio.dev/dashboard, update `.env`. This restores tests from 123/127 to 127/127 and pipeline check from 22/23 to 23/23.
- [ ] **Refresh YouTube OAuth token** — last verified 2026-05-21. Run `python scripts/publisher_healthcheck.py` to verify.
- [ ] **Verify references [6] and [7]** — both are marked "Verify before submission" in the report. Confirm they are real published papers.
- [ ] **Create presentation slides** — no .pptx exists. Use `docs/DEMO_PRESENTATION_SCRIPT.md` as outline. Need 10-15 slides covering: title, problem, architecture, agents, demo, metrics, ethics, conclusion.
- [ ] **Populate Appendix E** in the report — currently empty. At minimum add "Presentation slides are provided as a separate file."

## Should do before submission

- [ ] **Record 60-second demo screencast** — run `python scripts/demo.py --no-publish` and screen-record. Fallback if live demo fails.
- [ ] **Initialize git repository** — `git init`, create `.gitignore`, commit. Report references "repository" repeatedly.
- [ ] **Run fresh tests with valid Composio key** — capture `python -m pytest virtuai/tests/ -v` output showing 127/127 to `docs/evidence/pytest_output.txt`.
- [ ] **Capture pipeline check** — `python scripts/agent_cli.py --pipeline-check --offline` showing 23/23 to `docs/evidence/pipeline_check.txt`.
- [ ] **Collect screenshots** for presentation: Instagram profile, YouTube Shorts, n8n workflow, pytest output, showcase website.

## Optional polish

- [ ] **Add architecture diagram** as a figure — would strengthen both report and presentation.
- [ ] **Export n8n workflow screenshot** — visual proof of the 34-node credit-aware workflow.
- [ ] **Run publisher healthcheck** and save output to `docs/evidence/healthcheck_output.txt`.
- [ ] **Add `docs/screenshots/` directory** with proof-of-publish screenshots.

## Current validation state (2026-06-02)

| Check | Result |
|-------|--------|
| Test suite | 123 passed, 2 failed (expired Composio key), 2 skipped |
| Pipeline check | 22/23 (publisher factory fails on expired key) |
| Locked baseline SHA-256 | 11/11 OK |
| Publishers import | OK (fixed) |
| Report DOCX | Repacked with 14 corrections, all validations passed |
