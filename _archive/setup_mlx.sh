#!/bin/bash
# =============================================================================
# setup_mlx.sh — One-command setup for VirtuAI local VLM backend
#
# What this installs:
#   - mlx              (Apple's ML framework for M-series chips)
#   - mlx-lm           (LLM inference + LoRA fine-tuning via MLX)
#   - mlx-vlm          (Vision Language Model support — LLaVA, etc.)
#   - fastapi          (API server for CrewAI tool integration)
#   - uvicorn          (ASGI server to run FastAPI)
#   - httpx            (HTTP client for CrewAI tools to call backend)
#   - pillow           (Image processing for vision tasks)
#   - pydantic         (Data validation for API schemas)
#
# After running this script:
#   1. python run_backend.py    — Start the VLM inference server
#   2. (In a new terminal): python main.py  — Run the content pipeline
#   3. (Optional) To fine-tune: ./virtuai/models/finetune/train_lora.sh
#
# Usage:
#   chmod +x setup_mlx.sh
#   ./setup_mlx.sh
# =============================================================================

set -e

echo "============================================================"
echo "  VirtuAI — MLX Backend Setup"
echo "  Target: Apple Silicon (M1/M2/M3/M4)"
echo "============================================================"
echo ""

# ── Check we're on Apple Silicon ──────────────────────────────────────────────
ARCH=$(uname -m)
if [ "$ARCH" != "arm64" ]; then
    echo "WARNING: This setup is optimized for Apple Silicon (arm64)."
    echo "Detected: $ARCH"
    echo "MLX may not work on non-Apple Silicon hardware."
    echo ""
fi

# ── Detect Python / venv ──────────────────────────────────────────────────────
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    PIP="pip"
    echo "✓ Using project venv"
elif [ -f "/Users/karammufleh/virtuai-venv/bin/activate" ]; then
    source /Users/karammufleh/virtuai-venv/bin/activate
    PIP="pip"
    echo "✓ Using ~/virtuai-venv"
else
    PIP="pip3"
    echo "WARNING: No virtual environment found. Installing to system Python."
    echo "Consider creating a venv first."
fi

echo ""

# ── Step 1: Upgrade pip ───────────────────────────────────────────────────────
echo "Step 1/5: Upgrading pip..."
$PIP install --upgrade pip --quiet

# ── Step 2: Install MLX core ──────────────────────────────────────────────────
echo "Step 2/5: Installing MLX (Apple's ML framework)..."
$PIP install mlx --quiet && echo "  ✓ mlx installed"

# ── Step 3: Install MLX-LM (text model inference + LoRA fine-tuning) ─────────
echo "Step 3/5: Installing mlx-lm (text inference + LoRA fine-tuning)..."
$PIP install mlx-lm --quiet && echo "  ✓ mlx-lm installed"

# ── Step 4: Install MLX-VLM (Vision Language Model support) ─────────────────
echo "Step 4/5: Installing mlx-vlm (Vision Language Model)..."
$PIP install mlx-vlm --quiet && echo "  ✓ mlx-vlm installed" || {
    echo "  WARNING: mlx-vlm install failed."
    echo "  The backend will run in text-only mode."
    echo "  Try manually: pip install mlx-vlm"
}

# ── Step 5: Install FastAPI backend dependencies ──────────────────────────────
echo "Step 5/5: Installing FastAPI + server dependencies..."
$PIP install fastapi uvicorn httpx pillow pydantic --quiet && echo "  ✓ Backend dependencies installed"

# ── Verify installations ──────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Verifying installations..."
echo "============================================================"

python -c "import mlx; print(f'  ✓ MLX {mlx.__version__}')" 2>/dev/null || echo "  ✗ MLX — check installation"
python -c "import mlx_lm; print('  ✓ mlx-lm')" 2>/dev/null || echo "  ✗ mlx-lm — check installation"
python -c "import mlx_vlm; print('  ✓ mlx-vlm (vision enabled)')" 2>/dev/null || echo "  ⚠ mlx-vlm not available (text-only mode)"
python -c "import fastapi; print(f'  ✓ FastAPI {fastapi.__version__}')" 2>/dev/null || echo "  ✗ FastAPI — check installation"
python -c "import uvicorn; print('  ✓ uvicorn')" 2>/dev/null || echo "  ✗ uvicorn — check installation"
python -c "import httpx; print(f'  ✓ httpx {httpx.__version__}')" 2>/dev/null || echo "  ✗ httpx — check installation"
python -c "import PIL; print(f'  ✓ Pillow {PIL.__version__}')" 2>/dev/null || echo "  ✗ Pillow — check installation"

# ── Pre-download the model ────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Pre-downloading LLaVA 1.5 7B (4-bit quantized)"
echo "  Size: ~4.5 GB — this may take a few minutes."
echo "  The model will be cached for future runs."
echo "============================================================"
echo ""

python -c "
try:
    from mlx_vlm import load
    print('Downloading mlx-community/llava-1.5-7b-4bit...')
    model, processor = load('mlx-community/llava-1.5-7b-4bit')
    print('✓ LLaVA 1.5 7B downloaded and verified!')
    del model, processor
except ImportError:
    print('mlx-vlm not available — trying mlx-lm fallback...')
    from mlx_lm import load
    model, tokenizer = load('mlx-community/llava-1.5-7b-4bit')
    print('✓ Model downloaded via mlx-lm')
    del model, tokenizer
except Exception as e:
    print(f'Download failed: {e}')
    print('You can download manually later when running run_backend.py')
"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Setup complete!"
echo ""
echo "  NEXT STEPS:"
echo ""
echo "  1. Start the VLM backend:"
echo "       python run_backend.py"
echo ""
echo "  2. In a new terminal, run the pipeline:"
echo "       python main.py"
echo ""
echo "  3. (Optional) Fine-tune on persona data:"
echo "       ./virtuai/models/finetune/train_lora.sh"
echo ""
echo "  API docs available at: http://localhost:8765/docs"
echo "============================================================"
