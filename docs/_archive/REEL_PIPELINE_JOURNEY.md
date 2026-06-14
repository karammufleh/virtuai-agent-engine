# VirtuAI Reel Pipeline — Build Journey

> Complete log of the reel-generation pipeline iterations from the moment we
> wired KIE.ai into the project through the current architecture (v14).
> Each iteration includes the rationale, what was built, what worked, what
> failed, and the user feedback that drove the next version.

---

## 1. Project context

**Goal**: produce platform-native short-form video reels (TikTok / Reels / Shorts)
of the synthetic persona **Daniel Calder** — a 28-year-old AI/automation
entrepreneur. The reels are part of the VirtuAI 8-agent CrewAI publishing
pipeline; this document covers only the video-generation half.

**Persona lock**: every reel must depict the same person. The canonical
face is `virtuai/persona/canonical_daniel.png` — a frame we extracted
from the Phase-1 multi-image Kling reel that the user explicitly
identified as "the real Daniel." All later generations are conditioned
on this image.

**Locked niche** (per user direction): every script must be about
*building, scaling, hiring, selling, or operating a business that uses
AI / automation as leverage*. Enforced in `virtuai/tools/script_writer.py`
via a `LOCKED_NICHE_BRIEF` constant.

---

## 2. API stack

| Service | Used for | Auth |
|---|---|---|
| **KIE.ai** | Unified gateway: DeepSeek LLM, ElevenLabs TTS, Kling 3.0 video, Kling Avatar Pro, Nano Banana Edit, Suno music, Topaz upscale | `KIE_API_KEY` |
| **Kling direct API** (`api-singapore.klingai.com`) | Multi-image-to-video (Phase 1), `videos/lip-sync` (video+audio → synced video) — the one capability KIE.ai still doesn't offer | `KLING_ACCESS_KEY` + `KLING_SECRET_KEY` (JWT) |
| **tmpfiles.org** | Temporary public hosting for images / audio that Kling endpoints need to consume via URL | none |
| **Local backend** (port 8765) | F5-TTS voice cloning, mflux Z-Image-Turbo, LLaVA, ArcFace face verification | local |
| **FFmpeg 7** | All post-production (concat, overlay, color grade, captions burn, loudnorm, music mix) | local |

KIE.ai replaced Gemini for agent reasoning to eliminate the 20-req/day free-tier ceiling.
KIE LLM endpoint: `https://kieai.erweima.ai/api/v1/chat/completions`,
model `deepseek-chat`. Wired into the CrewAI pipeline as
`_create_kie_llm()` in `virtuai/pipelines/content_pipeline.py`, and exposed
on the CLI as `python main.py --llm kie` (now the default).

---

## 3. Modules built

### `virtuai/tools/kie_kling.py`
KIE.ai Kling 3.0 client. Submits jobs to `POST /api/v1/jobs/createTask`,
polls `GET /api/v1/jobs/recordInfo?taskId=`, parses `resultJson` for the
video URL, downloads. Supports `kling_elements` face references,
`multi_shots`, native audio, etc.

### `virtuai/tools/kling_omni.py` *(Phase-1, kept active)*
Direct Kling API client (JWT). Provides `multi_image_to_video()` and
`lip_sync()` — the latter is the only video+audio→video lipsync we
have access to. Used in v13/v14 as the lipsync step.

### `virtuai/tools/script_writer.py` **NEW**
DeepSeek-powered script generator. Returns a validated JSON schema:
```
{ topic, hook_summary, total_words, estimated_seconds,
  scenes: [ { id, audio_text, visual_prompt, duration_hint } ],
  loop_back_line }
```
Enforces:
- Viral hook patterns (contrarian, specific number, pain question, curiosity gap, in-media-res)
- Locked niche (business + AI/automation only — no productivity hacks, no morning routines, no self-help)
- No amateur tells (no "Hey guys", no "PART 2", no "follow me")
- Scene-by-scene `visual_prompt` written for Kling 3.0
- Story arc: setup → conflict → proof → close (or loop-back)

