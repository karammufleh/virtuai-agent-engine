#!/usr/bin/env python3
"""
produce_images.py — Generate portrait posts + 5-slide carousels.

Modes:
  --mode portrait    one 1080×1350 quote portrait
  --mode carousel    five 1080×1350 slides (Instagram carousel)
  --mode both        portrait + carousel in one shot (default for autopilot)

Pipeline:
  1. Claude Sonnet 4.6 writes structured content (headline/subhead/image prompts/caption)
  2. Nano Banana 2 generates background images:
       - persona slides (1 and 5) use canonical_daniel as image reference
       - concept slides (2,3,4) are pure text-to-image via Imagen 4
  3. Pillow renders typography overlays into final 1080×1350 PNGs
  4. Saves to virtuai/data/generated_images/posts/<run>/ with caption.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("produce_images")

KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_API_KEY = os.environ.get("KIE_API_KEY", "").strip()

POSTS_DIR = ROOT / "virtuai/data/generated_images/posts"
POSTS_DIR.mkdir(parents=True, exist_ok=True)

CANONICAL_FACE = ROOT / "virtuai" / "persona" / "canonical_daniel.png"

POLL_INTERVAL = 10
POLL_TIMEOUT = 900
MAX_RETRIES = 4
HTTP_TIMEOUT = 90


def _headers():
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


def upload_to_tmpfiles(filepath: Path) -> str:
    """Now backed by KIE's own file-stream-upload (more reliable)."""
    from virtuai.tools.kie_upload import upload as _kie_upload
    return _kie_upload(filepath)


