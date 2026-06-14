"""
run_consistency_report.py — Compose the persona consistency dashboard.

Bootstraps both metrics (face + text) if not already cached, then runs them
across all generated assets and writes a JSON + markdown report to:
    virtuai/persona/eval/_reports/consistency_<timestamp>.{json,md}

Usage:
    python -m virtuai.persona.eval.run_consistency_report
    python -m virtuai.persona.eval.run_consistency_report --bootstrap
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from virtuai.persona.eval.face_similarity import FaceSimilarity
from virtuai.persona.eval.text_style import TextStyleSimilarity

ROOT = Path(__file__).resolve().parents[3]
PERSONA_DIR = ROOT / "virtuai" / "persona"
GENERATED_IMAGES_DIR = ROOT / "virtuai" / "data" / "generated_images"
DEMO_CONTENT = ROOT / "virtuai" / "data" / "demo_content.json"
REPORT_DIR = PERSONA_DIR / "eval" / "_reports"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true",
                        help="Force-rebuild face reference bank + text centroid even if cached")
    parser.add_argument("--images-dir", default=str(GENERATED_IMAGES_DIR),
                        help="Directory of generated face images to score")
    args = parser.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    report: dict = {"timestamp": timestamp}

    # ── Face metric ─────────────────────────────────────────────────────────
    print("\n[face] initializing…")
    fs = FaceSimilarity(lazy=True)
    if args.bootstrap or fs._reference_embeddings is None:
        print("[face] bootstrapping reference bank from training images…")
        n = fs.bootstrap_reference()
        print(f"[face] reference bank: {n} embeddings")
    else:
        print(f"[face] using cached bank ({len(fs._reference_files)} embeddings)")

    images_dir = Path(args.images_dir)
    if images_dir.exists():
        print(f"[face] scoring images in {images_dir}…")
        face_report = fs.score_directory(images_dir, recursive=False)
        report["face"] = face_report
    else:
        report["face"] = {"error": f"images dir not found: {images_dir}"}

    # ── Text metric ─────────────────────────────────────────────────────────
    print("\n[text] initializing…")
    ts = TextStyleSimilarity()
    if args.bootstrap or ts._centroid is None:
        if DEMO_CONTENT.exists():
            print("[text] bootstrapping centroid from demo_content.json…")
            n = ts.bootstrap_from_demo_content()
            print(f"[text] centroid built from {n} posts")
        else:
            report["text"] = {"error": "demo_content.json missing — cannot bootstrap centroid"}
    else:
        print(f"[text] using cached centroid (corpus size {ts._n_corpus})")

    if ts._centroid is not None and DEMO_CONTENT.exists():
        # Score the corpus against itself as a baseline (sanity check), and
        # surface the variance — this tells us how tight the persona corpus is.
        data = json.loads(DEMO_CONTENT.read_text(encoding="utf-8"))
        posts = data if isinstance(data, list) else data.get("posts", [])
        texts = []
        for p in posts:
            if isinstance(p, dict):
                t = p.get("content") or p.get("text") or p.get("caption") or p.get("body") or ""
                if t:
                    texts.append(t)
        if texts:
            print(f"[text] scoring {len(texts)} corpus posts vs centroid (self-similarity baseline)…")
            corpus_self = ts.score_batch(texts)
            report["text_corpus_self_similarity"] = {
                "n": corpus_self["n"],
                "mean": corpus_self["mean_similarity"],
                "median": corpus_self["median_similarity"],
                "min": corpus_self["min_similarity"],
                "max": corpus_self["max_similarity"],
                "stdev": corpus_self["stdev_similarity"],
            }

    # ── Write reports ───────────────────────────────────────────────────────
    json_path = REPORT_DIR / f"consistency_{timestamp}.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md = _format_markdown(report)
    md_path = REPORT_DIR / f"consistency_{timestamp}.md"
    md_path.write_text(md, encoding="utf-8")

    print(f"\n✓ Report written:\n  {json_path}\n  {md_path}\n")
    print(md)


def _format_markdown(report: dict) -> str:
    lines = [f"# VirtuAI Persona Consistency Report — {report['timestamp']}\n"]

    f = report.get("face", {})
    lines.append("## Face Similarity (ArcFace, buffalo_l)\n")
    if "error" in f:
        lines.append(f"_skipped — {f['error']}_\n")
    elif f.get("n_with_face", 0) == 0:
        lines.append(f"No faces detected in {f.get('n_images', 0)} images.\n")
    else:
        lines.append(f"- Images scored: **{f['n_images']}** ({f['n_with_face']} with detectable face)")
        lines.append(f"- Mean similarity to Daniel: **{f['mean_similarity']:.3f}**")
        lines.append(f"- Median: {f['median_similarity']:.3f}, range "
                     f"[{f['min_similarity']:.3f}, {f['max_similarity']:.3f}], σ={f['stdev_similarity']:.3f}")
        lines.append(f"- Strong match (≥0.65): **{f['above_strong_threshold_0.65']}** images")
        lines.append(f"- Acceptable (≥0.45): {f['above_acceptable_threshold_0.45']} images")
        lines.append(f"- Identity drift (<0.30): **{f['below_drift_threshold_0.30']}** images\n")

    t = report.get("text_corpus_self_similarity", {})
    lines.append("## Text Style Consistency (sentence-transformers/all-MiniLM-L6-v2)\n")
    if not t:
        lines.append("_skipped — no text corpus or centroid available_\n")
    else:
        lines.append(f"- Corpus posts: **{t['n']}**")
        lines.append(f"- Mean self-similarity to centroid: **{t['mean']:.3f}**")
        lines.append(f"- Median: {t['median']:.3f}, range "
                     f"[{t['min']:.3f}, {t['max']:.3f}], σ={t['stdev']:.3f}")
        lines.append("")
        lines.append("_Interpretation: high mean + low stdev = tight, consistent voice. "
                     "If stdev exceeds 0.15 the corpus is too varied to use as a style anchor._\n")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
