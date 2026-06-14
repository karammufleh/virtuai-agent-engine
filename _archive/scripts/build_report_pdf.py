"""
build_report_pdf.py — Compile CAPSTONE.md + CHALLENGES.md into a single
academically-styled PDF using markdown-it-py for parsing and headless Chrome
for layout/print.

Output: virtuai_capstone_report.pdf at the project root.

Usage:
    python scripts/build_report_pdf.py
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPSTONE_MD = ROOT / "CAPSTONE.md"
CHALLENGES_MD = ROOT / "CHALLENGES.md"
OUTPUT_PDF = ROOT / "virtuai_capstone_report.pdf"

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# ── HTML template with academic styling ─────────────────────────────────────
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{
    size: A4;
    margin: 22mm 20mm 22mm 20mm;
    @bottom-center {{
      content: counter(page) " / " counter(pages);
      font-family: "Georgia", serif;
      font-size: 9pt;
      color: #666;
    }}
  }}
  html {{
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  body {{
    font-family: "Georgia", "Times New Roman", serif;
    font-size: 11pt;
    line-height: 1.55;
    color: #1a1a1a;
    max-width: none;
    margin: 0;
    padding: 0;
  }}
  .titlepage {{
    text-align: center;
    margin: 0;
    padding: 30mm 0 30mm 0;
    page-break-after: always;
  }}
  .titlepage h1 {{
    font-size: 30pt;
    margin: 60mm 0 6mm 0;
    color: #0a2540;
    font-weight: 700;
    letter-spacing: -0.5px;
  }}
  .titlepage .subtitle {{
    font-size: 14pt;
    color: #444;
    font-style: italic;
    margin: 0 0 30mm 0;
  }}
  .titlepage .meta {{
    font-size: 10pt;
    color: #666;
    line-height: 1.8;
  }}
  h1 {{
    font-size: 20pt;
    color: #0a2540;
    border-bottom: 2px solid #0a2540;
    padding-bottom: 4px;
    margin-top: 0;
    margin-bottom: 14pt;
    page-break-before: always;
    page-break-after: avoid;
  }}
  h1:first-of-type {{
    page-break-before: auto;
  }}
  h2 {{
    font-size: 14pt;
    color: #0a2540;
    margin-top: 22pt;
    margin-bottom: 8pt;
    page-break-after: avoid;
  }}
  h3 {{
    font-size: 12pt;
    color: #1a3a5c;
    margin-top: 16pt;
    margin-bottom: 6pt;
    page-break-after: avoid;
  }}
  h4 {{
    font-size: 11pt;
    color: #1a3a5c;
    font-style: italic;
    margin-top: 12pt;
    margin-bottom: 4pt;
    page-break-after: avoid;
  }}
  p {{
    margin: 0 0 8pt 0;
    text-align: justify;
    orphans: 3;
    widows: 3;
  }}
  ul, ol {{
    margin: 6pt 0 8pt 0;
    padding-left: 22pt;
  }}
  li {{
    margin-bottom: 3pt;
    text-align: justify;
  }}
  code {{
    font-family: "Menlo", "Consolas", monospace;
    font-size: 9.5pt;
    background: #f4f4f8;
    padding: 1pt 4pt;
    border-radius: 2pt;
    color: #c0392b;
  }}
  pre {{
    background: #f4f4f8;
    border-left: 3px solid #0a2540;
    padding: 8pt 12pt;
    overflow-x: auto;
    page-break-inside: avoid;
    border-radius: 2pt;
    margin: 8pt 0;
  }}
  pre code {{
    background: none;
    padding: 0;
    color: #1a1a1a;
    font-size: 9pt;
    white-space: pre-wrap;
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 10pt 0;
    font-size: 9.5pt;
    page-break-inside: avoid;
  }}
  th, td {{
    border: 1px solid #c0c0c0;
    padding: 5pt 7pt;
    text-align: left;
    vertical-align: top;
  }}
  th {{
    background: #0a2540;
    color: white;
    font-weight: 600;
  }}
  tr:nth-child(even) td {{
    background: #f4f6f9;
  }}
  blockquote {{
    border-left: 3px solid #999;
    color: #555;
    margin: 8pt 0;
    padding: 4pt 0 4pt 12pt;
    font-style: italic;
  }}
  a {{
    color: #1a4d8c;
    text-decoration: none;
  }}
  hr {{
    border: none;
    border-top: 1px solid #d0d0d0;
    margin: 18pt 0;
  }}
  .toc {{
    page-break-after: always;
    padding-top: 10mm;
  }}
  .toc h1 {{
    border: none;
    page-break-before: auto;
  }}
  .toc ol {{
    list-style: decimal;
    line-height: 2;
  }}
  .toc a {{
    color: #1a3a5c;
  }}
  strong {{
    color: #0a2540;
  }}
</style>
</head>
<body>

<div class="titlepage">
  <h1>{title}</h1>
  <div class="subtitle">An Autonomous Multi-Modal Persona Generation System</div>
  <div class="meta">
    Capstone Report &mdash; AI Engineering<br>
    {date}
  </div>
</div>

<div class="toc">
<h1>Table of Contents</h1>
{toc}
</div>

{body}

</body>
</html>
"""


