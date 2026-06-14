"""
preprocess_face_dataset.py — Prepares face training images for mflux LoRA training.

What it does for every PNG in SOURCE_DIR:
  1. Crops the top-left label band (the "1", "15" overlays baked into the source images)
  2. Center-crops to a square focused on the face
  3. Upscales to 1024x1024 using LANCZOS resampling
  4. Writes to OUTPUT_DIR/<stem>.png
  5. Writes a paired caption file OUTPUT_DIR/<stem>.txt

The trigger token is "dnlcldr" — a unique short string the LoRA will learn to
associate with this face. Use it in inference prompts as: "a photo of dnlcldr man, ..."

Usage:
    python preprocess_face_dataset.py
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = Path("/Users/karammufleh/Desktop/imageonline")
OUTPUT_DIR = ROOT / "virtuai" / "persona" / "face_dataset"

TRIGGER = "dnlcldr"
TARGET_SIZE = 1024
LABEL_CROP_TOP = 55          # pixels to shave off the top of every source image
                             # (covers single- and two-digit labels in the top-left corner)

# Diverse caption pool — each image gets a randomized caption so the LoRA
# learns the face/identity rather than a fixed setting. Captions stay short
# and consistent in style; the trigger token always appears first.
SCENE_CAPTIONS = [
    "a portrait photo of {trigger} man, looking at camera, soft natural studio light, shallow depth of field",
    "a candid photo of {trigger} man, slight smile, warm directional lighting, blurred background",
    "a close-up portrait of {trigger} man, neutral expression, even soft light, plain backdrop",
    "a side-profile photo of {trigger} man, three-quarter view, dramatic side lighting, dark background",
    "a photo of {trigger} man looking off camera, thoughtful expression, cinematic lighting",
    "an editorial portrait of {trigger} man, dark moody background, sharp facial details, professional headshot",
    "a portrait of {trigger} man, eye contact with camera, soft window light, neutral grey backdrop",
    "a photo of {trigger} man, slight head tilt, golden hour lighting, shallow focus",
    "a studio portrait of {trigger} man, balanced even lighting, simple dark background",
    "a photo of {trigger} man, calm confident expression, warm rim light from behind",
]


def preprocess_one(src: Path, dst_image: Path, dst_caption: Path, rng: random.Random) -> None:
    img = Image.open(src).convert("RGB")
    w, h = img.size

    # 1. Crop the top label band
    img = img.crop((0, LABEL_CROP_TOP, w, h))
    w, h = img.size

    # 2. Center square crop
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))

    # 3. Upscale to TARGET_SIZE with high-quality Lanczos
    img = img.resize((TARGET_SIZE, TARGET_SIZE), Image.Resampling.LANCZOS)

    # 4. Save image
    img.save(dst_image, format="PNG", optimize=True)

    # 5. Write caption
    caption = rng.choice(SCENE_CAPTIONS).format(trigger=TRIGGER)
    dst_caption.write_text(caption + "\n", encoding="utf-8")


def main() -> None:
    if not SOURCE_DIR.exists():
        raise SystemExit(f"Source dir not found: {SOURCE_DIR}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    sources = sorted(p for p in SOURCE_DIR.iterdir() if p.suffix.lower() == ".png")
    if not sources:
        raise SystemExit(f"No PNGs found in {SOURCE_DIR}")

    # Fixed seed so caption assignment is reproducible across re-runs
    rng = random.Random(42)

    print(f"Preprocessing {len(sources)} images → {OUTPUT_DIR}")
    print(f"  - cropping {LABEL_CROP_TOP}px off the top (label band)")
    print(f"  - center square crop")
    print(f"  - upscaling to {TARGET_SIZE}x{TARGET_SIZE} (LANCZOS)")
    print(f"  - trigger token: '{TRIGGER}'")

    for src in sources:
        stem = src.stem  # "00", "01", …
        dst_img = OUTPUT_DIR / f"{stem}.png"
        dst_txt = OUTPUT_DIR / f"{stem}.txt"
        preprocess_one(src, dst_img, dst_txt, rng)
        print(f"  ✓ {src.name} → {dst_img.name} + caption")

    # Write one preview pair so mflux training can render previews each epoch.
    # Re-uses the first image's caption seed for consistency.
    preview_caption = (
        f"a portrait photo of {TRIGGER} man, looking at camera, "
        "professional studio headshot, soft balanced lighting, neutral grey background"
    )
    (OUTPUT_DIR / "preview.txt").write_text(preview_caption + "\n", encoding="utf-8")

    print(f"\nDone. {len(sources)} training pairs + 1 preview.txt written.")
    print(f"Inspect: open '{OUTPUT_DIR}'")


if __name__ == "__main__":
    main()
