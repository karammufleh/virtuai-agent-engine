# Challenges in Video Generation for the Persona

> Suggested location in the capstone report: a "Challenges" section,
> placed before the proposed-solution / future-direction chapter that
> introduces the chosen commercial video service. ~1,400 words. This
> section characterises the problem only — it does not describe how we
> resolved it.

## 1. Problem Statement

A central goal of this project was to produce talking-head video clips of
the synthetic persona for short-form video platforms (TikTok, Instagram
Reels, YouTube Shorts). Of the four generative modalities required —
text, image, voice, and video — three reached an acceptable level of
consistency on the available compute. The text component, fine-tuned
from a pretrained Phi-3.5-mini-instruct backbone, produced stylistically
stable output. The image component, fine-tuned with a 30-photo LoRA over
Z-Image-Turbo, achieved a measured ArcFace identity similarity of 0.664,
a +120% lift over the pre-LoRA baseline of 0.301. The voice component,
based on F5-TTS zero-shot cloning, reproduced the target voice from a
single 9.77-second reference clip across arbitrary scripts.

The fourth modality, **audio-driven talking-head video**, did not converge
to the same level of consistency. This section documents the failure modes
we observed, the hardware constraints that bounded the search, and the
specific gaps that remain unresolved.

## 2. What "Inconsistent" Means in This Context

The video inconsistency we observed was not at the level of identity
drift — the face LoRA, used as the source still for every video,
preserved a single recognisable face across generations. The
inconsistency manifested at three other levels.

**Inconsistent motion quality across models.** Different audio-driven
talking-head models produce qualitatively different kinds of "motion".
Wav2Lip (Prajwal et al., 2020) produces sharp lip movements but leaves
the rest of the head and body essentially frozen, making the output
read as a still photo with moving lips. SadTalker (Zhang et al., 2023)
produces natural head turns, blinks, and micro-expressions but softens
the lip region. Neither, alone, produces output that resembles
contemporary commercial talking-head video products such as HeyGen,
Synthesia, or D-ID.

**Inconsistent render-time vs. quality tradeoffs.** Identical
configurations (model, source image, audio file) produced highly
variable wall-clock render times depending on system memory pressure.
We measured one continuous render's iteration pace degrade from
33 s/iter to 1,294 s/iter — a 39× slowdown — as macOS swap usage rose
from negligible to 11.4 GB out of 13 GB available. This made it
impossible to plan a content pipeline around predictable wall-clock
budgets: a clip that took 7 minutes on a fresh boot could take over
two hours mid-session.

**Inconsistent perceived realism across clip lengths.** A short clip
(3–5 seconds) of a model's talking-head output reads as reasonably
natural. A 60–70-second clip of the same generation method reveals
the underlying repetition in head motion patterns and the model's
inability to gesture, change scene, or vary background — breaking
the illusion that the platform algorithms select against.

These three failure modes share a root cause: the underlying audio-driven
video models were not built for, and do not run well on, the hardware we
had available.

## 3. The Apple Silicon Compute Ceiling

This project was developed on a single M-series Apple Silicon laptop
(unified memory, no NVIDIA GPU). For text, image, and voice generation
this is a workable platform — Apple's MLX framework supports the
foundation models we used, and inference times for those modalities
were measured in seconds or minutes. For video generation the same
hardware is a substantially harder constraint.

The 2024–25 generation of audio-driven talking-head and full-body
animators — Hallo3 (Cui et al., 2025), EchoMimic v2 and v3 (Meng et al.,
2024), MuseTalk 1.5 (Tencent, 2025), JoyVASA (JD AI, 2024), and
LiveAvatar (Alibaba, 2025) — share a common architectural pattern:
diffusion-based latent video generation conditioned on audio features
and a face image. This architecture has two consequences for our
setting.

