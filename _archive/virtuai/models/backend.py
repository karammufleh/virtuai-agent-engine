"""
backend.py — Local Multi-Model Inference Server for VirtuAI

Multi-model architecture (all loaded/called locally on Apple Silicon):
  1. Phi-3.5-mini-instruct  (4-bit, fine-tuned via LoRA) — text generation, sentiment, safety
  2. LLaVA 1.5 7B           (mlx-vlm, 4-bit)             — vision + language image analysis
  3. Z-Image-Turbo          (mflux, 4-bit + persona LoRA) — face-locked image generation
  4. F5-TTS v1 Base         (zero-shot voice cloning)     — Daniel Calder voice
  5. SadTalker V0.0.2       (isolated venv subprocess)    — talking-head video
  6. moviepy fallback       (legacy slideshow video)

Endpoints:
  POST /generate              — Text generation (Phi-3.5-mini fine-tuned)
  POST /generate-image        — Image generation (Z-Image-Turbo + Daniel Calder LoRA)
  POST /generate-voice        — Voice cloning (F5-TTS, locked reference clip)
  POST /generate-talking-head — Daniel speaks: text → F5-TTS → SadTalker → mp4
  POST /generate-video        — Legacy slideshow placeholder
  POST /analyze-image         — Vision + language analysis (LLaVA)
  POST /analyze-sentiment     — Tone/sentiment analysis (Phi fine-tuned)
  POST /safety-check          — Content safety gate (Phi fine-tuned)
  GET  /health                — Health check + model info

Start with:  python run_backend.py
"""

from __future__ import annotations

import os
import sys
import base64
import json
import time
import logging
import subprocess
import shutil
from pathlib import Path
from typing import Optional

# F5-TTS uses torchaudio → torchcodec → libavutil. Brew default ffmpeg is v8
# (libavutil.60); torchcodec needs FFmpeg ≤7 (libavutil.59). If ffmpeg@7 is
# installed keg-only, prepend it to DYLD_FALLBACK_LIBRARY_PATH and re-exec
# so dyld finds it before torchcodec is loaded.
_FFMPEG7_LIB = "/opt/homebrew/opt/ffmpeg@7/lib"
if Path(_FFMPEG7_LIB).is_dir():
    _existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if _FFMPEG7_LIB not in _existing.split(":"):
        _new_env = os.environ.copy()
        _new_env["DYLD_FALLBACK_LIBRARY_PATH"] = (
            f"{_FFMPEG7_LIB}:{_existing}" if _existing else _FFMPEG7_LIB
        )
        os.execvpe(sys.executable, [sys.executable] + sys.argv, _new_env)

_venv_bin = str(Path(sys.executable).parent)
if _venv_bin not in os.environ.get("PATH", "").split(":"):
    os.environ["PATH"] = f"{_venv_bin}:{os.environ.get('PATH', '')}"

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("virtuai.backend")

# ── Model paths ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.parent
FUSED_MODEL_PATH = ROOT / "virtuai" / "models" / "finetune" / "fused_model"
ADAPTER_PATH     = ROOT / "virtuai" / "models" / "finetune" / "adapters"

# Text model: Phi-3.5-mini-instruct (LoRA fine-tuned on persona data, fused)
# Falls back to the base Phi-3.5-mini model if no fused/adapter weights are present
TEXT_BASE_MODEL  = "mlx-community/Phi-3.5-mini-instruct-4bit"

# Vision model: LLaVA 1.5 7B via mlx-vlm (inference only, not fine-tuned)
VISION_MODEL     = "mlx-community/llava-1.5-7b-4bit"

# Image generation: Z-Image-Turbo via mflux (8-bit, LoRA-capable, training-supported).
# Switched from FLUX.1-schnell because mflux dropped LoRA training support for FLUX.1.
# Z-Image-Turbo is 4-step like schnell but produces sharper, more photoreal output and
# is the only base model the persona LoRA can train against locally.
IMAGE_MODEL      = "z-image-turbo"
IMAGE_QUANTIZE   = 8

# Persona LoRA — produced by `mflux-train --config virtuai/persona/training_config.json`.
# Loaded at inference if a checkpoint exists; otherwise we fall back to plain Z-Image-Turbo.
#
# IMPORTANT path note: mflux-train resolves the config's `output_path` relative
# to the process CWD, NOT relative to the config file. Since we run from the
# project root, mflux writes to `<root>/training_runs/` (or `training_runs_<ts>/`
# if the dir already exists). Checkpoints are *zip files* containing the LoRA
# safetensors, not bare safetensors. The loader below extracts the newest zip.
PERSONA_DIR             = ROOT / "virtuai" / "persona"
PERSONA_ANCHOR          = PERSONA_DIR / "persona_anchor.json"
TRAINING_RUNS_PARENT    = ROOT  # project root — mflux output_path is relative to CWD
EXTRACTED_LORA_CACHE    = PERSONA_DIR / "training_runs" / "_extracted"

# Voice clone (F5-TTS) — locked reference clip + transcript live here.
# Generated speech lands in voice_clone/generated/.
VOICE_REF_WAV   = PERSONA_DIR / "voice_sample" / "daniel_voice_ref.wav"
VOICE_REF_TXT   = PERSONA_DIR / "voice_sample" / "daniel_voice_ref_trimmed.txt"
VOICE_OUTPUT    = PERSONA_DIR / "voice_clone" / "generated"

# Talking-head video (SadTalker) — runs in an isolated venv so its 2023-era
# pinned deps (numpy 1.23, scipy 1.10, librosa 0.9) don't poison our main env.
SADTALKER_DIR     = PERSONA_DIR / "sadtalker"
SADTALKER_VENV_PY = Path("/Users/karammufleh/virtuai-sadtalker-venv/bin/python")
TALKING_HEAD_OUT  = PERSONA_DIR / "talking_head" / "generated"
DEFAULT_TH_FACE   = PERSONA_DIR / "face_dataset" / "daniel_hero.png"

IMAGES_OUTPUT    = ROOT / "virtuai" / "data" / "generated_images"
VIDEOS_OUTPUT    = ROOT / "virtuai" / "data" / "generated_videos"