def render_markdown(md_text: str) -> str:
    """Convert markdown text to HTML using markdown-it-py with sensible defaults."""
    from markdown_it import MarkdownIt
    md = (
        MarkdownIt("commonmark", {"html": True, "linkify": True, "typographer": True})
        .enable("table")
        .enable("strikethrough")
    )
    return md.render(md_text)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    return re.sub(r"\s+", "-", s)[:60]


def add_heading_ids(html: str) -> tuple[str, list[tuple[int, str, str]]]:
    """
    Inject id="" anchors into <h1>/<h2> tags and return a flat TOC list.
    Returns (modified_html, [(level, id, text), ...]).
    """
    toc: list[tuple[int, str, str]] = []
    pattern = re.compile(r"<h([12])>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)

    def repl(m: re.Match) -> str:
        level = int(m.group(1))
        text = re.sub(r"<.*?>", "", m.group(2)).strip()
        slug = slugify(text)
        # Avoid id collisions
        original_slug = slug
        i = 2
        existing_slugs = {t[1] for t in toc}
        while slug in existing_slugs:
            slug = f"{original_slug}-{i}"
            i += 1
        toc.append((level, slug, text))
        return f'<h{level} id="{slug}">{m.group(2)}</h{level}>'

    return pattern.sub(repl, html), toc


def build_toc_html(toc: list[tuple[int, str, str]]) -> str:
    """Render a clean nested TOC in HTML."""
    if not toc:
        return ""
    lines: list[str] = ["<ol>"]
    inside_sub = False
    for level, slug, text in toc:
        if level == 1:
            if inside_sub:
                lines.append("</ol></li>")
                inside_sub = False
            lines.append(f'<li><a href="#{slug}">{text}</a>')
        elif level == 2:
            if not inside_sub:
                lines.append("<ol>")
                inside_sub = True
            lines.append(f'<li><a href="#{slug}">{text}</a></li>')
    if inside_sub:
        lines.append("</ol></li>")
    # Close any open level-1 items
    open_li = sum(1 for ln in lines if ln.startswith("<li>")) - sum(1 for ln in lines if ln.startswith("</li>") or "</ol></li>" in ln)
    lines.append("</li>" * max(open_li, 0))
    lines.append("</ol>")
    return "\n".join(lines)


def main() -> None:
    if not CAPSTONE_MD.exists():
        sys.exit(f"missing: {CAPSTONE_MD}")
    if not CHALLENGES_MD.exists():
        sys.exit(f"missing: {CHALLENGES_MD}")
    if not Path(CHROME).exists():
        sys.exit(f"Chrome not found at {CHROME}")

    capstone = CAPSTONE_MD.read_text(encoding="utf-8")
    challenges = CHALLENGES_MD.read_text(encoding="utf-8")

    # Strip the first H1 from each so we can inject our own chapter headers.
    def strip_first_h1(md: str) -> tuple[str, str]:
        lines = md.splitlines()
        title = ""
        out: list[str] = []
        stripped = False
        for ln in lines:
            if not stripped and ln.startswith("# "):
                title = ln[2:].strip()
                stripped = True
                continue
            out.append(ln)
        return title, "\n".join(out).lstrip("\n")

    capstone_title, capstone_body = strip_first_h1(capstone)
    challenges_title, challenges_body = strip_first_h1(challenges)

    # Compose the full document
    composed = (
        f"# {capstone_title or 'VirtuAI — Capstone Final Report'}\n\n"
        f"{capstone_body}\n\n"
        f"---\n\n"
        f"# {challenges_title or 'Challenges'}\n\n"
        f"{challenges_body}\n"
    )

    # Render markdown -> HTML
    body_html = render_markdown(composed)
    body_html_with_ids, toc_entries = add_heading_ids(body_html)
    toc_html = build_toc_html(toc_entries)

    # Title for the cover page comes from CAPSTONE.md's first H1
    cover_title = capstone_title or "VirtuAI Capstone Report"

    full_html = HTML_TEMPLATE.format(
        title=cover_title,
        date=datetime.now().strftime("%B %Y"),
        toc=toc_html,
        body=body_html_with_ids,
    )

    # Write HTML to a temp file, then print to PDF via Chrome headless
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_html = Path(tmp_dir) / "report.html"
        tmp_html.write_text(full_html, encoding="utf-8")

        cmd = [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            "--virtual-time-budget=8000",
            f"--print-to-pdf={OUTPUT_PDF.resolve()}",
            f"file://{tmp_html.resolve()}",
        ]
        print(f"[chrome] rendering PDF → {OUTPUT_PDF.relative_to(ROOT)}")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        # Chrome prints status to stderr; only treat genuine non-zero as failure
        if not OUTPUT_PDF.exists():
            sys.stderr.write(proc.stderr)
            sys.exit("Chrome did not produce a PDF")

    size_kb = OUTPUT_PDF.stat().st_size / 1024
    print(f"\n✓ {OUTPUT_PDF}\n  {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
