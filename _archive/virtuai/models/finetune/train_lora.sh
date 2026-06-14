#!/bin/bash
# =============================================================================
# train_lora.sh — Fine-tune Phi-3.5-mini with LoRA using MLX on Apple Silicon
#
# Architecture (dual-model):
#   - Phi-3.5-mini-instruct  → fine-tuned here for VirtuAI persona text generation
#   - LLaVA 1.5 7B           → loaded separately in backend.py for vision tasks
#
# mlx-lm supports text-only transformer models for LoRA fine-tuning.
# LLaVA has a nested vision+text architecture and must be used via mlx-vlm
# for inference only (not fine-tuning). This is the correct split.
#
# Usage:
#   cd "/Users/karammufleh/Desktop/capstone  101"
#   ./virtuai/models/finetune/train_lora.sh
# =============================================================================

set -e  # Exit on any error

# ── Configuration ─────────────────────────────────────────────────────────────
# Phi-3.5-mini: 3.8B params, 4-bit quantized (~2GB), mlx-lm compatible, fully cached
MODEL="mlx-community/Phi-3.5-mini-instruct-4bit"
DATA_DIR="virtuai/models/finetune/data"
ADAPTER_DIR="virtuai/models/finetune/adapters"
FUSED_DIR="virtuai/models/finetune/fused_model"

# Training hyperparameters (tuned for Apple Silicon, conservative defaults)
ITERS=600           # Total training iterations (increase to 1000 for better results)
BATCH_SIZE=2        # Batch size per step (2 is safe for 8GB unified memory)
LEARNING_RATE=1e-5  # Learning rate (1e-5 is standard for LoRA fine-tuning)
LORA_LAYERS=8       # Number of layers to apply LoRA to (8 is a good balance)
GRAD_CHECKPOINTING=true  # Saves memory at cost of ~20% speed
STEPS_PER_EVAL=50   # Validate every N steps
STEPS_PER_SAVE=100  # Save checkpoint every N steps
MAX_SEQ_LEN=1024    # Max sequence length (1024 fits most examples)

# ── Activate venv ──────────────────────────────────────────────────────────────
echo "============================================================"
echo "  VirtuAI — LoRA Fine-Tuning Pipeline"
echo "  Model: $MODEL"
echo "  Iterations: $ITERS | Batch: $BATCH_SIZE | LR: $LEARNING_RATE"
echo "============================================================"
echo ""

# Check if we're in the right directory
if [ ! -f "main.py" ]; then
    echo "ERROR: Run this script from the project root (capstone 101/)"
    exit 1
fi

# Activate the virtual environment
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    echo "✓ Virtual environment activated"
elif [ -f "/Users/karammufleh/virtuai-venv/bin/activate" ]; then
    source /Users/karammufleh/virtuai-venv/bin/activate
    echo "✓ Virtual environment activated (from ~/virtuai-venv)"
else
    echo "WARNING: No virtual environment found. Using system Python."
fi

# ── Check MLX installation ─────────────────────────────────────────────────────
echo ""
echo "Checking MLX dependencies..."
python -c "
import mlx
try:
    from importlib.metadata import version
    v = version('mlx')
except Exception:
    v = 'installed'
print(f'✓ MLX {v}')
" || {
    echo "ERROR: MLX not installed. Run: pip install mlx"
    exit 1
}
python -c "import mlx_lm; print('✓ mlx-lm installed')" || {
    echo "ERROR: mlx-lm not installed. Run: pip install mlx-lm"
    exit 1
}

# ── Dataset validation ─────────────────────────────────────────────────────────
echo ""
echo "Validating dataset..."
TRAIN_COUNT=$(wc -l < "$DATA_DIR/train.jsonl" | tr -d ' ')
VALID_COUNT=$(wc -l < "$DATA_DIR/valid.jsonl" | tr -d ' ')
echo "✓ Training examples: $TRAIN_COUNT"
echo "✓ Validation examples: $VALID_COUNT"

if [ "$TRAIN_COUNT" -lt 10 ]; then
    echo "ERROR: Too few training examples. Run prepare_dataset.py first."
    exit 1
fi

# ── Create output directories ──────────────────────────────────────────────────
mkdir -p "$ADAPTER_DIR"
mkdir -p "$FUSED_DIR"
echo "✓ Output directories ready"

# ── Download model (if not cached) ────────────────────────────────────────────
echo ""
echo "Checking model cache (will download if not cached)..."
python -c "
from mlx_lm import load
print('Attempting to load model — will download if not cached...')
model, tokenizer = load('$MODEL')
print('✓ Model loaded successfully')
del model, tokenizer
" 2>&1 | grep -E "(✓|ERROR|Downloading|Fetching)" || true

# ── Run LoRA Fine-Tuning ───────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Starting LoRA fine-tuning..."
echo "  This will take approximately 15-40 minutes on Apple Silicon."
echo "  Progress is saved every $STEPS_PER_SAVE steps."
echo "  Press Ctrl+C to stop early — adapters will still be saved."
echo "============================================================"
echo ""

python -m mlx_lm lora \
    --model "$MODEL" \
    --train \
    --data "$DATA_DIR" \
    --iters "$ITERS" \
    --batch-size "$BATCH_SIZE" \
    --learning-rate "$LEARNING_RATE" \
    --num-layers "$LORA_LAYERS" \
    --adapter-path "$ADAPTER_DIR" \
    --steps-per-eval "$STEPS_PER_EVAL" \
    --save-every "$STEPS_PER_SAVE" \
    --max-seq-length "$MAX_SEQ_LEN" \
    --grad-checkpoint

FINETUNE_EXIT=$?

if [ $FINETUNE_EXIT -ne 0 ]; then
    echo ""
    echo "ERROR: Fine-tuning failed with exit code $FINETUNE_EXIT"
    echo "Check the output above for details."
    exit $FINETUNE_EXIT
fi

echo ""
echo "============================================================"
echo "  Fine-tuning complete!"
echo "  LoRA adapters saved to: $ADAPTER_DIR"
echo "============================================================"

# ── Fuse Adapters into Deployable Model ───────────────────────────────────────
echo ""
echo "Fusing LoRA adapters into base model..."
echo "(This creates a standalone model that doesn't need adapter files at runtime)"
echo ""

python -m mlx_lm fuse \
    --model "$MODEL" \
    --adapter-path "$ADAPTER_DIR" \
    --save-path "$FUSED_DIR" \
    --dequantize

FUSE_EXIT=$?

if [ $FUSE_EXIT -ne 0 ]; then
    echo "WARNING: Adapter fusion failed. Backend will use base model + adapters instead."
    echo "This is fine — the backend supports both modes."
else
    echo "✓ Fused model saved to: $FUSED_DIR"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  VirtuAI LoRA Fine-Tuning — COMPLETE"
echo ""
echo "  Base model:      $MODEL"
echo "  LoRA adapters:   $ADAPTER_DIR"
if [ $FUSE_EXIT -eq 0 ]; then
echo "  Fused model:     $FUSED_DIR"
fi
echo ""
echo "  Next step: Start the inference backend"
echo "    python run_backend.py"
echo ""
echo "  The backend will auto-detect fused_model/ if it exists,"
echo "  otherwise it will load base model + adapters."
echo "============================================================"