### `virtuai/tools/caption_generator.py` *(updated)*
Whisper word-level timestamps → CapCut-style ASS subtitles. Bumped
font size from 56 → 72px and moved position from lower-third to
vertical center to match the industry standard (Hormozi/Welsh =
~90-120px on 1080×1920, scaled here for 720×1280).
Currently disabled in the active pipeline — user direction: no captions.

### `virtuai/tools/video_reviewer.py` **NEW**
Programmatic quality gate. Concrete deterministic checks the
text-based Reviewer agent couldn't perform:
- Audio continuity (silence gaps > 1.8s = bad cut detected)
- Aspect ratio (must be ~9:16)
- Duration in 8-60s window
- Cut pacing: avg shot 1.5-3.5s, longest shot ≤5s (from competitor research)
- ArcFace face consistency across sampled frames (mean pairwise ≥0.70)

Returns `{verdict: PASS | REVISE, score, issues[], stats}`.

### `virtuai/tools/local_tools.py` *(updated)*
Added `review_video_quality(video_path)` CrewAI tool, which wraps
`video_reviewer.review_video()` so the Reviewer agent can invoke it
on any generated reel.

### `virtuai/agents/reviewer_agent.py` *(updated)*
Reviewer agent's tool list extended to include `review_video_quality`.
Its backstory now teaches it: "For VIDEO/REEL assets: use
`review_video_quality` on the final MP4. If REVISE, the reel CANNOT
be published — demand a re-render."

---

## 4. Iteration log — `scripts/produce_reel_v{N}.py`

### v4 — Premium Avatar Pro baseline
**Stack**: DeepSeek script → ElevenLabs Liam (`TX3LPaxmHKxFdv7VOQHJ`) → Kling AI Avatar Pro on `hero_ref_0.png` → Whisper captions → reel_builder.
**Result**: Reviewer PASS. Lip sync proper, hook visible, end card present.
**User feedback**: "the persona isn't consistent." The LoRA had drifted from training photos.
**Fix**: Locked the canonical face by extracting a Phase-1 reel frame → `virtuai/persona/canonical_daniel.png`.

### v5 — Multi-shot with b-roll cuts
**Stack**: same talking head + 2 Kling 3.0 b-roll clips (hands typing, screen UI dashboard) intercut into the avatar by physically cutting the avatar video.
**Result**: Reviewer REVISE — 5.0s total silence across 2 gaps (HIGH severity).
**User feedback**: "the sound between the clips is not cut off, don't do these types of b-roll, the reviewer should reject these."
**Root cause**: cutting the avatar video into segments and replacing them with silent b-roll dropped Daniel's voice mid-word.

### v6 — Continuous-audio overlay + new b-roll subjects
**Stack**: same talking head + b-roll **overlaid on top of the continuous avatar** via FFmpeg `overlay=enable='between(t,start,end)'`. New subjects: skyline drone shot, whiteboard marker drawing, chess move close-up.
**Result**: First-pass had a scaling bug (overlay only filled top of frame). Fixed to force all inputs to 720×1280 and overlay at 0,0. Reviewer threshold relaxed from 0.6s → 1.8s after confirming the v4 reel the user liked also had a natural 1.28s pause. Final v6 reel: PASS (1.00), 0 silence gaps.
**User feedback**: "this is much better but we can do more — try with better prompts and stitch a new clip with the existing, use whatever API helps."

### v7 — Extended cliffhanger + Suno music
**Stack**: added Part-2 audio + Part-2 Avatar Pro, 4 semantic b-roll cuts (PDF/chatbot, blueprint, neural brain, 6-agent dashboard), Suno background music (fixed endpoint from `/api/v1/suno-api/generate` → `/api/v1/generate`).
**Result**: 25.9s reel, Reviewer PASS.
**User feedback**: "the video is horrible — lip syncing not good, scenes not realistic." We were over-producing.