def submit_kie(model: str, input_data: dict) -> str:
    for attempt in range(MAX_RETRIES):
        try:
            r = httpx.post(
                f"{KIE_API_BASE}/jobs/createTask", headers=_headers(),
                json={"model": model, "input": input_data}, timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            tid = (r.json().get("data") or {}).get("taskId")
            if not tid:
                raise RuntimeError(f"submit {model}: {r.text[:300]}")
            return tid
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as e:
            log.warning(f"submit_kie retry {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError(f"submit_kie {model}: exhausted retries")


def poll_task(tid: str, label: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT
    last = ""
    while time.time() < deadline:
        try:
            r = httpx.get(f"{KIE_API_BASE}/jobs/recordInfo",
                          params={"taskId": tid}, headers=_headers(), timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            d = r.json().get("data", {})
            state = d.get("state", "")
            if state != last:
                log.info(f"  {label}: {state}")
                last = state
            if state in ("success", "completed", "succeed"):
                return d
            if state in ("failed", "error", "fail"):
                raise RuntimeError(f"{label} failed: {d.get('failMsg', d)}")
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError,
                httpx.RemoteProtocolError) as e:
            log.warning(f"  {label} transient: {type(e).__name__}: {e}")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(label)


def download_first(data: dict, out: Path) -> Path:
    rj = json.loads(data.get("resultJson", "{}"))
    urls = rj.get("resultUrls", [])
    if not urls:
        raise RuntimeError(f"No URLs: {rj}")
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        dl = c.get(urls[0])
        dl.raise_for_status()
        out.write_bytes(dl.content)
    return out


# ── Image generation ────────────────────────────────────────────────────────

def gen_persona_bg(prompt: str, canonical_url: str, out_path: Path) -> Path:
    """Nano Banana edit: place canonical Daniel in the prompted scene."""
    full_prompt = (
        "The subject is the SAME MAN as the reference image — a man in his "
        "early 30s. Preserve his gender, age, face, hair and identity EXACTLY. "
        "IGNORE any description of a different person, gender, or age (e.g. 'a "
        "woman', 'her', 'late 30s') if it appears in the text below; the person "
        "on camera is always this same man. " + prompt +
        " The person remains the same man from the reference image. "
        "Photo-real candid documentary photography, slight natural grain, "
        "authentic phone-camera look. No text in the image."
    )
    tid = submit_kie("google/nano-banana-edit", {
        "prompt": full_prompt[:1500],
        "image_urls": [canonical_url],
        "output_format": "png",
        "image_size": "9:16",
    })
    d = poll_task(tid, f"nano-{out_path.stem}")
    return download_first(d, out_path)


def gen_concept_bg(prompt: str, canonical_url: str, out_path: Path) -> Path:
    """Nano Banana 2 for concept slides — keep the same look-and-feel as
    persona slides but tell it the person is off-frame or background-only."""
    full_prompt = (
        "Replace the entire scene with a new image: " + prompt +
        " The same person from the reference is NOT visible in this image — "
        "this is a pure environment/concept shot. Photo-real candid documentary "
        "photography, natural light, real environment, shallow depth of field. "
        "Slight natural grain. No text in the image."
    )
    tid = submit_kie("google/nano-banana-edit", {
        "prompt": full_prompt[:1500],
        "image_urls": [canonical_url],
        "output_format": "png",
        "image_size": "9:16",
    })
    d = poll_task(tid, f"concept-{out_path.stem}")
    return download_first(d, out_path)


# ── Portrait pipeline ───────────────────────────────────────────────────────

def produce_portrait(
    *, outfit: str, recent_topics: list[str] | None = None,
    mood: str | None = None, run_dir: Path | None = None,
    topic: str | None = None, content: dict | None = None,
    recent_outfits: list[str] | None = None,
    recent_moods:   list[str] | None = None,
    recent_scenes:  list[str] | None = None,
    recent_hooks:   list[str] | None = None,
) -> dict:
    log.info("=== Producing PORTRAIT ===")
    from virtuai.tools.image_content_writer import write_portrait, write_image_caption
    from virtuai.tools.slide_renderer import render_portrait_quote

    if content is None:
        content = write_portrait(
            topic=topic, outfit=outfit, recent_topics=recent_topics, mood=mood,
            recent_outfits=recent_outfits, recent_moods=recent_moods,
            recent_scenes=recent_scenes, recent_hooks=recent_hooks,
        )
    else:
        log.info("Using Creator-authored portrait content (write_portrait skipped)")

    run_dir = run_dir or POSTS_DIR / f"portrait_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "content.json").write_text(json.dumps(content, indent=2))

    log.info("Uploading canonical face for Nano Banana...")
    canonical_url = upload_to_tmpfiles(CANONICAL_FACE)

    bg_path = run_dir / "bg.png"
    gen_persona_bg(content["image_prompt"], canonical_url, bg_path)

    final = run_dir / "portrait.png"
    render_portrait_quote(
        bg_path,
        headline=content["headline"],
        subhead=content["subhead"],
        out_path=final,
    )

    if content.get("_source") == "creator" and content.get("post_caption_long"):
        cap = content["post_caption_long"]
        captions = {"instagram": cap, "linkedin": cap,
                    "tweet": content.get("hook_summary", "")[:270],
                    "alt_text": content.get("headline", "")}
    else:
        captions = write_image_caption(content)
    (run_dir / "captions.json").write_text(json.dumps(captions, indent=2))

    log.info(f"✓ Portrait: {final}")
    return {
        "type": "portrait",
        "content": content,
        "image": str(final),
        "captions": captions,
        "run_dir": str(run_dir),
    }


# ── Carousel pipeline ───────────────────────────────────────────────────────

def produce_carousel(
    *, outfit: str, recent_topics: list[str] | None = None,
    mood: str | None = None, run_dir: Path | None = None,
    topic: str | None = None, content: dict | None = None,
    recent_outfits: list[str] | None = None,
    recent_moods:   list[str] | None = None,
    recent_scenes:  list[str] | None = None,
    recent_hooks:   list[str] | None = None,
) -> dict:
    log.info("=== Producing 5-SLIDE CAROUSEL ===")
    from virtuai.tools.image_content_writer import write_carousel, write_image_caption
    from virtuai.tools.slide_renderer import render_slide

    if content is None:
        content = write_carousel(
            topic=topic, outfit=outfit, recent_topics=recent_topics, mood=mood,
            recent_outfits=recent_outfits, recent_moods=recent_moods,
            recent_scenes=recent_scenes, recent_hooks=recent_hooks,
        )
    else:
        log.info("Using Creator-authored carousel content (write_carousel skipped)")
    slides_data = content["slides"]
    assert len(slides_data) == 5, f"expected 5 slides, got {len(slides_data)}"

    run_dir = run_dir or POSTS_DIR / f"carousel_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "content.json").write_text(json.dumps(content, indent=2))

    log.info("Uploading canonical face for persona slides...")
    canonical_url = upload_to_tmpfiles(CANONICAL_FACE)

    bg_dir = run_dir / "_bg"
    bg_dir.mkdir(exist_ok=True)

    # Generate all 5 backgrounds in parallel
    log.info("Generating 5 backgrounds in parallel...")
    def _gen(slide):
        bg_path = bg_dir / f"slide_{slide['id']}_bg.png"
        if slide.get("uses_persona"):
            return gen_persona_bg(slide["image_prompt"], canonical_url, bg_path)
        return gen_concept_bg(slide["image_prompt"], canonical_url, bg_path)

    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        bg_paths = list(ex.map(_gen, slides_data))

    # Render typography
    log.info("Rendering 5 slides with typography...")
    slide_paths = []
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
        slide_paths.append(out)

    if content.get("_source") == "creator" and content.get("post_caption_long"):
        cap = content["post_caption_long"]
        captions = {"instagram": cap, "linkedin": cap,
                    "tweet": content.get("hook_summary", "")[:270],
                    "alt_text": content.get("hook_summary", "")}
    else:
        captions = write_image_caption(content)
    (run_dir / "captions.json").write_text(json.dumps(captions, indent=2))

    log.info(f"✓ Carousel: 5 slides in {run_dir}")
    return {
        "type": "carousel",
        "content": content,
        "slides": [str(p) for p in slide_paths],
        "captions": captions,
        "run_dir": str(run_dir),
    }


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["portrait", "carousel", "both"], default="both")
    ap.add_argument("--outfit", default="navy zip-up hoodie over a white tee")
    ap.add_argument("--mood", default=None)
    args = ap.parse_args()

    t0 = time.time()
    outputs = []
    if args.mode in ("portrait", "both"):
        outputs.append(produce_portrait(outfit=args.outfit, mood=args.mood))
    if args.mode in ("carousel", "both"):
        outputs.append(produce_carousel(outfit=args.outfit, mood=args.mood))

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s")
    for o in outputs:
        log.info(f"  {o['type']}: {o['run_dir']}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
