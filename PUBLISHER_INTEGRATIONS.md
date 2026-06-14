# Publisher Integrations: Wiring the Persona to Production Platforms

> Suggested location in the capstone report: a chapter immediately after the
> persona-stack description (face, voice, text) and before the
> safety/Guardian chapter. ~2,400 words. This section documents the
> distribution layer of the system — how content produced by the persona
> reaches real users on real platforms — and the engineering decisions and
> external constraints that shaped that layer.

## 1. Problem Statement

The first half of this project produced an early on-device persona prototype
and an autonomous content-generation pipeline orchestrated by CrewAI agents.
Those on-device generators were later superseded by the final cloud workflow
(the KIE.ai gateway — Claude Sonnet 4.6, Kling 3.0 with a Seedance 2.0 fallback,
Nano Banana 2, Suno). The output of that Phase-1 pipeline, however, lived only on disk.
For VirtuAI to satisfy its capstone framing — *"an autonomous AI persona
that publishes to platforms without human intervention"* — that output
needed to leave the laptop and reach LinkedIn, Instagram, YouTube, and the
other social channels in the original scope.

This chapter describes the distribution layer we built to close that gap:
the eighth agent (the **Publisher**), the third-party orchestration tools
we evaluated, the three distinct authentication patterns we ended up
implementing across four live platforms, and the three platforms we
documented as out of reach for capstone scope. It also describes the one
significant gap we identified in our chosen orchestration vendor's SDK and
the workaround we engineered around it.

## 2. The Publisher Agent

The Publisher Agent sits at position 7 of the 8-agent CrewAI pipeline
(`virtuai/pipelines/content_pipeline.py`), between the Guardian
(safety/ethics review) and the Analyzer (post-publication tracking).
Its task description is structured as a routing decision: for each item
in the Guardian's report, the agent looks at the verdict (`APPROVE` /
`REVISE` / `BLOCK`) and the platform field, and dispatches to the
matching publishing tool. `REVISE` and `BLOCK` items are skipped. Items
with no available tool for their platform are recorded as
`tool_unavailable` in the publish report rather than discarded silently —
a deliberate choice so that downstream analytics can distinguish *was
not approved* from *was not deliverable*.

The Publisher's tool list (defined in `virtuai/agents/publisher_agent.py`)
is constructed at agent-build time from a combination of Composio's hosted
SDK and one direct-API integration. The composition is intentional and
documented below.

## 3. Choice of Orchestration Layer

Three orchestration vendors were considered:

- **Zapier**, the historical incumbent, has the broadest catalog of
  application connectors. However, write-side webhooks are gated behind
  the paid Starter tier ($19.99/month after a 14-day trial). For a
  capstone running on a student budget, this is a recurring cost on top
  of platform-level paywalls (see §6).
- **n8n**, an open-source self-hosted workflow tool, was rejected
  because it adds an additional infrastructure component (a long-running
  service) to a project whose entire value proposition is local-first
  operation.
- **Composio**, a more recent entrant with a Python SDK and a CrewAI
  provider, exposes connectors as Python tools that integrate naturally
  with our existing CrewAI agents. Its free tier covers our usage volume
  and its "Composio Managed" OAuth feature avoids the need to register a
  developer application with each platform individually.

We selected Composio. The integration lives in
`virtuai/tools/composio_tools.py` and exposes a uniform tool list that the
Publisher Agent loads alongside its Composio-Managed authentication
configurations. A second module, `virtuai/tools/youtube_direct.py`,
implements a direct-to-Google-API path used for one platform where the
Composio wrapper proved insufficient. The reason for that detour is the
subject of §5.

## 4. Three Authentication Patterns

The four live platform integrations exercise three meaningfully
different authentication patterns. We list them in increasing order of
complexity because each pattern reveals a different class of
real-world constraint.

### 4.1 Composio Managed OAuth — LinkedIn, Facebook

The simplest case. We register an Auth Config in Composio's dashboard
under our project, click "Connect Account," log in to the platform with
the persona's account, and bind the resulting connection to a stable
`user_id` (`danielcalder-`). The OAuth client itself is registered and
maintained by Composio across all of their users; we do not own it. From
the SDK side, calling
`tools.execute('LINKEDIN_CREATE_LINKED_IN_POST', arguments={...},
user_id=...)` is sufficient — Composio handles token storage, refresh,
and the actual HTTP call to the platform.

This pattern handled LinkedIn (single-action posting) and Facebook (Page
post, requires the numeric `page_id` of the Daniel Calder Page, which we
captured during the OAuth Page-selection step and stored in `.env`).

A small but real subtlety surfaced during LinkedIn integration: the
`commentary` field name in Composio's wrapper differs from LinkedIn's
documented `text` field. We flagged this in the Publisher Agent's
backstory so the agent does not infer the field name from generic
post-publishing intuition.

