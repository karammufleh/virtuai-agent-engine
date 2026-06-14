"""
topic_memory.py — Local FAISS index of past posts for anti-repetition.

Embeds every published post into a 384-dim vector via sentence-transformers
(all-MiniLM-L6-v2) and stores them in a FAISS IndexFlatIP. Before generating
a new post, the Strategy Agent calls `is_novel(candidate_topic)` — if cosine
similarity with any of the last N posts exceeds NOVELTY_THRESHOLD, the topic
is rejected and the agent retries with a different angle.

Why local FAISS instead of Pinecone?
  - We have ~100 posts target. FAISS handles millions trivially on CPU.
  - Zero external dependencies, no API keys, no monthly bill.
  - File-backed (pickle + .index) — survives restarts, no DB to run.

Why all-MiniLM-L6-v2 specifically?
  - 384-dim, ~22 MB model, runs on CPU in <50ms per encode.
  - Best quality-vs-size tradeoff for short-text similarity.
  - Standard baseline — easy to defend in the capstone writeup.

Public API:
  TopicMemory.add(post_id, text, metadata={...})
  TopicMemory.is_novel(text) -> (is_novel: bool, max_similarity: float, nearest_post_id: str | None)
  TopicMemory.search(text, k=5) -> [(post_id, similarity, metadata), ...]
  TopicMemory.size() -> int
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np

# macOS arm64 stability fixes — these MUST be set before torch/faiss/transformers import:
#   - TOKENIZERS_PARALLELISM=false  → sentence-transformers leaks multiprocessing
#     semaphores on shutdown, which segfault the next process to import it.
#   - OMP_NUM_THREADS=1             → faiss-cpu and torch both bring their own
#     OpenMP runtime; concurrent calls into both segfault on Apple Silicon.
#     Single-threaded BLAS is plenty fast for our embedding scale.
#   - KMP_DUPLICATE_LIB_OK=TRUE     → belt-and-suspenders for the same conflict.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX_DIR = ROOT / "virtuai" / "persona" / "topic_memory_data"

EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384

# Cosine similarity threshold above which a candidate is considered too
# similar to a past post and gets rejected. 0.85 is empirical for MiniLM —
# above 0.85 means substantively the same topic, below 0.85 is "related but
# distinct". Tunable per project.
NOVELTY_THRESHOLD = 0.85


@dataclass
class PostRecord:
    post_id: str
    text: str
    metadata: dict


class TopicMemory:
    """
    Thread-safe FAISS-backed memory of past posts.

    Embeddings are L2-normalized so IndexFlatIP returns cosine similarity
    in [-1, 1]. We use IP (inner product) instead of L2 because cosine is
    the semantically correct metric for sentence embeddings.
    """

    def __init__(self, index_dir: Path = DEFAULT_INDEX_DIR):
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.index_dir / "topic.index"
        self.records_path = self.index_dir / "records.json"
        # Legacy pickle path — read once if present, then we never write to it again.
        self._legacy_pickle_path = self.index_dir / "records.pkl"

        self._lock = threading.Lock()
        self._encoder = None  # lazy
        self._index = None    # lazy
        self._records: list[PostRecord] = []
        self._load()

    # ── Lazy loaders ──────────────────────────────────────────────────────

    def _ensure_encoder(self):
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer
            self._encoder = SentenceTransformer(EMBED_MODEL_NAME, device="cpu")
        return self._encoder

    def _ensure_index(self):
        if self._index is None:
            import faiss
            self._index = faiss.IndexFlatIP(EMBED_DIM)
        return self._index

    # ── Persistence ───────────────────────────────────────────────────────

    def _load(self):
        # Prefer JSON; fall back to legacy pickle once and rewrite as JSON.
        if self.records_path.exists():
            data = json.loads(self.records_path.read_text(encoding="utf-8"))
            self._records = [PostRecord(**r) for r in data.get("records", [])]
        elif self._legacy_pickle_path.exists():
            # Read pickle by injecting PostRecord into the unpickle namespace.
            # This handles both module-qualified ("virtuai.persona.topic_memory")
            # and bare ("__main__") pickled records from old bootstrap runs.
            import pickle
            class _Unpickler(pickle.Unpickler):
                def find_class(self, module, name):
                    if name == "PostRecord":
                        return PostRecord
                    return super().find_class(module, name)
            with open(self._legacy_pickle_path, "rb") as f:
                self._records = _Unpickler(f).load()
            # Migrate immediately so we never go through pickle again.
            self._save_records_json()
            try:
                self._legacy_pickle_path.unlink()
            except OSError:
                pass
        if self.index_path.exists():
            import faiss
            self._index = faiss.read_index(str(self.index_path))

    def _save_records_json(self):
        self.records_path.write_text(
            json.dumps(
                {"records": [asdict(r) for r in self._records]},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _save(self):
        import faiss
        if self._index is not None:
            faiss.write_index(self._index, str(self.index_path))
        self._save_records_json()

    # ── Embedding ─────────────────────────────────────────────────────────

    def _embed(self, text: str) -> np.ndarray:
        encoder = self._ensure_encoder()
        v = encoder.encode([text], normalize_embeddings=True)
        return np.asarray(v, dtype=np.float32)

    # ── Public API ────────────────────────────────────────────────────────

    def add(self, post_id: str, text: str, metadata: Optional[dict] = None) -> None:
        with self._lock:
            if any(r.post_id == post_id for r in self._records):
                raise ValueError(f"post_id already in memory: {post_id}")
            vec = self._embed(text)
            idx = self._ensure_index()
            idx.add(vec)
            self._records.append(PostRecord(
                post_id=post_id,
                text=text,
                metadata=metadata or {},
            ))
            self._save()

    def is_novel(self, text: str, threshold: float = NOVELTY_THRESHOLD) -> tuple[bool, float, Optional[str]]:
        """
        Returns (is_novel, max_similarity, nearest_post_id).
        is_novel == True iff max_similarity < threshold.
        """
        with self._lock:
            if not self._records:
                return True, 0.0, None
            vec = self._embed(text)
            idx = self._ensure_index()
            sims, indices = idx.search(vec, 1)
            max_sim = float(sims[0][0])
            nearest_idx = int(indices[0][0])
            nearest = self._records[nearest_idx].post_id
            return max_sim < threshold, max_sim, nearest

    def search(self, text: str, k: int = 5) -> list[tuple[str, float, dict]]:
        """Return top-k most similar past posts as (post_id, similarity, metadata)."""
        with self._lock:
            if not self._records:
                return []
            vec = self._embed(text)
            idx = self._ensure_index()
            k = min(k, len(self._records))
            sims, indices = idx.search(vec, k)
            return [
                (self._records[int(i)].post_id, float(s), self._records[int(i)].metadata)
                for s, i in zip(sims[0], indices[0])
                if i >= 0
            ]

    def size(self) -> int:
        return len(self._records)

    def all_post_ids(self) -> list[str]:
        return [r.post_id for r in self._records]

    def clear(self) -> None:
        """Wipe the index. Intended for tests/re-bootstrap only."""
        with self._lock:
            self._records = []
            self._index = None
            if self.index_path.exists():
                self.index_path.unlink()
            if self.records_path.exists():
                self.records_path.unlink()


# ── Singleton accessor ───────────────────────────────────────────────────

_singleton: Optional[TopicMemory] = None


def get_topic_memory() -> TopicMemory:
    global _singleton
    if _singleton is None:
        _singleton = TopicMemory()
    return _singleton


# ── Bootstrap from existing demo_content.json ────────────────────────────

def bootstrap_from_demo_content() -> int:
    """
    One-shot: ingest every post in virtuai/data/demo_content.json into the
    topic memory so the Strategy Agent can dedup against existing content.
    Returns the number of posts added.
    """
    demo_path = ROOT / "virtuai" / "data" / "demo_content.json"
    if not demo_path.exists():
        print(f"No demo content found at {demo_path}")
        return 0

    data = json.loads(demo_path.read_text(encoding="utf-8"))
    posts = data if isinstance(data, list) else data.get("posts", [])

    memory = get_topic_memory()
    added = 0
    skipped = 0
    for i, post in enumerate(posts):
        if not isinstance(post, dict):
            continue
        # Try common content-bearing keys
        text = (post.get("content")
                or post.get("text")
                or post.get("caption")
                or post.get("body")
                or "")
        if not text or not isinstance(text, str):
            continue
        post_id = str(post.get("id") or post.get("post_id") or f"demo_{i}")
        if post_id in memory.all_post_ids():
            skipped += 1
            continue
        try:
            memory.add(post_id, text, metadata={
                "platform": post.get("platform"),
                "topic": post.get("topic"),
                "batch": post.get("batch"),
                "source": "demo_content.json",
            })
            added += 1
        except ValueError:
            skipped += 1

    print(f"Bootstrapped topic memory: +{added} added, {skipped} skipped, {memory.size()} total")
    return added


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "bootstrap":
        bootstrap_from_demo_content()
    elif len(sys.argv) > 1 and sys.argv[1] == "size":
        print(get_topic_memory().size())
    elif len(sys.argv) > 1 and sys.argv[1] == "check":
        text = " ".join(sys.argv[2:])
        if not text:
            sys.exit("Usage: python topic_memory.py check <text>")
        is_novel, sim, nearest = get_topic_memory().is_novel(text)
        verdict = "NOVEL" if is_novel else "TOO SIMILAR"
        print(f"{verdict}  (max_sim={sim:.3f}, nearest={nearest})")
    else:
        print("Usage:")
        print("  python topic_memory.py bootstrap            # ingest demo_content.json")
        print("  python topic_memory.py size                 # count posts in memory")
        print("  python topic_memory.py check <text>         # novelty check")
