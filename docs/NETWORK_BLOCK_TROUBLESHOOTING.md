# Network Block Troubleshooting

_What to do when KIE's temporary CDN host (`tempfile.aiquickdraw.com`) is unreachable from your network and the demo's asset-download step hangs or fails. Diagnosed and reproduced on this codebase on 2026-05-19/20._

---

## TL;DR

If your demo run shows either of these errors **after generation succeeds**:

```
httpx.ConnectError:  [SSL: CERTIFICATE_VERIFY_FAILED]
                     certificate verify failed: unable to get local issuer certificate
```

or

```
httpcore.ConnectTimeout: [Errno 60] Operation timed out
```

you are almost certainly on a network whose IPS / web-filter is blocking `tempfile.aiquickdraw.com`. **It is not a code bug.** Switch network (mobile hotspot, home WiFi, VPN) and rerun. The SSL workaround alone won't fix it.

---

## How the symptom presents

In the API server log:

```
[INFO] produce_images:   nano-bg: success
[INFO] produce_reel_v16: kling-A: success
[INFO] produce_reel_v16: kling-B: success
[ERROR] api_server: pack failed
httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] ...
```

Generation completed — both Kling reels and all 5 Nano Banana backgrounds rendered fine on KIE's side. The pipeline died at the download step in `scripts/produce_reel_v16.py::download_first()` which pulls the rendered mp4 from `https://tempfile.aiquickdraw.com/...`.

---

## Step-by-step diagnosis

### 1. Confirm KIE.ai (the API) is reachable

```bash
curl -sI --connect-timeout 5 https://api.kie.ai/ | head -2
```

If this prints any HTTP response (`200`, `403`, `404` — anything), the API host is fine. Move on. If it hangs, your KIE_API_KEY also can't reach the API — that's a different (worse) block.

### 2. Probe the CDN host

```bash
curl -kI --connect-timeout 10 https://tempfile.aiquickdraw.com/ 2>&1 | head -3
```

Look at the headers. There are three diagnostic outcomes:

| Output | What it means |
|---|---|
| `HTTP/2 200` or `HTTP/1.1 200` | Reachable. Network is clean. Your failure is from something else — check the API server log. |
| `Location: https://10.<x>.<y>.<z>:<port>/ips/block/...` | **Your network's IPS gateway is blocking this host.** No SSL workaround will help. |
| `Connection timed out` after the full timeout | TCP can't reach the host. Either DNS poisoning, firewall, or routing block. |
| `SSL certificate problem: unable to get local issuer certificate` (and nothing else) | Genuine upstream cert-chain issue. Our SSL workaround DOES help here. |

### 3. If you saw a `Location:` to an internal IP

That's an IPS / web-filter signature. Example we observed on 2026-05-19:

```
HTTP/1.1 307 Temporary Redirect
Location: https://10.214.28.254:8090/ips/block/webcat?cat=46&pl=1&lu=0&url=...
```

The `cat=46` is the filter's category code. The `10.214.28.254:8090` is the internal block-page server — unreachable from outside the corporate network.

This setup also explains why the SSL error appears in the first place: the IPS gateway is doing **TLS MITM** with its own corporate root cert, which `httpx`/`curl` (and the Python `certifi` bundle) correctly reject.

### 4. Verify your SSL workaround correctly engaged

If the API server was started with `VIRTUAI_TRUST_KIE_CDN=true`, you should see this WARNING line in `/tmp/virtuai_api.log` for every blocked download:

```
[WARNING] virtuai.utils.asset_download: [KIE-CDN-WORKAROUND] SSL verification
  TEMPORARILY DISABLED for asset download from host 'tempfile.aiquickdraw.com'...
```

If the warning **does** appear and the download **still** fails — that's the IPS block. The workaround bypassed the (legitimately rejected) MITM cert but the request then ran into the block-page redirect to an internal IP, which can't be routed.

If the warning **does not** appear, the env var didn't reach the right process. Restart the API server with the env var set:

```bash
pkill -f "uvicorn scripts.api_server"; sleep 2
nohup env VIRTUAI_TRUST_KIE_CDN=true ./.venv/bin/python -m uvicorn \
    scripts.api_server:app --host 0.0.0.0 --port 9090 \
    > /tmp/virtuai_api.log 2>&1 &
```

Confirm:

```bash
ps eww $(pgrep -f "uvicorn scripts.api_server" | head -1) | grep -o "VIRTUAI_TRUST_KIE_CDN=[^ ]*"
# expected: VIRTUAI_TRUST_KIE_CDN=true
```

---

## Why the SSL workaround alone can't beat the IPS

The KIE CDN SSL workaround at `virtuai/utils/asset_download.py` does exactly one thing: pass `verify=False` to `httpx.Client` for that single request, but only when:

1. `VIRTUAI_TRUST_KIE_CDN=true` is in the environment, **AND**
2. the URL's host is in the small KIE-CDN allowlist (currently just `tempfile.aiquickdraw.com`)

When the IPS gateway is doing TLS MITM, `verify=False` will let the TLS handshake complete with the gateway's cert. **But the gateway then returns a 307 redirect to its own block page**, which sits on a private IP your host can't route to. So the next request immediately fails with `ConnectTimeout`.