### 4.2 Composio Managed + Multi-Step Meta Graph Chain — Instagram

Instagram's API requires three preconditions Meta enforces at the platform
level, none of which Composio can substitute for:

1. The Instagram account must be a **Business or Creator account**
   (personal accounts cannot publish via API).
2. The Instagram account must be **linked to a Facebook Page** owned by
   a Facebook account in good standing.
3. Publishing is a **two-step API call**: `INSTAGRAM_CREATE_MEDIA_CONTAINER`
   accepts an `image_url` (publicly accessible — local files are not
   supported, Meta crawls the URL itself) and returns a `creation_id`,
   which is then passed to `INSTAGRAM_CREATE_POST`.

We satisfied requirement 1 by switching the persona's Instagram account
to Creator mode. Requirement 2 became blocking when Meta disabled the
project owner's existing Facebook account, which prevented linking the
Instagram account to any Page. Resolving this required a teammate to
create a fresh Facebook account, create a Page from it, link our IG
account to that Page, then OAuth into Composio with the new credentials.
Requirement 3 we handle in the Publisher's task description (and in the
demo dispatcher) with explicit two-step logic.

A separate piece of friction emerged here: Meta's Graph API requires the
**IG Business Account ID** as input to both publishing calls, but
Composio does not expose this identifier through its connection metadata.
The conventional way to obtain it (Facebook's Graph API Explorer)
requires a Meta Developer registration — which Meta's anti-fraud system
declined to grant for the email associated with the persona's account.
We resolved this by querying Composio's `INSTAGRAM_GET_USER_INFO` action,
which returns the IG Business Account ID without requiring it as input.
We persisted the value in `.env` as `IG_USER_ID` and re-ran the publish
call successfully.

The Instagram path is therefore the most complex of the four live
platforms: a five-account dependency chain (capstone owner → teammate
→ new Facebook account → Facebook Page → Instagram account) that must
be intact for any post to succeed. We documented this chain explicitly
because it represents a real operational risk: any one link breaking
(Facebook account ban, Page deletion, IG account suspension) stops
publishing entirely with no graceful fallback.

### 4.3 BYO OAuth — YouTube

The third pattern emerged not by design but in response to a specific
Composio limitation that we discuss in §5. After diagnosing the issue,
we registered our own Google Cloud project (`virtuai-capstone`), enabled
the YouTube Data API v3, configured an OAuth consent screen, created
Desktop-application OAuth credentials, and ran a one-time consent flow
with `google-auth-oauthlib` to obtain a refresh token. The refresh token
is stored in `.env` (`YOUTUBE_OAUTH_REFRESH_TOKEN`); on each upload the
`youtube_direct.py` module trades it for a short-lived access token at
`oauth2.googleapis.com/token` and uses that to drive a resumable upload
directly against `googleapis.com/upload/youtube/v3/videos`.

This pattern carries more setup cost than Composio Managed (~20 minutes
for the GCP/OAuth configuration vs. ~2 minutes for a Composio click-through)
and means we maintain the OAuth client ourselves. In return we gain
exact control over the upload payload — which is what made it necessary
in the first place.

## 5. The COPPA Field Gap and the BYO Workaround

The most consequential technical discovery of the integration phase was a
silent data-loss issue in Composio's `YOUTUBE_UPLOAD_VIDEO` wrapper.

Symptom: API calls to upload a video would return `successful: True` with
a YouTube video ID, but every uploaded video would appear in YouTube
Studio's content tab with the status "Processing abandoned — the video
could not be processed." The same video file, uploaded manually through
YouTube Studio's web interface, processed normally. We confirmed across
multiple re-encodings (including baseline H.264 with `bt709` color space
and faststart-flagged MP4 atoms) that the file itself was acceptable to
YouTube; the difference had to be in the API request.

Diagnosis required reading Composio's tool schema and comparing it
against YouTube's official API. YouTube's `videos.insert` endpoint
requires a `selfDeclaredMadeForKids` boolean field as part of the
`status` resource — a 2020 COPPA-driven addition that became mandatory
shortly after introduction. Composio's `YOUTUBE_UPLOAD_VIDEO` schema
omits this field. When the field is absent from an API request,
YouTube's processing pipeline silently abandons the upload after
returning a successful `videos.insert` response. From the API caller's
perspective the upload succeeded; from the user's perspective in Studio
the video never plays.

We attempted three mitigations before settling on the chosen path:

1. **Pass the field as an extra argument to `tools.execute`.** Composio
   accepted the call without error but appears to drop fields not in its
   declared schema before forwarding to YouTube. The downstream behaviour
   was identical to omitting the field.
2. **Pull Composio's stored access token and call YouTube directly.**
   Composio deliberately redacts OAuth tokens in the SDK return value as
   a security feature. The literal string `REDACTED` is returned in the
   `access_token` field, making this path infeasible.
3. **Use `YOUTUBE_UPDATE_VIDEO` after the upload to set the field
   retroactively.** Composio's update wrapper also omits the field.

The mitigation that worked was the BYO OAuth pattern described in §4.3,
implemented in `virtuai/tools/youtube_direct.py`. We bypass Composio's
wrapper entirely for YouTube, retain Composio for the other three live
platforms, and expose the direct-API path to the Publisher Agent as a
CrewAI tool (`YOUTUBE_DIRECT_UPLOAD`) that takes the same shape as a
Composio tool. From the agent's perspective the integration is uniform;
the heterogeneity is hidden in the tool factory.

We have not reported this issue to Composio because we had not exhausted
that channel by submission. The workaround is in place, well-tested, and
carries no operational disadvantage other than maintaining one more set
of OAuth credentials.

## 6. Documented Exclusions

Three platforms in the original scope were dropped during the integration
phase. Each illustrates a different class of external blocker.

| Platform | Class of blocker | Detail |
|---|---|---|
| **X (Twitter)** | Platform-level paywall | X removed free API write access in 2023, and the Basic developer tier ($100/month) is the lowest plan that permits programmatic posting. X was evaluated and **dropped from the project scope** (it was replaced by Facebook Page publishing in May 2026); the paid API cost was not justified for the prototype, and the X publisher code was removed. |
| **Threads** | Platform-vendor gate | Meta's Threads API is documented and free to use, but requires registering a Meta App with Threads-specific permissions, which in turn requires a Meta Developer account. The persona's owning email was rejected for developer registration by Meta's anti-fraud system, and Composio does not yet ship a Threads toolkit that would substitute for our own developer registration. Without either path, programmatic Threads posting is not currently available to this project. |
| **Medium** | Platform-side API deprecation | Medium stopped issuing Integration Tokens for new accounts in 2023 and no longer maintains a public posting API for new applications. Composio does not list a Medium toolkit. The available substitute paths — Zapier's grandfathered Medium connector, or attempting to obtain a legacy Integration Token by direct request to Medium — both involve external dependencies (a paid Zapier subscription in the first case, or arbitrary-discretion approval from Medium in the second). For a long-form distribution channel, the project uses LinkedIn's article format instead, which is already integrated. |

These exclusions are not gaps in the engineering; they are accurate
characterisations of the current state of each platform's developer
ecosystem. We list them because a complete capstone deliverable should
distinguish between *did not build* and *built and constrained by an
external party*.

## 7. Verification

Phase 1 verification consisted of running a deterministic publisher
script (`virtuai/persona/scripts/demo_publisher.py`) that bypasses the
CrewAI agent layer and calls the Publisher's tools directly with a
synthetic Guardian report. This isolates the integration correctness
from agent tool-calling reliability. (In the Phase-1 prototype this also worked
around the on-device Phi-3.5 MLX model exhausting unified memory before the
Publisher step; the final system runs agent reasoning through the KIE.ai gateway,
so that constraint no longer applies.) The deterministic
script confirms that the integration code paths produce real,
verifiable posts on all four live platforms. A future Phase 2 effort
involves running the full crew with a smaller-footprint LLM
configuration so the autonomous-end-to-end claim is fully demonstrable
in one process.

For the four live platforms, the demonstration outputs are:

| Platform | Verification artifact |
|---|---|
| LinkedIn | Live post URL on the persona's LinkedIn feed (URN format `urn:li:share:<id>`) |
| YouTube | Live video on the persona's YouTube channel (`youtube.com/watch?v=<id>`) |
| Instagram | Live image post on the persona's Instagram profile (post id format `<numeric>`) |
| Facebook | Live post on the Daniel Calder Facebook Page (post id format `<page_id>_<post_id>`) |

All four were published without manual intervention beyond invoking the
deterministic script. The Instagram post used a placeholder image hosted
externally (catbox.moe) for the integration test; replacing this with
pipeline-rendered persona-consistent images is the first task of Phase 2.

## 8. Summary

Phase 1 produced four live, autonomous platform integrations
demonstrating three distinct authentication patterns: Composio Managed
OAuth (LinkedIn, Facebook), Composio + multi-step Meta Graph chain
(Instagram), and BYO OAuth via Google Cloud (YouTube). The BYO path was
forced by a documented schema gap in Composio's YouTube wrapper that
silently breaks every API upload by omitting a required COPPA field;
the workaround is robust, well-tested, and isolated to one tool module.
Three platforms were excluded with documented external blockers — paid
API tier (X), platform-vendor developer-registration gate (Threads), and
public-API deprecation (Medium) — none of which represent project-side
gaps. The Publisher Agent's tool list is now uniform from the agent's
perspective regardless of which underlying integration pattern is in
use, so the autonomous publishing flow is identical across all four live
platforms.