### v8 — Parallel KIE submission
Same content as v7 but submitted all KIE jobs (voice + b-roll + music + scene edits) concurrently via `ThreadPoolExecutor`. Wall-clock dropped from 13.2 min → 6.2 min (≈50% faster).

### v10 — Industry-grade post
**Triggered by** a 3-agent parallel research dispatch covering camera/motion, endings, and the full production stack. Key findings adopted:
- Killed the "PART 2 →" yellow card + boom + hard-zoom ending — explicitly flagged by research as the #1 amateur tell ("every Opus Clip / Submagic template ships with this; top creators NEVER do it").
- New script with **loop-back ending**: "Four hours. That's the rule." (closes the opening hook).
- Locked-off Daniel (no continuous Ken Burns). Added **micro post-zoom punches** (1.0 → 1.10× in 4 frames) only on emphasis keywords (`four-hour`, `tripled`, `focus`) detected from caption timing.
- **M31-style orange-teal grade** via FFmpeg `eq + colorbalance + curves`.
- **Film grain** (`noise=alls=6:allf=t+u`) to mask plastic AI skin.
- Caption size 56→72px, position lower-third → vertical center.
- Loudnorm dialogue to **-14 LUFS** (TikTok standard).
- ElevenLabs speed **1.15×** for tighter pacing.
- Static `@daniel.calder` corner watermark (Welsh-style; not animated, not begging).

**User feedback**: "I don't like the coloring, the scene is the same, looks AI-made."

### v11 — Multi-scene via Nano Banana 2
**Stack**: Used Google Nano Banana 2 (`google/nano-banana-edit`) to edit the canonical Daniel into 3 different real environments (outdoor European café, airport lounge, home study). Avatar Pro then animated each scene image with its portion of the audio. Concatenated with the natural look (no orange-teal). Face identity preserved cleanly across all 3 locations.
**Result**: Reviewer REVISE — longest shot 7.2s (over 5s threshold). Scene-changes succeeded but Avatar Pro on edited images degraded lip sync.
**User feedback**: "video quality so bad, lip syncing not good, scenes not realistic. Cancel captions. Don't overlay clips between avatar talking."

### v12 — Stripped clean
Pure post-pass on v10's existing avatar (canonical face, 4-hour rule audio). No captions, no overlays, no scene edits. Just natural look + music + handle. 22.4s reel, built in 8 seconds.
**User feedback**: "eyes aren't locked, parts have no sound at the end, the video is so basic. Use only Kling for video then add ElevenLabs sound and use lipsync to do the syncing."

### v13 — Cinematic Kling + lipsync (architecture rewrite)
**New architecture** (per user direction):
1. `script_writer.write_script()` — DeepSeek picks viral business+AI topic (locked niche), produces scene-by-scene plan
2. ElevenLabs Liam → full audio in one pass
3. **No Avatar Pro.** Kling 3.0 with `multi_shots: true` and `kling_elements` face refs generates ONE continuous cinematic video chaining all scenes
4. **Kling V1-6 direct API `lip_sync()`** re-aligns the cinematic video's mouth to the ElevenLabs audio
5. Natural look + Suno music + corner handle — no captions

Worked through three Kling 3.0 multi_shots constraint errors:
- `image_urls` must contain exactly 1 entry when `multi_shots: true` (not 2)
- Must use `multi_prompt` array, not single `prompt`
- Each `multi_prompt` item is an OBJECT `{prompt: string, duration: int}`, not a string
- Sum of per-shot durations must equal total `duration`

Voice + Suno music succeeded but Kling 3.0 still hit 500s; the multi-shot path is fragile.

### v14 — Live-background image-to-video *(current iteration)*
**Insight from user feedback on v13**: when Avatar Pro animates a still face image, the background stays frozen. We needed a model that animates the WHOLE frame.