In short:
- The workaround **fixes** a real upstream cert-chain issue at the CDN.
- The workaround **cannot fix** a network policy that doesn't want you to reach the host at all.

---

## Three ways to unblock

| Option | Difficulty | Notes |
|---|---|---|
| **Switch network** | easiest | Phone hotspot (5G), home WiFi, café WiFi — any non-corporate connection where the host isn't filtered |
| **Use a VPN** | medium | Must egress outside the corporate filter. A consumer VPN is usually enough; some enterprise VPNs route through the same filter (no help) |
| **Whitelist at the network admin** | hardest | Ask IT to whitelist `tempfile.aiquickdraw.com` (and ideally `*.aiquickdraw.com`) in category `46` or whatever your filter calls it |

After any of these, re-run:

```bash
curl -sI --connect-timeout 6 https://tempfile.aiquickdraw.com/ 2>&1 | head -2
```

When that returns an HTTP response (not a redirect to `10.*` and not a timeout), you are clear to run the live demo:

```bash
python scripts/demo.py --no-publish
```

The cert-chain problem (independent issue) may still need the workaround. Enable it only if needed:

```bash
export VIRTUAI_TRUST_KIE_CDN=true
python scripts/demo.py --no-publish
unset VIRTUAI_TRUST_KIE_CDN
```

---

## Demo Day Fallback — Plan B

If you cannot unblock the network in time:

1. Run `python scripts/agent_cli.py --pipeline-check --offline` — 23 / 23 green, no network required
2. Run `python scripts/agent_cli.py --validate-latest` — proves the schema layer works on a real saved package
3. Open the latest reel mp4 from `virtuai/data/generated_videos/` — 216 are on disk from prior successful runs
4. Open the latest daily-pack JSON from `virtuai/data/content_packages/` — shows YouTube URL, LinkedIn URN, IG media ID for that pack

This Plan B is **fully demonstrable** without touching the network. See `docs/DEMO_PRESENTATION_SCRIPT.md` §6:30 for the exact walkthrough.

---

## Confirm a fix worked end-to-end

After switching network or installing a VPN:

```bash
# 1. Reach probe — must NOT redirect to 10.*
curl -kI --connect-timeout 6 https://tempfile.aiquickdraw.com/ 2>&1 | head -3

# 2. Helper-level probe
VIRTUAI_TRUST_KIE_CDN=true ./.venv/bin/python -c "
from virtuai.utils.asset_download import _should_relax_verify, download_generated_asset
print('relax check :', _should_relax_verify('https://tempfile.aiquickdraw.com/x'))
# Real download — picks any asset still alive on KIE's CDN
# (Test only when you have a known-valid URL.)
"

# 3. Restart the API server with the env var
pkill -f "uvicorn scripts.api_server"; sleep 2
nohup env VIRTUAI_TRUST_KIE_CDN=true ./.venv/bin/python -m uvicorn \
    scripts.api_server:app --host 0.0.0.0 --port 9090 \
    > /tmp/virtuai_api.log 2>&1 &
until curl -sf -o /dev/null http://localhost:9090/healthz; do sleep 2; done

# 4. Run the demo
python scripts/demo.py --no-publish
```

If step 4 produces `outputs/.../reel.mp4`, you are unblocked.

---

## When to roll back the SSL workaround

The KIE CDN's cert chain issue is upstream and will eventually be fixed. After it's fixed, leaving `VIRTUAI_TRUST_KIE_CDN=true` in any persistent shell or `.env` file is a small but real security liability. Roll back:

```bash
# In any persistent file (.env / .zshrc / .bashrc): make sure it is FALSE (or absent)
unset VIRTUAI_TRUST_KIE_CDN

# Restart API server without the env var
pkill -f "uvicorn scripts.api_server"; sleep 2
nohup ./.venv/bin/python -m uvicorn scripts.api_server:app \
    --host 0.0.0.0 --port 9090 > /tmp/virtuai_api.log 2>&1 &
```

Quick test to confirm the rollback:

```bash
curl -sI --connect-timeout 6 https://tempfile.aiquickdraw.com/ 2>&1 | head -2
# When this returns HTTP 200 WITHOUT any "SSL certificate problem" — upstream is healthy.
```

---

## Quick reference

| Diagnostic | Command | Meaning |
|---|---|---|
| Is the CDN reachable? | `curl -kI --connect-timeout 6 https://tempfile.aiquickdraw.com/` | Look for HTTP response vs `Location: 10.*` |
| Is the API host reachable? | `curl -sI --connect-timeout 5 https://api.kie.ai/` | Should be 200/403/404 (anything that's not a hang) |
| Did the workaround engage? | `grep KIE-CDN-WORKAROUND /tmp/virtuai_api.log` | Two WARNING lines per blocked download |
| Did env reach the API? | `ps eww $(pgrep -f uvicorn) \| grep VIRTUAI_TRUST_KIE_CDN` | `VIRTUAI_TRUST_KIE_CDN=true` should appear |
| Does the locked baseline still verify? | `cd virtuai/locked/v1_2026-05-18 && shasum -a 256 -c manifest.sha256` | Every line must say `OK` |
