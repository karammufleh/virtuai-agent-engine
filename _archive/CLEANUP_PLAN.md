# VirtuAI — Clean Claude/Cloud System: Cleanup Plan

Date: 2026-06-02. Status: inspection complete; safe doc edits applied; structural moves awaiting approval.

## Core principle discovered during inspection
The **runtime is already cloud/Claude-based**. `main.py` defaults to `--llm kie` (Claude Sonnet 4.6);
`daily_pack.py`, `autopilot.py`, and `demo.py` all use the KIE.ai cloud path. No simplification of the
runtime is needed — only repo slimming and documentation alignment.

## KEEP (do not touch — load-bearing or genuine utility)
- `virtuai/agents/*.py` — the 8 agents.
- `virtuai/tools/cloud_tools.py` — Claude/Kling/Nano/Suno via KIE.
- `virtuai/tools/local_tools.py` — **imported by Reviewer, Guardian, Research agents; in the locked baseline.**
  Provides deterministic ArcFace + ffmpeg + Whisper utilities (NOT AI generation). Removing it breaks the
  build, all 127 tests, and the locked-baseline checksum claim.
- `virtuai/models/vlm_client.py` — imported by `local_tools.py` (load-bearing).
- `virtuai/tools/video_reviewer.py`, `caption_generator.py`, `reel_builder.py`, `matte_video.py`,
  `slide_renderer.py` — deterministic media utilities used by the cloud pipeline. Reframe as "utilities," not "AI fallback."
- `virtuai/persona/eval/` (ArcFace face_similarity) — the Reviewer's identity gate + the consistency results.
- `virtuai/pipelines/content_pipeline.py` — keep both LLM factories; `kie` is the default.
- `n8n/virtuai_unified.json`, `virtuai/locked/`, `virtuai/schemas/`, `virtuai/config/`, `virtuai/tests/`.

## ARCHIVE — needs user approval (not imported by core; reversible move, not delete)
Recommended target: `archive/phase1_local/` and `archive/website_unused/`.
- `virtuai/website/` + `run_website.py` — Phase-1 showcase, isolated. → `archive/website_unused/`
- `virtuai/persona/sadtalker/` (2.5G), `wav2lip/` (1.7G), `liveportrait/` (76M), `video_retalking/` (86M)
  — ~4.3 GB of Phase-1 talking-head model weights. Not imported by core. → `archive/phase1_local/`
- `virtuai/models/backend.py`, `virtuai/models/finetune/`, `run_backend.py`, `setup_mlx.sh`
  — local backend + Phi-3.5 fine-tuning. Not imported by core (run only by entry scripts). → `archive/phase1_local/`
  CAUTION: these are the artifacts behind the LoRA + consistency-delta results. Keep referenced in the report.

## DO NOT REMOVE FROM REPORT (it is the thesis, not stale fallback)
- The local→cloud journey, the Phase-1 LoRA training, the ArcFace consistency delta (0.301 → 0.664),
  and the "Challenges in Video Generation" chapter. These justify the cloud pivot and are the academic core.

## Report status
`VirtuAI_Final_Report_Draft.docx` is already cloud-aligned: zero website mentions, local correctly framed
as "fallback." Only optional Low-priority trims remain. The OLD `CAPSTONE.md` / `virtuai_capstone_report.pdf`
still carry the local-heavy narrative — do NOT regenerate the PDF from CAPSTONE.md; submit the DOCX.

## Approval-ready commands (run only on user approval)
```bash
mkdir -p "archive/website_unused" "archive/phase1_local"
git mv 2>/dev/null || true   # (no git yet; use mv)
mv virtuai/website archive/website_unused/website
mv run_website.py archive/website_unused/
mv virtuai/persona/sadtalker virtuai/persona/wav2lip \
   virtuai/persona/liveportrait virtuai/persona/video_retalking archive/phase1_local/
# Then fix main.py line ~169 (the "View on website" print) and verify: pytest + pipeline-check.
```
