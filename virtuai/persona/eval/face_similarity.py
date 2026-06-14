"""
face_similarity.py — Measure how consistent generated images are with Daniel's locked face.

Uses InsightFace's ArcFace model (buffalo_l, 512-dim embeddings) downloaded
on first use. Each face image → unit-normalized 512-dim vector → cosine
similarity with the reference embedding bank built from the training dataset.

Public API:
    fs = FaceSimilarity()
    fs.bootstrap_reference()                       # one-time: embed all 30 training photos
    score = fs.score("path/to/generated_face.png")  # float in [0,1], higher = more like Daniel
    report = fs.score_directory("virtuai/data/generated_images/")

Threshold guide (empirical for ArcFace cosine sim):
    > 0.65  strong identity match (clearly same person)
    > 0.45  acceptable identity match
    > 0.30  weak match (likely different person but similar features)
    < 0.30  identity drift — LoRA is failing
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

# Same OpenMP fix as topic_memory — insightface + onnxruntime can deadlock
# at import time on Apple Silicon without these.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

ROOT = Path(__file__).resolve().parents[3]
PERSONA_DIR = ROOT / "virtuai" / "persona"
TRAINING_DIR = PERSONA_DIR / "face_dataset"
EVAL_CACHE_DIR = PERSONA_DIR / "eval" / "_cache"
REFERENCE_BANK_PATH = EVAL_CACHE_DIR / "daniel_reference_bank.npy"
REFERENCE_META_PATH = EVAL_CACHE_DIR / "daniel_reference_bank.json"

ARC_MODEL = "buffalo_l"  # InsightFace's standard ArcFace bundle (~280 MB on first download)


@dataclass
class FaceScore:
    image_path: str
    similarity: float           # cosine similarity to mean reference embedding, [0,1]
    nearest_reference_sim: float  # max single-image sim from reference bank
    n_faces_detected: int       # 0 = no face found in image (reported as 0.0)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "image_path": self.image_path,
            "similarity": round(self.similarity, 4),
            "nearest_reference_sim": round(self.nearest_reference_sim, 4),
            "n_faces_detected": self.n_faces_detected,
            "note": self.note,
        }


class FaceSimilarity:
    def __init__(self, *, lazy: bool = True):
        EVAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._app = None  # InsightFace FaceAnalysis app
        self._reference_embeddings: Optional[np.ndarray] = None
        self._reference_mean: Optional[np.ndarray] = None
        self._reference_files: list[str] = []
        if not lazy:
            self._ensure_app()
            self._load_reference_bank()
        else:
            # Try to lazy-load reference bank without spinning up insightface
            self._try_load_cached_bank()

    def _ensure_app(self):
        if self._app is not None:
            return
        from insightface.app import FaceAnalysis
        # `det_size` controls the detector resolution — 640 is the InsightFace default.
        self._app = FaceAnalysis(name=ARC_MODEL, providers=["CPUExecutionProvider"])
        self._app.prepare(ctx_id=0, det_size=(640, 640))

    def _try_load_cached_bank(self) -> bool:
        if REFERENCE_BANK_PATH.exists() and REFERENCE_META_PATH.exists():
            self._reference_embeddings = np.load(REFERENCE_BANK_PATH)
            meta = json.loads(REFERENCE_META_PATH.read_text(encoding="utf-8"))
            self._reference_files = meta.get("files", [])
            self._reference_mean = self._reference_embeddings.mean(axis=0)
            self._reference_mean /= np.linalg.norm(self._reference_mean) + 1e-8
            return True
        return False

    def _load_reference_bank(self) -> None:
        if not self._try_load_cached_bank():
            self.bootstrap_reference()

    @staticmethod
    def _normalize(v: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(v)
        return v if n == 0 else v / n

    def _embed_image(self, image_path: Path) -> tuple[Optional[np.ndarray], int]:
        """Returns (unit_embedding, num_faces_detected) for the dominant face."""
        import cv2
        self._ensure_app()
        img = cv2.imread(str(image_path))
        if img is None:
            return None, 0
        faces = self._app.get(img)
        if not faces:
            return None, 0
        # Pick the largest face by bounding box area
        faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
        embedding = faces[0].normed_embedding  # already unit-normalized
        return embedding.astype(np.float32), len(faces)

    def bootstrap_reference(self, source_dir: Path = TRAINING_DIR) -> int:
        """Embed every PNG/JPEG in `source_dir` into the reference bank."""
        self._ensure_app()
        EVAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(p for p in source_dir.iterdir()
                       if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
        embeddings: list[np.ndarray] = []
        used_files: list[str] = []
        for f in files:
            emb, n = self._embed_image(f)
            if emb is not None:
                embeddings.append(emb)
                used_files.append(f.name)
        if not embeddings:
            raise RuntimeError(f"No faces detected in {source_dir}")
        bank = np.stack(embeddings, axis=0)
        np.save(REFERENCE_BANK_PATH, bank)
        REFERENCE_META_PATH.write_text(
            json.dumps(
                {
                    "model": ARC_MODEL,
                    "embedding_dim": bank.shape[1],
                    "n_references": len(used_files),
                    "files": used_files,
                    "source_dir": str(source_dir),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self._reference_embeddings = bank
        self._reference_files = used_files
        self._reference_mean = self._normalize(bank.mean(axis=0))
        return len(used_files)

    def score(self, image_path: str | Path) -> FaceScore:
        """Score a single image against the reference bank."""
        if self._reference_embeddings is None:
            self._load_reference_bank()
        path = Path(image_path)
        if not path.exists():
            return FaceScore(str(path), 0.0, 0.0, 0, "image_not_found")
        emb, n = self._embed_image(path)
        if emb is None:
            return FaceScore(str(path), 0.0, 0.0, 0, "no_face_detected")

        # Cosine sim to the mean (overall identity)
        mean_sim = float(np.dot(emb, self._reference_mean))
        # Max cosine sim to any single reference image (most-similar pose match)
        all_sims = self._reference_embeddings @ emb
        max_sim = float(all_sims.max())

        return FaceScore(
            image_path=str(path),
            similarity=max(mean_sim, 0.0),  # clamp tiny negatives from FP error
            nearest_reference_sim=max_sim,
            n_faces_detected=n,
        )

    def score_directory(
        self,
        directory: str | Path,
        *,
        extensions: tuple[str, ...] = (".png", ".jpg", ".jpeg"),
        recursive: bool = False,
    ) -> dict:
        """Score every image in a directory. Returns aggregate stats + per-image scores."""
        path = Path(directory)
        files = (path.rglob("*") if recursive else path.iterdir())
        scores: list[FaceScore] = []
        for f in files:
            if not f.is_file() or f.suffix.lower() not in extensions:
                continue
            scores.append(self.score(f))

        scored = [s for s in scores if s.n_faces_detected > 0]
        if not scored:
            return {"n_images": len(scores), "n_with_face": 0, "scores": [s.to_dict() for s in scores]}
        sims = np.array([s.similarity for s in scored])
        return {
            "n_images": len(scores),
            "n_with_face": len(scored),
            "mean_similarity": round(float(sims.mean()), 4),
            "median_similarity": round(float(np.median(sims)), 4),
            "min_similarity": round(float(sims.min()), 4),
            "max_similarity": round(float(sims.max()), 4),
            "stdev_similarity": round(float(sims.std()), 4),
            "above_strong_threshold_0.65": int((sims >= 0.65).sum()),
            "above_acceptable_threshold_0.45": int((sims >= 0.45).sum()),
            "below_drift_threshold_0.30": int((sims < 0.30).sum()),
            "scores": [s.to_dict() for s in scores],
        }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse
    p = argparse.ArgumentParser(description="VirtuAI face consistency evaluator")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap", help="Build the Daniel reference bank from training images")
    p_score = sub.add_parser("score", help="Score one image against the reference")
    p_score.add_argument("image")
    p_dir = sub.add_parser("score-dir", help="Score every image in a directory")
    p_dir.add_argument("directory")
    p_dir.add_argument("--recursive", action="store_true")

    args = p.parse_args()
    fs = FaceSimilarity(lazy=True)

    if args.cmd == "bootstrap":
        n = fs.bootstrap_reference()
        print(f"Reference bank built: {n} embeddings → {REFERENCE_BANK_PATH}")
    elif args.cmd == "score":
        result = fs.score(args.image)
        print(json.dumps(result.to_dict(), indent=2))
    elif args.cmd == "score-dir":
        result = fs.score_directory(args.directory, recursive=args.recursive)
        # Hide per-image dump for big runs
        per_image = result.pop("scores", [])
        print(json.dumps(result, indent=2))
        if len(per_image) <= 20:
            for s in per_image:
                print(f"  {s['similarity']:.3f}  {s['image_path']}  ({s['n_faces_detected']} faces)")


if __name__ == "__main__":
    _cli()
