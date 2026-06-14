"""
youtube_oauth_setup.py — One-time OAuth flow to acquire a YouTube refresh token.

Why this exists:
  Composio's YOUTUBE_UPLOAD_VIDEO wrapper omits the COPPA-required
  `selfDeclaredMadeForKids` field, causing API uploads to be silently
  rejected at YouTube's processing stage. The fix is to bypass Composio for
  YouTube and call YouTube Data API v3 directly. To do that we need our own
  OAuth refresh token.

How to use it:
  1. Place your downloaded OAuth client JSON at:
     virtuai/persona/secrets/youtube_oauth_client.json
  2. Run:
     python virtuai/persona/scripts/youtube_oauth_setup.py
  3. A browser tab opens for Google's consent screen. Log in with the
     YOUTUBE CHANNEL'S Google account (the one that owns the channel — not
     your GCP-project account if they differ). Click "Allow".
  4. The script captures the refresh token and prints lines you should add
     to your .env file.

Run this ONCE. Refresh tokens don't expire unless idle 6 months or password
changes. After this, virtuai/tools/youtube_direct.py reads the token from
.env on each upload and is fully autonomous.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CLIENT_JSON = ROOT / "virtuai" / "persona" / "secrets" / "youtube_oauth_client.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def main() -> None:
    if not CLIENT_JSON.exists():
        print(f"ERROR: OAuth client JSON not found at: {CLIENT_JSON}")
        print(
            "Download your OAuth 2.0 Client ID JSON from Google Cloud Console "
            "and place it at the path above."
        )
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow

    print("=" * 60)
    print("  YouTube OAuth Setup — one-time refresh-token acquisition")
    print("=" * 60)
    print()
    print("A browser window will open. Sign in with the GOOGLE ACCOUNT that")
    print("owns your YouTube channel (the Daniel Calder one — typically")
    print("danielcalderdc1@gmail.com).")
    print()
    print("If a 'Google hasn't verified this app' warning appears:")
    print("  → click 'Advanced' → 'Go to VirtuAI Capstone (unsafe)'.")
    print("  This is normal for apps in Testing mode.")
    print()
    input("Press Enter to launch the browser…")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_JSON),
        scopes=SCOPES,
    )

    # access_type=offline + prompt=consent forces Google to issue a
    # refresh_token (otherwise it might only return an access_token
    # if this scope was previously granted to this account).
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        open_browser=True,
    )

    if not creds.refresh_token:
        print()
        print("ERROR: No refresh_token returned by Google.")
        print(
            "This usually means you've authorized this client before and "
            "Google didn't re-issue a refresh token. Fix:\n"
            "  1. Go to https://myaccount.google.com/permissions\n"
            "  2. Find your VirtuAI Capstone app and revoke access\n"
            "  3. Re-run this script"
        )
        sys.exit(1)

    print()
    print("=" * 60)
    print("  ✓ OAuth flow complete.")
    print("=" * 60)
    print()
    print("Add these three lines to your .env file:")
    print()
    print(f"YOUTUBE_OAUTH_CLIENT_ID={creds.client_id}")
    print(f"YOUTUBE_OAUTH_CLIENT_SECRET={creds.client_secret}")
    print(f"YOUTUBE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print()
    print("After saving, you can publish to YouTube via VirtuAI's pipeline.")


if __name__ == "__main__":
    main()
