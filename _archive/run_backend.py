"""
run_backend.py — Start the VirtuAI local VLM inference server.

This must be running BEFORE you run the content pipeline (main.py).

Usage:
    python run_backend.py

The server starts at: http://localhost:8765
Health check:         http://localhost:8765/health

Two-terminal workflow:
    Terminal 1: python run_backend.py     # Start VLM server (keep running)
    Terminal 2: python main.py            # Run content pipeline
"""

import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("run_backend")


def main():
    logger.info("=" * 60)
    logger.info("  VirtuAI — Local VLM Backend")
    logger.info("  Model: LLaVA 1.5 7B (Vision Language Model)")
    logger.info("  Platform: Apple Silicon / MLX")
    logger.info("=" * 60)
    logger.info("")
    logger.info("  Server URL:   http://localhost:8765")
    logger.info("  Health check: http://localhost:8765/health")
    logger.info("  API docs:     http://localhost:8765/docs")
    logger.info("")
    logger.info("  The model will load on first startup (~30-60 seconds).")
    logger.info("  Keep this terminal running while using the pipeline.")
    logger.info("=" * 60)
    logger.info("")

    # Check Python version
    if sys.version_info < (3, 10):
        logger.error("Python 3.10+ required. You have: %s", sys.version)
        logger.error("You are using the wrong Python. Run with your venv:")
        logger.error("  source ~/virtuai-venv/bin/activate && python run_backend.py")
        sys.exit(1)

    # Check critical dependencies
    missing = []
    try:
        import fastapi
    except ImportError:
        missing.append("fastapi")
    try:
        import uvicorn
    except ImportError:
        missing.append("uvicorn")
    try:
        import httpx
    except ImportError:
        missing.append("httpx")

    if missing:
        logger.error("Missing dependencies: %s", ", ".join(missing))
        logger.error("Run: pip install %s --break-system-packages", " ".join(missing))
        sys.exit(1)

    # Check MLX
    try:
        import mlx
        try:
            from importlib.metadata import version as pkg_version
            mlx_ver = pkg_version("mlx")
        except Exception:
            mlx_ver = getattr(mlx, "__version__", "installed")
        logger.info("MLX %s ✓", mlx_ver)
    except ImportError:
        logger.error("MLX not installed in this venv.")
        logger.error("Run:  pip install mlx mlx-lm mlx-vlm fastapi uvicorn httpx pillow")
        sys.exit(1)

    try:
        import mlx_lm
        logger.info("mlx-lm available ✓")
    except ImportError:
        logger.warning("mlx-lm not found. Install with: pip install mlx-lm")

    try:
        import mlx_vlm
        logger.info("mlx-vlm available ✓ (vision capability enabled)")
    except ImportError:
        logger.warning(
            "mlx-vlm not found — text-only mode. "
            "Install with: pip install mlx-vlm"
        )

    logger.info("")
    logger.info("Starting server...")

    import uvicorn
    uvicorn.run(
        "virtuai.models.backend:app",
        host="0.0.0.0",
        port=8765,
        reload=False,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
