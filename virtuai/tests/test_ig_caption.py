"""
Regression tests for the Instagram caption builder.

After the 2026-05-20 fix, Instagram captions are short-form (≈ 200-500
chars) instead of essay-length. LinkedIn captions keep the long form.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from virtuai.tools.image_content_writer import (
    _build_ig_caption,
    write_image_caption,
)


_LONG_CAPTION = (
    "I fired my VA after building one Zap.\n\n"
    "She was good at her job. That wasn't the problem.\n\n"
    "The problem was $1,800/mo for tasks that looked like work but were "
    "actually just data movement. Form submission comes in → copy it into "
    "a spreadsheet → send a Slack message to the right person. Same "
    "sequence, every single time, thirty times a week.\n\n"
    "That's not a job. That's a script waiting to be written.\n\n"
    "---\n\n"
    "The reframe that changed how I staff every project now:\n\n"
    "Repetition isn't skill.\n\n"
    "If a task runs the same way every week, what looked like \"good "
    "execution\" is actually pattern recognition that a fifteen-line "
    "Zap can replicate for $20/mo flat.\n\n"
    "The math: $1,800/mo human × 12 months = $21,600/yr. Zap = $240/yr. "
    "Difference = $21,360 that goes into hiring someone who does work a "
    "script CAN'T."
)


_CONTENT = {
    "post_caption_long": _LONG_CAPTION,
    "hashtags":          ["automation", "zapier", "aiinbusiness",
                           "founders", "operationsdesign"],
    "hook_summary":      "I fired my $1,800/mo VA after building one Zap.",
    "slides": [
        {"headline": "I fired my VA after one Zap."},
        {"headline": "$1,800/mo for data movement."},
        {"headline": "Repetition isn't skill."},
        {"headline": "Zap = $240/yr. Difference = $21,360."},
        {"headline": "Automate the job before you post it."},
    ],
}


# ────────────────────────────────────────────────────────────────────────────

def test_ig_caption_is_short():
    out = _build_ig_caption(_CONTENT, _LONG_CAPTION,
                             hashtags="#a #b #c #d")
    assert len(out) <= 800, f"too long: {len(out)} chars"
    assert len(out) >= 60, f"suspiciously short: {len(out)} chars"


def test_ig_caption_starts_with_hook():
    out = _build_ig_caption(_CONTENT, _LONG_CAPTION, hashtags="")
    assert out.startswith("I fired my VA after building one Zap.")


def test_ig_caption_ends_with_hashtags():
    out = _build_ig_caption(_CONTENT, _LONG_CAPTION,
                             hashtags="#a #b #c")
    assert out.rstrip().endswith("#a #b #c")


def test_ig_caption_appends_slide5_aphorism():
    """The slide-5 quotable headline should land between body and hashtags."""
    out = _build_ig_caption(_CONTENT, _LONG_CAPTION, hashtags="#a")
    assert '"Automate the job before you post it."' in out


def test_ig_caption_skips_horizontal_rule_paragraph():
    """The '---' paragraph in the long caption must NOT appear."""
    out = _build_ig_caption(_CONTENT, _LONG_CAPTION, hashtags="")
    assert "---" not in out


def test_write_image_caption_keeps_linkedin_long():
    """LinkedIn variant stays the full essay; only IG shortens.

    We assert LinkedIn is meaningfully longer than IG rather than a fixed
    absolute count — real-world long captions are 2000+ chars; the test
    fixture is shorter on purpose so the suite stays fast.
    """
    caps = write_image_caption(_CONTENT)
    assert len(caps["instagram"]) <= 800
    # LinkedIn must keep the full-form caption + hashtags, not the IG short form.
    assert len(caps["linkedin"]) >= int(len(caps["instagram"]) * 1.5), (
        f"linkedin caption was not meaningfully longer than IG: "
        f"li={len(caps['linkedin'])} ig={len(caps['instagram'])}"
    )


def test_write_image_caption_tweet_under_280():
    caps = write_image_caption(_CONTENT)
    assert len(caps["tweet"]) <= 280


def test_handles_empty_long_caption():
    out = _build_ig_caption({"slides": []}, "", hashtags="#x")
    assert "#x" in out


def test_handles_missing_slides():
    """No slides → no closing aphorism, but still works."""
    content_no_slides = {"post_caption_long": _LONG_CAPTION,
                          "hashtags": ["a"]}
    out = _build_ig_caption(content_no_slides, _LONG_CAPTION, hashtags="#a")
    assert "#a" in out
    # No aphorism appended (since slides missing)
    assert '"Automate the job' not in out


def test_ig_caption_never_exceeds_2200():
    """IG hard cap safety net."""
    huge = ("very long paragraph " * 1000)
    out = _build_ig_caption({"slides": []}, huge, hashtags="#" + "a" * 100)
    assert len(out) <= 2200
