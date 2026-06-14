#!/usr/bin/env python3
"""Resume the carousel from the partially-generated run."""
import json, sys
from pathlib import Path
import concurrent.futures as cf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.produce_images import (
    gen_persona_bg, gen_concept_bg, upload_to_tmpfiles, CANONICAL_FACE,
)
from virtuai.tools.slide_renderer import render_slide
from virtuai.tools.image_content_writer import write_image_caption
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("finish")

run_dir = Path(sys.argv[1])
content = json.loads((run_dir / "content.json").read_text())
slides_data = content["slides"]
bg_dir = run_dir / "_bg"
bg_dir.mkdir(exist_ok=True)

canonical_url = upload_to_tmpfiles(CANONICAL_FACE)

def _gen(slide):
    bg_path = bg_dir / f"slide_{slide['id']}_bg.png"
    if bg_path.exists() and bg_path.stat().st_size > 10000:
        log.info(f"  slide {slide['id']}: already have bg")
        return bg_path
    if slide.get("uses_persona"):
        return gen_persona_bg(slide["image_prompt"], canonical_url, bg_path)
    return gen_concept_bg(slide["image_prompt"], canonical_url, bg_path)

log.info("Generating any missing backgrounds...")
with cf.ThreadPoolExecutor(max_workers=5) as ex:
    bg_paths = list(ex.map(_gen, slides_data))

log.info("Rendering typography on 5 slides...")
for slide, bg_path in zip(slides_data, bg_paths):
    out = run_dir / f"slide_{slide['id']:02d}.png"
    render_slide(
        bg_path,
        headline=slide["headline"],
        subhead=slide["subhead"],
        out_path=out,
        slide_index=slide["id"],
        total=5,
    )

captions = write_image_caption(content)
(run_dir / "captions.json").write_text(json.dumps(captions, indent=2))

log.info(f"Done. Carousel slides in: {run_dir}")
for slide in slides_data:
    log.info(f"  slide_{slide['id']:02d}: {slide['headline']!r} — {slide['subhead']!r}")
