# _archive — superseded material (not part of the final submission)

These files describe **earlier iterations** of VirtuAI and are retained only for
project history. They do **not** reflect the final working system and should not
be used to evaluate it. The authoritative description of the delivered project is
the final report (`VirtuAI_Final_Report_Draft.docx`) and the live `README.md`.

What changed and why these were archived:

- **Pre-pivot "local pipeline" docs** (`CAPSTONE.md`, `CHALLENGES.md`,
  `TECHNICAL_STATUS_REPORT.md`, `virtuai_capstone_report.pdf`): describe an
  abandoned fully-local Apple-Silicon stack (Wav2Lip / SadTalker / F5-TTS /
  mflux). The final system is cloud-based via the KIE.ai gateway (Claude Sonnet
  4.6, Kling 3.0 with a Seedance 2.0 fallback, Nano Banana 2, Suno).
- **Retired platforms** (`VirtuAI_Publish_Ready_Content_Package.docx`,
  `content_packages/run_20260425_170232`, `run_20260506_214555`,
  `docs/AGENT_UPGRADE_REPORT.md`): reference X/Twitter, TikTok, and Medium, which
  are out of scope. The final system publishes to Instagram, YouTube Shorts,
  Facebook, and LinkedIn.
- **Stale evidence captures** (`docs/evidence/*`): show old test counts
  (110 / 127), an old 22/23 pipeline check, and a credential failure. Current
  state is 140/140 tests and 23/23 readiness (see `docs/evidence/pytest_output.txt`).
- **Internal scratch / planning notes** and the old PDF-build script
  (`build_report_pdf.py`) that compiled the superseded `virtuai_capstone_report.pdf`.
