"""
text_style.py — Measure how on-brand generated text is, vs the persona corpus.

Reuses the sentence-transformers/all-MiniLM-L6-v2 encoder we already use for
topic memory. Builds a "persona centroid" from a corpus of approved Daniel
posts and scores new candidates by cosine similarity to that centroid.

Public API:
    ts = TextStyleSimilarity()
    ts.bootstrap_from_demo_content()        # build centroid from demo_content.json
    score = ts.score("Build systems instead of trading time.")  # float in [0,1]
    report = ts.score_batch([...])

Threshold guide (empirical for MiniLM cosine sim against a small corpus):
    > 0.55  strong style match
    > 0.45  on-brand
    > 0.35  borderline
    < 0.35  off-brand — reviewer should reject
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import numpy as np

# Same OpenMP fix as topic_memory — sentence-transformers + faiss/onnxruntime
# can deadlock on Apple Silicon without these.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

ROOT = Path(__file__).resolve().parents[3]
PERSONA_DIR = ROOT / "virtuai" / "persona"
EVAL_CACHE_DIR = PERSONA_DIR / "eval" / "_cache"
CENTROID_PATH = EVAL_CACHE_DIR / "text_style_centroid.npy"
CENTROID_META_PATH = EVAL_CACHE_DIR / "text_style_centroid.json"

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class TextStyleSimilarity:
    def __init__(self):
        EVAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._encoder = None
        self._centroid: Optional[np.ndarray] = None
        self._n_corpus: int = 0
        self._try_load_cached_centroid()

    def _ensure_encoder(self):
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer
            self._encoder = SentenceTransformer(EMBED_MODEL, device="cpu")
        return self._encoder

    def _try_load_cached_centroid(self) -> bool:
        if CENTROID_PATH.exists() and CENTROID_META_PATH.exists():
            self._centroid = np.load(CENTROID_PATH)
            meta = json.loads(CENTROID_META_PATH.read_text(encoding="utf-8"))
            self._n_corpus = meta.get("n_corpus", 0)
            return True
        return False

    def _embed(self, texts: list[str]) -> np.ndarray:
        encoder = self._ensure_encoder()
        return np.asarray(
            encoder.encode(texts, normalize_embeddings=True),
            dtype=np.float32,
        )

    def bootstrap_from_corpus(self, texts: list[str]) -> int:
        """Build the centroid from an explicit list of approved posts."""
        if not texts:
            raise ValueError("Cannot bootstrap from empty corpus")
        embeddings = self._embed(texts)
        centroid = embeddings.mean(axis=0)
        # Re-normalize so cosine sim stays in [-1,1] cleanly
        centroid /= np.linalg.norm(centroid) + 1e-8
        self._centroid = centroid.astype(np.float32)
        self._n_corpus = len(texts)
        np.save(CENTROID_PATH, self._centroid)
        CENTROID_META_PATH.write_text(
            json.dumps(
                {
                    "model": EMBED_MODEL,
                    "embedding_dim": int(self._centroid.shape[0]),
                    "n_corpus": self._n_corpus,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return self._n_corpus

    def bootstrap_from_demo_content(self) -> int:
        """Read virtuai/data/demo_content.json and build the centroid from it."""
        demo_path = ROOT / "virtuai" / "data" / "demo_content.json"
        if not demo_path.exists():
            raise FileNotFoundError(f"No demo content at {demo_path}")
        data = json.loads(demo_path.read_text(encoding="utf-8"))
        posts = data if isinstance(data, list) else data.get("posts", [])
        texts: list[str] = []
        for p in posts:
            if not isinstance(p, dict):
                continue
            t = p.get("content") or p.get("text") or p.get("caption") or p.get("body") or ""
            if t and isinstance(t, str):
                texts.append(t)
        if not texts:
            raise RuntimeError("demo_content.json had no extractable post text")
        return self.bootstrap_from_corpus(texts)

    def score(self, text: str) -> dict:
        """Cosine similarity of `text` to the centroid."""
        if self._centroid is None:
            raise RuntimeError("Call bootstrap_from_corpus() or bootstrap_from_demo_content() first")
        emb = self._embed([text])[0]
        sim = float(np.dot(emb, self._centroid))
        return {
            "text": text[:120] + ("..." if len(text) > 120 else ""),
            "similarity": round(max(sim, 0.0), 4),
            "verdict": (
                "strong" if sim >= 0.55
                else "on_brand" if sim >= 0.45
                else "borderline" if sim >= 0.35
                else "off_brand"
            ),
        }

    def score_batch(self, texts: list[str]) -> dict:
        """Score a list of texts and return aggregate stats + per-text rows."""
        if self._centroid is None:
            raise RuntimeError("Bootstrap first")
        if not texts:
            return {"n": 0}
        embs = self._embed(texts)
        sims = embs @ self._centroid
        rows = [
            {
                "text": t[:120] + ("..." if len(t) > 120 else ""),
                "similarity": round(float(max(s, 0.0)), 4),
            }
            for t, s in zip(texts, sims.tolist())
        ]
        sims_arr = np.array(sims)
        return {
            "n": len(texts),
            "n_corpus": self._n_corpus,
            "mean_similarity": round(float(sims_arr.mean()), 4),
            "median_similarity": round(float(np.median(sims_arr)), 4),
            "min_similarity": round(float(sims_arr.min()), 4),
            "max_similarity": round(float(sims_arr.max()), 4),
            "stdev_similarity": round(float(sims_arr.std()), 4),
            "rows": rows,
        }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse
    p = argparse.ArgumentParser(description="VirtuAI text style consistency evaluator")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap", help="Build centroid from demo_content.json")
    p_score = sub.add_parser("score", help="Score a single piece of text")
    p_score.add_argument("text")

    args = p.parse_args()
    ts = TextStyleSimilarity()

    if args.cmd == "bootstrap":
        n = ts.bootstrap_from_demo_content()
        print(f"Centroid built from {n} posts → {CENTROID_PATH}")
    elif args.cmd == "score":
        print(json.dumps(ts.score(args.text), indent=2))


if __name__ == "__main__":
    _cli()
