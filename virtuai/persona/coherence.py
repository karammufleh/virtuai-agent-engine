"""
coherence.py — Pre-publish gate that checks scene description ↔ post topic match.

The Visual Agent picks a scene description from a pool (e.g. "moody studio
portrait, dark navy backdrop, dramatic side lighting") and feeds it to the
Z-Image-Turbo + Daniel LoRA. The face stays locked. But there's no automatic
guarantee that the scene's mood matches the topic's tone — you can end up
with a moody dark studio shot on a hopeful "compounding" post, or a cheerful
sunlit office on a tough-love "stop chasing motivation" post.

Originally we asked LLaVA to look at the rendered image. Two problems:
  1. mlx-vlm 0.4.4 crashes on our PNGs with `'int' and 'NoneType'` on `//`.
  2. The information that controls mood is the scene STRING we picked — we
     already know it. Asking a vision model to re-read what we wrote is slower
     and indirect.

So we ask the Phi-3.5-mini text backend whether the scene description fits the
topic + post text. Same result, no vision model required.

Public API:
    result = check_scene_coherence(scene, topic, post_text)
    # → CoherenceResult(decision='COHERENT'|'BORDERLINE'|'MISMATCH', reason=..., raw=...)

If you have an image and want backwards-compat with the old API, pass the
scene through `check_image_coherence(image_path, topic, post_text, scene=...)`.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BACKEND = os.environ.get("VIRTUAI_BACKEND", "http://localhost:8765")


@dataclass
class CoherenceResult:
    decision: str          # 'COHERENT' | 'BORDERLINE' | 'MISMATCH' | 'UNKNOWN'
    reason: str
    raw: str               # full model response for debugging

    def passes(self, *, allow_borderline: bool = True) -> bool:
        if self.decision == "COHERENT":
            return True
        if self.decision == "BORDERLINE" and allow_borderline:
            return True
        return False

    def to_dict(self) -> dict:
        return {"decision": self.decision, "reason": self.reason, "raw": self.raw}


COHERENCE_PROMPT_TEMPLATE = """\
You are evaluating whether a visual scene description fits the tone of a social media post.

POST TOPIC: {topic}
POST FIRST LINE: "{first_line}"
IMAGE SCENE DESCRIPTION: {scene}

Does the visual mood, lighting, and setting described above support the post's
emotional tone? Identity (the person's face) is locked separately and not
relevant — focus only on whether the scene FEELS RIGHT for this topic.

Respond in EXACTLY this format and nothing else:

VERDICT: COHERENT
REASON: one sentence

(use COHERENT if the scene fits well, BORDERLINE if it kind-of-fits, or
MISMATCH if the scene works AGAINST the topic's tone)
"""


def _first_line(text: str, *, max_chars: int = 200) -> str:
    """Pick the first sentence/line of the post text."""
    text = text.strip()
    nl = text.find("\n")
    pd = text.find(". ")
    candidates = [c for c in (nl, pd) if c > 0]
    cut = min(candidates) if candidates else len(text)
    out = text[:cut].strip().rstrip(".") + ("." if cut < len(text) else "")
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0] + "..."
    return out


def _post(endpoint: str, payload: dict, timeout: float = 120.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BACKEND}{endpoint}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read()[:300].decode('utf-8', errors='replace')}"}
    except Exception as e:
        return {"_error": str(e)}


def _parse_verdict(raw: str) -> tuple[str, str]:
    decision = "UNKNOWN"
    reason = ""
    for line in raw.splitlines():
        s = line.strip()
        upper = s.upper()
        if upper.startswith("VERDICT"):
            tail = s.split(":", 1)[-1].strip().upper()
            for tok in ("COHERENT", "BORDERLINE", "MISMATCH"):
                if tok in tail:
                    decision = tok
                    break
        elif upper.startswith("REASON"):
            reason = s.split(":", 1)[-1].strip()

    if decision == "UNKNOWN":
        upper = raw.upper()
        for tok in ("MISMATCH", "BORDERLINE", "COHERENT"):
            if tok in upper:
                decision = tok
                break
    if not reason:
        for line in raw.splitlines():
            s = line.strip()
            if s and "VERDICT" not in s.upper():
                reason = s
                break
    return decision, reason or raw.strip()[:160]


def check_scene_coherence(scene: str, topic: str, post_text: str,
                          *, platform: str | None = None) -> CoherenceResult:
    """Ask Phi whether this scene description fits the topic's vibe."""
    prompt = COHERENCE_PROMPT_TEMPLATE.format(
        topic=topic.strip(),
        first_line=_first_line(post_text),
        scene=scene.strip(),
    )

    payload = {
        "prompt": prompt,
        "max_tokens": 120,
        "temperature": 0.2,  # crisp judgement, not creative
        "system": (
            "You are a brand-coherence reviewer. Be strict. "
            "If the visual mood would clash with or distract from the post's "
            "emotional tone, mark it MISMATCH. If it strongly supports the "
            "post, mark it COHERENT. Otherwise BORDERLINE."
        ),
    }
    if platform:
        payload["platform"] = platform

    res = _post("/generate", payload, timeout=120)
    if "_error" in res:
        return CoherenceResult("UNKNOWN", res["_error"], "")
    raw = (res.get("content") or "").strip()
    decision, reason = _parse_verdict(raw)
    return CoherenceResult(decision=decision, reason=reason, raw=raw)


# Backwards-compat shim so callers that still pass image_path keep working.
def check_image_coherence(image_path: Path | str, topic: str, post_text: str,
                          *, scene: str | None = None,
                          platform: str | None = None,
                          max_tokens: int = 200) -> CoherenceResult:
    """
    Compatibility wrapper. If `scene` is provided, runs the (preferred) text
    check. If not, returns SKIPPED (we removed LLaVA-based checks because of a
    bug in mlx-vlm 0.4.4).
    """
    if scene:
        return check_scene_coherence(scene, topic, post_text, platform=platform)
    return CoherenceResult(
        "SKIPPED",
        "no scene supplied; image-only coherence check is disabled",
        "",
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Check scene-text coherence via Phi")
    p.add_argument("--scene", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--platform", default=None)
    args = p.parse_args()
    result = check_scene_coherence(args.scene, args.topic, args.text, platform=args.platform)
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    _cli()
