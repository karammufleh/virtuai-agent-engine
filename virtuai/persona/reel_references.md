# Reel reference URLs

User-provided references for the visual style we want Daniel's reels to match.
These are Instagram reels — auth-gated, so the patterns/descriptions need to
be filled in manually or from screenshots.

| # | URL | Pattern (TBD) |
|---|---|---|
| 1 | https://www.instagram.com/reel/DWr2imujLx6/ | _to-be-described_ |
| 2 | https://www.instagram.com/reel/DXpLqJmDA58/ | _to-be-described_ |
| 3 | https://www.instagram.com/reel/DXp_hDKh25S/ | _to-be-described_ |
| 4 | https://www.instagram.com/reel/DXnauOJPDiQ/ | _to-be-described_ |
| 5 | https://www.instagram.com/reel/DXXUGFYD0lb/ | _to-be-described_ |
| 6 | https://www.instagram.com/reel/DWSA_ifgi22/ | _to-be-described_ |
| 7 | https://www.instagram.com/reel/DUOJtqQgG86/ | _to-be-described_ |

## What we extract from each reference (once described or screenshotted)

- **Shot count** — how many cuts in the reel?
- **Average shot duration** — fast (1-2s) or slow (5+s)?
- **Camera movement** — static / handheld / walking / gimbal smooth?
- **Subject motion** — talking head / walking / gesturing / multi-location?
- **B-roll ratio** — % of shots that are NOT the speaker on camera
- **Caption style** — bottom-third large text / floating animated / none?
- **Color grade** — warm / cool / neutral / heavy filter?
- **Music** — silent / lo-fi / energetic / cinematic?
- **Audio sync** — voiceover throughout / talking on camera / music only?

## How we'll use these

Each pattern becomes a prompt template for Wan 2.2 (or EchoMimic v3 if
talking-on-camera). We generate 4-6 short clips per reel, stitch them
with F5-TTS voiceover and captions on the local Mac via ffmpeg.

The reference reels ARE NOT used as training data. We extract structural
patterns only — no visual content is copied. Capstone defense: we engineered
the reel grammar from observed examples and applied our own face/voice/text
to it.