**New architecture**:
1. `script_writer.write_script()` (locked niche, 4 scenes)
2. ElevenLabs Liam → full audio (parallel)
3. Suno → background music (parallel)
4. **Nano Banana 2 (`google/nano-banana-edit`)** edits canonical Daniel into N different real-world locations (preserves identity well)
5. **Kling 3.0 IMAGE-TO-VIDEO `mode: "pro"`** animates each scene image with a `LIVE_MOTION_CLAUSE` prompt — slow handheld camera drift, leaves rustling, pedestrians moving, atmospheric haze. This is what gives the *"live background"* feel — the entire frame is alive, not just the face.
6. Concat the N cinematic 5-second clips
7. **Kling V1-6 direct API `lip_sync()`** re-aligns the mouth to the ElevenLabs audio (the only video→video lipsync available to us)
8. FFmpeg post: natural color (no LUT), mild grain, vignette, loudnorm, music at -22dB, corner handle, NO captions
9. Reviewer gate

**Three KIE schema bugs surfaced during integration** (each fixed in <5 lines):
- Wrong model name `google/nanobanana2` → correct: `google/nano-banana-edit`
- Wrong field name `image_url` (string) → correct: `image_urls` (array)
- Need `output_format: "png"` and `image_size: "9:16"` for nano-banana-edit

v14 is the current target architecture. *(As of writing, v14 is mid-debug — the script_writer prompt has been updated to keep DeepSeek in the locked niche and the i2v live-motion clause is in place.)*

---

## 5. What the research agents told us

We dispatched 4 parallel research agents over the course of the build:

| Agent | Key findings adopted |
|---|---|
| **Viral reel structure** | 5 hook patterns; cut every 1.5-3s; talking-head:b-roll ≈70:30; 8 cliffhanger phrasings; no "PART 2 →" yellow card |
| **Camera & motion** | Top creators use locked tripod + post-zoom PUNCH on emphasis (not continuous Ken Burns); film grain + halation to mask plastic AI skin; orange-teal M31 LUT |
| **Endings & cliffhangers** | The "PART 2 →" card is the #1 amateur tell; flat-landing, imperative, aphorism, loop-back are the 4 that actually work; pin "Part 2 is up" in comments rather than a graphic |
| **Production stack** | Captions 90-120px on 1080×1920 (Montserrat 900 / The Bold Font); position 50-55% vertical; -14 LUFS dialogue, -22 LUFS music; 180-220 WPM speech; cut all breaths >150ms |
| **Lipsync API options** | KIE.ai has NO video→video lipsync; recommendation is sync.so `lipsync-2` ($1/reel) — but we have direct Kling API keys so we use that instead |
| **Kling 3.0 i2v schema** | Same `kling-3.0/video` model in i2v mode when `image_urls` supplied; prompt is free-form (accepts cinematography terms); max 15s; mode `pro` for highest quality |

---

## 6. Decisions locked from user direction

| Question | Locked answer |
|---|---|
| Persona | Daniel Calder, locked to `canonical_daniel.png` |
| Niche | Business + AI + automation (script_writer enforces) |
| Topic source | DeepSeek picks each time within the locked niche |
| Voice | ElevenLabs preset "Liam" (`TX3LPaxmHKxFdv7VOQHJ`) |
| Live background | Required — environmental motion AND camera motion |
| Captions | Never |
| Hook text overlay | Removed (was reading as amateur on prior versions) |
| "PART 2 →" end card | Banned forever |
| Color grade | Natural (no orange-teal). Mild S-curve only. |
| Length | Dynamic per topic, long enough to land the message |
| Budget | Cost no object — use `pro` mode, Topaz upscale OK, sync.so OK |
| Agents | Keep the CrewAI 8-agent pipeline; add/edit as needed |

---

## 7. File map (the reel-pipeline pieces)

