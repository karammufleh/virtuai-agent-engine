"""
VirtuAI Models.

    vlm_client.py — httpx client used by virtuai.tools.local_tools to reach an
                    OPTIONAL on-device VLM backend. Importing it needs only httpx
                    (no MLX), so it is safe in a cloud-only install.

The Phase-1 local VLM backend (FastAPI + MLX) and the LoRA fine-tuning pipeline
are not part of the final cloud workflow and have been moved to `_archive/`.
"""
