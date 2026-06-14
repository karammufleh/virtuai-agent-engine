# KIE CDN Download Workaround

_Last updated 2026-05-20. Opt-in workaround for downloading already-rendered assets when KIE's temporary CDN is serving an incomplete TLS certificate chain. Strictly limited to one host. The KIE API itself, Composio, YouTube Direct, and every other API endpoint always use full SSL verification._

---

## What happened

A live demo run on 2026-05-19 successfully generated:
- Two Kling 3.0 multi-shot reel renders (face-locked, native lipsync)
- Five Nano Banana 2 background renders (carousel slides)

But the post-render download step from `https://tempfile.aiquickdraw.com/...` failed with:

```
httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]
  certificate verify failed: unable to get local issuer certificate
```

The KIE API calls themselves (`/jobs/createTask`, `/jobs/recordInfo`) worked fine — only the asset-download from the temporary CDN failed.

## Why generation succeeded but download failed

Different hosts:
- `api.kie.ai` — KIE's API host — chain is valid, all calls succeeded
- `tempfile.aiquickdraw.com` — KIE's temporary CDN for finished assets — serving an **incomplete** chain (the intermediate CA isn't being sent)

Confirmed independently:
- `curl https://tempfile.aiquickdraw.com` with macOS keychain → fails the same way
- Python with the freshest `certifi` 2026.02.25 (272 KB) → fails the same way

That's an upstream KIE infrastructure issue, not a project bug, and not something this codebase can fix.

## How to enable the workaround temporarily

Two conditions are both required for the workaround to engage:

1. The environment variable `VIRTUAI_TRUST_KIE_CDN=true` is set.
2. The URL's hostname is `tempfile.aiquickdraw.com` (the only entry in the allowlist).

When (and only when) both hold, SSL verification is disabled for that **one** httpx request and a `WARNING` is logged naming the host.

```bash
# Enable the workaround (terminal session only)
export VIRTUAI_TRUST_KIE_CDN=true

# Run the demo — generation + download both work
python scripts/demo.py --no-publish

# Disable it again as soon as you're done
unset VIRTUAI_TRUST_KIE_CDN
```

## What is NOT affected by this workaround

The workaround is fenced off by an explicit deny-list and a tiny allow-list:

| Host | Workaround engages? |
|---|---|
| `api.kie.ai` | **Never** — explicitly in `_NEVER_RELAX_HOSTS` |
| `kieai.redpandaai.co` (KIE file upload) | **Never** — explicitly in `_NEVER_RELAX_HOSTS` |
| `api.anthropic.com`, `api.openai.com` | **Never** — explicitly in `_NEVER_RELAX_HOSTS` |
| `api.json2video.com`, `api.creatomate.com`, `api.shotstack.io`, `api.elevenlabs.io` | **Never** — explicitly in `_NEVER_RELAX_HOSTS` |
| Any random / unknown domain | **Never** — not on the allowlist |
| Subdomain like `evil.tempfile.aiquickdraw.com` | **Never** — exact-match only |
| `tempfile.aiquickdraw.com` (KIE temporary CDN) | Only when `VIRTUAI_TRUST_KIE_CDN=true` |

Composio publishing, YouTube Direct OAuth uploads, and every API call to the KIE gateway always use full SSL verification regardless of this env var.

## Security note

Disabling certificate verification — even for one host — is a real security relaxation. The risk is mitigated here by:

- **Default off.** If the env var is missing or false, behavior is identical to today.
- **Allowlist of one host.** Only `tempfile.aiquickdraw.com` qualifies.
- **Deny-list for API endpoints.** Even if the allowlist grows accidentally, the KIE API can never get relaxed verification.
- **Logged warning.** Every relaxed request emits a WARNING with the hostname so the workaround can't run silently.

Still: **only enable the workaround for the duration you need it, and unset the env var immediately after.** Do not commit `VIRTUAI_TRUST_KIE_CDN=true` to `.env`.

## Exact commands

```bash
# 1) Enable for a single run
export VIRTUAI_TRUST_KIE_CDN=true
python scripts/demo.py --no-publish
unset VIRTUAI_TRUST_KIE_CDN

# 2) Verify the workaround respects the allowlist
python -c "
import os
os.environ['VIRTUAI_TRUST_KIE_CDN'] = 'true'
from virtuai.utils.asset_download import _should_relax_verify
print('tempfile.aiquickdraw.com →', _should_relax_verify('https://tempfile.aiquickdraw.com/v/x.mp4'))
print('api.kie.ai             →', _should_relax_verify('https://api.kie.ai/api/v1/jobs/createTask'))
print('example.com            →', _should_relax_verify('https://example.com/x'))
"
# Expected:
#   tempfile.aiquickdraw.com → True
#   api.kie.ai             → False
#   example.com            → False

# 3) Run the safety tests
.venv/bin/python -m pytest virtuai/tests/test_asset_download.py -v
```

## Files involved

| Path | Purpose |
|---|---|
| `virtuai/utils/asset_download.py` | The helper + the host allowlist + the env-var gate |
| `scripts/produce_reel_v16.py::download_first()` | Now delegates to the helper (minimal 3-line patch) |
| `virtuai/tests/test_asset_download.py` | 14 tests covering every safety property |
| `docs/KIE_CDN_DOWNLOAD_WORKAROUND.md` | This document |

`virtuai/pipelines/content_pipeline.py`, the n8n workflow, Composio, and YouTube Direct were **not** touched.

## When to remove this workaround

Remove the workaround (or stop using the env var) as soon as either:

- KIE fixes the cert chain on `tempfile.aiquickdraw.com` (likely within hours-to-days)
- You move asset downloads through a different, properly-chained CDN

You can confirm the upstream is fixed with:

```bash
curl -sIv https://tempfile.aiquickdraw.com/  2>&1 | grep -E "SSL certificate|HTTP/"
```

When that prints `HTTP/2 200` (or similar) without `SSL certificate problem:`, the workaround is no longer needed.