**Memory.** A 1024×1024×24fps video diffusion forward pass at the
resolutions these models target requires 40–80 GB of GPU VRAM. Apple
Silicon's unified memory architecture nominally exposes more memory
than this, but the Metal Performance Shaders (MPS) backend in PyTorch
does not yet implement the full set of operations these models depend
on, and several of the models contain custom CUDA kernels with no MPS
equivalent. We confirmed this empirically: each candidate project's
official repository documents NVIDIA-only support, and the
community-hosted HuggingFace Spaces hosting them (e.g.,
`fffiloni/echomimic-v2`) are explicitly labelled "CPU showcase" and
return an `AppError` when actual inference is requested.

**Throughput.** Even when an MPS port can be coerced into running, it
does so without the tensor-core acceleration that NVIDIA H100s use for
the dominant compute pattern in diffusion models. The published
real-time-on-H100 performance of these systems degrades to multiple
minutes per second of output on Apple Silicon when a port runs at all.

The audio-driven video models we *could* run on Apple Silicon were
older — the 2020 and 2023-vintage models cited above. Their output
quality is visibly below what a 2025 reviewer expects from the term
"AI talking head", and using them in production was not viable for
the project's stated goal of producing native-feeling content for
commercial platforms.

## 4. The Speed–Quality Frontier We Mapped

To characterise the achievable quality on our hardware, we measured five
distinct configurations of the available models against the same
3.68-second reference audio. The table below summarises the results.

| Configuration | Render time (3.68 s audio) | Notes |
|---|---|---|
| Wav2Lip (Improved quality, 256×256) | 27 s | Sharp lip sync, identity preserved, no head motion or expression |
| SadTalker default (256×256, motion-enabled, no enhancer) | ≈ 7 min | Natural head turns and blinks, lip sync visibly soft |
| SadTalker max (512×512, GFPGAN enhancer, motion suppressed) | 75 min | High face fidelity, no head motion |
| Stacked: SadTalker motion + Wav2Lip refinement | 24 min | Head motion + sharp lips — best single-model result available |
| Stacked, scaled to a 43-second TikTok script (extrapolated) | ≈ 4 hours | Quality identical, wall-clock time prohibitive for routine production |

Two findings emerged.

**No single configuration dominated.** Wav2Lip was fast and sharp at the
lips but flat from the neck up. SadTalker produced motion but smudged the
mouth. The best available combination required composing the two and
multiplied the render cost.

**Render time scaled super-linearly with audio length on this hardware,**
because longer renders held memory pressure higher for longer, which
triggered the swap-thrashing degradation described in §2. A 71-second
YouTube Shorts script under the highest-quality SadTalker configuration
would have taken closer to six hours than a linear extrapolation
suggests.

The strongest result the project's compute envelope allowed was the
24-minute stacked render, and only at clip lengths under 15 seconds.
Routine production of platform-native content (TikTok at 15–60 sec,
Reels at 30–90 sec, Shorts at 30–60 sec) was not feasible at this
quality level.

## 5. Open Challenges

Three problems remain unresolved at the time of writing.

**Realism gap relative to commercial systems.** None of the
locally-runnable models produce output that a contemporary social-media
viewer would mistake for genuine human-recorded video. The visible
artefacts (soft lips, frozen torso, repetitive head motion, background
inertia) read as "AI-generated" within the first second of viewing. This
is the gap between 2020-vintage and 2025-vintage talking-head technology,
and on the available hardware that gap is not closeable.

**Compute-bound throughput.** Even at the achievable quality level,
the wall-clock cost of a single 60-second clip exceeds what an
autonomous content pipeline can sustain. The persona was originally
designed to publish multiple posts per week across six platforms; a
single video post at the highest local quality would consume multiple
hours of compute and lock the workstation against any other use.

**Body and scene variation.** Every locally-runnable model we tested
operates on a fixed face crop. The persona never walks, gestures with
their hands, sits at a desk, or appears in any scene other than a
medium-shot from the neck up. This is structurally inadequate for
short-form video formats, where pattern-interrupt scene changes are a
genre convention.

These three challenges, taken together, define the boundary between
what was achievable on this project's compute and what the target
content quality requires. They motivated the Phase 2 architecture
described below.

## 6. Phase 2 Resolution: Kling API + Local Post-Production

