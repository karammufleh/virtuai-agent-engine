"""
slide_renderer.py — Pillow-based typography overlay for slides.

Takes a background image + headline + subhead and produces a 1080×1350
(4:5 Instagram feed) PNG with professional typography.

Design system:
  - Headline: Montserrat Black, UPPERCASE, 96-130px depending on length
  - Subhead: Inter / Helvetica, 38px, slightly translucent white
  - Bottom-third dark gradient overlay for legibility
  - Slide indicator (e.g. "1 / 5") top-right, small monospace
  - Handle (@daniel.calder) bottom-left, small, translucent

Public API:
    render_slide(bg_path, headline, subhead, out_path, slide_index, total)
    render_portrait_quote(bg_path, headline, subhead, out_path)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger("virtuai.tools.slide_renderer")

# Target canvas (4:5 Instagram feed ratio)
W, H = 1080, 1350

# Font resolution — fall back gracefully if not installed
FONT_CANDIDATES_BOLD = [
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/Library/Fonts/Montserrat-Black.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
FONT_CANDIDATES_REGULAR = [
    "/System/Library/Fonts/Supplemental/InterMedium.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
]
FONT_CANDIDATES_MONO = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.dfont",
    "/System/Library/Fonts/Courier.ttc",
]


def _load_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _fit_canvas(bg: Image.Image) -> Image.Image:
    """Cover-fit the background to W×H."""
    bw, bh = bg.size
    target_ratio = W / H
    src_ratio = bw / bh
    if src_ratio > target_ratio:
        # Source is wider — scale by height
        new_h = H
        new_w = int(bw * (H / bh))
    else:
        new_w = W
        new_h = int(bh * (W / bw))
    bg = bg.resize((new_w, new_h), Image.LANCZOS)
    # Center crop
    left = (new_w - W) // 2
    top = (new_h - H) // 2
    return bg.crop((left, top, left + W, top + H))


def _add_bottom_gradient(img: Image.Image, height_frac: float = 0.55, opacity: int = 220) -> Image.Image:
    """Dark gradient from transparent at top to ~opacity-black at bottom."""
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gh = int(H * height_frac)
    for y in range(gh):
        # Quadratic ease so the strong darkness lives near the bottom
        t = y / gh
        alpha = int(opacity * (t ** 1.7))
        for x in range(W):
            grad.putpixel((x, H - gh + y), (0, 0, 0, alpha))
    # Faster gradient via numpy-style trick — use draw rectangles per row
    return Image.alpha_composite(img.convert("RGBA"), grad)


def _add_bottom_gradient_fast(img: Image.Image,
                              height_frac: float = 0.55,
                              opacity: int = 220) -> Image.Image:
    """Faster gradient using row-fill instead of per-pixel."""
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(grad)
    gh = int(H * height_frac)
    for y in range(gh):
        t = y / gh
        alpha = int(opacity * (t ** 1.7))
        draw.line([(0, H - gh + y), (W, H - gh + y)], fill=(0, 0, 0, alpha))
    return Image.alpha_composite(img.convert("RGBA"), grad)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy wrap by words to fit within max_width pixels."""
    words = text.split()
    if not words:
        return []
    lines = []
    current = words[0]
    for w in words[1:]:
        trial = f"{current} {w}"
        bbox = font.getbbox(trial)
        if bbox[2] - bbox[0] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = w
    lines.append(current)
    return lines


def _fit_headline_size(text: str, max_width: int, max_lines: int = 3) -> int:
    """Pick the largest font size that fits headline in max_lines."""
    for size in (130, 118, 108, 98, 88, 78, 68, 60, 54):
        f = _load_font(FONT_CANDIDATES_BOLD, size)
        lines = _wrap_text(text, f, max_width)
        if len(lines) <= max_lines:
            return size
    return 54


