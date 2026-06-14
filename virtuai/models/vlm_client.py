"""
vlm_client.py — HTTP client for the VirtuAI local VLM backend.

Used by all CrewAI tools to talk to the local FastAPI inference server.
Provides clean Python functions that mirror the backend endpoints.

Backend must be running at http://localhost:8765 before the pipeline starts.
Start it with: python run_backend.py
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("virtuai.vlm_client")

BACKEND_URL = "http://localhost:8765"
TIMEOUT = 180.0  # seconds — inference on CPU/GPU can be slow


class VLMClient:
    """Synchronous HTTP client for the VirtuAI local VLM backend."""

    def __init__(self, base_url: str = BACKEND_URL, timeout: float = TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, endpoint: str, payload: dict, timeout: Optional[float] = None) -> dict:
        """POST request with error handling. Per-call timeout overrides client default."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            with httpx.Client(timeout=effective_timeout) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.ConnectError:
            raise RuntimeError(
                f"Cannot connect to VirtuAI backend at {self.base_url}. "
                "Make sure the backend is running: python run_backend.py"
            )
        except httpx.TimeoutException:
            raise RuntimeError(
                f"Request to {url} timed out after {effective_timeout}s. "
                "Consider increasing the timeout or using a smaller model."
            )
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Backend error {e.response.status_code}: {e.response.text}")

    def health(self) -> dict:
        """Check if the backend is running and return model info."""
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/health")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        platform: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        """
        Generate text content using the fine-tuned VLM.

        Args:
            prompt: The instruction or user query.
            system: Optional system prompt override (uses VirtuAI default if None).
            platform: Target platform (linkedin, x, instagram, tiktok, youtube_shorts, medium).
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative).

        Returns:
            Generated text string.
        """
        payload = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        if platform:
            payload["platform"] = platform

        result = self._post("/generate", payload)
        return result["content"]

    def analyze_image(
        self,
        image_path: str | Path,
        prompt: str,
        platform: Optional[str] = None,
        max_tokens: int = 400,
    ) -> str:
        """
        Analyze an image using the VLM's vision capabilities.

        Args:
            image_path: Path to the image file (PNG/JPEG).
            prompt: What to analyze or ask about the image.
            platform: Platform context for caption generation.
            max_tokens: Maximum tokens to generate.

        Returns:
            Image analysis / caption as a string.
        """
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "image_b64": image_b64,
            "prompt": prompt,
            "max_tokens": max_tokens,
        }
        if platform:
            payload["platform"] = platform

        result = self._post("/analyze-image", payload)
        return result["analysis"]

    def analyze_image_bytes(
        self,
        image_bytes: bytes,
        prompt: str,
        platform: Optional[str] = None,
        max_tokens: int = 400,
    ) -> str:
        """
        Analyze image bytes directly (no file needed).

        Args:
            image_bytes: Raw image bytes.
            prompt: What to analyze.
            platform: Platform context.

        Returns:
            Analysis string.
        """
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        payload = {
            "image_b64": image_b64,
            "prompt": prompt,
            "max_tokens": max_tokens,
        }
        if platform:
            payload["platform"] = platform

        result = self._post("/analyze-image", payload)
        return result["analysis"]

    def analyze_sentiment(self, text: str) -> dict:
        """
        Analyze sentiment and VirtuAI persona match.

        Returns dict with: sentiment, confidence, tone, energy_level,
                           persona_match, issues.
        """
        result = self._post("/analyze-sentiment", {"text": text})
        return result

    def safety_check(self, content: str, platform: Optional[str] = None) -> dict:
        """
        Content safety gate.

        Returns dict with: decision (APPROVE/REVISE/BLOCK),
                           safety_score, issues, reasoning.
        """
        payload = {"content": content}
        if platform:
            payload["platform"] = platform
        return self._post("/safety-check", payload)

    def generate_image(
        self,
        prompt: str,
        platform: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        steps: int = 4,
        seed: Optional[int] = None,
    ) -> dict:
        """
        Generate an image using FLUX.1-schnell via mflux.

        Returns dict with: image_path, prompt_used, model,
                           generation_time_ms, width, height.
        """
        payload = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "steps": steps,
        }
        if platform:
            payload["platform"] = platform
        if seed is not None:
            payload["seed"] = seed
        return self._post("/generate-image", payload)

    def generate_video(
        self,
        prompts: list[str],
        platform: Optional[str] = None,
        fps: int = 1,
        duration_per_frame: float = 3.0,
        width: int = 1024,
        height: int = 1024,
        add_text_overlay: bool = True,
    ) -> dict:
        """
        Generate a video from a sequence of image prompts.

        Returns dict with: video_path, frame_paths, total_frames,
                           duration_seconds, generation_time_ms.
        """
        payload = {
            "prompts": prompts,
            "fps": fps,
            "duration_per_frame": duration_per_frame,
            "width": width,
            "height": height,
            "add_text_overlay": add_text_overlay,
        }
        if platform:
            payload["platform"] = platform
        return self._post("/generate-video", payload)

    def generate_voice(
        self,
        text: str,
        speed: float = 1.0,
        seed: int = 42,
        nfe_step: int = 32,
    ) -> dict:
        """
        Generate Daniel Calder's voice speaking `text` via F5-TTS.
        Returns dict with: audio_path, duration_s, sample_rate, generation_time_ms.
        """
        return self._post("/generate-voice", {
            "text": text,
            "speed": speed,
            "seed": seed,
            "nfe_step": nfe_step,
        })

    def generate_talking_head(
        self,
        text: Optional[str] = None,
        audio_path: Optional[str] = None,
        source_image: Optional[str] = None,
        size: int = 256,
        preprocess: str = "full",
        still: bool = False,
        enhancer: Optional[str] = None,
        cpu: bool = True,
    ) -> dict:
        """
        Generate a Daniel Calder talking-head video.
        Provide either `text` (will be voiced first) or a pre-generated `audio_path`.
        Returns dict with: video_path, audio_path, duration_s, generation_time_ms,
                            source_image, model.
        """
        if (text is None) == (audio_path is None):
            raise ValueError("Provide exactly one of `text` or `audio_path`")
        payload: dict = {
            "size": size,
            "preprocess": preprocess,
            "still": still,
            "cpu": cpu,
        }
        if text is not None:
            payload["text"] = text
        if audio_path is not None:
            payload["audio_path"] = audio_path
        if source_image is not None:
            payload["source_image"] = source_image
        if enhancer is not None:
            payload["enhancer"] = enhancer
        # Talking-head can take 5+ minutes for a longer clip; bump timeout.
        return self._post("/generate-talking-head", payload, timeout=1800)

    def is_ready(self) -> bool:
        """Return True if backend is up and model is loaded."""
        health = self.health()
        return health.get("status") == "ok"


# ── Module-level singleton ────────────────────────────────────────────────────
_client: Optional[VLMClient] = None


def get_client() -> VLMClient:
    """Get (or create) the module-level VLM client singleton."""
    global _client
    if _client is None:
        _client = VLMClient()
    return _client


def check_backend() -> None:
    """
    Verify the backend is running. Raises RuntimeError with clear instructions if not.
    Call this at pipeline startup.
    """
    client = get_client()
    health = client.health()
    if health.get("status") != "ok":
        raise RuntimeError(
            "\n"
            "═══════════════════════════════════════════════════════════\n"
            "  VirtuAI Backend is NOT running!\n"
            "\n"
            "  Start it first:\n"
            "    python run_backend.py\n"
            "\n"
            "  Then re-run the pipeline in a separate terminal.\n"
            "═══════════════════════════════════════════════════════════"
        )
    logger.info(
        f"Backend connected — model: {health.get('model', 'unknown')} "
        f"| vision: {health.get('vision_capable', False)}"
    )