Phase 2 addressed all three open challenges by splitting the pipeline
into a cloud video stage and a local post-production stage.

**Cloud video generation (Kling V1-6 API).** The Kling
multi-image-to-video endpoint accepts 1-4 reference photographs of
the persona and a rich scene prompt, producing a 5- or 10-second
9:16 video in which the persona appears in the described environment
with natural body language, scene depth, and camera motion. The Kling
lip-sync endpoint then synchronises the persona's mouth to a provided
audio file. Both calls run on Kling's Singapore cluster and return
results in 2-4 minutes regardless of local hardware.

This resolved the three open challenges directly:

| Challenge | Phase 1 State | Phase 2 State |
|---|---|---|
| Realism gap | Wav2Lip/SadTalker artefacts visible within 1 second | Kling-generated motion, scene, and lip-sync; ArcFace identity 0.696 average across frames |
| Compute throughput | 4+ hours for a 60 s clip on Apple Silicon | 7 minutes end-to-end for a 10 s reel (including voice + captions + assembly) |
| Body and scene variation | Fixed face crop, no environment | Full-body medium shots in varied real-world environments (café, office, rooftop) |

**Local post-production pipeline.** Three components remain fully
local, preserving the project's on-device generation constraint for
persona-locked assets:

1. **F5-TTS voice cloning** — generates Daniel's voice from text using
   the 9.77 s reference clip. Runs on Apple Silicon via the local
   backend (port 8765). Output uploaded to catbox.moe for Kling's
   lip-sync endpoint.

2. **Whisper caption generation** — word-level timestamps from the
   generated audio, formatted as ASS subtitles with CapCut-style
   pop animation and keyword highlighting.

3. **FFmpeg reel builder** — concatenates clips, burns ASS captions,
   adds a hook text overlay with fade animation, and mixes background
   music at -22 dB. Produces a publish-ready H.264 MP4.

**Identity verification.** ArcFace scores across frames of the
Phase 2 reel ranged from 0.672 to 0.710 (mean 0.695), compared to
the Phase 1 LoRA-generated still peak of 0.664. The Kling
multi-image reference approach matched or exceeded the fine-tuned
LoRA for identity consistency.

**Cost.** Kling API usage is approximately 0.8 units per second of
video at standard tier. A 10-second base clip plus lip-sync costs
roughly 12-16 units total. At the project's remaining balance of
~$7.30, this supports approximately 15-20 additional production runs.

**Remaining limitation.** Kling's maximum clip duration is 10 seconds.
Reels longer than 10 seconds require generating and concatenating
multiple clips, doubling the API cost per additional segment. The
reel builder's multi-clip concatenation supports this, but the
per-segment identity consistency has not been validated at scale.

## 7. Phase 3 Resolution: Native Joint Generation + Daily Pack Autopilot

Phase 2 worked but kept three persistent issues. Phase 3 (May 14) addressed
each with a different architectural fix.

**Problem 1 — Lip-sync drift at the end of multi-segment reels.**
The Phase-2 pipeline generated a silent multi-image-to-video reel with
Kling V1-6 then ran a *separate* lip-sync re-render against an
ElevenLabs voice track. Because the lip-sync pass treated the input
video as a starting point and re-rendered the face per frame against a
new audio timeline, accumulated sub-frame timing error showed visibly
as drifting mouth-shape vs. spoken word by the back half of the 30-
second reel.

*Fix.* Replaced the two-step pipeline with Kling 3.0 multi-shot
(`sound: true`). This single endpoint *jointly* generates video, native
speech, and lip-sync in one render — there is no compositing seam to
drift. For 30-second reels we chain two 15-second Kling renders;
concatenation now uses FFmpeg `aresample=async=1` with constant-rate
re-encode at 48 kHz / 30 fps so the audio is locked to video timestamps
across the join. Lip-sync now holds end-to-end.