```
virtuai/
├── persona/
│   └── canonical_daniel.png          ← THE locked face. Every reel starts here.
├── tools/
│   ├── kie_kling.py                  ← KIE Kling 3.0 client
│   ├── kling_omni.py                 ← Direct Kling API client (lipsync)
│   ├── script_writer.py              NEW — DeepSeek viral script writer
│   ├── video_reviewer.py             NEW — programmatic quality gate
│   ├── caption_generator.py          (updated font size + position)
│   ├── reel_builder.py
│   └── local_tools.py                (added review_video_quality tool)
├── agents/
│   └── reviewer_agent.py             (wired video_reviewer in)
├── pipelines/
│   └── content_pipeline.py           (added _create_kie_llm)
└── data/
    ├── scripts/                      ← v13/v14 saved scripts (one per run)
    ├── generated_videos/             ← all reel outputs + intermediate clips
    └── sfx/                          ← whoosh/boom/riser SFX library

scripts/
├── produce_reel_v4.py                ← Avatar Pro baseline
├── produce_reel_v6.py                ← continuous-audio overlay technique
├── produce_reel_v7.py                ← extended cliffhanger + music
├── produce_reel_v8.py                ← parallel KIE submission
├── produce_reel_v10.py               ← industry-grade post (LUT, etc.)
├── produce_reel_v11.py               ← multi-scene via Nano Banana
├── produce_reel_v12.py               ← stripped clean (no captions/overlays)
├── produce_reel_v13.py               ← cinematic Kling + lipsync
├── produce_reel_v14.py               ← live-background i2v (current)
├── generate_sfx.py                   ← ElevenLabs SFX library generator
└── v13_resume.py                     ← resume helper for partial v13 runs

.env:
  KIE_API_KEY=...                     (KIE.ai unified gateway)
  KLING_ACCESS_KEY=...                (direct Kling API)
  KLING_SECRET_KEY=...
```

---

## 8. Cost per reel (v14 estimate)

| Step | API call | Approx cost |
|---|---|---|
| DeepSeek script | KIE LLM | < $0.01 |
| ElevenLabs Liam (full script) | KIE | ~$0.50 |
| Suno background music | KIE | ~$1.00 |
| 4× Nano Banana scene edits | KIE | ~$0.30 |
| 4× Kling 3.0 i2v @ 5s, `mode: pro` | KIE | ~$6.00 |
| Kling V1-6 lipsync | Direct | ~$1.50 |
| FFmpeg post | local | $0 |
| **Total** | | **~$9-10 per reel** |

Wall-clock with parallel architecture: ~12-15 minutes.

---

## 9. Known limitations / open issues

- **v14 still in debug**: nano-banana-edit field-shape errors fixed; next failure mode TBD.
- **Kling 3.0 multi_shots** is finicky — strict per-shot duration sums, exactly-1 image_url, requires multi_prompt OBJECT format. The v14 architecture sidesteps it by using single-shot i2v per scene then concat.
- **Kling V1-6 lipsync** requires the source video to have an audio track (even silent). v14 attaches `anullsrc` before submitting.
- **tmpfiles.org URL lifetime** is short — uploads can expire between submit and Kling fetching. catbox.moe (used in Phase 1) has been intermittently unreachable; tmpfiles is our current default but worth monitoring.
- **Avatar Pro** has been retired from the active path — its frozen-background was the dealbreaker. All current reels go through Kling i2v.
- **Captions** are off by user direction. Caption generator module still exists and is updated to industry sizing/position, ready to re-enable if needed.

---

## 10. Files to read next

If you're continuing this work:

1. `scripts/produce_reel_v14.py` — current target architecture
2. `virtuai/tools/script_writer.py` — locked-niche script generator
3. `virtuai/tools/video_reviewer.py` — quality gate logic
4. `virtuai/tools/kling_omni.py:240` — `lip_sync()` function
5. This document (`REEL_PIPELINE_JOURNEY.md`) for context on why the
   architecture looks the way it does.

---

*Last updated: 2026-05-14, mid-iteration on v14.*
