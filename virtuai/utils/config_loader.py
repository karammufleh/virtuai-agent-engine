"""
Loads persona and platform configurations from YAML files.
"""

import os
from pathlib import Path
import yaml


CONFIG_DIR = Path(__file__).parent.parent / "config"
PERSONAS_DIR = CONFIG_DIR / "personas"
PLATFORMS_DIR = CONFIG_DIR / "platforms"


def load_yaml(file_path: str | Path) -> dict:
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_persona(persona_name: str = "virtuai_mentor") -> dict:
    """Load a persona config by name (filename without .yaml)."""
    path = PERSONAS_DIR / f"{persona_name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Persona config not found: {path}")
    return load_yaml(path)


def load_platform(platform_id: str) -> dict:
    """Load a platform config by ID (e.g., 'linkedin', 'x_twitter')."""
    path = PLATFORMS_DIR / f"{platform_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Platform config not found: {path}")
    return load_yaml(path)


def load_all_platforms() -> dict[str, dict]:
    """Load all enabled platform configs. Returns {platform_id: config}."""
    platforms = {}
    for file in PLATFORMS_DIR.glob("*.yaml"):
        config = load_yaml(file)
        if config.get("enabled", True):
            platforms[config["platform_id"]] = config
    return platforms


def get_persona_for_platform(persona: dict, platform_id: str) -> dict:
    """Merge base persona with platform-specific adaptations."""
    adaptations = persona.get("platform_adaptations", {}).get(platform_id, {})
    return {
        "base": persona,
        "platform_overrides": adaptations,
        "platform_id": platform_id,
    }


# ── KIE model catalogue ──────────────────────────────────────────────────────

_MODELS_CACHE: dict | None = None


def load_models() -> dict:
    """
    Load the centralized KIE model catalogue from virtuai/config/models.yaml.
    Memoized — call cost is negligible after the first hit.
    """
    global _MODELS_CACHE
    if _MODELS_CACHE is None:
        path = CONFIG_DIR / "models.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Models catalogue not found: {path}")
        _MODELS_CACHE = load_yaml(path)
    return _MODELS_CACHE


def model_slug(key: str) -> str:
    """
    Resolve a logical model key (e.g. 'reel_video', 'image_post',
    'music_underbed', 'script_writer') to the KIE slug.
    """
    cat = load_models()
    section = cat["models"]
    if key not in section:
        raise KeyError(
            f"Unknown model key '{key}'. Available: {sorted(section.keys())}"
        )
    return section[key]["slug"]


def kie_endpoint(name: str = "create_task") -> str:
    """Resolve an endpoint name (create_task / record_info / claude /
    file_upload) to the full URL."""
    cat = load_models()
    base = cat["base_url"]
    ep = cat["endpoints"].get(name)
    if not ep:
        raise KeyError(f"Unknown KIE endpoint '{name}'. "
                       f"Have: {sorted(cat['endpoints'].keys())}")
    return ep if ep.startswith("http") else f"{base}{ep}"