**Problem 2 — Static-background "AI portrait" feel.**
The Avatar-Pro approach (image + audio → talking video) animated only
the face; the surrounding scene was a still image. The result reads as
an AI-generated render within the first two seconds. Top short-form
creators in the same niche (e.g. @andyhan.ai) explicitly use Veo 3 /
Kling 3.0 for the opposite reason: native joint generation produces a
*world* (passers-by, traffic, leaves, ambient motion) rather than a
frozen photo with a moving mouth.

*Fix.* Kling 3.0 multi-shot with `kling_elements` for face-locking
generates the entire world from scratch per render, including
environmental motion. Additionally, Claude visual prompts now *require*
explicit camera motion (handheld drift / push-in / dolly / orbit) and
2-3 named moving background elements; prompts that read as "locked-off"
or "static" are rejected at script-validation time.

**Problem 3 — Single-format output.**
Phase 2 produced reels only. Real creator feeds run a daily mix:
reels for reach, portrait quotes for saves, carousels for swipe-through
depth. A reel-only pipeline cannot sustain a creator account.

*Fix.* A new orchestrator, `scripts/daily_pack.py`, runs three content
types in parallel:

- **Reel** — Claude 6-beat script → 2× Kling 3.0 multi-shot → audio
  resync → Suno underbed.
- **Portrait** — Claude headline + caption → Nano Banana 2 face-locked
  scene edit → Pillow typography overlay → 1080×1350 PNG.
- **Carousel** — Claude 5-slide story (cover / problem / insight /
  proof / payoff) → 5× Nano Banana → 5× Pillow slides.

All three pieces use distinct outfit / mood / setting / topic seeds
within a single pack, with rotation tracked in
`autopilot_history.json`. Wall-clock: ~12 min per pack; spend: ~$5–7.

**Reliability improvements that landed in Phase 3:**

| Issue | Fix |
|---|---|
| `tmpfiles.org` rate-limiting mid-job (Kling could not fetch supplied URLs) | Switched every KIE-bound upload to KIE's own `file-stream-upload` on the `redpandaai.co` CDN |
| Concept-slide static backgrounds | Visual-prompt validator rejects prompts without motion + named moving elements |
| Outfit / posture repetition across packs | `autopilot.py` rotation pools (8 outfits / 8 moods / 5 setting pools); each pack picks 3 distinct combos avoiding the last 4-6 |
| Topics clustering on the same anecdote ("fired my X for AI") | 14-entry topic seed list, with intra-pack avoid-list passed to each piece's Claude call |

**Phase 3 results.** Two daily packs (six published pieces total)
shipped to YouTube + Instagram + LinkedIn between 19:43 and 21:08 on
May 14, with no human intervention. Lip-sync drift no longer observed.
Background motion verified on both reels. All six pieces in the two
packs use distinct outfits, moods, and topic seeds. The full pipeline
is now exposed as HTTP endpoints (`scripts/api_server.py`) for n8n /
Zapier / cron scheduling.

**What Phase 3 leaves open.** True 5-slide Instagram carousel (vs.
single-image cover) requires bypassing Composio's `INSTAGRAM_CREATE_
MEDIA_CONTAINER` validator and calling Meta's Graph API directly. X /
Twitter publishing is blocked on a one-time Composio OAuth dance for
the X toolkit. Optional ElevenLabs Speech-to-Speech voice-cloning to a
specific target voice is wired in `produce_reel_v16.py` but requires an
`ELEVENLABS_API_KEY` to activate; without it, packs ship with Kling
3.0's native voice.

---

## References

- Cui, J., et al. (2025). *Hallo3: Highly Dynamic and Realistic Portrait
  Image Animation with Video Diffusion Transformer*. CVPR.
- Meng, R., et al. (2024). *EchoMimic V2: Towards Striking, Simplified,
  and Semi-Body Human Animation*. CVPR 2025.
- Prajwal, K. R., et al. (2020). *A Lip Sync Expert Is All You Need for
  Speech to Lip Generation In The Wild*. ACM Multimedia.
- Zhang, W., et al. (2023). *SadTalker: Learning Realistic 3D Motion
  Coefficients for Stylized Audio-Driven Single Image Talking Face
  Animation*. CVPR.