# Ensure output directories exist
IMAGES_OUTPUT.mkdir(parents=True, exist_ok=True)
VIDEOS_OUTPUT.mkdir(parents=True, exist_ok=True)
VOICE_OUTPUT.mkdir(parents=True, exist_ok=True)
TALKING_HEAD_OUT.mkdir(parents=True, exist_ok=True)

# ── System prompt for all generations ────────────────────────────────────────
VIRTUAI_SYSTEM_PROMPT = """You are VirtuAI Mentor — a direct, high-energy AI business authority.
Your niche: AI in business, entrepreneurship, self-improvement.
Voice: direct, motivational, intense, no fluff.
Style: authority-driven, future-focused, short to medium sentences.
Power words: build, scale, automate, execute, dominate, unlock, compound, leverage, ship, iterate.
ALWAYS: challenge the audience, give actionable insights, use strong hooks, focus on results.
NEVER: be vague, over-explain, sound soft or uncertain, use filler language, use banned phrases.
Banned phrases: "in today's fast-paced world", "it's important to note", "game-changer",
"revolutionary", "at the end of the day", "dive deep", "let's unpack", "I'm excited to share".
Every piece of content must open with a strong hook and close with exactly one CTA."""

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="VirtuAI Local VLM Backend",
    description="Local inference server for VirtuAI using fine-tuned LLaVA (Apple Silicon / MLX)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global model state ────────────────────────────────────────────────────────
# Text model (Phi-3.5-mini fine-tuned) — handles /generate, /analyze-sentiment, /safety-check
_lm_model     = None
_lm_tokenizer = None
_lm_name      = None

# Vision model (LLaVA 1.5) — handles /analyze-image
_vlm_model     = None
_vlm_processor = None
_vlm_name      = None

# Voice clone (F5-TTS) — handles /generate-voice. Loaded lazily on first call
# because (a) it's a heavy model (~1.4 GB) and (b) voice generation is far
# less frequent than text/image generation in the typical pipeline.
_f5_model = None


def _load_text_model():
    """Load fine-tuned Phi-3.5-mini for text generation via mlx-lm."""
    global _lm_model, _lm_tokenizer, _lm_name
    from mlx_lm import load as lm_load

    # Use fused model if available (post fine-tuning), otherwise base + adapters
    if FUSED_MODEL_PATH.exists() and any(FUSED_MODEL_PATH.iterdir()):
        model_path = str(FUSED_MODEL_PATH)
        logger.info(f"Loading fused fine-tuned Phi-3.5-mini model: {model_path}")
        _lm_model, _lm_tokenizer = lm_load(model_path)
        logger.info(f"✓ Fine-tuned Phi-3.5-mini (fused LoRA) loaded")
    elif ADAPTER_PATH.exists() and any(ADAPTER_PATH.glob("*.safetensors")):
        logger.info(f"Loading Phi-3.5-mini base + LoRA adapters...")
        _lm_model, _lm_tokenizer = lm_load(
            TEXT_BASE_MODEL, adapter_path=str(ADAPTER_PATH)
        )
        logger.info(f"✓ Phi-3.5-mini + LoRA adapters loaded")
    else:
        logger.info(f"Loading Phi-3.5-mini base model (no adapters yet — run train_lora.sh to fine-tune)")
        _lm_model, _lm_tokenizer = lm_load(TEXT_BASE_MODEL)
        logger.info(f"✓ Phi-3.5-mini base model loaded")

    _lm_name = TEXT_BASE_MODEL


def _load_vision_model():
    """Load LLaVA 1.5 for vision+language tasks via mlx-vlm."""
    global _vlm_model, _vlm_processor, _vlm_name
    try:
        from mlx_vlm import load as vlm_load
        logger.info(f"Loading LLaVA vision model: {VISION_MODEL}")
        _vlm_model, _vlm_processor = vlm_load(VISION_MODEL)
        _vlm_name = VISION_MODEL
        logger.info(f"✓ LLaVA 1.5 7B loaded (vision capability enabled)")
    except ImportError:
        logger.warning("mlx-vlm not installed — /analyze-image endpoint unavailable")
    except Exception as e:
        logger.warning(f"LLaVA load failed: {e} — /analyze-image endpoint unavailable")


def _unload_vision_model():
    """Free LLaVA from memory. Called after Visual Agent finishes."""
    global _vlm_model, _vlm_processor, _vlm_name
    if _vlm_model is not None:
        del _vlm_model
        del _vlm_processor
        _vlm_model = None
        _vlm_processor = None
        _vlm_name = None
        import gc; gc.collect()
        logger.info("LLaVA unloaded — freed ~4 GB unified memory")


def _load_models():
    """Load text model at startup. Vision model lazy-loads on first /analyze-image call."""
    logger.info("Loading text model (Phi-3.5-mini-instruct, 4-bit)...")
    _load_text_model()
    logger.info("Vision model (LLaVA 1.5 7B): DEFERRED — will lazy-load on first /analyze-image call")


# ── Request/Response schemas ──────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="The user instruction/prompt")
    system: Optional[str] = Field(None, description="System prompt override (uses VirtuAI default if not set)")
    platform: Optional[str] = Field(None, description="Target platform (linkedin, x, instagram, etc.)")
    max_tokens: int = Field(512, ge=50, le=2048)
    temperature: float = Field(0.7, ge=0.0, le=2.0)


class GenerateResponse(BaseModel):
    content: str
    model: str
    tokens_generated: int
    generation_time_ms: float


class ImageAnalysisRequest(BaseModel):
    image_b64: str = Field(..., description="Base64-encoded image (PNG/JPEG)")
    prompt: str = Field(..., description="What to analyze or ask about the image")
    platform: Optional[str] = Field(None, description="Platform context for caption generation")
    max_tokens: int = Field(400, ge=50, le=1024)


class ImageAnalysisResponse(BaseModel):
    analysis: str
    model: str
    generation_time_ms: float


class SentimentRequest(BaseModel):
    text: str = Field(..., description="Content to analyze")


class SentimentResponse(BaseModel):
    sentiment: str
    confidence: float
    tone: list[str]
    energy_level: str
    persona_match: bool
    issues: list[str]