def _draw_text_block(
    img: Image.Image,
    *,
    headline: str,
    subhead: str,
    bottom_pad: int = 70,
    side_pad: int = 70,
    headline_color=(255, 255, 255, 255),
    subhead_color=(255, 255, 255, 200),
) -> None:
    """Draw headline + subhead anchored to the bottom of the canvas."""
    draw = ImageDraw.Draw(img)
    max_width = W - 2 * side_pad

    # Headline auto-size
    head_size = _fit_headline_size(headline.upper(), max_width)
    head_font = _load_font(FONT_CANDIDATES_BOLD, head_size)
    head_lines = _wrap_text(headline.upper(), head_font, max_width)

    sub_size = 38
    sub_font = _load_font(FONT_CANDIDATES_REGULAR, sub_size)
    sub_lines = _wrap_text(subhead, sub_font, max_width)

    line_gap_head = int(head_size * 0.12)
    line_gap_sub = int(sub_size * 0.20)
    block_gap = 30

    head_h = len(head_lines) * head_size + max(0, len(head_lines) - 1) * line_gap_head
    sub_h = len(sub_lines) * sub_size + max(0, len(sub_lines) - 1) * line_gap_sub
    total_h = head_h + block_gap + sub_h

    y = H - bottom_pad - total_h
    # Draw headline
    for line in head_lines:
        bbox = head_font.getbbox(line)
        line_w = bbox[2] - bbox[0]
        x = side_pad  # left-aligned
        # Soft drop shadow
        draw.text((x + 3, y + 4), line, font=head_font, fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=head_font, fill=headline_color)
        y += head_size + line_gap_head

    y = y - line_gap_head + block_gap
    # Subhead
    for line in sub_lines:
        draw.text((side_pad + 2, y + 2), line, font=sub_font, fill=(0, 0, 0, 160))
        draw.text((side_pad, y), line, font=sub_font, fill=subhead_color)
        y += sub_size + line_gap_sub


def _draw_chrome(img: Image.Image, slide_index: Optional[int], total: Optional[int],
                 handle: str = "@daniel.calder") -> None:
    draw = ImageDraw.Draw(img)
    # Slide indicator top-right
    if slide_index is not None and total is not None:
        f = _load_font(FONT_CANDIDATES_MONO, 28)
        label = f"{slide_index:02d} / {total:02d}"
        bbox = f.getbbox(label)
        lw = bbox[2] - bbox[0]
        draw.text((W - lw - 50 + 1, 51), label, font=f, fill=(0, 0, 0, 180))
        draw.text((W - lw - 50, 50), label, font=f, fill=(255, 255, 255, 220))

    # Handle bottom-left
    f = _load_font(FONT_CANDIDATES_REGULAR, 24)
    draw.text((51, H - 51), handle, font=f, fill=(0, 0, 0, 160))
    draw.text((50, H - 52), handle, font=f, fill=(255, 255, 255, 180))


# ── Public API ──────────────────────────────────────────────────────────────

def render_slide(
    bg_path: str | Path,
    *,
    headline: str,
    subhead: str,
    out_path: str | Path,
    slide_index: int,
    total: int,
    handle: str = "@daniel.calder",
) -> Path:
    """Render one 1080×1350 carousel slide."""
    bg = Image.open(bg_path).convert("RGB")
    canvas = _fit_canvas(bg)
    canvas = _add_bottom_gradient_fast(canvas)
    _draw_text_block(canvas, headline=headline, subhead=subhead)
    _draw_chrome(canvas, slide_index, total, handle=handle)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    logger.info(f"Slide → {out.name}")
    return out


def render_portrait_quote(
    bg_path: str | Path,
    *,
    headline: str,
    subhead: str,
    out_path: str | Path,
    handle: str = "@daniel.calder",
) -> Path:
    """Render a single portrait quote post (1080×1350)."""
    bg = Image.open(bg_path).convert("RGB")
    canvas = _fit_canvas(bg)
    canvas = _add_bottom_gradient_fast(canvas, height_frac=0.50, opacity=210)
    _draw_text_block(canvas, headline=headline, subhead=subhead)
    _draw_chrome(canvas, None, None, handle=handle)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    logger.info(f"Portrait → {out.name}")
    return out
