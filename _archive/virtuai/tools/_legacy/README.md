# Legacy / archived tools

These files were used during earlier iterations of VirtuAI and are kept here for
reference and reproducibility — **none of them are imported by the current
pipeline**. Do not add imports back to them; the active toolset lives in
`virtuai/tools/local_tools.py`.

## Contents

| File                            | What it was for                                                  |
|---------------------------------|------------------------------------------------------------------|
| `search_tools.py`               | Pre-pivot trend search using `gemini-2.5-flash-lite`.            |
| `content_tools.py`              | Pre-pivot text + Imagen image generation via Google Gemini.      |
| `guardian_tools.py`             | Pre-pivot Gemini-backed safety/persona checks.                   |
| `generate_api_content.py`       | One-off batch script: Imagen 4.0 + Veo 3.0 content generation.   |
| `generate_platform_content.py`  | One-off batch script: FLUX.1 + moviepy local generation.         |
| `generate_batch3.py`            | One-off batch run for diversifying demo topics.                  |
| `create_post_images.py`         | One-off PIL-based image compositor (persona overlays).           |
| `script_director.py`            | Earlier 2-pass scene-direction helper; overlap with `script_writer.py`. |

The pre-Kling-3.0 direct-Kling clients (`kling_omni.py`, `kling_video.py`)
were removed on 2026-05-19 — Kling lives entirely behind the KIE.ai
gateway now.

## Why keep them?

They generated the demo images and videos under `virtuai/data/generated_images/`
and `virtuai/website/static/`. Removing them would make those outputs
unreproducible. They also document the project's pivot from external APIs
(Gemini / Imagen / Veo) to the fully local MLX stack.