class SafetyCheckRequest(BaseModel):
    content: str = Field(..., description="Content to safety-check")
    platform: Optional[str] = Field(None)


class SafetyCheckResponse(BaseModel):
    decision: str  # APPROVE / REVISE / BLOCK
    safety_score: float  # 0.0 (unsafe) to 1.0 (safe)
    issues: list[str]
    reasoning: str


# ── Image Generation schemas ────────────────────────────────────────────────

class ImageGenerateRequest(BaseModel):
    prompt: str = Field(..., description="Text prompt describing the image to generate")
    platform: Optional[str] = Field(None, description="Target platform (affects aspect ratio)")
    width: int = Field(1024, description="Image width in pixels")
    height: int = Field(1024, description="Image height in pixels")
    steps: int = Field(4, ge=1, le=20, description="Inference steps (4 for schnell)")
    seed: Optional[int] = Field(None, description="Random seed for reproducibility")


class ImageGenerateResponse(BaseModel):
    image_path: str
    prompt_used: str
    model: str
    generation_time_ms: float
    width: int
    height: int


# ── Video Generation schemas ────────────────────────────────────────────────

class VideoGenerateRequest(BaseModel):
    prompts: list[str] = Field(..., description="List of prompts for each frame/scene")
    platform: Optional[str] = Field(None, description="Target platform (affects aspect ratio/duration)")
    fps: int = Field(1, ge=1, le=30, description="Frames per second")
    duration_per_frame: float = Field(3.0, ge=1.0, le=10.0, description="Seconds per frame")
    width: int = Field(1024, description="Frame width")
    height: int = Field(1024, description="Frame height")
    add_text_overlay: bool = Field(True, description="Add text overlay to frames")


class VideoGenerateResponse(BaseModel):
    video_path: str
    frame_paths: list[str]
    total_frames: int
    duration_seconds: float
    generation_time_ms: float


# ── Voice Generation schemas (F5-TTS) ────────────────────────────────────────

class VoiceGenerateRequest(BaseModel):
    text: str = Field(..., description="Text to synthesize in Daniel's cloned voice")
    speed: float = Field(1.0, ge=0.5, le=2.0, description="Playback speed multiplier")
    seed: int = Field(-1, description="Random seed (-1 = random each time for prosody variety)")
    nfe_step: int = Field(48, ge=8, le=64, description="F5-TTS NFE steps (48 = quality sweet spot)")


class VoiceGenerateResponse(BaseModel):
    audio_path: str
    duration_s: float
    sample_rate: int
    generation_time_ms: float
    seed: int
    model: str


# ── Talking-Head Video schemas (SadTalker) ───────────────────────────────────

class TalkingHeadRequest(BaseModel):
    text: Optional[str] = Field(None, description="Text to speak (will be voiced via F5-TTS first). Mutually exclusive with audio_path.")
    audio_path: Optional[str] = Field(None, description="Pre-generated audio WAV. Skips TTS step.")
    source_image: Optional[str] = Field(None, description="Source face image path. Defaults to face_dataset/00.png until persona LoRA is trained.")
    size: int = Field(256, description="SadTalker output size — 256 (fast) or 512 (high quality)")
    preprocess: str = Field("full", description="SadTalker preprocess mode: crop, extcrop, resize, full, extfull")
    still: bool = Field(False, description="Still mode — minimal head motion (faster)")
    enhancer: Optional[str] = Field(None, description="Optional GFPGAN face enhancer (slower but sharper)")
    cpu: bool = Field(True, description="Force CPU. SadTalker MPS support is partial — CPU is more reliable.")


class TalkingHeadResponse(BaseModel):
    video_path: str
    audio_path: str
    duration_s: float
    generation_time_ms: float
    source_image: str
    model: str


# ── Helper: build prompt (Phi-3.5-mini instruct format) ─────────────────────

def _build_chat_prompt(system: str, user: str) -> str:
    """Build prompt in Phi-3.5-mini instruct format."""
    return f"<|system|>\n{system}<|end|>\n<|user|>\n{user}<|end|>\n<|assistant|>\n"


def _generate_text(prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> tuple[str, float]:
    """
    Run text inference via Phi-3.5-mini (fine-tuned). Returns (text, time_ms).

    Uses repetition_penalty=1.15 via logits processors to suppress the
    `<|end|><unk>` overflow that 4-bit Phi-3.5-mini occasionally exhibits
    past its EOS token on long generations.
    """
    if _lm_model is None:
        raise HTTPException(status_code=503, detail="Text model not loaded")

    from mlx_lm import generate as lm_generate
    from mlx_lm.sample_utils import make_sampler

    # Logits processors are optional in older mlx-lm versions; guard the import.
    try:
        from mlx_lm.sample_utils import make_logits_processors
        logits_processors = make_logits_processors(repetition_penalty=1.15)
    except Exception:
        logits_processors = None

    start = time.time()
    sampler = make_sampler(temp=temperature)
    gen_kwargs = dict(
        prompt=prompt,
        max_tokens=max_tokens,
        sampler=sampler,
        verbose=False,
    )
    if logits_processors is not None:
        gen_kwargs["logits_processors"] = logits_processors

    output = lm_generate(_lm_model, _lm_tokenizer, **gen_kwargs)

    # Belt-and-suspenders: strip Phi-3 chat-template tokens that occasionally
    # leak through the sampler on quantized weights.
    for tok in ("<|end|>", "<|endoftext|>", "<|user|>", "<|assistant|>", "<|system|>", "<unk>"):
        output = output.replace(tok, "")
    return output.strip(), (time.time() - start) * 1000


def _generate_with_image(image_b64: str, prompt: str, max_tokens: int = 400) -> tuple[str, float]:
    """Run vision+language inference via LLaVA 1.5. Lazy-loads on first call."""
    if _vlm_model is None:
        logger.info("Lazy-loading LLaVA for first /analyze-image call...")
        _load_vision_model()
    if _vlm_model is None:
        raise HTTPException(
            status_code=503,
            detail="Vision model (LLaVA) failed to load. Check backend logs."
        )

    from PIL import Image
    from mlx_vlm import generate as vlm_generate
    import io

    start = time.time()
    image_bytes = base64.b64decode(image_b64)
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    full_prompt = f"USER: <image>\n{prompt}\nASSISTANT:"

    output = vlm_generate(
        _vlm_model,
        _vlm_processor,
        full_prompt,
        image=image,
        max_tokens=max_tokens,
        verbose=False,
    )
    return output, (time.time() - start) * 1000


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    logger.info("=" * 60)
    logger.info("  VirtuAI Local VLM Backend — Starting Up")
    logger.info("  Text model:  Phi-3.5-mini-instruct (4-bit, fine-tuned via LoRA)")
    logger.info("  Vision model: LLaVA 1.5 7B (mlx-vlm)")
    logger.info(f"  Image model:  {IMAGE_MODEL} (mflux, {IMAGE_QUANTIZE}-bit)")
    logger.info("  Voice model:  F5-TTS v1 Base (lazy-loaded on first /generate-voice)")
    logger.info("  Video engine: moviepy (placeholder — Phase 3 swaps to LivePortrait)")
    logger.info("=" * 60)
    _load_models()
    # Check mflux availability
    if shutil.which("mflux-generate"):
        logger.info(f"  {IMAGE_MODEL} image generation: READY")
        persona_lora = _find_persona_lora()
        if persona_lora is not None:
            logger.info(f"  Persona LoRA: LOADED ({persona_lora.name})")
        else:
            logger.info("  Persona LoRA: not trained yet — run `mflux-train --config virtuai/persona/training_config.json`")
    else:
        logger.warning("  mflux not found — image generation unavailable")
    if VOICE_REF_WAV.exists() and VOICE_REF_TXT.exists():
        logger.info(f"  Voice reference: READY ({VOICE_REF_WAV.name})")
    else:
        logger.info("  Voice reference: not prepared — run prep_voice_reference.py")
    logger.info("=" * 60)
    logger.info("  Backend ready at http://localhost:8765")
    logger.info("=" * 60)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/unload-vision")
async def unload_vision():
    """Free LLaVA from memory. Call after Visual Agent finishes to reclaim ~4 GB."""
    was_loaded = _vlm_model is not None
    _unload_vision_model()
    return {"unloaded": was_loaded, "status": "ok"}


@app.get("/health")
async def health_check():
    """Health check + model info for both models."""
    # Check mflux availability
    mflux_available = shutil.which("mflux-generate") is not None
    persona_lora = _find_persona_lora()

    return {
        "status": "ok",
        "text_model": _lm_name,
        "vision_model": _vlm_name,
        "vision_capable": _vlm_model is not None,
        "image_model": f"{IMAGE_MODEL}-q{IMAGE_QUANTIZE}" if mflux_available else None,
        "image_capable": mflux_available,
        "persona_lora": str(persona_lora) if persona_lora else None,
        "persona_lora_loaded": persona_lora is not None,
        "video_capable": mflux_available,
        "voice_capable": VOICE_REF_WAV.exists() and VOICE_REF_TXT.exists(),
        "voice_model": "F5TTS_v1_Base",
        "voice_loaded": _f5_model is not None,
        "talking_head_capable": (
            SADTALKER_VENV_PY.exists()
            and (SADTALKER_DIR / "checkpoints" / "SadTalker_V0.0.2_512.safetensors").exists()
        ),
        "talking_head_model": "SadTalker_V0.0.2",
        "adapters_loaded": ADAPTER_PATH.exists() and any(ADAPTER_PATH.glob("*.safetensors")),
        "fused_model": FUSED_MODEL_PATH.exists() and any(FUSED_MODEL_PATH.iterdir()),
    }


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    """
    Generate text content using the fine-tuned Phi-3.5-mini.
    Used by Creator Agent, Research Agent, Strategy Agent.
    """
    if _lm_model is None:
        raise HTTPException(status_code=503, detail="Text model not loaded")

    system = req.system or VIRTUAI_SYSTEM_PROMPT

    # Add platform context if specified
    if req.platform:
        platform_hints = {
            "linkedin": "Format for LinkedIn: professional edge, framework-style, 3-5 hashtags at bottom.",
            "x": "Format for X/Twitter: max 280 chars per tweet, 1-2 hashtags, punchy.",
            "instagram": "Format for Instagram: hook first line, line breaks, 15-20 hashtags.",
            "tiktok": "Format for TikTok: hook in first 2 seconds, spoken delivery style.",
            "youtube_shorts": "Format for YouTube Shorts: 45 seconds max, subscribe CTA.",
            "medium": "Format for Medium: analytical, long-form, headers and structure.",
        }
        platform_hint = platform_hints.get(req.platform, "")
        if platform_hint:
            system = f"{system}\n\nPlatform context: {platform_hint}"

    prompt = _build_chat_prompt(system, req.prompt)

    try:
        content, time_ms = _generate_text(prompt, req.max_tokens, req.temperature)
        tokens = len(content.split())  # Approximate token count
        return GenerateResponse(
            content=content.strip(),
            model=_lm_name or "phi-3.5-mini",
            tokens_generated=tokens,
            generation_time_ms=round(time_ms, 1),
        )
    except Exception as e:
        logger.error(f"Generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze-image", response_model=ImageAnalysisResponse)
async def analyze_image(req: ImageAnalysisRequest):
    """
    Analyze an image using LLaVA's vision capabilities.
    Used by Visual Agent — understands images, suggests captions, checks visual brand consistency.
    """
    if _vlm_model is None:
        raise HTTPException(status_code=503, detail="Vision model (LLaVA) not loaded")

    system_context = (
        "You are the VirtuAI visual analyst. Analyze images for brand consistency with "
        "the VirtuAI aesthetic (dark, futuristic, minimal, electric blue and neon green accents). "
        "When asked to generate captions, use the VirtuAI Mentor voice: direct, high-energy, no fluff."
    )

    full_prompt = f"{system_context}\n\n{req.prompt}"
    if req.platform:
        full_prompt += f"\n\nTarget platform: {req.platform}"

    try:
        analysis, time_ms = _generate_with_image(req.image_b64, full_prompt, req.max_tokens)
        return ImageAnalysisResponse(
            analysis=analysis.strip(),
            model=_vlm_name or "llava-1.5-7b",
            generation_time_ms=round(time_ms, 1),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Image analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze-sentiment", response_model=SentimentResponse)
async def analyze_sentiment(req: SentimentRequest):
    """
    Analyze tone, sentiment, and VirtuAI persona match.
    Used by Reviewer Agent.
    """
    if _lm_model is None:
        raise HTTPException(status_code=503, detail="Text model not loaded")

    analysis_prompt = (
        f"Analyze this content for tone and persona match:\n\n\"{req.text}\"\n\n"
        "Return a JSON object with these exact keys:\n"
        "- sentiment: positive/negative/neutral\n"
        "- confidence: 0.0 to 1.0\n"
        "- tone: array of detected tones (e.g. [\"direct\", \"motivational\"])\n"
        "- energy_level: low/medium/high\n"
        "- persona_match: true/false (does it match VirtuAI Mentor — direct, no-fluff, authoritative?)\n"
        "- issues: array of specific problems found (empty array if none)\n"
        "Return ONLY valid JSON, no markdown formatting, no explanation."
    )

    system = (
        "You are a content analyst specializing in brand voice consistency. "
        "Evaluate content against the VirtuAI Mentor persona: direct, motivational, intense, no fluff, "
        "authority-driven, strong hooks, clear CTAs. Flag weak language, banned phrases, missing hooks/CTAs."
    )

    prompt = _build_chat_prompt(system, analysis_prompt)

    try:
        output, _ = _generate_text(prompt, max_tokens=300, temperature=0.1)

        # Parse JSON from output
        output = output.strip()
        # Extract JSON block if wrapped in markdown
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            # Fallback: return safe defaults
            data = {
                "sentiment": "neutral",
                "confidence": 0.5,
                "tone": ["unknown"],
                "energy_level": "medium",
                "persona_match": False,
                "issues": ["Could not parse model output — manual review recommended"],
            }

        return SentimentResponse(
            sentiment=data.get("sentiment", "neutral"),
            confidence=float(data.get("confidence", 0.5)),
            tone=data.get("tone", []),
            energy_level=data.get("energy_level", "medium"),
            persona_match=bool(data.get("persona_match", False)),
            issues=data.get("issues", []),
        )
    except Exception as e:
        logger.error(f"Sentiment analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/safety-check", response_model=SafetyCheckResponse)
async def safety_check(req: SafetyCheckRequest):
    """
    Content safety gate — Guardian Agent endpoint.
    Returns APPROVE / REVISE / BLOCK decision with reasoning.
    """
    if _lm_model is None:
        raise HTTPException(status_code=503, detail="Text model not loaded")

    safety_prompt = (
        f"Perform a content safety check on this text:\n\n\"{req.content}\"\n\n"
        "Check for:\n"
        "1. Forbidden topics: hate speech, violence, illegal activities, discrimination, "
        "   pornography, terrorism, self-harm, doxxing\n"
        "2. Restricted topics needing disclaimers: medical advice, financial advice, "
        "   political opinions\n"
        "3. False claims or fabricated statistics\n"
        "4. Manipulation tactics (fake urgency, guaranteed returns, false scarcity)\n"
        "5. Platform policy violations\n\n"
        "Return a JSON object with these keys:\n"
        "- decision: APPROVE / REVISE / BLOCK\n"
        "- safety_score: 0.0 (unsafe) to 1.0 (safe)\n"
        "- issues: array of specific issues found (empty if none)\n"
        "- reasoning: brief explanation of decision\n"
        "Return ONLY valid JSON, no markdown."
    )

    system = (
        "You are a content safety moderator. Your job is to protect the VirtuAI brand and audience. "
        "Be strict: when in doubt, BLOCK. Financial claims need disclaimers. Hate speech is always BLOCK. "
        "Missing disclaimer on financial/medical advice is REVISE, not BLOCK. "
        "Safe, clean business content is APPROVE."
    )

    prompt = _build_chat_prompt(system, safety_prompt)

    try:
        output, _ = _generate_text(prompt, max_tokens=300, temperature=0.05)
        output = output.strip()

        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            data = {
                "decision": "REVISE",
                "safety_score": 0.5,
                "issues": ["Safety check parsing failed — manual review required"],
                "reasoning": "Model output could not be parsed. Default to REVISE.",
            }

        decision = data.get("decision", "REVISE").upper()
        if decision not in ("APPROVE", "REVISE", "BLOCK"):
            decision = "REVISE"

        return SafetyCheckResponse(
            decision=decision,
            safety_score=float(data.get("safety_score", 0.5)),
            issues=data.get("issues", []),
            reasoning=data.get("reasoning", ""),
        )
    except Exception as e:
        logger.error(f"Safety check error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# Image Generation — Z-Image-Turbo + persona LoRA via mflux (runs as subprocess)
# ══════════════════════════════════════════════════════════════════════════════

def _get_platform_dimensions(platform: Optional[str]) -> tuple[int, int]:
    """Get recommended image dimensions for a platform."""
    dims = {
        "instagram": (1080, 1080),   # Square post
        "linkedin": (1200, 627),     # Landscape
        "x": (1200, 675),            # Landscape 16:9
        "tiktok": (1080, 1920),      # Portrait 9:16
        "youtube_shorts": (1080, 1920),  # Portrait 9:16
        "medium": (1400, 787),       # Wide landscape
    }
    return dims.get(platform, (1024, 1024))


def _find_persona_lora() -> Optional[Path]:
    """
    Locate the most recent persona LoRA produced by `mflux-train` and return
    the extracted .safetensors path that mflux-generate can `--lora-paths` into.

    Discovery walks every `<root>/training_runs*` directory (mflux suffixes
    each run with `_<timestamp>` if the base dir exists), finds the newest
    `*_checkpoint.zip`, and extracts its `*_adapter.safetensors` member
    into EXTRACTED_LORA_CACHE/. Cached files are reused if they're newer than
    their source zip. Stale extractions older than the parent zip are
    invalidated (handles the "training keeps producing newer checkpoints"
    case without leaking files).
    """
    import zipfile

    # Find all checkpoint zips across every training_runs* dir
    candidates: list[Path] = []
    for parent in TRAINING_RUNS_PARENT.glob("training_runs*"):
        if not parent.is_dir():
            continue
        ckpt_dir = parent / "checkpoints"
        if not ckpt_dir.is_dir():
            continue
        candidates.extend(ckpt_dir.glob("*_checkpoint.zip"))

    # Also check older bare-safetensors layout (legacy, before we knew the layout)
    legacy = list((PERSONA_DIR / "training_runs").rglob("*.safetensors"))
    if legacy and not candidates:
        return max(legacy, key=lambda p: p.stat().st_mtime)

    if not candidates:
        return None

    # Newest checkpoint zip wins
    newest_zip = max(candidates, key=lambda p: p.stat().st_mtime)

    EXTRACTED_LORA_CACHE.mkdir(parents=True, exist_ok=True)
    cached_lora = EXTRACTED_LORA_CACHE / f"{newest_zip.stem}_adapter.safetensors"

    # Use cached extraction if newer than source zip
    if (
        cached_lora.exists()
        and cached_lora.stat().st_mtime >= newest_zip.stat().st_mtime
    ):
        return cached_lora

    # Extract the LoRA member from the zip
    with zipfile.ZipFile(newest_zip) as z:
        lora_members = [n for n in z.namelist() if n.endswith("_adapter.safetensors")]
        if not lora_members:
            logger.warning(f"No lora_adapter.safetensors in {newest_zip.name}")
            return None
        member_name = lora_members[0]
        with z.open(member_name) as src, open(cached_lora, "wb") as dst:
            dst.write(src.read())

    logger.info(f"Extracted LoRA from {newest_zip.name} → {cached_lora.name}")
    return cached_lora


def _generate_image_mflux(
    prompt: str,
    output_path: str,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
    seed: Optional[int] = None,
) -> float:
    """
    Generate an image using Z-Image-Turbo via mflux CLI, with the persona
    LoRA applied if it has been trained. Returns generation time in ms.
    """
    # Z-Image-Turbo needs the dedicated CLI — the generic mflux-generate
    # loader expects a FLUX-style text_encoder_2/ subdir that doesn't exist
    # in the Tongyi-MAI/Z-Image-Turbo HF repo.
    mflux_bin = shutil.which("mflux-generate-z-image-turbo") or "mflux-generate-z-image-turbo"
    cmd = [
        mflux_bin,
        "--quantize", str(IMAGE_QUANTIZE),
        "--prompt", prompt,
        "--width", str(width),
        "--height", str(height),
        "--steps", str(steps),
        "--output", output_path,
    ]

    persona_lora = _find_persona_lora()
    if persona_lora is not None:
        cmd.extend(["--lora-paths", str(persona_lora)])
        logger.info(f"Using persona LoRA: {persona_lora.name}")

    if seed is not None:
        cmd.extend(["--seed", str(seed)])

    logger.info(f"Generating image: {width}x{height}, steps={steps}")
    start = time.time()

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,  # 5 min timeout
    )

    elapsed_ms = (time.time() - start) * 1000

    if result.returncode != 0:
        logger.error(f"mflux error: {result.stderr}")
        raise RuntimeError(f"Image generation failed: {result.stderr[:500]}")

    logger.info(f"Image generated in {elapsed_ms:.0f}ms: {output_path}")
    return elapsed_ms


@app.post("/generate-image", response_model=ImageGenerateResponse)
async def generate_image(req: ImageGenerateRequest):
    """
    Generate an image using Z-Image-Turbo + persona LoRA (local, Apple Silicon).
    Used by the Visual Agent for creating platform-specific persona-locked visuals.
    """
    # Build persona-enhanced prompt
    enhanced_prompt = (
        f"{req.prompt}. "
        "Style: dark moody background, minimal, futuristic, professional, "
        "electric blue #007AFF and neon green #00D46A accents, "
        "cinematic lighting, high contrast, tech-forward aesthetic."
    )

    # Use platform-specific dimensions if not custom
    if req.platform and req.width == 1024 and req.height == 1024:
        width, height = _get_platform_dimensions(req.platform)
    else:
        width, height = req.width, req.height

    # Generate unique filename
    timestamp = int(time.time() * 1000)
    platform_tag = req.platform or "general"
    filename = f"{platform_tag}_{timestamp}.png"
    output_path = str(IMAGES_OUTPUT / filename)

    try:
        elapsed_ms = _generate_image_mflux(
            prompt=enhanced_prompt,
            output_path=output_path,
            width=width,
            height=height,
            steps=req.steps,
            seed=req.seed,
        )

        return ImageGenerateResponse(
            image_path=output_path,
            prompt_used=enhanced_prompt,
            model=f"{IMAGE_MODEL}-q{IMAGE_QUANTIZE}",
            generation_time_ms=round(elapsed_ms, 1),
            width=width,
            height=height,
        )
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# Video Generation — image sequences + moviepy
# ══════════════════════════════════════════════════════════════════════════════

def _generate_video_from_frames(
    frame_paths: list[str],
    output_path: str,
    fps: int = 1,
    duration_per_frame: float = 3.0,
    text_overlays: list[str] | None = None,
) -> None:
    """
    Create a video from a sequence of generated images using moviepy.
    Adds smooth transitions and optional text overlays.
    """
    from moviepy import ImageClip, concatenate_videoclips, TextClip, CompositeVideoClip

    clips = []
    for i, frame_path in enumerate(frame_paths):
        clip = ImageClip(frame_path, duration=duration_per_frame)

        if text_overlays and i < len(text_overlays) and text_overlays[i]:
            txt = TextClip(
                text=text_overlays[i],
                font_size=36,
                color="white",
                font="Arial-Bold",
                stroke_color="black",
                stroke_width=2,
                size=(clip.size[0] - 80, None),
                method="caption",
            )
            txt = txt.with_position(("center", "bottom")).with_duration(duration_per_frame)
            clip = CompositeVideoClip([clip, txt])

        clips.append(clip)

    if clips:
        final = concatenate_videoclips(clips, method="compose")
        final.write_videofile(
            output_path,
            fps=max(fps, 24),
            codec="libx264",
            audio=False,
            logger=None,
        )
        # Clean up
        final.close()
        for c in clips:
            c.close()


@app.post("/generate-video", response_model=VideoGenerateResponse)
async def generate_video(req: VideoGenerateRequest):
    """
    Generate a short video by creating a sequence of images and stitching them.
    Used by the Visual Agent for TikTok, YouTube Shorts, and Instagram Reels.
    """
    if not req.prompts:
        raise HTTPException(status_code=400, detail="At least one prompt is required")

    # Use platform dimensions
    if req.platform:
        width, height = _get_platform_dimensions(req.platform)
    else:
        width, height = req.width, req.height

    timestamp = int(time.time() * 1000)
    platform_tag = req.platform or "general"
    frame_paths = []
    text_overlays = []

    start = time.time()

    try:
        # Generate each frame as an image
        for i, prompt in enumerate(req.prompts):
            enhanced_prompt = (
                f"{prompt}. "
                "Style: dark moody background, minimal, futuristic, professional, "
                "electric blue and neon green accents, cinematic lighting."
            )

            frame_filename = f"frame_{platform_tag}_{timestamp}_{i:03d}.png"
            frame_path = str(IMAGES_OUTPUT / frame_filename)

            _generate_image_mflux(
                prompt=enhanced_prompt,
                output_path=frame_path,
                width=width,
                height=height,
                steps=4,
                seed=42 + i,  # Consistent but varied seeds
            )
            frame_paths.append(frame_path)

            # Extract short overlay text from prompt
            if req.add_text_overlay:
                # Use first sentence or first 80 chars as overlay
                overlay = prompt.split(".")[0][:80]
                text_overlays.append(overlay)

        # Stitch frames into video
        video_filename = f"video_{platform_tag}_{timestamp}.mp4"
        video_path = str(VIDEOS_OUTPUT / video_filename)

        _generate_video_from_frames(
            frame_paths=frame_paths,
            output_path=video_path,
            fps=req.fps,
            duration_per_frame=req.duration_per_frame,
            text_overlays=text_overlays if req.add_text_overlay else None,
        )

        total_duration = len(req.prompts) * req.duration_per_frame
        elapsed_ms = (time.time() - start) * 1000

        return VideoGenerateResponse(
            video_path=video_path,
            frame_paths=frame_paths,
            total_frames=len(frame_paths),
            duration_seconds=total_duration,
            generation_time_ms=round(elapsed_ms, 1),
        )
    except Exception as e:
        logger.error(f"Video generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# Voice Generation — F5-TTS zero-shot cloning of Daniel Calder's voice
# ══════════════════════════════════════════════════════════════════════════════

def _load_f5_model():
    """Load F5-TTS lazily on first /generate-voice call."""
    global _f5_model
    if _f5_model is not None:
        return _f5_model

    if not VOICE_REF_WAV.exists() or not VOICE_REF_TXT.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                "Voice reference not prepared. Run "
                "virtuai/persona/scripts/prep_voice_reference.py first."
            ),
        )

    import torch
    from f5_tts.api import F5TTS

    device = "mps" if torch.backends.mps.is_available() else (
        "cuda" if torch.cuda.is_available() else "cpu"
    )
    logger.info(f"Loading F5-TTS (device={device})...")
    _f5_model = F5TTS(model="F5TTS_v1_Base", device=device)
    logger.info("✓ F5-TTS loaded")
    return _f5_model


@app.post("/generate-voice", response_model=VoiceGenerateResponse)
async def generate_voice(req: VoiceGenerateRequest):
    """
    Generate Daniel Calder's voice speaking the given text.

    Zero-shot cloning via F5-TTS, conditioned on the locked reference WAV
    + transcript at virtuai/persona/voice_sample/. Output is a 24 kHz mono
    WAV in virtuai/persona/voice_clone/generated/.

    Used by the Visual Agent to produce Daniel's voice for lip-sync and
    talking-head video generation.
    """
    f5 = _load_f5_model()

    ref_text = VOICE_REF_TXT.read_text(encoding="utf-8").strip()
    if not ref_text:
        raise HTTPException(status_code=503, detail="Reference transcript is empty")

    timestamp = int(time.time() * 1000)
    out_path = VOICE_OUTPUT / f"daniel_{timestamp}.wav"

    import random
    actual_seed = req.seed if req.seed >= 0 else random.randint(0, 2**31 - 1)

    try:
        start = time.time()
        f5.infer(
            ref_file=str(VOICE_REF_WAV),
            ref_text=ref_text,
            gen_text=req.text,
            file_wave=str(out_path),
            seed=actual_seed,
            speed=req.speed,
            nfe_step=req.nfe_step,
            remove_silence=False,
        )
        elapsed_ms = (time.time() - start) * 1000

        import soundfile as sf
        info = sf.info(str(out_path))

        return VoiceGenerateResponse(
            audio_path=str(out_path),
            duration_s=info.duration,
            sample_rate=info.samplerate,
            generation_time_ms=round(elapsed_ms, 1),
            seed=actual_seed,
            model="F5TTS_v1_Base",
        )
    except Exception as e:
        logger.error(f"Voice generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# Talking-Head Video — SadTalker via isolated venv subprocess
# Pipeline: text → F5-TTS clone → audio.wav → SadTalker(audio + face) → mp4
# ══════════════════════════════════════════════════════════════════════════════

def _run_sadtalker(
    audio_path: Path,
    source_image: Path,
    *,
    size: int = 256,
    preprocess: str = "full",
    still: bool = False,
    enhancer: Optional[str] = None,
    cpu: bool = True,
) -> Path:
    """Invoke SadTalker via the isolated venv. Returns the produced .mp4 path."""
    if not SADTALKER_VENV_PY.exists():
        raise HTTPException(
            status_code=503,
            detail=f"SadTalker venv missing: {SADTALKER_VENV_PY}",
        )
    if not (SADTALKER_DIR / "checkpoints" / "SadTalker_V0.0.2_512.safetensors").exists():
        raise HTTPException(
            status_code=503,
            detail="SadTalker checkpoints missing — see virtuai/persona/sadtalker/scripts/download_models.sh",
        )

    cmd = [
        str(SADTALKER_VENV_PY),
        "inference.py",
        "--driven_audio", str(audio_path),
        "--source_image", str(source_image),
        "--result_dir", str(TALKING_HEAD_OUT),
        "--size", str(size),
        "--preprocess", preprocess,
    ]
    if still:
        cmd.append("--still")
    if enhancer:
        cmd.extend(["--enhancer", enhancer])
    if cpu:
        cmd.append("--cpu")

    logger.info(f"[SadTalker] {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=str(SADTALKER_DIR),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        # Surface tail of stderr in the API response for debugging
        tail = (result.stderr or "")[-800:]
        raise HTTPException(
            status_code=500,
            detail=f"SadTalker exited {result.returncode}: ...{tail}",
        )

    mp4s = sorted(TALKING_HEAD_OUT.rglob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not mp4s:
        raise HTTPException(status_code=500, detail="SadTalker exited 0 but produced no .mp4")
    return mp4s[-1]


@app.post("/generate-talking-head", response_model=TalkingHeadResponse)
async def generate_talking_head(req: TalkingHeadRequest):
    """
    Generate a Daniel Calder talking-head video.

    Flow:
      1. If `text` is provided, voice-clone it via F5-TTS (POST /generate-voice).
      2. Else use `audio_path` directly (must be a WAV).
      3. Run SadTalker(audio, source_image) → mp4 in talking_head/generated/.

    SadTalker runs in an isolated venv (~/virtuai-sadtalker-venv) because its
    pinned deps conflict with our main env.
    """
    if not req.text and not req.audio_path:
        raise HTTPException(status_code=400, detail="Provide either 'text' or 'audio_path'")
    if req.text and req.audio_path:
        raise HTTPException(status_code=400, detail="Provide only one of 'text' or 'audio_path'")

    start = time.time()

    # Step 1 — produce audio
    if req.text:
        audio_resp = await generate_voice(VoiceGenerateRequest(text=req.text))
        audio_path = Path(audio_resp.audio_path)
        audio_duration = audio_resp.duration_s
    else:
        audio_path = Path(req.audio_path)
        if not audio_path.exists():
            raise HTTPException(status_code=400, detail=f"Audio not found: {audio_path}")
        import soundfile as sf
        audio_duration = sf.info(str(audio_path)).duration

    # Step 2 — pick source image
    source_image = Path(req.source_image) if req.source_image else DEFAULT_TH_FACE
    if not source_image.exists():
        raise HTTPException(status_code=400, detail=f"Source image not found: {source_image}")

    # Step 3 — animate
    mp4 = _run_sadtalker(
        audio_path=audio_path,
        source_image=source_image,
        size=req.size,
        preprocess=req.preprocess,
        still=req.still,
        enhancer=req.enhancer,
        cpu=req.cpu,
    )

    elapsed_ms = (time.time() - start) * 1000
    return TalkingHeadResponse(
        video_path=str(mp4),
        audio_path=str(audio_path),
        duration_s=audio_duration,
        generation_time_ms=round(elapsed_ms, 1),
        source_image=str(source_image),
        model="SadTalker_V0.0.2",
    )


# ══════════════════════════════════════════════════════════════════════════════
# OpenAI-compatible endpoint — lets CrewAI use the local Phi model directly
# CrewAI uses LiteLLM which speaks the OpenAI Chat Completions API format.
# By exposing /v1/chat/completions we make the local model a drop-in replacement
# for any OpenAI-compatible LLM provider — no Ollama or external service needed.
# ══════════════════════════════════════════════════════════════════════════════

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "phi-3.5-mini"
    messages: list[ChatMessage]
    # 1024 default lets agent reasoning chains complete without truncation;
    # individual /generate calls still default to 512 unless overridden.
    max_tokens: Optional[int] = 1024
    temperature: Optional[float] = 0.7
    stream: Optional[bool] = False


@app.post("/v1/chat/completions")
async def openai_chat_completions(req: ChatCompletionRequest):
    """
    OpenAI-compatible chat completions endpoint.
    Allows CrewAI (via LiteLLM) to use the local fine-tuned Phi-3.5-mini
    as the agent reasoning LLM — no external API needed.

    CrewAI usage in content_pipeline.py:
        LLM(model="openai/phi-3.5-mini",
            base_url="http://localhost:8765/v1",
            api_key="local")
    """
    if _lm_model is None:
        raise HTTPException(status_code=503, detail="Text model not loaded")

    # Convert OpenAI messages format → Phi-3.5-mini instruct prompt
    system_content = ""
    user_content = ""
    conversation = []

    for msg in req.messages:
        if msg.role == "system":
            system_content = msg.content
        elif msg.role == "user":
            user_content = msg.content
            conversation.append(("user", msg.content))
        elif msg.role == "assistant":
            conversation.append(("assistant", msg.content))

    # Build Phi-3.5-mini instruct formatted prompt
    if system_content and user_content:
        prompt = _build_chat_prompt(system_content, user_content)
    elif len(conversation) > 0:
        # Multi-turn: build full conversation in Phi-3.5 instruct format
        prompt = ""
        if system_content:
            prompt += f"<|system|>\n{system_content}<|end|>\n"
        for role, content in conversation:
            phi_role = "user" if role == "user" else "assistant"
            end_tok = "<|end|>\n" if role == "user" else "<|end|>\n"
            prompt += f"<|{phi_role}|>\n{content}{end_tok}"
        prompt += "<|assistant|>\n"
    else:
        prompt = user_content or ""

    try:
        content, time_ms = _generate_text(
            prompt,
            max_tokens=req.max_tokens or 1024,
            temperature=req.temperature or 0.7,
        )

        # Return OpenAI-format response
        return {
            "id": f"chatcmpl-local-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content.strip(),
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": len(content.split()),
                "total_tokens": len(prompt.split()) + len(content.split()),
            },
        }
    except Exception as e:
        logger.error(f"Chat completion error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Also expose /v1/models so LiteLLM doesn't error on model listing
@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "phi-3.5-mini", "object": "model", "owned_by": "virtuai-local"},
            {"id": "llava-1.5-7b", "object": "model", "owned_by": "virtuai-local"},
            {"id": IMAGE_MODEL, "object": "model", "owned_by": "virtuai-local"},
        ]
    }


# ── Dev run ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "virtuai.models.backend:app",
        host="0.0.0.0",
        port=8765,
        reload=False,
        log_level="info",
    )
